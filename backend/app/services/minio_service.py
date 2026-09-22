"""Object storage for the raw statutory documents — app/services/minio_service.py

`app/main.py` imports `minio_service` and calls `_ensure_bucket_exists()` at
startup; `app/routers/ingestion.py` calls `upload_file(...)` for every upload.
Those two calls, the four module-level names (`minio_service`, `MinIOService`,
`BUCKET_NAME`, `MINIO_ENDPOINT`) and the `"<bucket>/<name>"` string that
`upload_file` returns are the contract with the rest of the backend. All of them
are unchanged here.

WHAT CHANGED, AND WHY EACH ONE MATTERS
--------------------------------------
1. `.env` is now read in THIS file, before the three `os.getenv` calls.

   Exactly the bug `app/db/database.py` had: the credentials were read at import
   and relied on `main.py` having called `load_dotenv()` first. Under uvicorn it
   works. Run a script that imports this module directly — a backfill, a
   re-upload, a test — and the well-known defaults below silently win, so the
   process talks to `localhost:9001` with `minioadmin/minioadmin` while `.env`
   says something else.

2. `secure` is `MINIO_SECURE` (default False — unchanged for your local box).

   It was hardcoded `secure=False`, which is right for a container on the same
   machine and wrong for anything that terminates TLS: the raw documents would
   cross the network unencrypted, and a portal served over HTTPS would have its
   download links refused as mixed content.

3. If any of the three connection settings falls back to its default, this file
   says so, once, on stderr. A silent fallback to `minioadmin` is worse than a
   failed upload.

   The default endpoint deserves special attention: **`localhost:9001` is
   MinIO's console port, not its S3 port.** The console is what the Raw Documents
   tab embeds; the S3 API is normally on 9000. If you ever see the warning below,
   that is the first thing to check — `bucket_exists` would then fail while the
   console URL opens fine in a browser, which looks like a credentials problem
   and is not one.

4. `upload_file` gives a failed upload a message that names the endpoint and the
   bucket, then re-raises the SAME exception type — `ingestion.py` catches
   `Exception` and falls back to local storage, so that path is untouched.

5. `presigned_url()` is new and optional. It exists because `upload_file` returns
   `"<bucket>/<object>"` and that is what `s3_object_key` holds, while every
   MinIO SDK call wants the bare object name. Passing the stored value straight
   into `presigned_get_object()` asks MinIO for an object literally called
   `pcf-raw-documents/...` inside the bucket and fails. The helper accepts both
   forms, so it is safe to hand it `s3_object_key` as it is stored today.
"""
from __future__ import annotations

import io
import logging
import os
import sys
from datetime import timedelta

# ---------------------------------------------------------------------------
# .env has to be read BEFORE the getenv() calls below.
# ---------------------------------------------------------------------------
try:
    from dotenv import find_dotenv, load_dotenv

    DOTENV_PATH = find_dotenv() or None      # walks up from this file: <backend>/.env
    if DOTENV_PATH:
        load_dotenv(DOTENV_PATH)
except ImportError:                          # python-dotenv absent; real env vars still work
    DOTENV_PATH = None

from minio import Minio
from minio.error import S3Error

logger = logging.getLogger(__name__)

DEFAULT_ENDPOINT = "localhost:9001"
DEFAULT_ACCESS_KEY = "minioadmin"
DEFAULT_SECRET_KEY = "minioadmin"
DEFAULT_BUCKET = "pcf-raw-documents"


def _flag(name: str, default: bool = False) -> bool:
    """Read a boolean environment switch: MINIO_SECURE=1, =true, =yes, =on."""
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


MINIO_ENDPOINT = (os.getenv("MINIO_ENDPOINT") or "").strip() or DEFAULT_ENDPOINT
MINIO_ACCESS_KEY = (os.getenv("MINIO_ACCESS_KEY") or "").strip() or DEFAULT_ACCESS_KEY
MINIO_SECRET_KEY = (os.getenv("MINIO_SECRET_KEY") or "").strip() or DEFAULT_SECRET_KEY
MINIO_SECURE = _flag("MINIO_SECURE")         # was hardcoded secure=False
BUCKET_NAME = (os.getenv("MINIO_BUCKET") or "").strip() or DEFAULT_BUCKET

_FELL_BACK = [
    name
    for name, value, default in (
        ("MINIO_ENDPOINT", MINIO_ENDPOINT, DEFAULT_ENDPOINT),
        ("MINIO_ACCESS_KEY", MINIO_ACCESS_KEY, DEFAULT_ACCESS_KEY),
        ("MINIO_SECRET_KEY", MINIO_SECRET_KEY, DEFAULT_SECRET_KEY),
    )
    if value == default
]

if _FELL_BACK:
    print(
        f"[MinIO] Not configured: {', '.join(_FELL_BACK)} — using the built-in\n"
        f"[MinIO] development defaults, i.e. endpoint {MINIO_ENDPOINT} with\n"
        f"[MinIO] access key {MINIO_ACCESS_KEY!r}. Put the real values in the\n"
        f"[MinIO] backend's .env before this goes anywhere near real filings."
        + ("" if DOTENV_PATH else "  (no .env file was found)"),
        file=sys.stderr,
    )
    if MINIO_ENDPOINT == DEFAULT_ENDPOINT:
        print(
            "[MinIO] Note: 9001 is the CONSOLE port. The S3 API is normally on 9000.",
            file=sys.stderr,
        )


