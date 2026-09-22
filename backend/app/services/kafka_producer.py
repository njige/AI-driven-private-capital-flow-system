"""Outbound filing events — app/services/kafka_producer.py

What this file is for: after a statutory filing is ingested, validated or
re-audited, the rest of the world (analytics, a DAG, the archive) is supposed to
hear about it as an event on `bot.c17.submissions`. `ingestion.py` records what
happened inside PostgreSQL; this is the half that leaves the process.

The contract with the rest of the backend is the module-level name
`stream_producer`, the class `StreamProducer`, the method name
`publish_ingestion_event` and its keyword arguments (filing_id, event_type,
payload, status). All of those are unchanged. `publish_ingestion_event` still
returns a bool — but the bool now means something (see 6 below).

WHAT CHANGED, AND WHY EACH ONE MATTERS
--------------------------------------
1. The event was carrying a FABRICATED identity.

   It read the company name and TIN from `payload["questionnaire"]["metadata"]`.
   Neither exists at that path. Every payload this pipeline stores is a flat
   BOTQuestionnaireC17 dump (`metadata`, `part_a`, `part_b`,
   `part_c_liabilities`, ...), and `company_name` / `tin_number` live at
   `part_a.details`, while `metadata` holds the survey period, not the entity.
   So `payload.get("questionnaire", {})` returned `{}` on every single call and
   the event went out saying `"company_name": "Unknown Entity"` and
   `"tin_number": "000000000"`.

   A made-up TIN in a published event is worse than a missing one: the
   downstream consumer cannot tell it apart from a real one, and it is the same
   bug that was removed from ingestion.py ("no fabricated TIN"). Now the values
   are read from the right paths, `None` is published when a value is genuinely
   absent, and the event carries a `warnings` list saying which ones were
   missing. Both payload shapes are accepted — the flat questionnaire and the
   `{"questionnaire": {...}}` envelope that validation_engine returns — because
   whichever one a caller happens to pass, the event should be right.

   The pipeline's own fields are read from where the schema declares them, and
   they are NOT all in the same place. `extraction_status`, `validation_warnings`
   and `not_applicable_fields` sit at the ROOT of BOTQuestionnaireC17 (see
   app/schemas/bot_questionnaire.py, "pipeline / audit metadata"), while
   `extraction_warnings`, `questionnaire_type` and `reporting_year` are under
   `metadata`. An earlier revision of this fix read `extraction_status` from
   `metadata`, where it never appears — the field was published as null on every
   event, which is the same quiet-null shape as the placeholder it replaced.

2. The event_id was not an id.

   `f"evt-{filing_id}-{int(utcnow().timestamp())}"` has one-second resolution, so
   two events for the same filing inside the same second — DOCUMENT_RECEIVED
   followed immediately by EXTRACTION_COMPLETED, which is the normal case — got
   THE SAME event id. Any consumer deduplicating on event_id silently drops the
   second event. It is now a UUID4, with `correlation_id` set to the filing id so
   the events of one filing can still be grouped, plus a `schema_version` so a
   consumer can tell which shape it is reading.

3. `utcnow()` was called twice and has no timezone.

   Two calls can straddle a second boundary, so the id and the timestamp of one
   event could disagree; and a naive `"timestamp"` is read as local time by every
   consumer outside UTC. One tz-aware clock read now feeds both, as
   `2026-09-21T14:03:11.482119Z`.

4. The whole payload was written to the log at INFO.

   `logger.info(f"...{json.dumps(event_message)}")` puts the company name, TIN,
   every reported figure and every shareholder on one log line — and log files
   get copied into places a balance sheet should not be. It also runs the
   `json.dumps` unconditionally, so one non-serializable value turns a logging
   line into an exception that kills the publish. The log line is now a
   summary — filing id, event type, status, and whether a name/year were present
   — and the message body is passed through `_json_safe()`.

5. `event_type` was free text and `status` defaulted to a clean pass.

   The docstring gave three examples and nothing enforced them, so a typo
   (`"EXTRACTION_COMPLETED "`, `"extraction_completed"`, `"EXTRACTION-DONE"`)
   reached the topic and broke the consumer's dispatch. The value is now
   upper-cased and matched against `EVENT_TYPES`; extend it in one place (or via
   `KAFKA_EVENT_TYPES`) rather than by sending something new and hoping. `status`
   defaulted to `"PROCESSED"` — the same "success by default" shape that was
   removed from models.py — so a caller that forgot to pass it published a
   successful-looking event. The default is now `"UNKNOWN"`.

6. It never published anything, and the log said it did.

   That is the reason to be careful here rather than clever: `logger.info(
   "Published to Kafka topic [...]")` and `print("--> [KAFKA STREAM] Dispatched
   ...")` are read by a human as "the event went out", and the method returned
   True unconditionally whether it did or not. An audit trail that cannot
   distinguish "delivered" from "simulated" is not an audit trail.

   So the transport is now explicit:
     * `KAFKA_MODE=simulated` (default) — nothing leaves the process. The log
       line says `[KAFKA STREAM:SIMULATED] NOT DELIVERED`, and the call returns
       **False**: True means the event left this process.
     * `KAFKA_MODE=kafka` — a real publish through `aiokafka` (imported lazily;
       missing package raises a RuntimeError naming the install command).
   `describe()` and `python -m app.services.kafka_producer` both state the mode,
   so "are the events actually going out?" is answerable without reading code.

   To make simulated mode useful rather than decorative, set
   `KAFKA_OUTBOX_PATH`: every not-delivered event is appended there as one JSON
   line, so the events can be replayed once a broker exists. No broker, no
   dependency, no database migration.

7. A transport failure returns False and logs at ERROR — it does not raise.

   The caller is a background task that has already committed the filing; a
   broker hiccup must not turn a good filing into a failed one. (Same reasoning
   as the lineage call in ingestion.py.) In simulated mode there is nothing to
   fail, so `False` there means "not delivered", not "broken" — the log line
   distinguishes the two.

WHAT IS DELIBERATELY LEFT ALONE
-------------------------------
* Nothing calls this yet. In every backend file in this project, no module
  imports `kafka_producer`. So nothing changes at runtime until you wire it in;
  the wiring is one `await` in ingestion.py / economist.py when you want it.
* The topic name stays `bot.c17.submissions`, and `"topic"` stays in the message
  body (a consumer already knows its topic; the field is kept so nothing that
  reads it breaks).
* No retries and no batching. One event is one call. If the volume ever justifies
  a module-level producer with a background flush, that is a different design and
  it should be a deliberate one.

Run this file directly for a report and a sample event (nothing is published):

    python -m app.services.kafka_producer
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

logger = logging.getLogger("KafkaStreams")

# ---------------------------------------------------------------------------
# Settings. Defaults are the values this file already used.
# ---------------------------------------------------------------------------
DEFAULT_TOPIC = "bot.c17.submissions"
DEFAULT_BOOTSTRAP = "localhost:9092"

# The event types the consumer knows how to dispatch. Extend this tuple (or set
# KAFKA_EVENT_TYPES="A,B,C") instead of publishing something new and hoping.
EVENT_TYPES: tuple[str, ...] = tuple(
    part.strip().upper()
    for part in (
        (os.getenv("KAFKA_EVENT_TYPES") or "").strip()
        or "DOCUMENT_RECEIVED,EXTRACTION_COMPLETED,AUDIT_UPDATED"
    ).split(",")
    if part.strip()
)

SCHEMA_VERSION = 1
VALID_MODES = ("simulated", "kafka")

KAFKA_MODE = ((os.getenv("KAFKA_MODE") or "").strip().lower()) or "simulated"
KAFKA_TOPIC = (os.getenv("KAFKA_TOPIC") or "").strip() or DEFAULT_TOPIC
KAFKA_BOOTSTRAP_SERVERS = (os.getenv("KAFKA_BOOTSTRAP_SERVERS") or "").strip() or DEFAULT_BOOTSTRAP
KAFKA_OUTBOX_PATH = (os.getenv("KAFKA_OUTBOX_PATH") or "").strip()

if KAFKA_MODE not in VALID_MODES:
    print(
        f"[Kafka] KAFKA_MODE={KAFKA_MODE!r} is not one of {', '.join(VALID_MODES)} — "
        f"falling back to 'simulated'. Nothing will be published.",
        file=sys.stderr,
    )
    KAFKA_MODE = "simulated"

if KAFKA_MODE == "simulated":
    print(
        f"[Kafka] MODE=simulated (topic {KAFKA_TOPIC!r}). Events are NOT published —\n"
        f"[Kafka] they are logged as [KAFKA STREAM:SIMULATED] and the call returns False.\n"
        f"[Kafka] Set KAFKA_MODE=kafka with KAFKA_BOOTSTRAP_SERVERS to deliver them."
        + ("" if DOTENV_PATH else "  (no .env file was found)"),
        file=sys.stderr,
    )
if not DOTENV_PATH:
    print(
        "[Kafka] No .env file was found above this module; using built-in defaults.\n"
        f"[Kafka] topic={KAFKA_TOPIC!r} bootstrap={KAFKA_BOOTSTRAP_SERVERS!r}",
        file=sys.stderr,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _iso_utc(moment: datetime | None = None) -> str:
    """ISO-8601 UTC with the Z suffix, so a consumer cannot read it as local time."""
    if moment is None:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_safe(value: Any, path: str = "message") -> Any:
    """JSON-serializable copy of `value`, or TypeError naming the offending path.

    The message is serialized before it is sent or logged, so a `Decimal` or a
    `datetime` left in the payload would fail at the worst moment. Coerce, and
    name the exact key when something genuinely cannot be serialized.
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


