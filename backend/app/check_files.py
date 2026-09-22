#!/usr/bin/env python3
"""
check_files.py — is every revised file actually IN PLACE in your project?

Run it from the backend root, in the venv you start uvicorn with:

    python check_files.py

It prints one line per file: CURRENT (matches this workspace exactly), STALE (the
file is there but it is an older revision), or MISSING. Then it imports the app
and reports the two things `/health` reports, so you can see storage and VLM
configuration without starting the server.

Nothing is written or changed. Nothing is uploaded anywhere.
"""
from __future__ import annotations

import ast
import hashlib
import os
import sys
from pathlib import Path

# --------------------------------------------------------------------------
# Locate the backend root: the folder containing the `app` package. Searched from
# this file's directory, its parent, and the working directory.
# --------------------------------------------------------------------------
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
_CANDIDATES: tuple[Path, ...] = ()
for _c in (_HERE, _HERE.parent, Path.cwd(), Path.cwd().parent):
    if _c not in _CANDIDATES:
        _CANDIDATES += (_c,)

BACKEND: Path | None = None
for _candidate in _CANDIDATES:
    if _looks_like_backend_root(_candidate):
        BACKEND = _candidate
        break

if BACKEND is None:
    print()
    print("Could not find the backend root — the folder that contains your `app` folder.")
    print()
    print("Searched:")
    for _candidate in _CANDIDATES:
        _app = _candidate / "app"
        if _app.is_dir():
            _inside = sorted(p.name for p in _app.iterdir())[:8]
            print(f"  {_candidate}")
            print(f"      has app/ but nothing recognisable in it: {_inside}")
        else:
            print(f"  {_candidate}")
            print(f"      no app/ folder here")
    print()
    print("Fix: copy this script into the backend root — the SAME folder you run")
    print("uvicorn from, the one with `app` directly inside it — and run:")
    print()
    print("    python check_files.py")
    raise SystemExit(2)

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

