"""Database engine and session factory  —  app/db/database.py

Three jobs, in this order:

    1. find the database URL   (environment variable, then `.env`, then a dev default)
    2. build ONE async engine and ONE session factory for the whole process
    3. hand a session to any route that asks for one through `Depends(get_db)`

`app/db/models.py` imports `Base` from here; `app/db/init_db.py` imports `Base`
and `engine` from here. Those four names — `engine`, `Base`, `AsyncSessionLocal`,
`get_db` — are the contract with the rest of the backend. All four are still
exported here, with the same meaning and the same signature.

WHAT CHANGED FROM YOUR VERSION, AND WHY EACH ONE MATTERS
--------------------------------------------------------
1. `load_dotenv()` now runs in THIS file, before `DATABASE_URL` is read.

   Your version read `os.getenv("DATABASE_URL")` at import time and relied on
   something else having loaded `.env` first. `app/main.py` does call
   `load_dotenv()` above its `app.*` imports, so uvicorn was fine — but
   `python -m app.db.init_db` and `alembic upgrade head` import this file
   directly and never go through `main.py`. Run that way the variable is absent,
   the default below silently wins, and the tables get created against
   `pcf_db` on localhost no matter what `.env` says.

   Loading it here fixes every entry point at once. It also means the file is
   found no matter which directory you run python from: the search walks upwards
   from this file. And it is harmless when `main.py` has already done it —
   python-dotenv does not override real environment variables, and loading twice
   changes nothing.

2. `echo=True` is now `DB_ECHO=1` — off unless you ask for it.

   Verified with a bind parameter: with echo on, SQLAlchemy logs every statement
   *together with its bound values* —

       INSERT INTO users VALUES (?, ?)
       [generated in 0.00016s] ('118-492-705', 'NjiGE@2026!')

   TINs, passcodes and filings end up in the uvicorn console and in any file it
   is piped to. `hide_parameters=True` is set as a second line of defence for
   when you do turn echo on while debugging.

3. `connect_args={"ssl": False}` is applied only to `postgresql+asyncpg://`
   URLs, and its value now comes from `DATABASE_SSL`. The default is unchanged
   (SSL off — correct for the Docker PostgreSQL container you run against).
   But `connect_args` are driver-specific: the same call against
   `sqlite+aiosqlite://` raises
   `TypeError: Connection() got an unexpected keyword argument 'ssl'`, and a
   hosted PostgreSQL that insists on TLS would fail its handshake. Set
   `DATABASE_SSL=1` for that case.

4. `pool_pre_ping=True` and `pool_recycle=1800` against a real server. Without
   pre-ping, a pooled connection that PostgreSQL closed while the app was idle
   is handed to the next request, which dies with "connection was closed in the
   middle of operation" — the classic *first request after a quiet night fails,
   the retry works* pattern. Both are skipped for SQLite.

5. `Base = declarative_base()` became `class Base(DeclarativeBase)` — the
   SQLAlchemy 2.0 form. `app/db/models.py` needs no change: it subclasses
   whatever `Base` means, so `class FilingRecord(Base)` behaves identically.
   The old call still works in 2.0; this just means you never have to migrate
   it later.

6. If the built-in development default is used, this file SAYS SO — once, on
   stderr, with the password masked. A missing `.env` should not look like a
   quiet success.
"""
from __future__ import annotations

import os
import sys

# ---------------------------------------------------------------------------
# .env has to be read BEFORE the getenv() below, so this import sits first.
# ---------------------------------------------------------------------------
try:
    from dotenv import find_dotenv, load_dotenv

    # find_dotenv() searches upwards from THIS file, so <backend>/.env is found
    # however you started python — from the backend root or from anywhere else.
    DOTENV_PATH = find_dotenv() or None
    if DOTENV_PATH:
        load_dotenv(DOTENV_PATH)
    DOTENV_LOADED = True
except ImportError:        # python-dotenv absent; real environment vars still work
    DOTENV_PATH = None
    DOTENV_LOADED = False

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