class MinIOService:
    def __init__(self):
        # Minio() only builds a client — no connection is opened here.
        self.client = Minio(
            MINIO_ENDPOINT,
            access_key=MINIO_ACCESS_KEY,
            secret_key=MINIO_SECRET_KEY,
            secure=MINIO_SECURE,
        )

    def _ensure_bucket_exists(self) -> bool:
        """Lazy-checks and creates the bucket safely without raising startup-blocking exceptions.

        Returns True when the bucket is known to exist, False when storage could
        not be reached. Callers that ignore the return value behave exactly as
        before — `main.py` ignores it on purpose, so that a MinIO outage cannot
        stop the API from booting.
        """
        try:
            if not self.client.bucket_exists(BUCKET_NAME):
                self.client.make_bucket(BUCKET_NAME)
                logger.info(f"[MinIO]: Bucket '{BUCKET_NAME}' verified/created.")
            return True
        except Exception as e:
            logger.warning(
                f"[MinIO Connection Warning]: Could not reach storage backend at "
                f"{MINIO_ENDPOINT} for bucket '{BUCKET_NAME}' "
                f"(secure={MINIO_SECURE}): {type(e).__name__}: {e}"
            )
            return False

    def upload_file(
        self,
        object_name: str,
        file_data: bytes,
        content_type: str | None = "application/pdf",
    ) -> str:
        """Uploads raw file stream to MinIO bucket and returns the object key as
        `"<bucket>/<object>"` — the string `s3_object_key` has always held."""
        # Ensure the bucket exists prior to write execution
        self._ensure_bucket_exists()

        file_stream = io.BytesIO(file_data)
        try:
            self.client.put_object(
                bucket_name=BUCKET_NAME,
                object_name=object_name,
                data=file_stream,
                length=len(file_data),
                # FastAPI's UploadFile.content_type is Optional — a browser that
                # sends no Content-Type would otherwise store content_type=None.
                content_type=content_type or "application/pdf",
            )
        except S3Error as e:
            if e.code == "NoSuchBucket":
                logger.error(
                    f"[MinIO]: bucket '{BUCKET_NAME}' does not exist on "
                    f"{MINIO_ENDPOINT} — create it, or set MINIO_BUCKET."
                )
            logger.error(
                f"[MinIO]: upload of '{object_name}' failed on {MINIO_ENDPOINT} "
                f"(bucket '{BUCKET_NAME}'): {e.code}: {e.message}"
            )
            raise  # same exception type, so ingestion.py's fallback still catches it
        return f"{BUCKET_NAME}/{object_name}"

    # ------------------------------------------------------------------ keys --
    @staticmethod
    def bare_object_name(object_name: str) -> str:
        """`"pcf-raw-documents/f.pdf"` -> `"f.pdf"`; a bare name passes through.

        Every MinIO SDK call wants the object name on its own. The stored
        `s3_object_key` carries the bucket prefix, so strip it rather than
        handing it over as part of the key.
        """
        prefix = f"{BUCKET_NAME}/"
        return object_name[len(prefix):] if object_name.startswith(prefix) else object_name

    def presigned_url(self, object_name: str, expires_seconds: int = 3600) -> str:
        """A temporary download URL — what a browser should be given.

        Accepts either form of the key, so `record.s3_object_key` can be passed
        in exactly as it is stored.
        """
        return self.client.presigned_get_object(
            BUCKET_NAME,
            self.bare_object_name(object_name),
            expires=timedelta(seconds=expires_seconds),
        )


# Instantiate non-blocking singleton
minio_service = MinIOService()


def describe() -> str:
    """One line for a startup banner or a support ticket."""
    key = f"{MINIO_ACCESS_KEY[:3]}***" if MINIO_ACCESS_KEY else "<unset>"
    return (
        f"{MINIO_ENDPOINT}  bucket={BUCKET_NAME}  secure={MINIO_SECURE}  key={key}  "
        f"[source: {'environment or .env' if not _FELL_BACK else 'built-in defaults'}]"
    )


if __name__ == "__main__":  # pragma: no cover - diagnostic, run as a module
    print("app/services/minio_service.py")
    print("  target :", describe())
    print("  dotenv :", DOTENV_PATH or "no .env file found in the backend root")
    reachable = minio_service._ensure_bucket_exists()
    print("  storage:", "reachable" if reachable else "NOT REACHABLE — see the warning above")
    if reachable:
        try:
            names = [o.object_name for o in minio_service.client.list_objects(BUCKET_NAME)]
            print(f"  objects: {len(names)} at the top level of '{BUCKET_NAME}'")
            for name in names[:5]:
                print("           -", name)
        except Exception as e:
            print("  objects: could not list:", type(e).__name__, e)
    print("  Run from the backend root:  python -m app.services.minio_service")