# --------------------------------------------------------------------------
# The files, with the fingerprints of the revision in this workspace.
#
# Three ways a file can be judged CURRENT, in descending order of confidence:
#   1. its md5 matches exactly;
#   2. its md5 matches after normalising line endings, a BOM and the trailing
#      newline — this is the common case on Windows, where the editor saves CRLF
#      and may drop the final newline. Formatting, not content;
#   3. every MARKER string is present, and every marker written with a leading
#      "!" is ABSENT. Markers are identifiers this revision introduced, so a file
#      that has all of them has the fix even if the local copy was edited
#      afterwards or reformatted. When only this test passes the verdict says so,
#      because "CURRENT" should not hide a local edit. The "!" form exists for
#      fixes that REMOVE a line: a file can carry every new identifier and still
#      hold the bug, so presence alone cannot clear it.
# --------------------------------------------------------------------------
EXPECTED = [
    ("app/schemas/bot_questionnaire.py", "4870069457a08a8c3071c90b6d4cc91f", "8d9909c92ba0d43d0aa8d6b2035ae41d", ("DEFAULT_QUESTIONNAIRE_TYPE", "_resolve_years")),
    # the "!" marker is the questionnaire_type fix: revisions before it carry that
    # line, and a file can hold every other marker and still fail a filing.
    ("app/services/ai_extractor.py", "c03dbf861a482dc6ac80f813235aa029", "242f082fad3d09c4c175c824645aa1e7",
     ("candidate_models", "build_failed_questionnaire", "_upload_and_wait", "_claimed",
      '!setdefault("questionnaire_type"')),
    ("app/services/validation_engine.py", "fac186997d9f4e60db10876fef3ff255", "0afeaa7ca3d78c34a731fdd8116d03be", ("evaluate_questionnaire", "HIGH_VALUE_USD_THRESHOLD")),
    ("app/core/auth.py", "f854fce3675e82b60f5ec85281eec4e0", "87398b3f4c6ad25b8c3845bd1942e21b", ("require_roles", "ECONOMIST_ROLES", "DEFAULT_CLIENT_ID")),
    ("app/routers/auth_router.py", "549eb02234edd7791cdfcf2740d49e79", "f29c6572bdcc29ff0da4d418438a371a", ("_authenticate", "_UNKNOWN_TIN_HASH", "BCRYPT_MAX_PASSWORD_BYTES")),
    ("app/routers/ingestion.py", "c5e76f073fcc24537a42f289866f59a4", "0e584aebee94bbbf73203d077818f21d", ("_unwrap_extraction", "_storable_payload", "STATUS_FLAGGED")),
    ("app/services/minio_service.py", "590709bf3df12fe4d835a4b07b10cfea", "d434ec2e46f2bb9d789609735a291331", ("load_dotenv", "MINIO_SECURE", "presigned_url")),
    ("app/services/lineage_service.py", "c508113c3c0b940e0696d0404f4a5478", "11cdfc4477f357e8b025c14d517822f6", ("run_id_for", "_json_safe", "botPcfAudit")),
    ("app/services/kafka_producer.py", "801cc48accfa26aa20b3716d53f47d7e", "8bb3a9f3dba79102ba8195f1f81e061f", ("KAFKA_MODE", "questionnaire_of", "schema_version")),
    ("app/routers/economist.py", "d03fa198c77653bc97088d6991d9d3ec", "20c36c9d25c55973bd8ab30b22c92b34", ("ECONOMIST_REQUIRE_AUTH", "RequireEconomist", "_actor_label")),
    ("app/main.py", "147b7415ba4ddccb648f15e968dc1207", "cfbd9ab69d261165eadd68b15526743e", ("STORAGE_SOURCE", "_resolve_storage_dir", "CORS_ALLOWED_ORIGINS")),
    ("app/db/models.py", "b139fe30043c94d9f9b4b8547434e9c3", "3b8b14c8edbd072c3aec8adcdcbffe23", ("utcnow_naive", "PROCESSING")),
    ("app/db/database.py", "8495362f2db7a69f001f12399cf646b9", "1680fd2a392a427e3efa0befbc26ac73", ("load_dotenv", "pool_pre_ping", "_IS_ASYNC_PG", "DeclarativeBase")),
    ("app/inspect_extraction.py", "9e5e8d33b7433cf57039207f11150bce", "5b31b8c3150f33a572a3659c602469f8",
     ("WHY THIS STATUS", "count_populated", "SECTIONS READ")),
    ("app/db/init_db.py", "b9681ccd6d512bf494eae96e3902739e", "dca33d0ea3875d4be88a543b3c520ac8", ("init_tables", "create_all", "python -m app.db.init_db")),
    ("pcf_analytics_view.sql", "b27db4297b9cbd49e675b457fc135c1b", "1dad1f392fea365b2e3e245e051654fc", ("DROP VIEW IF EXISTS", "industrial_classifications", "extraction_status")),
    ("superset_config.py", "317c6646e740cdd427672eff1b24a482", "4cb4ffbe02ea0d3e665df0b947315178", ("REFUSING TO START", "frame-ancestors", "SUPERSET_ALLOW_SHARED_METADATA_DB", "ab_permission_view_role")),
    ("docker-compose.yml", "d0034775f445809dfed5e8565917785e", "76c78cc48131912fb1bedf5df9018575", ("quay.io/minio/minio", "/app/pythonpath/superset_config.py", "condition: service_healthy", "SUPERSET_ALLOW_SHARED_METADATA_DB")),
    ("Dockerfile.superset", "5b39740ce92fbc720d7025e14f55427a", "226241451768a72059e49e7f451a1f94", ("SUPERSET_CONFIG_PATH", "/app/.venv/bin/python -m pip install", "USER superset")),
    ("vite.config.ts", "4be16306796e0128235af1d51f4f923c", "b5f8e67ee9c494ea861fb68c3a1cceed", ("strictPort", "VITE_DEV_API_TARGET", "changeOrigin")),
    ("api.ts", "2d235eea0410d88d968a478ad87af3e2", "22aadefa878da3e1bb14a9adf9fd8423", ("extracted_payload_override", "confirmPhrase")),
    ("Overview.tsx", "e2d5ae6a70df4f1d9301cff4e273971e", "e0ce04016ea8d7b2bace7a6fb55d8323", ("PROCESSED_WITH_ALERTS", "extracted_payload_override")),
    ("EconomistDashboard.tsx", "f0ba7cb918809ae633b653bc69df8450", "6812d844752065727332d72770f7d0a3", ("VITE_SUPERSET_URL", "PANE_HIDDEN", "aria-current")),
    ("AuditReview.tsx", "4472165315cfec84cb286f645f84b81e", "68db6a4594d6ccb8a7027f767eb77d72", ("handleTriggerSave", "handlePayloadFieldChange", "normaliseExtractedPayload")),
]