# Database URL format: postgresql+asyncpg://user:password@host:port/dbname
DEFAULT_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/pcf_db"


def _flag(name: str, default: bool = False) -> bool:
    """Read a boolean environment switch: DB_ECHO=1, DATABASE_SSL=true, ..."""
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _normalise_url(url: str) -> str:
    """A bare postgres:// URL selects a SYNCHRONOUS driver. Fix it loudly.

    `postgresql://...` makes SQLAlchemy load psycopg2 and then fail inside the
    asyncio layer with a message that never mentions the URL — so if the only
    thing wrong is a missing `+asyncpg`, say that here instead.
    """
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            fixed = "postgresql+asyncpg://" + url[len(prefix):]
            print(
                f"[DB] DATABASE_URL starts with '{prefix}', which selects a "
                f"synchronous driver.\n"
                f"[DB] Using '{fixed.split('@')[-1]}' with asyncpg instead — "
                f"rewrite the URL as 'postgresql+asyncpg://...' to silence this.",
                file=sys.stderr,
            )
            return fixed
    return url


def redacted(url: str) -> str:
    """A connection URL safe to print: the password becomes ***."""
    try:
        return str(make_url(url).set(password="***"))
    except Exception:
        return "<unparseable DATABASE_URL>"


DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
DATABASE_URL_SOURCE = "DATABASE_URL (environment or .env)"
if not DATABASE_URL:
    DATABASE_URL = DEFAULT_DATABASE_URL
    DATABASE_URL_SOURCE = "built-in development default"
DATABASE_URL = _normalise_url(DATABASE_URL)

if DATABASE_URL_SOURCE == "built-in development default":
    print(
        f"[DB] DATABASE_URL is not set — using the development default\n"
        f"[DB]   {redacted(DATABASE_URL)}\n"
        f"[DB] Put DATABASE_URL in the backend's .env to point somewhere else."
        + ("" if DOTENV_LOADED else "  (python-dotenv is missing, so .env was not read)")
        + ("" if DOTENV_PATH else "  (no .env file was found either)"),
        file=sys.stderr,
    )

# Kept for anything that still imports the old name.
POSTGRES_URL = DATABASE_URL

_IS_SQLITE = DATABASE_URL.startswith("sqlite")
_IS_ASYNC_PG = DATABASE_URL.startswith("postgresql+asyncpg")

_engine_kwargs: dict = {
    "echo": _flag("DB_ECHO"),          # was hardcoded True
    "hide_parameters": True,           # never write bind values to the log
}
if _IS_ASYNC_PG:
    # asyncpg only — ssl=False is what keeps the Docker container stable.
    _engine_kwargs["connect_args"] = {"ssl": _flag("DATABASE_SSL")}
if not _IS_SQLITE:
    _engine_kwargs["pool_pre_ping"] = True
    _engine_kwargs["pool_recycle"] = 1800

engine = create_async_engine(DATABASE_URL, **_engine_kwargs)

AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


class Base(DeclarativeBase):
    """Declarative base for every model in app/db/models.py."""


async def get_db():
    """FastAPI dependency — one session per request, closed when the request ends."""
    async with AsyncSessionLocal() as session:
        yield session


def describe() -> str:
    """One line for a startup banner or a support ticket."""
    return (
        f"{redacted(DATABASE_URL)}  "
        f"[source: {DATABASE_URL_SOURCE}; echo={engine.echo}; "
        f"ssl={'on' if _flag('DATABASE_SSL') else 'off' if _IS_ASYNC_PG else 'n/a'}]"
    )


if __name__ == "__main__":  # pragma: no cover - diagnostic, run as a module
    print("app/db/database.py")
    print("  URL    :", describe())
    print("  dotenv :", DOTENV_PATH or ("python-dotenv NOT INSTALLED" if not DOTENV_LOADED
          else "no .env file found — create one in the backend root"))
    print("  Run from the backend root:")
    print("      python -m app.db.database     (this diagnostic)")
    print("      python -m app.db.init_db      (create the tables)")
