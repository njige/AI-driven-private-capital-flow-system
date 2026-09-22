#!/usr/bin/env python3
"""
check_vlm.py — find out why the cloud VLM is not extracting your documents.

Run it in the SAME virtualenv you start uvicorn with. It finds the backend root
itself (the folder containing `app/`), so it works from either location and from
any other directory:

    python check_vlm.py                        # from the backend root
    python app/check_vlm.py                    # from the backend root, file in app/
    python check_vlm.py "C:\\path\\to\\a\\filing.pdf"

It walks the chain — package, key, .env, client, model, upload, real extractor —
and reports each link separately, so the output points at the broken one instead
of "extraction failed".

Two things worth knowing before you read the output:

  * The model list it probes is the APP's own list: it asks
    `app.services.ai_extractor.candidate_models()` which names will actually be
    tried, so the report cannot drift from what the server does.
  * Your PDF is uploaded TWICE when you pass it on the command line — once by
    the upload probe (step 5) and once by the real extractor (step 6). Each copy
    is deleted from the provider when its step ends. Nothing touches your
    database.
"""
from __future__ import annotations

import importlib
import os
import sys
import textwrap
import traceback
from pathlib import Path

MASK = "\u2588"
LINE = "=" * 72

# The provider's own 404 text names this as the replacement for the retired
# `gemini-2.5-flash` (see the comment above MODEL_NAME in ai_extractor.py), so it
# is worth probing even when the app does not list it.
PROVIDER_SUGGESTED = "gemini-3.6-flash"

# ---------------------------------------------------------------------------
# Locate the backend root so `app.*` imports resolve, wherever this file sits.
# The file is used from the backend root AND from inside app/ — `python
# app/check_vlm.py` puts app/ on sys.path, not the root, and then every
# `from app...` import fails for a reason that has nothing to do with the VLM.
# ---------------------------------------------------------------------------
def _looks_like_backend_root(candidate: Path) -> bool:
    """True when `candidate` contains an `app` package.

    Deliberately does NOT require app/__init__.py. A project may use implicit
    namespace packages (PEP 420, Python 3.3+) and have no __init__.py anywhere —
    `uvicorn app.main:app` and `from app.routers import ...` both work fine in
    that layout. Requiring the marker file made this script refuse to run on a
    project where everything else was working.
    """
    app_dir = candidate / "app"
    if not app_dir.is_dir():
        return False
    return any(
        (app_dir / marker).exists()
        for marker in ("__init__.py", "main.py", "routers", "services",
                       "schemas", "core", "db", "config.py")
    )

_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT: Path | None = None
for _candidate in (_HERE, _HERE.parent, Path.cwd(), Path.cwd().parent):
    if _looks_like_backend_root(_candidate):
        _PROJECT_ROOT = _candidate
        if str(_candidate) not in sys.path:
            sys.path.insert(0, str(_candidate))
        break

results: list[tuple[str, bool, str]] = []

# Checks that failed for expected reasons (a key that lives in .env and is read
# by the startup file is "not in this shell" by design). The summary must not
# point at one of these as the thing to fix.
informational: set[str] = set()


def step(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}]  {name}")
    if detail:
        for line in detail.splitlines():
            print(f"          {line}")


def fail_help(text: str) -> None:
    """Print a remediation block with consistent indentation.

    The blocks are written as triple-quoted strings indented to match the
    surrounding code, so they are dedented first — otherwise the first line
    (stripped) and the rest (still indented) line up differently.
    """
    print()
    print("  " + "-" * 68)
    for line in textwrap.dedent(text).strip().splitlines():
        print(f"  {line}")


def mask(value: str) -> str:
    if not value:
        return "(empty)"
    if len(value) <= 10:
        return MASK * len(value)
    return f"{value[:6]}{MASK * 8}{value[-4:]}  (length {len(value)})"


# Directories that never contain the filing the user meant.
_WALK_SKIP = {"node_modules", ".git", ".venv", "venv", "__pycache__", "dist",
              "build", ".next", ".mypy_cache", ".pytest_cache"}


