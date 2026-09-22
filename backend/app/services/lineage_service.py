"""Audit lineage for the PCF pipeline — app/services/lineage_service.py

Every ingested statutory document is supposed to leave a trail: which object was
read out of MinIO, which job touched it, which table it landed in, and what the
run's outcome was. That trail is the product of this file. `app/routers/
ingestion.py` calls `lineage_service.log_data_lineage(...)` after the filing row
is committed, and `lineage_audit_events` (app/db/models.py) is the sink.

The contract with the rest of the backend is the module-level name
`lineage_service`, the class `OpenLineageService`, the method name
`log_data_lineage` and its existing keyword arguments. All of those are
unchanged. The method now also RETURNS the event id it wrote (it used to return
None), which is additive.

WHAT CHANGED, AND WHY EACH ONE MATTERS
--------------------------------------
1. `eventTime` was written without a UTC offset, and `datetime.utcnow()` is
   deprecated. It now reads `2026-09-21T14:03:11.482119Z`.

   The stored string was naive, so every consumer had to *assume* a timezone.
   Read back in Dar es Salaam it becomes a different instant than in UTC — in an
   audit trail that is the difference between "before the filing deadline" and
   "after it". `utcnow()` also raises DeprecationWarning on Python 3.12+.

2. There was no `run`. Only `eventType`/`job`/`inputs`/`outputs` were emitted, so
   the events could not be grouped: each one carried a fresh random `event_id`
   and nothing tied two events about the same filing together. A run is what the
   standard correlates on, so `run.runId` is now emitted.

   It is DETERMINISTIC when you pass `correlation_id` (e.g. `filing_id`):
   `run_id_for(job_name, filing_id)` is a UUIDv5, so the same filing always
   yields the same run id without threading a value through ingestion.py. Pass
   `correlation_id=None` and you get a random uuid4, as before.

3. `eventType` was stored verbatim. The standard allows exactly six values
   (START / RUNNING / COMPLETE / ABORT / FAIL / OTHER). Anything else — a typo,
   `"SUCCESS"`, `"Completed"` — was written to the audit table and surfaced later
   as an unreadable run instead of as an error. Now the value is upper-cased and
   matched against those six, and anything else raises ValueError BEFORE anything
   is written. Case is the only thing forgiven: `"complete"` is stored as
   `"COMPLETE"`, while a near-miss like `"Completed"` is refused rather than
   guessed at. In an audit table a wrong-but-plausible value is worse than a
   refusal, and ingestion.py already passes "COMPLETE" exactly.

4. A custom top-level `"facets"` key is not part of the standard. The key/value
   data the caller passes is real, so it is not thrown away: facets belong in
   `job.facets` / `run.facets`, every facet needs `_producer` and `_schemaURL`,
   and the caller's operational data describes THIS run's outcome — so it is
   emitted as a run facet. The old flat `"facets"` key is kept as a mirror so
   nothing that reads `payload["facets"]` breaks.

5. The payload was never checked for JSON-serializability. `payload` is a JSON
   column, so a `datetime`, a `Decimal` or a `UUID` inside `facets` blew up at
   flush time with a driver-level error pointing at the column rather than the
   value. `_json_safe()` now coerces those four types and, for anything else,
   names the exact key path that cannot be serialized.

6. A failed lineage write could leave the caller's session unusable, and the
   caller's session is the one `ingestion.py` is still holding. When a session is
   passed in, the insert now runs inside a SAVEPOINT: if it fails, the savepoint
   is rolled back and the session is left clean and usable. With `db=None` the
   service opens and closes its own session instead (same DB, `AsyncSessionLocal`).

   It still does NOT swallow errors. An audit trail that fails silently is worse
   than one that fails loudly, and ingestion.py already wraps this call in its own
   try/except that prints `[Lineage Warning]`.

7. `.env` is now read in THIS file, before the settings below are read — the same
   bug `app/db/database.py` and `app/services/minio_service.py` had. Under uvicorn
   `main.py` loads it first; import this module from any script instead and it
   used the built-in defaults, pointing the audit trail at the wrong job
   namespace without saying so.

WHAT IS DELIBERATELY LEFT ALONE
-------------------------------
* No HTTP transport. This is a storage sink: events land in
  `lineage_audit_events`. Shipping them to Marquez or any other OpenLineage
  backend is a separate job and none is configured — `describe()` reports the
  endpoint as `(local table only)` so that is not a surprise later.
* No START / FAIL events are emitted and none are invented here. The pipeline
  sends exactly one COMPLETE per filing. The standard wants one START and one
  terminal (COMPLETE/ABORT/FAIL) per run; that is a change in ingestion.py, not
  in this file. `run_id_for()` exists so those two calls, when you want them,
  land in the same run as this one.

Run this file directly for a config report (it does NOT write to the DB unless
you pass --write):

    python -m app.services.lineage_service
"""
from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any

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

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import OpenLineageAuditLog

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Settings. The defaults are the values this file already used, so nothing
# changes on a box that has no .env — it just says so out loud.
# ---------------------------------------------------------------------------
DEFAULT_PRODUCER = "https://github.com/OpenLineage/pcf-engine"
DEFAULT_JOB_NAMESPACE = "bot.pcf.pipeline"
DEFAULT_SCHEMA_URL = "https://openlineage.io/spec/1-0-5/OpenLineage.json#/definitions/RunEvent"