def _as_dict(value: Any) -> dict:
    """A dict, or {} — never a crash on `None` or a string where a dict was expected."""
    return value if isinstance(value, dict) else {}


def _nested(payload: dict, *path: str) -> Any:
    """`payload["part_a"]["details"]["tin_number"]`, or None at any missing step."""
    node: Any = payload
    for key in path:
        node = _as_dict(node).get(key)
        if node is None:
            return None
    return node


def questionnaire_of(payload: Any) -> dict:
    """The questionnaire out of whatever shape the caller passed.

    Two shapes are in use in this project and both are accepted:
      * the flat BOTQuestionnaireC17 dump that `extracted_payload` holds
        (`{"metadata": …, "part_a": …, "part_c_liabilities": [...]}`);
      * the envelope `validation_engine.evaluate_questionnaire` returns
        (`{"is_valid": …, "status": …, "questionnaire": {...}, ...}`).
    """
    data = _as_dict(payload)
    inner = data.get("questionnaire")
    return _as_dict(inner) if inner is not None else data


def identity_of(questionnaire: dict) -> tuple[dict, list[str]]:
    """(identity fields, warnings) — read from the schema's real paths.

    A missing value is reported as missing. It is never replaced with a
    placeholder: a fabricated TIN is indistinguishable from a real one once it
    is on the topic.
    """
    details = _as_dict(questionnaire.get("part_a")).get("details")
    details = _as_dict(details)
    metadata = _as_dict(questionnaire.get("metadata"))

    company_name = details.get("company_name") or None
    tin_number = details.get("tin_number") or None
    reporting_year = metadata.get("reporting_year")

    # ROOT of BOTQuestionnaireC17 — not metadata. See the module docstring.
    extraction_status = questionnaire.get("extraction_status") or None
    validation_warnings = questionnaire.get("validation_warnings") or []
    not_applicable = questionnaire.get("not_applicable_fields") or []

    warnings: list[str] = []
    if not company_name:
        warnings.append("company_name is empty in part_a.details — published as null")
    if not tin_number:
        warnings.append("tin_number is empty in part_a.details — published as null")
    if reporting_year is None:
        warnings.append("reporting_year is missing from metadata — published as null")
    if extraction_status is None:
        warnings.append(
            "extraction_status is missing from the payload root — published as null, "
            "so this event cannot say whether the filing was read cleanly"
        )
    elif extraction_status != "ok":
        warnings.append(f"extraction_status is {extraction_status!r}, not 'ok'")

    return (
        {
            "company_name": company_name,
            "tin_number": tin_number,
            "reporting_year": reporting_year,
            "questionnaire_type": metadata.get("questionnaire_type"),
            "extraction_status": extraction_status,
            "extraction_warning_count": len(metadata.get("extraction_warnings") or []),
            "validation_warning_count": len(validation_warnings),
            "not_applicable_count": len(not_applicable),
        },
        warnings,
    )