def _walk_files(base: Path, depth: int = 4):
    """Yield files under `base`, bounded in depth and skipping noise dirs."""
    if not base.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(base):
        here = Path(dirpath)
        if len(here.relative_to(base).parts) >= depth:
            dirnames[:] = []
        dirnames[:] = [d for d in dirnames if d not in _WALK_SKIP and not d.startswith(".")]
        for f in filenames:
            yield here / f


def find_pdf(given: str, root: Path | None) -> tuple[Path | None, list[str]]:
    r"""Resolve the PDF the user named on the command line.

    `python check_vlm.py "..\uploads\filing.pdf"` names a different file from
    every directory you can stand in, and the old step 5 stopped at
    `[FAIL] ..\uploads\filing.pdf exists` without saying where it looked or
    what it found. So: try the path as typed, then against the backend root and
    the project root, then by file name anywhere near the project.
    """
    tried: list[str] = []
    raw = Path(given)
    candidates = [raw]
    if not raw.is_absolute() and root is not None:
        candidates += [root / raw, root.parent / raw]
    for cand in candidates:
        tried.append(str(cand))
        try:
            if cand.is_file():
                return cand.resolve(), tried
        except OSError:
            pass
    name = raw.name.lower()
    for base in ([root.parent, root] if root is not None else []):
        for path in _walk_files(base):
            if path.name.lower() == name:
                tried.append(str(path))
                return path.resolve(), tried
    return None, tried


def nearby_pdfs(root: Path | None, limit: int = 6) -> list[Path]:
    """The PDFs that ARE near the project, shortest path first — so a typo in the
    name still ends in a command line that works."""
    if root is None:
        return []
    seen: set[str] = set()
    found: list[Path] = []
    for base in (root.parent, root):
        for path in _walk_files(base):
            if path.suffix.lower() == ".pdf" and str(path) not in seen:
                seen.add(str(path))
                found.append(path)
    return sorted(found, key=lambda q: (len(str(q)), str(q)))[:limit]


def under_root(relative: str) -> Path:
    """A project path, resolved against the backend root — NOT the cwd.

    This is the fix for the worst bug this script had: `Path("app/main.py")`
    resolved against wherever you happened to stand, so running it from any
    directory other than the root reported "does not exist" for every startup
    file and then told you to go and edit a main.py that was already correct.
    """
    root = _PROJECT_ROOT if _PROJECT_ROOT is not None else Path.cwd()
    return root / relative


# ===========================================================================
print()
print(LINE)
print("  STEP 0 — what is installed")
print(LINE)

print(f"  python            : {sys.version.split()[0]}")
print(f"  working directory : {os.getcwd()}")
print(f"  script location   : {Path(__file__).resolve()}")
print(f"  backend root      : {_PROJECT_ROOT if _PROJECT_ROOT else 'NOT FOUND'}")

if _PROJECT_ROOT is None:
    step("found the backend root (the folder containing your app package)", False)
    fail_help("""
    Could not find a folder containing an `app` package, searching this script's
    directory, its parent, and the working directory.

    Copy check_vlm.py into the BACKEND ROOT — the folder that has `app/` directly
    inside it, the same one you run uvicorn from — and run it there:

        python check_vlm.py
    """)
    raise SystemExit(1)
step("found the backend root", True, str(_PROJECT_ROOT))

if Path.cwd() != _PROJECT_ROOT:
    print(f"  note              : you are not standing in the backend root — paths")
    print(f"                      below are resolved against {_PROJECT_ROOT}")
    print(f"                      (the old version resolved them against the cwd)")

try:
    import google.genai as genai
    from google.genai import types
    from google.genai.errors import APIError
    sdk_version = getattr(genai, "__version__", "unknown")
    step("google-genai is installed", True, f"version {sdk_version}")