PRODUCER = (os.getenv("OPENLINEAGE_PRODUCER") or "").strip() or DEFAULT_PRODUCER
JOB_NAMESPACE = (os.getenv("OPENLINEAGE_JOB_NAMESPACE") or "").strip() or DEFAULT_JOB_NAMESPACE
SCHEMA_URL = (os.getenv("OPENLINEAGE_SCHEMA_URL") or "").strip() or DEFAULT_SCHEMA_URL

# The six values the standard allows for eventType.
EVENT_TYPES = ("START", "RUNNING", "COMPLETE", "ABORT", "FAIL", "OTHER")
TERMINAL_EVENT_TYPES = frozenset({"COMPLETE", "ABORT", "FAIL"})

# Fixed for the life of the project: changing it would re-key every run id.
RUN_ID_NAMESPACE = uuid.UUID("5a9deaeb-6169-59de-b3d9-69b85b31e310")

# Where the run facet is declared. Point OPENLINEAGE_FACET_SCHEMA at the real
# schema once it is hosted; the producer URI is the honest default.
FACET_SCHEMA_URL = (
    os.getenv("OPENLINEAGE_FACET_SCHEMA") or ""
).strip() or f"{PRODUCER}#/facets/BotPcfAuditRunFacet"
RUN_FACET_NAME = "botPcfAudit"

_UNSET = [
    name
    for name in ("OPENLINEAGE_PRODUCER", "OPENLINEAGE_JOB_NAMESPACE")
    if not (os.getenv(name) or "").strip()
]
if not DOTENV_PATH:
    print(
        "[Lineage] No .env file was found above this module, so the settings below\n"
        f"[Lineage] are the built-in defaults: job namespace {JOB_NAMESPACE!r},\n"
        f"[Lineage] producer {PRODUCER!r}. Events are still written to\n"
        "[Lineage] lineage_audit_events, but the job namespace is the key every run\n"
        "[Lineage] is grouped under — it should be set before the first real filing.",
        file=sys.stderr,
    )