class StreamProducer:
    """Publishes statutory filing events to a topic — or says that it did not.

    `mode` comes from KAFKA_MODE: "simulated" (default) or "kafka".
    """

    def __init__(self, topic: str = DEFAULT_TOPIC, mode: str | None = None,
                 bootstrap_servers: str | None = None, outbox_path: str | None = None):
        self.topic = topic
        self.mode = (mode or KAFKA_MODE).strip().lower()
        self.bootstrap_servers = bootstrap_servers or KAFKA_BOOTSTRAP_SERVERS
        self.outbox_path = outbox_path if outbox_path is not None else KAFKA_OUTBOX_PATH

    # -- transport ---------------------------------------------------------
    async def _deliver(self, message: dict) -> None:
        """Hand the message to a broker. Raises if that is not possible."""
        if self.mode != "kafka":
            return
        try:
            from aiokafka import AIOKafkaProducer
        except ImportError as exc:                # pragma: no cover - depends on the install
            raise RuntimeError(
                "KAFKA_MODE=kafka needs an async Kafka client that is not installed. "
                "Install it with `pip install aiokafka`, or set KAFKA_MODE=simulated."
            ) from exc

        body = json.dumps(message, ensure_ascii=False).encode("utf-8")
        producer = AIOKafkaProducer(bootstrap_servers=self.bootstrap_servers)
        await producer.start()
        try:
            await producer.send_and_wait(self.topic, body)
        finally:
            await producer.stop()

    def _remember(self, message: dict) -> None:
        """Append a not-delivered event to the outbox, when one is configured.

        Simulated mode is the default, so without this the event exists only in
        the log. One JSON object per line, appended, so it can be replayed.
        """
        if not self.outbox_path:
            return
        try:
            with open(self.outbox_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(message, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.error("[Kafka] outbox %s could not be written: %s", self.outbox_path, exc)

    # -- the call the backend makes ---------------------------------------
    async def publish_ingestion_event(
        self,
        filing_id: str,
        event_type: str,
        payload: dict,
        status: str = "UNKNOWN",
    ) -> bool:
        """Publish one filing event. Returns True only if the event left this process.

        Raises ValueError for an event_type the consumers do not know (that is a
        programming error, not a transport problem) and for a missing filing_id.
        A transport failure is logged at ERROR and returns False — it never
        raises, because the caller has already committed the filing.
        """
        if not (filing_id or "").strip():
            raise ValueError("filing_id is required — it is the correlation id of the event")

        kind = (event_type or "").strip().upper()
        if kind not in EVENT_TYPES:
            raise ValueError(
                f"event_type {event_type!r} is not one of {', '.join(EVENT_TYPES)}. "
                "Add it to EVENT_TYPES (or KAFKA_EVENT_TYPES) if the consumers know it."
            )

        # One clock read, used for both the id and the timestamp.
        now = datetime.now(timezone.utc)
        questionnaire = questionnaire_of(payload)
        identity, warnings = identity_of(questionnaire)

        event_message = {
            "event_id": str(uuid.uuid4()),
            "correlation_id": filing_id,
            "schema_version": SCHEMA_VERSION,
            "event_type": kind,
            "topic": self.topic,
            "timestamp": _iso_utc(now),
            "data": {
                "filing_id": filing_id,
                "status": status,
                **identity,
                "warnings": warnings,
                "payload": payload,
            },
        }
        try:
            event_message = _json_safe(event_message, "event_message")
        except TypeError as exc:
            logger.error("[Kafka] %s event for %s was not published: %s", kind, filing_id, exc)
            return False

        delivered = False
        try:
            await self._deliver(event_message)
            delivered = self.mode == "kafka"
        except Exception as exc:                       # noqa: BLE001 - never break the caller
            logger.error(
                "[Kafka] %s event for %s was NOT delivered to %s at %s: %s: %s",
                kind, filing_id, self.topic, self.bootstrap_servers, type(exc).__name__, exc,
            )
            self._remember(event_message)
            return False

        if not delivered:
            self._remember(event_message)

        # Never the payload: it holds the company name, TIN and every figure.
        logger.info(
            "[KAFKA STREAM%s] %s filing_id=%s status=%s name=%s year=%s warnings=%d event_id=%s",
            "" if delivered else ":SIMULATED",
            kind,
            filing_id,
            status,
            "yes" if identity["company_name"] else "MISSING",
            identity["reporting_year"] if identity["reporting_year"] is not None else "MISSING",
            len(warnings),
            event_message["event_id"],
        )
        print(
            f"--> [KAFKA STREAM{'' if delivered else ':SIMULATED'}] "
            f"{'Dispatched' if delivered else 'NOT DELIVERED'} {kind} event "
            f"for filing_id: {filing_id}"
        )
        return delivered

    # -- diagnostics -------------------------------------------------------
    def describe(self) -> str:
        """What this service will actually do, in one screen."""
        return "\n".join(
            [
                "app/services/kafka_producer.py",
                f"  mode      : {self.mode}"
                + ("  — events are NOT published" if self.mode != "kafka" else "  — events are published"),
                f"  topic     : {self.topic}",
                f"  bootstrap : {self.bootstrap_servers}"
                + ("" if self.mode == "kafka" else "  (unused in simulated mode)"),
                f"  outbox    : {self.outbox_path or '(none — simulated events are logged only)'}",
                f"  eventTypes: {', '.join(EVENT_TYPES)}",
                f"  schema    : v{SCHEMA_VERSION}",
                f"  dotenv    : {DOTENV_PATH or 'no .env file found in the backend root'}",
            ]
        )


stream_producer = StreamProducer()


if __name__ == "__main__":                                   # pragma: no cover
    import asyncio

    print(stream_producer.describe())
    sample = {
        "metadata": {"questionnaire_type": "PCF/C17/2024", "reporting_year": 2024,
                     "extraction_status": "ok", "extraction_warnings": []},
        "part_a": {"details": {"company_name": "Example Holdings Ltd",
                               "tin_number": "118-492-705"}},
    }
    delivered = asyncio.run(
        stream_producer.publish_ingestion_event(
            filing_id="SUB-A1758450000",
            event_type="EXTRACTION_COMPLETED",
            payload=sample,
            status="PROCESSED",
        )
    )
    print(f"\n  returned {delivered} — True would mean the event left this process.")