except ImportError as exc:
    step("google-genai is installed", False, f"{type(exc).__name__}: {exc}")
    fail_help("""
    The SDK the extractor imports is not installed in THIS python.

        pip install google-genai tenacity

    If you already installed it, you are probably running check_vlm.py with a
    different python than the one uvicorn runs — check the venv is activated
    (you should see (.venv) at the start of your prompt).
    """)
    raise SystemExit(1)

try:
    import tenacity  # noqa: F401
    step("tenacity is installed", True)
except ImportError:
    step("tenacity is installed", False, "pip install tenacity")
    raise SystemExit(1)

# The module object, not `from ... import MODEL_NAME`: MODEL_NAME is computed at
# import time from GEMINI_MODEL, and if the value lives only in .env (the common
# case for running this script at all) we must reload after step 2 loads .env.
extractor = None
MODEL_NAME = os.environ.get("GEMINI_MODEL", "").strip() or "gemini-flash-latest"
try:
    import app.services.ai_extractor as extractor
    MODEL_NAME = extractor.MODEL_NAME
    step("app.services.ai_extractor imports", True,
         f"MODEL_NAME = {extractor.MODEL_NAME!r}"
         + (f"\n   (from GEMINI_MODEL in the environment)"
            if os.environ.get("GEMINI_MODEL", "").strip() else
            "\n   (GEMINI_MODEL is not set, so the module default is in use)"))
except Exception as exc:
    step("app.services.ai_extractor imports", False,
         f"{type(exc).__name__}: {exc}\n"
         f"falling back to MODEL_NAME = {MODEL_NAME!r}")
    fail_help("""
    Importing the extractor failed. It could not even be loaded, so extraction
    was never going to run. The error above is the reason. If it mentions a
    missing module, pip install it; if it mentions app.schemas.bot_questionnaire,
    check that file is in place.
    """)


def app_candidate_models() -> list[str]:
    """The names the APP will actually try, plus the provider-suggested one.

    Reading the list from `candidate_models()` instead of repeating it here is
    deliberate: a hand-written list can silently probe models the app never
    calls, or miss the fallback that would have saved a filing.
    """
    names: list[str] = []
    if extractor is not None and hasattr(extractor, "candidate_models"):
        try:
            names = [str(n) for n in extractor.candidate_models()]
        except Exception:                                  # noqa: BLE001
            names = []
    if not names:
        fallback = os.environ.get("GEMINI_MODEL_FALLBACKS", "").strip()
        names = [MODEL_NAME, *[n.strip() for n in fallback.split(",") if n.strip()]]
    return list(dict.fromkeys([*names, PROVIDER_SUGGESTED]))


# ===========================================================================
print()
print(LINE)
print("  STEP 1 — the API key")
print(LINE)

gem = os.environ.get("GEMINI_API_KEY", "")
goog = os.environ.get("GOOGLE_API_KEY", "")
print(f"  GEMINI_API_KEY    : {mask(gem)}")
print(f"  GOOGLE_API_KEY    : {mask(goog)}")

if gem:
    step("GEMINI_API_KEY is set in this process", True)
elif goog:
    step("GEMINI_API_KEY is set in this process", False,
         "GOOGLE_API_KEY is set, but the project's get_client() checks GEMINI_API_KEY\n"
         "and raises before the SDK ever sees the GOOGLE_API_KEY.")
    fail_help("""
    FIX: rename it, or set both.

        # .env
        GEMINI_API_KEY=same-value-as-your-GOOGLE_API_KEY

    The SDK would accept either name on its own, but ai_extractor.get_client()
    looks only for GEMINI_API_KEY.
    """)
else:
    step("GEMINI_API_KEY is set in this process", False, "neither variable is set")
    fail_help("""
    No key in the environment, so `get_client()` raises
      RuntimeError: GEMINI_API_KEY is not set — the extraction service cannot run.
    ...and that error is what lands in audit_notes as "Extraction failed: ...".

    Now the important part: is the key missing from your .env, or is .env simply
    NOT BEING READ by the process uvicorn started? Step 2 answers that.
    """)