OTHER_NAMES = {dest: (raw, norm, markers) for dest, raw, norm, markers in EXPECTED
               if not dest.startswith("app/")}
SQL_SCRIPTS = {dest: v for dest, v in OTHER_NAMES.items() if dest.endswith(".sql")}
# service configuration that lives next to the app rather than inside it
DEPLOY_NAMES = {dest: v for dest, v in OTHER_NAMES.items()
                if dest.endswith(("superset_config.py", "docker-compose.yml", "Dockerfile.superset", "nginx.conf"))}
FRONTEND_NAMES = {k: v for k, v in OTHER_NAMES.items()
                  if not k.endswith(".sql") and k not in DEPLOY_NAMES}
BACKEND_FILES = [(dest, raw, norm, markers) for dest, raw, norm, markers in EXPECTED
                 if dest.startswith("app/")]

SKIP_DIRS = {"node_modules", ".git", "dist", "build", ".venv", "venv", "__pycache__", ".next"}


def normalise(data: bytes) -> bytes:
    """Content comparison that ignores what an editor does to line endings.

    Windows editors save CRLF; the workspace files use LF. Many also drop the
    final newline. Neither changes a single line of code, and comparing raw bytes
    therefore reported every file as STALE on a project where nine of nine
    backend files were already correct.
    """
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    text = data.decode("utf-8", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n").encode("utf-8")


def judge(path: Path, raw_md5: str, norm_md5: str, markers: tuple[str, ...]) -> tuple[str, str]:
    """Return (verdict, detail). Verdicts: CURRENT, CURRENT*, EDITED, STALE."""
    data = path.read_bytes()
    if hashlib.md5(data).hexdigest() == raw_md5:
        return "CURRENT", "byte-for-byte identical to this workspace"
    if hashlib.md5(normalise(data)).hexdigest() == norm_md5:
        return "CURRENT", "same content; line endings or final newline differ (harmless)"
    text = data.decode("utf-8", errors="replace")
    absent = [m[1:] for m in markers if m.startswith("!")]        # must NOT appear
    present = [m for m in markers if not m.startswith("!")]       # must appear
    still_here = [m for m in absent if m in text]
    if still_here:
        return "STALE", ("still contains the line this revision removes: "
                         + ", ".join(repr(m) for m in still_here))
    missing = [m for m in present if m not in text]
    if not missing:
        return "CURRENT*", ("this revision IS here, but the file has been edited since — "
                            "all the markers are present, the bytes are not")
    return "STALE", "missing: " + ", ".join(missing)


def find_by_name(root: Path, name: str, max_depth: int = 5) -> list[Path]:
    """Bounded search for a file name, used for the frontend files."""
    hits: list[Path] = []
    root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        depth = len(Path(dirpath).relative_to(root).parts)
        if depth > max_depth:
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        if name in filenames:
            hits.append(Path(dirpath) / name)
    return hits


LINE = "=" * 76
print()
print(LINE)
print("  BACKEND FILES")
print(LINE)
print(f"  backend root : {BACKEND}")
print(f"  interpreter  : {sys.executable}")
print()

missing: list[str] = []
stale: list[str] = []
edited: list[str] = []

for rel, raw_md5, norm_md5, markers in BACKEND_FILES:
    path = BACKEND / rel
    if not path.is_file():
        print(f"  {'MISSING':<9} {rel:<38} not in your project")
        missing.append(rel)
        continue

    verdict, detail = judge(path, raw_md5, norm_md5, markers)
    print(f"  {verdict:<9} {rel:<38} {detail}")
    if verdict == "STALE":
        stale.append(rel)
    elif verdict == "CURRENT*":
        edited.append(rel)

# the alternative main.py location, reported but never a failure
alt = BACKEND / "app/services/main.py"
if alt.is_file():
    print(f"  {'ALSO':<9} {'app/services/main.py':<38} "
          f"present; {'app/main.py' if (BACKEND / 'app/main.py').is_file() else 'unclear which'} "
          f"is the one uvicorn loads")

# --------------------------------------------------------------------------
print()
print(LINE)
print("  FRONTEND FILES  (searched near the project)")
print(LINE)
print()
project_root = BACKEND.parent
for name, (raw_md5, norm_md5, markers) in FRONTEND_NAMES.items():
    hits = find_by_name(project_root, name)
    if not hits:
        print(f"  {'NOT FOUND':<9} {name}")
        missing.append(name)
        continue
    path = hits[0]
    verdict, detail = judge(path, raw_md5, norm_md5, markers)
    print(f"  {verdict:<9} {name}")
    print(f"            {path}")
    print(f"            {detail}")
    if verdict == "STALE":
        stale.append(name)
    elif verdict == "CURRENT*":
        edited.append(name)

# --------------------------------------------------------------------------
if DEPLOY_NAMES:
    print()
    print(LINE)
    print("  SERVICE CONFIG  (loaded by the services around the app, not by it)")
    print(LINE)
    print()
    for name, (raw_md5, norm_md5, markers) in DEPLOY_NAMES.items():
        hits = find_by_name(BACKEND.parent, name)
        if not hits:
            print(f"  {'NOT FOUND':<9} {name}")
            print("            looked for this file near the project; the Superset frame keeps")
            print("            whatever the running container was started with")
            continue
        path = hits[0]
        verdict, detail = judge(path, raw_md5, norm_md5, markers)
        print(f"  {verdict:<9} {name}")
        print(f"            {path}")
        print(f"            {detail}")
        if name.startswith("Dockerfile"):
            print("            (this is the image, so it only takes effect on a REBUILD:")
            print("             docker compose up -d --build superset — a config edit alone")
            print("             needs only `docker compose restart superset`))")
        elif name.endswith("docker-compose.yml"):
            print("            (the file being right is not the whole story — it has to be the")
            print("             file compose RUNS, and it has to load the config above. verify with:")
            print("             python verify_compose.py            (add --live when the stack is up))")
        else:
            print("            (the file being right is not the whole story — the container has to")
            print("             LOAD it. verify with:")
            print("             python verify_superset_config.py --url http://localhost:8088)")
        if verdict == "STALE":
            stale.append(name)
        elif verdict == "CURRENT*":
            edited.append(name)

# --------------------------------------------------------------------------
if SQL_SCRIPTS:
    print()
    print(LINE)
    print("  SQL / REPORTING SCRIPTS  (run against the database, not pasted over a file)")
    print(LINE)
    print()
    for name, (raw_md5, norm_md5, markers) in SQL_SCRIPTS.items():
        hits = find_by_name(BACKEND.parent, name)
        if not hits:
            print(f"  {'NOT FOUND':<9} {name}")
            print("            this is a reporting view — nothing breaks without it, but the")
            print("            analytics dashboard has nothing to read until it is run")
            continue
        path = hits[0]
        verdict, detail = judge(path, raw_md5, norm_md5, markers)
        print(f"  {verdict:<9} {name}")
        print(f"            {path}")
        print(f"            {detail}")
        print("            (a file here only means the script is present — the views exist")
        print("             after you RUN it against the database)")
        if verdict == "STALE":
            stale.append(name)
        elif verdict == "CURRENT*":
            edited.append(name)

# --------------------------------------------------------------------------
print()
print(LINE)
print("  SECOND STORAGE LAYERS  (in-memory stores that a router might still use)")
print(LINE)
print()

# app/db/session.py in this project holds filings in a plain Python list:
# `submissions_db: List[Dict]`, with get_all_submissions / add_submission /
# clear_submissions. It is not the database, and anything still wired to it loses
# every record on restart — which looks exactly like "the upload did nothing".
# This looks for the module and for anything importing it.
IN_MEMORY_MARKERS = (
    "app.db.session",
    "app.db import session",
    "from .session import",
    "from app.db.session import",
)
IN_MEMORY_SYMBOLS = ("submissions_db", "get_all_submissions", "clear_submissions")

_session_module = BACKEND / "app" / "db" / "session.py"
if _session_module.is_file():
    print(f"  found    app/db/session.py ({len(_session_module.read_text(errors='replace').splitlines())} lines)")
    _src = _session_module.read_text(errors="replace")
    _in_memory = any(marker in _src for marker in IN_MEMORY_SYMBOLS)
    print(f"           holds data in memory: {_in_memory}"
          f"{'   (a plain Python list, not the database)' if _in_memory else ''}")
else:
    print("  absent   app/db/session.py")

print()
referrers: list[str] = []
for _py in sorted((BACKEND / "app").rglob("*.py")):
    if _py.name in ("session.py",) or _py.name.startswith(("verify_", "check_")):
        continue
    try:
        _tree = ast.parse(_py.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        continue
    for _node in ast.walk(_tree):
        if isinstance(_node, ast.ImportFrom):
            if (_node.module or "").endswith("db.session") or (_node.module or "") == "session":
                referrers.append(f"{_py.relative_to(BACKEND)}:{_node.lineno} imports {_node.module}")
        elif isinstance(_node, ast.Import):
            for _alias in _node.names:
                if _alias.name.endswith("db.session"):
                    referrers.append(f"{_py.relative_to(BACKEND)}:{_node.lineno} imports {_alias.name}")
        elif isinstance(_node, ast.Name) and _node.id in IN_MEMORY_SYMBOLS:
            referrers.append(f"{_py.relative_to(BACKEND)}:{_node.lineno} uses {_node.id}")

if referrers:
    print("  WARNING — something still uses the in-memory store:")
    for _line in referrers:
        print(f"    {_line}")
    print()
    print("  Every record it holds is lost when the process restarts, and the")
    print("  database is not where that code is reading from. Point it at")
    print("  app.db.database.AsyncSessionLocal + FilingRecord instead.")
else:
    print("  nothing imports app/db/session.py, and no module references its")
    print("  symbols — it is dead code, left over from the prototype. It can be")
    print("  deleted safely; keeping it invites someone to wire a route to a store")
    print("  that empties itself on every restart.")

# --------------------------------------------------------------------------
print()
print(LINE)
print("  LIVE CONFIGURATION  (what the app computes right now)")
print(LINE)
print()

storage_dir = None
storage_source = "unknown"
try:
    from app.routers import ingestion

    storage_dir = getattr(ingestion, "STORAGE_DIR", None)
    print(f"  ingestion.py writes to : {storage_dir}")

    # Which directory expression the file on disk uses. Computed from both
    # candidates rather than matched against a folder name, so it works whatever
    # your project folder is called.
    _ing = Path(ingestion.__file__).resolve()
    _two = _ing.parent.parent / "storage" / "raw_documents"
    _three = _ing.parent.parent.parent / "storage" / "raw_documents"
    if storage_dir and os.path.normcase(str(Path(storage_dir).resolve())) == \
            os.path.normcase(str(_two)):
        depth_marker = "two dirnames -> <backend>/app/storage/raw_documents  (matches this workspace)"
    elif storage_dir and os.path.normcase(str(Path(storage_dir).resolve())) == \
            os.path.normcase(str(_three)):
        depth_marker = ("three dirnames -> <backend>/storage/raw_documents  "
                        "(OLDER revision: it writes one level ABOVE the app package)")
    elif os.environ.get("RAW_DOCUMENTS_DIR", "").strip():
        depth_marker = "RAW_DOCUMENTS_DIR from the environment (overrides both)"
    else:
        depth_marker = "neither expected expression — inspect the file"
    print(f"    expression resolves  : {depth_marker}")
    print(f"    folder exists        : {Path(storage_dir).is_dir() if storage_dir else 'n/a'}")
    has_unwrap = hasattr(ingestion, "_unwrap_extraction")
    print(f"    has _unwrap_extraction: {has_unwrap}  "
          f"{'(revised file)' if has_unwrap else '(OLDER revision — paste the new one)'}")
except Exception as exc:                                    # noqa: BLE001
    print(f"  could not import app.routers.ingestion: {type(exc).__name__}: {exc}")
    print(f"  interpreter in use: {sys.executable}")
    if "ModuleNotFoundError" in type(exc).__name__ or "No module named" in str(exc):
        print("  -> a dependency is missing from THIS python. If you start uvicorn from")
        print("     a venv, run this with that venv's python — e.g. .\\.venv\\Scripts\\python.exe check_files.py")
        print("     or activate the venv first: .\\.venv\\Scripts\\Activate.ps1")

try:
    import app.main as main_module

    print()
    print(f"  main.py serves /files from : {main_module.STORAGE_DIR}")
    print(f"    decided by               : {main_module.STORAGE_SOURCE}")
    if storage_dir:
        same = os.path.normcase(str(main_module.STORAGE_DIR)) == os.path.normcase(str(storage_dir))
        print(f"    same folder as ingestion : {same}"
              f"{'' if same else '   <-- PDFs will not display'}")

    # Filings written by an older revision of ingestion.py, which resolved
    # ./storage/raw_documents against the WORKING DIRECTORY: start uvicorn from
    # the backend root and the files land one level above the folder /files
    # serves. The rows still list, the document pane quietly has nothing to show,
    # and nothing else in the log says why.
    served = Path(str(main_module.STORAGE_DIR))
    legacy = BACKEND / "storage" / "raw_documents"
    if legacy.is_dir() and os.path.normcase(str(legacy)) != os.path.normcase(str(served)):
        stranded = sorted(legacy.glob("*.pdf"))
        if stranded:
            print()
            print(f"  {len(stranded)} PDF(s) sit in the OLD storage folder, which /files does NOT")
            print(f"  serve — filings uploaded while uvicorn ran from the backend root:")
            print(f"      {legacy}")
            for one in stranded[:3]:
                print(f"          {one.name}")
            if len(stranded) > 3:
                print(f"          ... and {len(stranded) - 3} more")
            print(f"  The document pane for those filings has nothing to render. Move them into")
            print(f"  the folder that IS served:")
            print(f'      move "{legacy}\\*.pdf" "{served}"')
except Exception as exc:                                    # noqa: BLE001
    print(f"\n  could not import app.main: {type(exc).__name__}: {exc}")
    print(f"  interpreter in use: {sys.executable}")

print()
key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""
print(f"  GEMINI_API_KEY visible to this process : {bool(key)}"
      f"{'  (length ' + str(len(key)) + ')' if key else ''}")
print(f"  GOOGLE_API_KEY visible                 : {bool(os.environ.get('GOOGLE_API_KEY'))}")
print(f"  if both are false, run check_vlm.py — it resolves .env as well")
print(f"  ECONOMIST_REQUIRE_AUTH={os.environ.get('ECONOMIST_REQUIRE_AUTH', '(unset -> auth ON)')}")
print(f"  ALLOW_SUBMISSION_CLEAR={os.environ.get('ALLOW_SUBMISSION_CLEAR', '(unset -> 403)')}")

# --------------------------------------------------------------------------
print()
print(LINE)
print("  VERDICT")
print(LINE)
print()
if not missing and not stale:
    print("  Every revised file is in place.")
    if edited:
        print()
        print("  These carry this revision but have been edited since — your changes are")
        print("  safe, and pasting the workspace copy over them would discard them:")
        for item in edited:
            print(f"    - {item}")
else:
    if missing:
        print(f"  {len(missing)} file(s) not found:")
        for item in missing:
            print(f"    - {item}")
    if stale:
        print(f"  {len(stale)} file(s) do NOT have this revision — paste these:")
        for item in stale:
            print(f"    - {item}")
    if edited:
        print()
        print(f"  {len(edited)} file(s) carry this revision but were edited after pasting:")
        for item in edited:
            print(f"    - {item}")
    print()
    print("  Copy them from PASTE_ALL.md in the workspace. Backend files go in as one")
    print("  batch, before you restart the server.")
    print()
    print("  Line endings are normalised before comparing, so a CRLF copy reports")
    print("  CURRENT rather than a false alarm.")
print()