elif _UNSET:
    print(
        f"[Lineage] {DOTENV_PATH} does not set {', '.join(_UNSET)} — using the built-in "
        f"defaults (namespace {JOB_NAMESPACE!r}, producer {PRODUCER!r}).",
        file=sys.stderr,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _iso_utc(moment: datetime | None = None) -> str:
    """ISO-8601 UTC with the Z suffix the standard's examples use."""
    if moment is None:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    if moment.tzinfo is None:
        # A naive datetime is taken as UTC — the same reading the old
        # utcnow().isoformat() implied, now stated instead of guessed.
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_safe(value: Any, path: str = "payload") -> Any:
    """Return a JSON-serializable copy of `value`, or raise naming the bad path.

    `payload` is a JSON column: whatever the caller puts here has to survive
    json.dumps unchanged. Coercing the four types below keeps a stray
    `datetime` in a facet from failing the INSERT.
    """
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise TypeError(f"{path} is {value!r}, which is not valid JSON")
        return value
    if isinstance(value, (datetime, date, time)):
        return _iso_utc(value) if isinstance(value, datetime) else value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v, f"{path}.{k}") for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = sorted(value, key=repr) if isinstance(value, (set, frozenset)) else value
        return [_json_safe(v, f"{path}[{i}]") for i, v in enumerate(items)]
    raise TypeError(
        f"{path} is a {type(value).__name__}, which json.dumps cannot serialize. "
        "Coerce it at the call site or extend _json_safe()."
    )


def dataset_ref(value: Any) -> dict:
    """A dataset reference the way the standard names them.

    OpenLineage identifies a dataset by the PAIR (namespace, name): the
    namespace says where it lives, the name says what it is called there. The
    pipeline's `s3_object_key` is the storage service's own `"<bucket>/<object>"`
    form, which is neither — passing it straight through as the name puts the
    bucket inside the dataset name. Given `"pcf-raw-documents/12_x.pdf"` this
    returns `{"namespace": "s3://pcf-raw-documents", "name": "12_x.pdf"}`.
    Already-formed dicts are passed through untouched, so it is safe to wrap
    anything you are not sure about.
    """
    if isinstance(value, dict):
        return dict(value)
    text = str(value)
    if "://" in text:
        scheme, _, rest = text.partition("://")
        bucket, _, obj = rest.partition("/")
        return {"namespace": f"{scheme}://{bucket}", "name": obj or bucket}
    bucket, _, obj = text.partition("/")
    return {"namespace": f"s3://{bucket}", "name": obj}


def run_id_for(job_name: str, correlation_id: str) -> str:
    """The run id for (job, filing). Stable: same inputs, same id, forever."""
    return str(uuid.uuid5(RUN_ID_NAMESPACE, f"{JOB_NAMESPACE}/{job_name}/{correlation_id}"))


def _facet(name: str, body: dict) -> dict:
    """Wrap caller data in a facet the standard accepts.

    Every facet object carries `_producer` (who measured it) and `_schemaURL`
    (what shape it has). Without them a real lineage backend has nothing to
    validate against and drops or rejects the facet.
    """
    facet = dict(body)
    facet["_producer"] = PRODUCER
    facet["_schemaURL"] = FACET_SCHEMA_URL
    return facet