# ===========================================================================
print()
print(LINE)
print("  STEP 2 — is your .env actually being read?")
print(LINE)

try:
    from dotenv import dotenv_values, find_dotenv
    has_dotenv = True
except ImportError:
    has_dotenv = False
    step("python-dotenv is installed", False, "pip install python-dotenv")

if has_dotenv:
    # The project's .env sits next to `app/`. Look THERE first — searching from
    # the cwd found a different file (or none) whenever the script was run from
    # anywhere else, which is the same class of bug as the startup-file scan.
    root_env = under_root(".env")
    found = str(root_env) if root_env.exists() else ""
    if not found:
        elsewhere = find_dotenv(usecwd=True)
        if elsewhere:
            found = elsewhere
            print(f"  .env found at     : {found}")
            print(f"  note              : that is NOT {root_env} — the server loads the")
            print(f"                      file next to app/, so check which one it reads")
        else:
            print(f"  .env              : not found at {root_env} or anywhere above the cwd")
    else:
        print(f"  .env found at     : {found}")

    values: dict[str, str] = {}
    key_already_present = bool(os.environ.get("GEMINI_API_KEY"))
    if found:
        values = dotenv_values(found)
        in_file = "GEMINI_API_KEY" in values
        print(f"  contains GEMINI_API_KEY : {in_file}")
        if in_file:
            print(f"  value in the file : {mask(str(values['GEMINI_API_KEY']))}")
        step(".env file exists and is readable", True)
    elif key_already_present:
        step(".env file exists and is readable", True,
             "no .env here, but GEMINI_API_KEY is already set in the environment,\n"
             "so nothing needs to load a file for THIS shell")
    else:
        step(".env file exists and is readable", False,
             f"no .env found next to app/ ({root_env}) or above the cwd")
        fail_help("""
    Either you have no .env, or you are running this from the wrong place.
    It must sit next to the folder that contains `app/` — i.e. in your backend
    root, not inside app/.
        """)

    # Which module does `uvicorn app.main:app` vs `uvicorn app.services.main:app` load?
    print()
    print("  which startup file loads the key first?")
    candidates = ["app/main.py", "app/services/main.py"]
    any_calls_dotenv = False
    for cand in candidates:
        p = under_root(cand)          # relative to the ROOT, not the cwd
        if not p.exists():
            print(f"    {cand:26} : does not exist")
            continue
        src = p.read_text(encoding="utf-8", errors="replace")
        calls = "load_dotenv" in src
        any_calls_dotenv = any_calls_dotenv or calls
        print(f"    {cand:26} : {'calls load_dotenv()' if calls else 'NEVER calls load_dotenv()'}")

    if not any_calls_dotenv:
        step("a startup file loads .env", False)
    else:
        step("a startup file loads .env", True)

    _any_startup_calls_dotenv = any_calls_dotenv

