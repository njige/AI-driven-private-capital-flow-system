"""
app/db/init_db.py  —  REVISED

Creates the PostgreSQL tables for the filing pipeline.

HOW TO RUN IT
-------------
Run it as a MODULE, from the backend root:

    python -m app.db.init_db

NOT as a path:

    python app/db/init_db.py        <-- fails

Reproduced:

    File "app/db/init_db.py", line 2, in <module>
        from app.db.database import engine, Base
    ModuleNotFoundError: No module named 'app'

Running a file by path puts THAT FILE'S directory on `sys.path` — `app/db/` — not
the backend root, so `app` cannot be imported. `python -m` puts the backend root on
the path instead, which is also why `uvicorn app.main:app` works. (The same mistake
in reverse is why check_files.py had to be copied to the backend root.)

The revised file detects that case and prints the command above instead of a
traceback. The detection has to sit ABOVE the `app.db` imports: the failure happens
at import time, so a check in `if __name__ == "__main__"` at the bottom of the file
never gets a chance to run.

WHAT `create_all` DOES, AND DOES NOT DO
---------------------------------------
It creates the tables that do not exist. It is idempotent — running it twice is
fine. It does NOT alter a table that already exists: no new column, no type change,
no rename, no index. It reports success either way.

That silence is the problem this file previously had. A demonstration: create
`filings` with only two of its sixteen columns, run the script, and it prints
"Database tables created successfully!" while the table is still two columns wide.
Anything reading a missing column then fails at query time, somewhere else
entirely, with

    column filings.reporting_year does not exist

and nothing to connect it to this script.

So after creating, this version READS BACK what is actually in the database and
compares it with the models. If a column the model expects is absent, it says so
and exits 1, naming the ALTER TABLE you would need. Creating a missing table is
never a substitute for migrating an existing one — see OPTIONAL_MIGRATIONS.sql for
the same reasoning applied to specific columns.

ALEMBIC
-------
This project also has alembic/ and alembic.ini. Do not run this script and
Alembic against the same tables: Alembic keeps its own record of what it has
migrated (`alembic_version`), and a table created here is invisible to it, so
`alembic upgrade head` will try to CREATE TABLE filings and fail with

    relation "filings" already exists

Pick one mechanism. This script is the quick path for a fresh development
database; Alembic is the right answer the moment more than one person or
environment has to agree on a schema.
"""
from __future__ import annotations

import asyncio
import sys

# ---------------------------------------------------------------------------
# THIS GUARD MUST COME BEFORE THE `app.db` IMPORTS BELOW.
#
# Running this file by path (`python app/db/init_db.py`) raises
# ModuleNotFoundError on the `from app.db.database import ...` line — at IMPORT
# time. A check inside `if __name__ == "__main__"` at the bottom of the file would
# never run, because the import failure happens first. Verified: the message was
# printed by nothing and the traceback won anyway.
#
# `__package__` is "" (or None) when a file is executed directly, and "app.db"
# when it is run with -m or imported.
# ---------------------------------------------------------------------------
if __package__ in (None, "") and __name__ == "__main__":
    print()
    print("Run this as a module, from the backend root — the folder with app/ in it:")
    print()
    print("    python -m app.db.init_db")
    print()
    print("Running the file by path puts app/db/ on sys.path instead of the backend")
    print("root, so `from app.db.database import ...` fails with ModuleNotFoundError.")
    print("`python -m` puts the backend root on the path, which is why")
    print("`uvicorn app.main:app` works the same way.")
    print()
    raise SystemExit(2)

from typing import Set

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.database import Base, engine

# Importing the models is what puts their tables into Base.metadata, so this
# import is load-bearing, not unused. Without it `create_all` finds an empty
# metadata and creates nothing at all — silently.
from app.db.models import OpenLineageAuditLog, FilingRecord  # noqa: F401

# Every mapped class, so a table added to models.py is covered here automatically
# instead of being forgotten. (FilingRecord and OpenLineageAuditLog are the two
# today; referencing Base.metadata keeps this honest if that changes.)
MODELS = (FilingRecord, OpenLineageAuditLog)


async def _existing_tables(conn: AsyncConnection) -> Set[str]:
    def _read(sync_conn) -> Set[str]:
        return set(inspect(sync_conn).get_table_names())

    return await conn.run_sync(_read)


async def _missing_columns(conn: AsyncConnection, table_name: str) -> Optional[Set[str]]:
    """Columns the model declares that the live table does not have.

    None means the table does not exist at all, which after create_all is itself
    worth saying.
    """

    def _read(sync_conn):
        inspector = inspect(sync_conn)
        if table_name not in inspector.get_table_names():
            return None
        return {column["name"] for column in inspector.get_columns(table_name)}

    live = await conn.run_sync(_read)
    if live is None:
        return None
    return {column.name for column in Base.metadata.tables[table_name].columns} - live


async def init_tables() -> int:
    """Create what is missing, then report what the database actually holds.

    Returns a process exit code: 0 when every model table exists with every model
    column, 1 when something is still missing after create_all.
    """
    print("Connecting to the database...")

    async with engine.begin() as conn:
        before = await _existing_tables(conn)
        print(f"Tables present before: {sorted(before) or '(none)'}")

        print("Creating any missing tables...")
        await conn.run_sync(Base.metadata.create_all)

        after = await _existing_tables(conn)
        created = sorted(after - before)
        print(f"Created: {created or '(nothing — everything already existed)'}")
        print()

        # ---- verify, rather than assume -------------------------------
        print("Checking the models against the database:")
        problems: list[str] = []
        for model in MODELS:
            table_name = model.__table__.name
            declared = {column.name for column in model.__table__.columns}
            missing = await _missing_columns(conn, table_name)

            if missing is None:
                problems.append(f"table {table_name!r} does not exist after create_all")
                print(f"  {table_name:<24} MISSING TABLE")
                continue
            if missing:
                problems.append(f"table {table_name!r} is missing columns: {sorted(missing)}")
                print(f"  {table_name:<24} EXISTS, but {len(missing)} column(s) missing:")
                for column in sorted(missing):
                    print(f"      - {column}")
            else:
                print(f"  {table_name:<24} OK ({len(declared)} columns)")

        if problems:
            print()
            print("!" * 72)
            print("  The database does not match the models.")
            print()
            for problem in problems:
                print(f"  - {problem}")
            print()
            print("  create_all only CREATES MISSING TABLES. It never alters one that")
            print("  already exists, which is why the difference above survived it.")
            print("  Fix it with an explicit ALTER TABLE, or drop the table and re-run")
            print("  this script on a development database:")
            print()
            for problem in problems:
                table = problem.split("'")[1] if "'" in problem else "?"
                print(f"      DROP TABLE IF EXISTS {table};   -- then: python -m app.db.init_db")
            print()
            print("  Back up first. On PostgreSQL, a running API will keep using the")
            print("  old schema until it reconnects.")
            print("!" * 72)
            return 1

        print()
        print("Database schema matches the models.")
        return 0


async def _main() -> int:
    try:
        return await init_tables()
    except Exception as exc:                                   # noqa: BLE001
        # The usual cause is the database itself: not running, wrong URL, wrong
        # credentials. Name the file that holds the URL rather than dumping a
        # driver traceback with no next step.
        print()
        print(f"Could not initialise the database: {type(exc).__name__}: {exc}")
        print()
        print("Check that PostgreSQL is running and that the URL in app/db/database.py")
        print("(usually read from DATABASE_URL in .env) points at it. A connection")
        print("refused, a wrong password and a missing database all land here.")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