class OpenLineageService:
    @staticmethod
    async def log_data_lineage(
        db: AsyncSession | None,
        job_name: str,
        event_type: str,
        inputs: list,
        outputs: list,
        facets: dict,
        run_id: str | None = None,
        correlation_id: str | None = None,
        parent_run_id: str | None = None,
        event_time: datetime | None = None,
        producer: str | None = None,
        job_namespace: str | None = None,
    ) -> str:
        """Record one lineage event. Returns the event id that was written.

        Emits an OpenLineage RunEvent tracking a transformation MinIO ->
        PostgreSQL. `db` may be None, in which case this opens its own session.
        Raises on a bad eventType or an unserializable payload, and re-raises
        storage errors — ingestion.py handles them and logs a warning.
        """
        kind = (event_type or "").strip().upper()
        if kind not in EVENT_TYPES:
            raise ValueError(
                f"event_type {event_type!r} is not an OpenLineage event type; "
                f"expected one of {', '.join(EVENT_TYPES)}"
            )
        if not (job_name or "").strip():
            raise ValueError("job_name is required — runs are grouped by (namespace, job_name)")
        if kind == "START" and not (run_id or correlation_id):
            raise ValueError(
                "a START event must carry run_id or correlation_id, otherwise the "
                "later COMPLETE event cannot be matched to it"
            )

        if run_id is None:
            run_id = run_id_for(job_name, correlation_id) if correlation_id else str(uuid.uuid4())

        active_producer = (producer or "").strip() or PRODUCER
        active_namespace = (job_namespace or "").strip() or JOB_NAMESPACE
        event_id = str(uuid.uuid4())
        event_time_iso = _iso_utc(event_time)

        run_facets: dict = {}
        if parent_run_id:
            run_facets["parent"] = {
                "job": {"namespace": active_namespace, "name": job_name},
                "run": {"runId": parent_run_id},
            }
        run_facets[RUN_FACET_NAME] = _facet(RUN_FACET_NAME, facets or {})

        event_payload = {
            "eventType": kind,
            "eventTime": event_time_iso,
            "producer": active_producer,
            "schemaURL": SCHEMA_URL,
            "job": {"namespace": active_namespace, "name": job_name},
            "run": {"runId": run_id, "facets": run_facets},
            "inputs": [dataset_ref(item) for item in (inputs or [])],
            "outputs": [dataset_ref(item) for item in (outputs or [])],
            # Kept for anything that already reads payload["facets"]; the
            # standard puts facets under job/run, above.
            "facets": _facet(RUN_FACET_NAME, facets or {}),
        }
        event_payload = _json_safe(event_payload, "payload")

        audit_entry = OpenLineageAuditLog(
            event_id=event_id,
            event_type=kind,
            job_name=job_name,
            producer=active_producer,
            payload=event_payload,
        )

        if db is None:
            # No session handed in: own one, so a caller that is not a request
            # handler can log lineage without wiring one up.
            from app.db.database import AsyncSessionLocal

            async with AsyncSessionLocal() as own_db:
                own_db.add(audit_entry)
                await own_db.commit()
            return event_id

        try:
            # SAVEPOINT. If this insert fails, only the savepoint is rolled
            # back — whatever else the caller's session is holding stays intact.
            async with db.begin_nested():
                db.add(audit_entry)
                await db.flush()
            await db.commit()
        except Exception:
            # Only roll back when the session actually needs it. After a
            # savepoint rollback the session is already usable again, and a
            # blanket rollback here would throw away whatever else the caller's
            # session is holding — ingestion.py runs this while still inside its
            # own task. `is_active` is False exactly when the session is in the
            # failed-transaction state that requires a rollback.
            if not db.is_active:
                try:
                    await db.rollback()
                except Exception as rollback_err:        # noqa: BLE001
                    logger.warning(
                        "[Lineage] rollback after a failed write also failed: %s", rollback_err
                    )
            raise
        return event_id

    @staticmethod
    def describe() -> str:
        """One-screen report of what this service will actually write."""
        lines = [
            "app/services/lineage_service.py",
            f"  producer  : {PRODUCER}"
            + ("  [built-in default]" if PRODUCER == DEFAULT_PRODUCER else "  [from env]"),
            f"  namespace : {JOB_NAMESPACE}"
            + ("  [built-in default]" if JOB_NAMESPACE == DEFAULT_JOB_NAMESPACE else "  [from env]"),
            f"  eventTypes: {', '.join(EVENT_TYPES)}",
            f"  stores in : lineage_audit_events (payload JSON)",
            "  transport : (local table only) — no OpenLineage HTTP endpoint configured",
            f"  dotenv    : {DOTENV_PATH or 'no .env file found in the backend root'}",
            f"  schemaURL : {SCHEMA_URL}",
        ]
        return "\n".join(lines)


lineage_service = OpenLineageService()


if __name__ == "__main__":                                   # pragma: no cover
    import asyncio

    print(OpenLineageService.describe())
    sample = asyncio.run(
        OpenLineageService.log_data_lineage(
            db=None,
            job_name="ingest_and_validate_bot_questionnaire",
            event_type="COMPLETE",
            inputs=["pcf-raw-documents/example.pdf"],     # normalized inside
            outputs=[{"namespace": "postgresql.public", "name": "filings"}],
            facets={"status": "PROCESSED", "extraction_status": "ok", "extraction_warnings": 0},
        )
    ) if "--write" in sys.argv else None
    if sample:
        print(f"  wrote event {sample}")
    else:
        print("\n  (dry run — nothing was written; pass --write to insert one row)")