# --- if the key exists but was never loaded, test that it WORKS when loaded ---
# This is a deliberate, announced state change: it lets steps 3-6 continue and
# prove the key is good, which separates "bad key" from "the app never read
# .env" — two problems that look identical from the dashboard.
loaded_from_env = False
if (has_dotenv and not os.environ.get("GEMINI_API_KEY")
        and found and "GEMINI_API_KEY" in values):
    print()
    print("  -> loading .env into THIS process now, to test whether the key is good")
    print("     (nothing is written back to any file)")
    from dotenv import load_dotenv
    load_dotenv(found, override=True)
    value = os.environ.get("GEMINI_API_KEY", "")
    step("GEMINI_API_KEY became available once .env was loaded", bool(value),
         f"now set to {mask(value)}" if value else "still empty after loading")
    if value:
        loaded_from_env = True
        # Where the key is missing from THIS shell but a startup file does call
        # load_dotenv(), this is EXPECTED and not a finding: the server reads .env
        # for itself, while this script was launched from a shell that never
        # loaded it. Reporting that as "almost certainly your problem" sent
        # someone to rewrite a main.py that was already correct.
        if _any_startup_calls_dotenv:
            informational.add("GEMINI_API_KEY is set in this process")
            step("...which is expected here, not a fault", True,
                 "this script was started from a shell without the variable; the\n"
                 "server reads .env itself at startup via load_dotenv(). Continuing\n"
                 "with the key loaded so the rest of this report can test it.")
        else:
            fail_help("""
        THIS IS THE PROBLEM.

        The key exists and is readable, and NO startup file calls load_dotenv(),
        so it never reaches the process that runs your API. `get_client()` raises
          RuntimeError: GEMINI_API_KEY is not set — the extraction service cannot run.
        the upload is stored anyway, and audit_notes records that sentence.

        The fix is not in ai_extractor.py. It is the startup file your start
        command loads — put this at the very top of it, above any app import:

            from dotenv import load_dotenv
            load_dotenv()

            # uvicorn app.main:app          -> app/main.py
            # uvicorn app.services.main:app -> app/services/main.py
        """)

        # GEMINI_MODEL may also have been inside .env — the module read it at
        # import time, before this load, so its MODEL_NAME can be stale. Re-read.
        if extractor is not None and hasattr(extractor, "candidate_models"):
            try:
                before = MODEL_NAME
                extractor = importlib.reload(extractor)
                MODEL_NAME = extractor.MODEL_NAME
                if MODEL_NAME != before:
                    print()
                    print(f"  -> the extractor's model name came from .env too:")
                    print(f"     {before!r} -> {MODEL_NAME!r} (re-read after loading)")
                else:
                    print()
                    print(f"  -> the extractor's model name is {MODEL_NAME!r} "
                          f"(unchanged after loading .env)")
            except Exception as exc:                       # noqa: BLE001
                print(f"  -> could not re-read the model name after loading .env: "
                      f"{type(exc).__name__}: {exc}")

# ===========================================================================
print()
print(LINE)
print("  STEP 3 — can the client be built at all?")
print(LINE)
if loaded_from_env:
    print("  (continuing with the key loaded from .env — the rest of this report")
    print("   tests the key and the model, not your app's environment)")
    print()

client = None
try:
    client = genai.Client()
    step("genai.Client() constructed", True,
         "the SDK found a usable key in the environment")
except Exception as exc:
    step("genai.Client() constructed", False, f"{type(exc).__name__}: {exc}")
    fail_help("""
    The SDK itself refused to build a client. That is normally a missing or
    malformed key (the SDK accepts GEMINI_API_KEY or GOOGLE_API_KEY).
    """)

# ===========================================================================
print()
print(LINE)
print("  STEP 4 — a one-word call, to separate 'key' from 'document'")
print(LINE)

