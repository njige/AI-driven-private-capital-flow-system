"""
app/main.py  —  REVISED

Application entry point. This is the file `uvicorn app.main:app` loads.

WHERE THIS FILE SITS MATTERS, SO IT NO LONGER DEPENDS ON WHERE IT SITS
--------------------------------------------------------------------
Your main.py used to live at app/services/main.py and has moved to app/main.py
(the tree in your screenshot, and your start command `uvicorn app.main:app`,
both agree on that). Moving it one level up silently changed this line:

    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    from app/main.py        -> <backend>/            -> <backend>/storage/raw_documents
    from app/services/main.py -> <backend>/app/      -> <backend>/app/storage/raw_documents

Same expression, two different folders, decided by the depth of the file. And
app/routers/ingestion.py writes to the SECOND one, because it is two levels down
as well. So the folder served at /files was no longer the folder the pipeline
writes to, and the review screen's iframe would have shown an empty box for every
document.

This version does not compute a path from its own location at all. It mounts the
directory ingestion.py itself reports, so the two cannot disagree:

    from app.routers import ingestion
    STORAGE_DIR = ingestion.STORAGE_DIR

WHAT WAS WRONG
--------------
1. TWO STORAGE PATHS THAT HAD TO AGREE BY HAND. Fixed as above. If ingestion.py
   is an older copy with no STORAGE_DIR attribute, this falls back to the `app`
   package's own location — which is the same anchor ingestion.py uses, so the two
   still agree — and says so in the startup log.

2. THE STATIC MOUNT COULD CRASH A FRESH CHECKOUT. `StaticFiles(directory=...)`
   raises `RuntimeError: Directory '...' does not exist` IN ITS CONSTRUCTOR, at
   import, which is before the lifespan handler runs. So the `os.makedirs` inside
   `lifespan` could never prevent it — it ran too late by design. Reproduced:

       app.mount("/files", StaticFiles(directory=STORAGE_DIR))
       RuntimeError: Directory '/tmp/shim/storage/raw_documents' does not exist

   The directory is now created BEFORE the mount, at import time. Your machine
   escapes this because the lifespan makedirs is relative to the CURRENT WORKING
   DIRECTORY while the mount is relative to the FILE — starting uvicorn from the
   backend root happens to create the folder the mount wants. Start it from one
   directory up and the app would have refused to boot.

3. THE LIFESPAN CREATED A DIFFERENT FOLDER DEPENDING ON WHERE YOU STARTED UVICORN.
   `os.makedirs("./storage/raw_documents")` is relative to the working directory.
   It now creates the same absolute path that is mounted.

4. CORS TRUSTED EVERY ORIGIN, WITH CREDENTIALS. To be accurate about what this
   does and does not do: Starlette does NOT send a broken header here. With
   `allow_origins=["*"]` and `allow_credentials=True` it reflects the caller's
   Origin back and adds `Access-Control-Allow-Credentials: true`:

       request Origin: http://localhost:5173
       access-control-allow-origin: http://localhost:5173
       access-control-allow-credentials: true

   So it works — it is not the cause of a failed request. The problem is that it
   will do that for ANY origin, which is a permissive default to ship in a central
   bank's API. Now configurable through CORS_ALLOWED_ORIGINS, and when that is
   unset the wildcard is used WITHOUT credentials, so the reflexive behaviour
   cannot happen by accident.

   This does not affect your Bearer-token flow either way: the frontend sends the
   token in a header, not a cookie, and Starlette always allows the headers.

5. MINIO COULD STOP THE API BOOTING. `minio_service._ensure_bucket_exists()` in
   the lifespan raises if MinIO is unreachable, which takes down an API that has a
   working local-storage fallback. Now wrapped: unreachable MinIO logs and the API
   boots.

UNCHANGED
---------
`load_dotenv()` at the top, before any `app.*` import — that ordering is correct
and load-bearing, and it is the reason the extractor can see GEMINI_API_KEY at
all. AUTH_REVIEW.md finding 1 was a caution about a file that did not do this; it
is already right here. The three routers, the route paths, the `title`,
`description`, `version` and the `/` response are all as you wrote them.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

# MUST stay above the app.* imports. The routers read configuration at import
# time — ALLOW_SUBMISSION_CLEAR and ECONOMIST_REQUIRE_AUTH in economist.py,
# RAW_DOCUMENTS_DIR in ingestion.py — so .env has to be loaded first or those
# values are read as absent.
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.routers import auth_router, economist, ingestion
from app.services.minio_service import minio_service


# ==========================================================================
# STORAGE — one definition, taken from the code that writes the files
# ==========================================================================

def _resolve_storage_dir() -> tuple[Path, str]:
    """Return (directory to serve at /files, how it was decided).

    Deliberately NOT derived from this file's own depth. See the docstring above.
    """
    override = os.environ.get("RAW_DOCUMENTS_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve(), "RAW_DOCUMENTS_DIR from the environment"

    reported = getattr(ingestion, "STORAGE_DIR", None)
    if reported:
        return Path(str(reported)).expanduser().resolve(), "app/routers/ingestion.py STORAGE_DIR"

    # Older ingestion.py without the constant: anchor on the `app` package, which
    # is what that file's own two-dirnames expression resolves to anyway.
    import app as app_package

    package_root = Path(app_package.__file__).resolve().parent
    return (package_root / "storage" / "raw_documents",
            "the app package location (ingestion.py has no STORAGE_DIR)")


STORAGE_DIR, STORAGE_SOURCE = _resolve_storage_dir()

# Created BEFORE the mount, because StaticFiles raises in its constructor if the
# directory is missing and the lifespan handler has not run yet at that point.
STORAGE_DIR.mkdir(parents=True, exist_ok=True)


# ==========================================================================
# LIFESPAN
# ==========================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup. The storage directory already exists by now; this is only a
    # safety net for the case where something removed it while running.
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)

    # An unreachable MinIO must not stop an API that has a working local
    # fallback. Previously this raised straight out of startup.
    try:
        minio_service._ensure_bucket_exists()
        print("[MINIO] bucket check completed")
    except Exception as exc:
        print(
            f"[MINIO] bucket check failed ({type(exc).__name__}: {exc}). "
            f"Continuing — uploads fall back to local storage at {STORAGE_DIR}."
        )

    print(f"[STORAGE] serving /files from {STORAGE_DIR}  ({STORAGE_SOURCE})")
    yield
    # Shutdown events can be placed here if needed


app = FastAPI(
    title="Private Capital Flow AI System API",
    description="Bank of Tanzania Automated PCF Statutory Verification & Audit Engine",
    version="1.0.0",
    lifespan=lifespan,
)


# ==========================================================================
# CORS
# ==========================================================================
# Explicit list when configured, which is what you want anywhere real:
#
#     # .env
#     CORS_ALLOWED_ORIGINS=http://localhost:5173,http://localhost:3000
#
# Unset falls back to the wildcard you had, but WITHOUT credentials — with
# allow_credentials=True Starlette reflects whatever Origin arrives, which is a
# permissive thing to leave on by default.
_origins_env = os.environ.get("CORS_ALLOWED_ORIGINS", "").strip()
if _origins_env:
    CORS_ORIGINS = [o.strip() for o in _origins_env.split(",") if o.strip()]
    CORS_ALLOW_CREDENTIALS = True
    _cors_note = f"{len(CORS_ORIGINS)} configured origin(s)"
else:
    CORS_ORIGINS = ["*"]
    CORS_ALLOW_CREDENTIALS = False
    _cors_note = "wildcard, credentials off (set CORS_ALLOWED_ORIGINS to restrict)"

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=CORS_ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==========================================================================
# STATIC DOCUMENTS
# ==========================================================================
# Mounted from STORAGE_DIR, created above. The review screen embeds
# /files/<name> in an <iframe>; an iframe cannot send an Authorization header,
# so this route is deliberately unauthenticated. See AUTH_REVIEW.md — the same
# documents are also reachable through ingestion's document-file route.
app.mount("/files", StaticFiles(directory=str(STORAGE_DIR)), name="files")

# A second, import-time report. uvicorn's own logging can be filtered or turned
# down; this line is the quickest way to see the path from a terminal.
print(f"[STORAGE] /files -> {STORAGE_DIR}  ({STORAGE_SOURCE})")
print(f"[CORS] {_cors_note}")


# ==========================================================================
# ROUTERS
# ==========================================================================

app.include_router(auth_router.router)
app.include_router(ingestion.router)
app.include_router(economist.router)


@app.get("/")
async def root():
    return {
        "system": "Private Capital Flow AI Platform",
        "status": "ONLINE",
        "bot_audit_engine": "ACTIVE",
    }


@app.get("/health")
async def health():
    """Cheap, read-only status. Answers the two questions that matter when an
    upload fails: is the extraction service configured in THIS process, and where
    are documents actually being written?

    Reports only whether the key is present. Never the key itself.
    """
    return {
        "status": "ok",
        "storage_dir": str(STORAGE_DIR),
        "storage_dir_exists": STORAGE_DIR.is_dir(),
        "storage_source": STORAGE_SOURCE,
        "documents_visible_at": "/files",
        "vlm_configured": bool(os.environ.get("GEMINI_API_KEY")),
        "vlm_key_env_var": "GEMINI_API_KEY",
        "economist_requires_auth": getattr(economist, "ECONOMIST_REQUIRE_AUTH", None),
        "submission_clear_allowed": getattr(economist, "ALLOW_SUBMISSION_CLEAR", None),
    }