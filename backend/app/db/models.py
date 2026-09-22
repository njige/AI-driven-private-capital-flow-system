"""
app/db/models.py  —  REVISED

SQLAlchemy models for the BoT PCF/C17 pipeline.

This file is mostly correct, and it constrains a lot of code elsewhere, so the
changes are deliberately small and NONE OF THEM REQUIRE A MIGRATION.

WHAT CHANGED
------------
1. `status` no longer defaults to "PROCESSED_BY_AI". A row inserted without an
   explicit status claimed a clean AI pass — the same "success by default" shape
   that made a failed extraction look like a good filing. It now defaults to
   "PROCESSING", which is what a new row actually is. Nothing sets this
   implicitly today (ingestion.py and economist.py both assign it explicitly), so
   this is inert until someone forgets.

2. `datetime.utcnow` is deprecated (Python 3.12+; it raises DeprecationWarning).
   Replaced with a helper that is STILL NAIVE UTC, on purpose. The columns are
   `DateTime` without `timezone=True`, which maps to TIMESTAMP WITHOUT TIME ZONE,
   and asyncpg rejects a tz-AWARE value for that type with a DataError. So the
   obvious replacement — `datetime.now(timezone.utc)` — would be a bug here. The
   helper strips the tzinfo back off.

WHAT WAS DELIBERATELY LEFT ALONE
--------------------------------
* `extracted_payload` stays `JSON`. `JSONB` would be faster to query and can be
  GIN-indexed, but switching means an ALTER TABLE and a model/column mismatch if
  the migration is skipped. At this table's size there is nothing to gain.
  One consequence worth knowing: the `json` type has NO equality operator, so
  `extracted_payload = '{}'::jsonb` raises `operator does not exist`. Use
  `extracted_payload::jsonb = '{}'::jsonb`. The `->>` operator needs no cast.
* `company_name` stays NOT NULL. It is the only NOT NULL column besides the
  primary key, and not always what you want — but the pipeline already satisfies
  it: ingestion.py writes "" when no name was read and never writes None.
* No `confidence_score` column is added. There is none, and that is correct: no
  score is computed anywhere. The review screen's VLM Score column therefore
  shows "not scored" rather than a number nobody measured.

SEE ALSO
--------
`OPTIONAL_MIGRATIONS.sql` next to this file, for `JSONB` and for
`approved_by` / `approved_at` columns. Those DO require a migration, which is why
they are not in this file — declaring a column that does not exist yet makes
every INSERT fail with "column ... does not exist".
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, Integer, JSON, String, Text

from app.db.database import Base


def utcnow_naive() -> datetime:
    """Naive UTC timestamp.

    Naive on purpose: these columns are TIMESTAMP WITHOUT TIME ZONE, and asyncpg
    raises DataError when handed a tz-aware datetime for one. Returning
    `datetime.now(timezone.utc)` directly would move that failure to every insert.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


class FilingRecord(Base):
    __tablename__ = "filings"

    filing_id = Column(String, primary_key=True, index=True)
    # The only NOT NULL column other than the primary key. ingestion.py resolves
    # its upload placeholder to "" when no name was read, so this is never violated.
    company_name = Column(String, nullable=False, index=True)
    tin_number = Column(String, index=True, nullable=True)

    # Metadata & Categorization
    reporting_year = Column(Integer, nullable=True, index=True)
    previous_year = Column(Integer, nullable=True)
    bpm6_category = Column(String, nullable=True)

    # Financial Overview Metrics
    total_assets_usd = Column(Float, nullable=True)
    total_assets_tzs = Column(Float, nullable=True)
    net_worth_usd = Column(Float, nullable=True)
    currency = Column(String, default="USD")

    # Full BoT C17 Structured Payload (Pydantic validated dict).
    # Declared JSON, not JSONB — see the module docstring before changing it.
    extracted_payload = Column(JSON, nullable=True)

    # Audit & Storage Tracking
    # Was default="PROCESSED_BY_AI": a new row that omitted this claimed a clean
    # pass. Bound on the Python side only, so no migration is involved.
    status = Column(String, default="PROCESSING", index=True)
    audit_notes = Column(Text, nullable=True)
    s3_object_key = Column(String, nullable=True)  # Reference to MinIO S3 bucket object

    # Timestamps (naive UTC — see utcnow_naive above)
    created_at = Column(DateTime, default=utcnow_naive)
    validated_at = Column(DateTime, nullable=True)


class OpenLineageAuditLog(Base):
    __tablename__ = "lineage_audit_events"

    # No default: the caller supplies this (lineage_service generates it).
    event_id = Column(String, primary_key=True)
    event_type = Column(String, nullable=False)  # e.g., START, COMPLETE, FAIL
    job_name = Column(String, nullable=False)
    producer = Column(String, nullable=False)
    payload = Column(JSON)
    created_at = Column(DateTime, default=utcnow_naive)