if client is not None:
    try:
        r = client.models.generate_content(
            model=MODEL_NAME,
            contents="Reply with the single word: OK",
            config=types.GenerateContentConfig(
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            ),
        )
        text = (getattr(r, "text", "") or "").strip()
        step(f"{MODEL_NAME} answered a text prompt", bool(text), f"model said: {text[:60]!r}")
    except APIError as exc:
        code = getattr(exc, "code", "?")
        status = getattr(exc, "status", "?")
        msg = getattr(exc, "message", str(exc))
        step(f"{MODEL_NAME} answered a text prompt", False, f"APIError {code} {status}\n{msg}")
        fail_help(f"""
    This is the error that has been reaching your audit_notes as
    "Extraction failed: ..." — except the old code always relabelled it
    "Model Busy", which is why it looked like a queue problem.

    Read the code:
      400 API_KEY_INVALID / INVALID_ARGUMENT  the key is wrong, or the API is
                                              not enabled for that project
      403 PERMISSION_DENIED / UNAUTHENTICATED the key works but is not allowed
                                              to use this model
      404 NOT_FOUND                           the model name is not available
                                              to this key — see step 5
      429 RESOURCE_EXHAUSTED                  quota finished for the moment
                                              (free tier is small, and a
                                              multi-page PDF burns a lot of it)
      500 / 503                               provider-side problem, just retry
    """)
    except Exception as exc:
        step(f"{MODEL_NAME} answered a text prompt", False,
             f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    print()
    print("  models this key can actually see:")
    try:
        names = []
        for m in client.models.list():
            n = (getattr(m, "name", "") or "").replace("models/", "")
            if n:
                names.append(n)
        flash = [n for n in names if "flash" in n]
        print(f"    {len(names)} models available; flash-family: {flash[:8]}")
        # Being LISTED does not mean it can be CALLED. `gemini-2.5-flash` appears
        # in the list for a key that then gets 404 when calling it, so the list is
        # reported as information and the call in step 4 is the authority.
        print(f"    {MODEL_NAME!r} appears in the list: {MODEL_NAME in names}")
        step("model availability reported above (the CALL in step 4 is what counts)",
             True,
             "a name can be listed and still be refused — see the 404 note")
    except Exception as exc:
        print(f"    could not list models: {type(exc).__name__}: {exc}")

# ===========================================================================
print()
print(LINE)
print("  STEP 4b — which model names can THIS key actually call?")
print(LINE)
print()
print("  (a name can be listed and still refused: gemini-2.5-flash is listed")
print("   for keys that then get 404 when calling it)")
print()

if client is None:
    print("  skipped — no client to call with")
else:
    candidates = app_candidate_models()
    source = ("the app's own candidate_models()"
              if extractor is not None and hasattr(extractor, "candidate_models")
              else "a built-in list (the extractor could not be imported)")
    print(f"  probing {len(candidates)} name(s), taken from {source}:")
    for c in candidates:
        print(f"      {c}")
    print()

    working: list[str] = []
    for model in candidates:
        try:
            r = client.models.generate_content(
                model=model,
                contents="Reply with the single word: OK",
                config=types.GenerateContentConfig(
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                ),
            )
            text = (getattr(r, "text", "") or "").strip()
            if text:
                working.append(model)
                print(f"    OK      {model:<28} answered {text[:12]!r}")
            else:
                print(f"    EMPTY   {model:<28} returned nothing")
        except APIError as exc:
            print(f"    FAIL    {model:<28} {getattr(exc, 'code', '?')} {getattr(exc, 'status', '?')}")
        except Exception as exc:                                        # noqa: BLE001
            print(f"    FAIL    {model:<28} {type(exc).__name__}: {str(exc)[:60]}")

    print()
    if working:
        best = "gemini-flash-latest" if "gemini-flash-latest" in working else working[0]
        step(f"{len(working)} of {len(candidates)} candidate model(s) answered", True,
             ", ".join(working))
        # Only advise a change when the name the APP will use did NOT answer.
        # Printing "set GEMINI_MODEL=..." while the configured model was fine
        # sent people to edit a working .env.
        if MODEL_NAME in working:
            fail_help(f"""
    No change needed: the model the app will use — {MODEL_NAME!r} — answered.
        """)
        else:
            fail_help(f"""
    SET THIS IN YOUR .env:

        GEMINI_MODEL={best}

    The app would use {MODEL_NAME!r}, which did not answer here. The extractor
    reads GEMINI_MODEL (app/services/ai_extractor.py); without it the default is
    `gemini-flash-latest`.

    Then restart the server and upload a filing. The extraction should run.
        """)
    else:
        step("no candidate model answered", True,
             "every one of them failed — that is a key or quota problem, not a name")
        fail_help("""
    Not a model-name problem: the key itself is being refused for all of them.
    Read the errors above — 400/403 is the key or the project, 429 is quota.
    Step 4's note lists what each code means.
    """)

# ===========================================================================
pdf_arg = sys.argv[1] if len(sys.argv) > 1 else None

if not pdf_arg:
    print()
    print(LINE)
    print("  STEP 5 — upload + extraction  (SKIPPED, no PDF given)")
    print(LINE)
    print("  To test the document half as well:")
    print('      python check_vlm.py "path\\to\\a\\filing.pdf"')
elif client is None:
    print()
    print(LINE)
    print("  STEP 5/6 — upload + extraction  (SKIPPED, no client)")
    print(LINE)
    print("  The key or the SDK failed above, so there is nothing to upload with.")
    print("  Fix step 3 first, then re-run with the PDF.")
else:
    print()
    print(LINE)
    print("  STEP 5 — uploading the document")
    print(LINE)
    pdf, tried = find_pdf(pdf_arg, _PROJECT_ROOT)
    if pdf is None:
        step(f"a PDF at '{pdf_arg}' (as typed)", False)
        print()
        print("  " + "-" * 68)
        print("  The file is not there. A relative path is read from the directory you")
        print(r"  are standing in, so a path such as  ..\uploads\filing.pdf  names a")
        print("  different file depending on where you run this.")
        print()
        print("  Looked at:")
        for cand in tried[:4]:
            print(f"      {cand}")
        print()
        others = nearby_pdfs(_PROJECT_ROOT)
        if others:
            print()
            print("  PDFs found near the project — copy the line that matches yours:")
            for cand in others:
                print(f'      python check_vlm.py "{cand}"')
        else:
            print()
            print("  No .pdf files found near the project either — check the file")
            print("  name, and pass the path between quotes if it contains spaces.")
    else:
        if str(pdf) != pdf_arg:
            print(f"  (resolved '{pdf_arg}' -> {pdf})")
        size_mb = pdf.stat().st_size / 1_048_576
        step(f"{pdf.name} exists", True, f"{size_mb:.2f} MB")
        if size_mb > 50:
            fail_help("""
    Large file. The File API handles it, but upload time counts against your
    patience and the processing wait is 120 s. If it times out, that is why.
            """)
        uploaded = None
        try:
            uploaded = client.files.upload(file=str(pdf))
            step("upload accepted by the File API", True, f"name={uploaded.name}")

            import time
            deadline = time.time() + 120
            state_name = ""
            while time.time() < deadline:
                state = getattr(uploaded, "state", None)
                # Same reading as ai_extractor._upload_and_wait: an SDK enum has
                # .name, an older/plain string is used as-is.
                state_name = getattr(state, "name", str(state)) if state is not None else ""
                if state_name in ("ACTIVE", "FileState.ACTIVE"):
                    break
                if state_name in ("FAILED", "FileState.FAILED"):
                    break
                time.sleep(2)
                uploaded = client.files.get(name=uploaded.name)
            step(f"file reached ACTIVE (state={state_name or 'unknown'})",
                 state_name in ("ACTIVE", "FileState.ACTIVE"),
                 "" if state_name in ("ACTIVE", "FileState.ACTIVE") else
                 "the provider could not process this file — try a different PDF\n"
                 "to find out whether it is the file or the service")
        except APIError as exc:
            step("upload accepted by the File API", False,
                 f"APIError {getattr(exc, 'code', '?')} {getattr(exc, 'status', '?')}: "
                 f"{getattr(exc, 'message', exc)}")
        except Exception as exc:
            step("upload accepted by the File API", False, f"{type(exc).__name__}: {exc}")
        finally:
            if uploaded is not None:
                try:
                    client.files.delete(name=uploaded.name)
                    print("          (this probe's copy deleted from the provider —")
                    print("           step 6 uploads its own, and deletes that too)")
                except Exception:
                    pass

        print()
        print(LINE)
        print("  STEP 6 — the real extractor, on this file")
        print(LINE)
        try:
            from app.services.ai_extractor import extract_and_classify_pcf_doc
            result = extract_and_classify_pcf_doc(str(pdf), raise_on_failure=True)

            # WHERE to read the status from: the payload ROOT. The extractor
            # writes `payload["extraction_status"]` there (and the schema and
            # AuditReview.tsx read it there). Reading `metadata.extraction_status`
            # — as this script used to — finds nothing on a successful run and
            # reports "'?' ... not a clean pass" for an extraction that worked.
            status = ""
            status_from = ""
            if extractor is not None and hasattr(extractor, "extraction_status"):
                try:
                    status = str(extractor.extraction_status(result))
                    status_from = "extractor.extraction_status()"
                except Exception:                              # noqa: BLE001
                    status = ""
            if not status:
                status = str(result.get("extraction_status")
                             or (result.get("metadata") or {}).get("extraction_status")
                             or "?")
                status_from = "payload['extraction_status'] (root, then metadata)"

            # Company name lives at part_a.details.company_name (PAYLOAD_MAP.md).
            details = (result.get("part_a") or {}).get("details") or {}
            company = details.get("company_name") or ""
            warnings_ = result.get("validation_warnings") or []

            step("extract_and_classify_pcf_doc returned", True,
                 f"extraction_status = {status!r}   (read from {status_from})\n"
                 f"company_name      = {company!r} (part_a.details.company_name)\n"
                 f"validation_warnings = {len(warnings_)}")
            for w in warnings_[:3]:
                print(f"          - {w}")
            if status == "partial" and warnings_:
                fail_help(f"""
    NOT A FAILURE — 'partial' means the document was read, and the schema then
    recorded {len(warnings_)} finding(s) in it: fields it could not read, or
    arithmetic that did not add up. Those findings are what the review screen
    shows in its data-quality panel. Check them against the PDF.
                """)
            elif status == "partial":
                fail_help("""
    NOT A FAILURE — and nothing failed a check: validation_warnings is empty, so
    the review screen's data-quality panel will be empty too.

    This status is the MODEL'S OWN answer about the document, not a finding
    against it. It reported the read as incomplete — expect sections it left
    blank, could not read, or filled in by guesswork. That is the thing worth a
    reviewer's eye, and the panel that would normally flag it is empty, so
    compare the extracted sections against the PDF by hand.
                """)
            elif status not in ("ok",):
                fail_help("""
    It ran without raising, but the result is not a clean pass. The warnings and
    audit notes inside the payload say why — that text is what the review screen
    shows in the data-quality panel.
                """)
        except Exception as exc:
            step("extract_and_classify_pcf_doc returned", False,
                 f"{type(exc).__name__}: {exc}")
            fail_help("""
    THIS is the real failure, with its real message — no "Model Busy" relabelling.
    Everything above it passed, so the key, the network and the SDK are fine and
    the problem is in this call.
            """)

# ===========================================================================
print()
print(LINE)
print("  SUMMARY")
print(LINE)
def _informational(name: str) -> bool:
    """True only for the checks that CANNOT pass on a correctly-set-up machine.

    Exactly one check belongs here: a key that lives in .env is absent from the
    SHELL by design, because the server reads the file itself. It is added to
    `informational` at the point where that is established.

    Everything else counts as a blocker when it fails. This used to be decided by
    substring hints — one of them was the word "exists" — which quietly swallowed
    the PDF-missing failure and ended the run with "Nothing blocking" while the
    whole point of the run, the upload and the extraction, had been skipped.
    """
    return name in informational


for name, ok, _ in results:
    print(f"  {'PASS' if ok else ('NOTE' if _informational(name) else 'FAIL')}  {name}")
failed = [n for n, ok, _ in results if not ok]
notes = [n for n in failed if _informational(n)]
blockers = [n for n in failed if n not in notes]
print()
if not failed:
    print("  Every check passed. If the dashboard still shows a failed extraction,")
    print("  the failure is on the app side of this script — send me the text in")
    print("  audit_notes for that filing.")
else:
    if blockers:
        print(f"  {len(blockers)} check(s) failed, in the order they were run:")
        for n in blockers:
            print(f"    - {n}")
        print()
        print("  Fix this one first, then re-run:")
        print(f"    {blockers[0]}")
    else:
        print("  No blocking failures — nothing here needs fixing.")
    if notes:
        print()
        print(f"  {len(notes)} informational item(s), expected on your setup and not")
        print("  counted as failures:")
        for n in notes:
            print(f"    - {n}")
print()
