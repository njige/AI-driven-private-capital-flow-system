"""
app/routers/ingestion.py  —  REVISED

Upload → extract → validate → persist for the PCF/C17 questionnaire.

WHAT WAS WRONG IN THE PREVIOUS VERSION
--------------------------------------
1. A failed extraction was persisted as a normal filing. Nothing here read
   `extraction_status`, so when the extractor returned its fallback payload the
   pipeline went on to write status "PROCESSED_BY_AI" and index totals of 0.00.
   The record looked like a successful extraction in every column.

2. `tin_number = ... or "000000000"` fabricated a Taxpayer Identification Number.
   This is where the "0000000000" in the review screen came from. An invented
   identifier on a central-bank return is worse than a blank one.

3. `bpm6_category = "Foreign Direct Investment (FDI)"` was hardcoded. Every filing
   was classified as FDI regardless of its contents, and that is the value shown
   in the submissions list. BPM6 is a review decision, not a default.

4. A lineage-logging failure destroyed a successful extraction. `log_data_lineage`
   was called inside the same try block, and the except handler re-fetched the
   record and set status "FLAGGED" with "SYSTEM ERROR during AI Extraction". The
   filing update had already been committed, so a hiccup in lineage flipped a
   good filing to FLAGGED and blamed the extractor for it.

5. `storage_dir = os.path.abspath("./storage/raw_documents")` resolves against the
   process working directory, so starting uvicorn from a different folder makes
   every stored document 404.

6. `db_record.extracted_payload = parsed_form.model_dump()` serialises enum
   members rather than their values. It happens to survive because the enums
   subclass `str`, but it depends on that detail rather than stating it. Switched
   to `mode="json"` so the intent is explicit and refactor-safe.

NOTE ON THE DOCUMENT ROUTE
--------------------------
This route works — it saves `{filing_id}_document.pdf` at upload and looks up the
same name, which is why the viewer now displays the PDF. The original 404 was
entirely the frontend sending the literal string "undefined".

Two things to decide about it, deliberately left as they are:
  * it has no auth dependency, so anyone who can reach the API and knows a
    filing_id can download the raw submission. Adding `Depends(get_current_user)`
    is the obvious fix, but an <iframe> cannot send an Authorization header — only
    the blob-fetch path in AuditReview.tsx can. So adding auth means the direct
    embed fallback stops working. Decide with that trade-off in view.
  * do not add `filename=` to FileResponse. Starlette then sends
    `content-disposition: attachment`, the browser downloads instead of
    displaying, and the in-app viewer goes blank.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import TokenData, get_current_user
from app.db.database import get_db
from app.db.models import FilingRecord
from app.schemas.bot_questionnaire import BOTQuestionnaireC17
from app.services.ai_extractor import extract_and_classify_pcf_doc
from app.services.lineage_service import lineage_service
from app.services.minio_service import minio_service
from app.services.validation_engine import ValidationAndCleansingEngine

router = APIRouter(prefix="/api/v1/ingest", tags=["1. Ingestion"])

# Resolved from THIS FILE's location rather than from the working directory, and
# anchored to the same directory main.py mounts at /files.
#
# This file is app/routers/ingestion.py, so two dirnames up is the `app`
# package. main.py sits at app/services/main.py and reaches the same place with
# two dirnames, which is why the storage folder lives at app/storage/.
# Previously this was the relative "./storage/raw_documents": wherever uvicorn
# was started from determined which folder was written, while /files always
# served app/storage/raw_documents. Any working directory other than the app
# package silently split the two.
_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORAGE_DIR = os.path.abspath(
    os.environ.get("RAW_DOCUMENTS_DIR")
    or os.path.join(_APP_ROOT, "storage", "raw_documents")
)

# Statuses. "PROCESSING" is set at upload; the pipeline replaces it. A failed
# extraction is recorded as FLAGGED because it needs a human, and FLAGGED is
# already understood by the submissions filter. The precise reason lives in
# audit_notes and in extracted_payload.metadata.extraction_status.
STATUS_PROCESSING = "PROCESSING"
STATUS_FLAGGED = "FLAGGED"


def safe_int_conversion(val: Any) -> Optional[int]:
    """Safely cast string/numeric values to integer, or None for DB insertion."""
    if val is None:
        return None
    try:
        s_val = str(val).strip()
        if not s_val or s_val.lower() in ("none", "null", "nan"):
            return None
        return int(float(s_val))
    except (ValueError, TypeError):
        return None


def _unwrap_extraction(payload: Any) -> Any:
    """Accept a bare payload, a {"questionnaire": {...}} wrapper, or a model.

    `app/models/pcf.py` defines PCFAuditExtraction, which nests the whole
    questionnaire under a `questionnaire` key. The rules engine already unwraps
    that shape; the same rule is applied here BEFORE the status is read, so a
    wrapped payload cannot be mistaken for "no payload at all" — which would mark
    every filing FLAGGED, including perfect extractions.
    """
    wrapped = getattr(payload, "questionnaire", None)
    if isinstance(wrapped, dict):
        return wrapped
    if isinstance(payload, dict) and isinstance(payload.get("questionnaire"), dict):
        return payload["questionnaire"]
    return payload


def _read_extraction_status(payload: Any) -> tuple[str, list[str]]:
    """Pull (status, warnings) out of an extraction payload.

    Checks the root and metadata, because the schema declares the root while an
    older model may nest it; the review UI reads the root. Defaults to "partial"
    rather than "ok" — an unknown status must never read as a clean extraction.
    """
    payload = _unwrap_extraction(payload)
    if not isinstance(payload, dict):
        return "failed", ["Extractor returned no payload."]

    meta = payload.get("metadata") or {}
    status = (
        meta.get("extraction_status")
        or payload.get("extraction_status")
        or "partial"
    )
    warnings = list(meta.get("extraction_warnings") or [])
    warnings += list(payload.get("validation_warnings") or [])
    return str(status), warnings


def _storable_payload(value: Any) -> Any:
    """Make a value safe for the JSON column, or fall back to the failure marker.

    Used only for the failed path. A successful extraction is stored as the parsed
    schema model; see the note in process_extraction_background for why a failure
    must NOT be.
    """
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return {
            "metadata": {
                "extraction_warnings": [
                    "the extractor returned a payload that could not be stored as JSON"
                ]
            },
            "extraction_status": "failed",
        }


async def process_extraction_background(filing_id: str, temp_file_path: str, s3_key: str):
    """Background handler: VLM extraction, rules engine, persist."""
    from app.db.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        try:
            # 1. VLM extraction. Returns a JSON-safe dict that always carries
            #    extraction_status — it does NOT raise on failure, so the status
            #    must be read rather than inferred from the absence of an error.
            raw_ai_data = extract_and_classify_pcf_doc(temp_file_path)
            extraction_status, extraction_warnings = _read_extraction_status(raw_ai_data)
            extraction_failed = extraction_status == "failed"

            # 2. Rules engine + schema validation.
            validated_result = ValidationAndCleansingEngine.evaluate_questionnaire(raw_ai_data)
            parsed_form = BOTQuestionnaireC17.model_validate(validated_result["questionnaire"])

            # 3. Locate the record before deriving values from it.
            result = await db.execute(
                select(FilingRecord).where(FilingRecord.filing_id == filing_id)
            )
            db_record = result.scalars().first()
            if db_record is None:
                print(f"[Background Task] Filing {filing_id} not found; aborting.")
                return

            # 4. Derive indexed metrics.
            company_details = parsed_form.part_a.details
            is_count_field: dict[str, Any] = {}  # placeholder for future expansion

            if extraction_failed:
                # Do not derive anything. Writing 0.00 totals for a document that
                # was never read is how a failed filing came to look like a
                # legitimate nil return.
                company_name = None
                rep_year = None
                prev_year = None
                assets_usd = assets_tzs = None
                net_worth_usd = None
                # NOT parsed_form.part_b.currency_used: the schema defaults that
                # field to CurrencyType.TZS, so reading it here wrote "TZS" onto a
                # filing nobody had read. Observed in a live run — the failed
                # record came back with currency='TZS'. None leaves the column as
                # the upload set it, and the `if currency:` guard below skips the
                # write entirely.
                currency = None
                new_status = STATUS_FLAGGED
            else:
                company_name = company_details.company_name or None
                rep_year = safe_int_conversion(parsed_form.metadata.reporting_year)
                prev_year = safe_int_conversion(parsed_form.metadata.previous_year)

                assets_rep = parsed_form.part_d_fats.total_assets_5.reporting_year_value
                net_worth_rep = parsed_form.part_d_fats.net_worth_7.reporting_year_value
                currency = parsed_form.part_b.currency_used.value

                assets_usd, assets_tzs = ValidationAndCleansingEngine.convert_currency(
                    assets_rep, currency
                )
                net_worth_usd, _ = ValidationAndCleansingEngine.convert_currency(
                    net_worth_rep, currency
                )
                new_status = validated_result["status"]

            # 5. TIN: the payload's value if the model read one, otherwise whatever
            #    the submitting user authenticated with. Never a fabricated value.
            payload_tin = getattr(company_details, "tin_number", None) or None

            # 6. Persist.
            #    company_name is NOT NULL in the filings table, and the record was
            #    created with "Processing (<filename>)..." as a placeholder. This
            #    function is what replaces it, so it has to resolve in BOTH
            #    directions: the extracted name when one was read, and a blank
            #    otherwise. Leaving the placeholder would advertise a job that has
            #    already finished. The submissions list renders 'N/A' for a blank.
            db_record.company_name = company_name or ""
            db_record.tin_number = payload_tin or db_record.tin_number or None
            db_record.reporting_year = rep_year
            db_record.previous_year = prev_year

            # BPM6 is assigned by an economist during review (the review screen has
            # an input for it). Leaving it None means the list shows "Unclassified"
            # instead of asserting a classification nobody made.
            db_record.bpm6_category = None

            db_record.total_assets_usd = assets_usd
            db_record.total_assets_tzs = assets_tzs
            db_record.net_worth_usd = net_worth_usd
            if currency:
                db_record.currency = currency

            # mode="json" states the intent: enum members become their wire values
            # rather than relying on str-subclassing to survive json.dumps.
            #
            # A FAILED extraction must not go through the schema on the way to the
            # column. BOTQuestionnaireC17 gives every field a default, so
            # `model_validate({})` succeeds and `model_dump()` then materialises
            # those defaults as if they had been read from the document:
            #
            #     questionnaire_type : "PCF/C17/2025"
            #     previous_year      : 2023
            #     reporting_year     : 2024
            #
            # Observed in a live run: a filing whose extraction returned 503 was
            # stored with exactly those values and 4 KB of zeroed fields, while
            # its `reporting_year` COLUMN stayed NULL. The column and the payload
            # then disagreed, and anything reading the payload — the review screen,
            # `pcf_analytics_view.sql` — would show a survey period for a document
            # that was never read.
            #
            # What is known about a failure is the failure, so that is what is
            # stored: the extractor's own dict, which carries extraction_status
            # and the real error in metadata.extraction_warnings — the field the
            # review screen's data-quality panel reads.
            if extraction_failed:
                db_record.extracted_payload = _storable_payload(raw_ai_data)
            else:
                db_record.extracted_payload = parsed_form.model_dump(mode="json")
            db_record.status = new_status

            if extraction_failed:
                detail = "; ".join(extraction_warnings) or "no detail reported by the extractor"
                db_record.audit_notes = (
                    f"EXT rejected: automated extraction did not complete. {detail}"
                )
            else:
                # The rules engine's own note. Do not let it claim a clean pass
                # over a filing that was only partially read.
                note = validated_result["audit_notes"]
                if extraction_status != "ok" and note:
                    note = f"{note} [extraction_status={extraction_status}]"
                db_record.audit_notes = note

            # Naive UTC: asyncpg rejects tz-aware values for TIMESTAMP WITHOUT TIME ZONE.
            db_record.validated_at = datetime.now(timezone.utc).replace(tzinfo=None)
            await db.commit()

            # 7. Lineage — in its own try/except on purpose. Previously a lineage
            #    failure fell into the outer handler, which re-fetched the record
            #    and flipped a successfully processed filing to FLAGGED with
            #    "SYSTEM ERROR during AI Extraction".
            try:
                await lineage_service.log_data_lineage(
                    db=db,
                    job_name="ingest_and_validate_bot_questionnaire",
                    event_type="COMPLETE",
                    inputs=[{"namespace": "minio", "name": s3_key}],
                    outputs=[{"namespace": "postgresql.public", "name": "filings"}],
                    facets={
                        "status": new_status,
                        "extraction_status": extraction_status,
                        "extraction_warnings": len(extraction_warnings),
                        "reporting_year": rep_year,
                    },
                )
            except Exception as lineage_err:
                print(f"[Lineage Warning] filing {filing_id}: {lineage_err}")

        except Exception as err:
            await db.rollback()
            print(f"[Background Task Error] filing {filing_id}: {type(err).__name__}: {err}")

            try:
                result = await db.execute(
                    select(FilingRecord).where(FilingRecord.filing_id == filing_id)
                )
                db_record = result.scalars().first()
                if db_record:
                    db_record.status = STATUS_FLAGGED
                    db_record.audit_notes = (
                        f"SYSTEM ERROR during AI extraction: {type(err).__name__}: {err}"
                    )
                    await db.commit()
            except Exception as inner:
                print(f"[Background Task] could not mark {filing_id} as FLAGGED: {inner}")

        finally:
            if os.path.exists(temp_file_path):
                try:
                    os.remove(temp_file_path)
                except OSError as exc:
                    print(f"[Background Task] temp file cleanup failed: {exc}")


@router.get("/document-file/{filing_id}")
async def get_document_file(filing_id: str):
    """Serve the raw submitted PDF for the economist workbench.

    Deliberately left without an auth dependency: the review screen embeds this
    in an <iframe>, and an iframe cannot carry an Authorization header. Only the
    blob-fetch path can. See the module docstring before changing it.
    """
    file_path = os.path.join(STORAGE_DIR, f"{filing_id}_document.pdf")

    if os.path.exists(file_path):
        # Do NOT pass filename= here — Starlette would then send
        # `content-disposition: attachment` and the browser would download the
        # file instead of rendering it inside the viewer.
        return FileResponse(file_path, media_type="application/pdf")

    # Tolerate a filename that differs slightly from the convention.
    if os.path.exists(STORAGE_DIR):
        for fname in os.listdir(STORAGE_DIR):
            if filing_id in fname and fname.endswith(".pdf"):
                return FileResponse(
                    os.path.join(STORAGE_DIR, fname), media_type="application/pdf"
                )

    raise HTTPException(
        status_code=404,
        detail=f"PDF document for filing {filing_id} not found on server.",
    )


@router.post("/upload")
async def process_statutory_upload(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    try:
        file_bytes = await file.read()
        now_utc = datetime.now(timezone.utc)
        timestamp = int(now_utc.timestamp())
        filing_id = f"SUB-A{timestamp}"
        filename = f"{timestamp}_{file.filename}"

        try:
            s3_key = minio_service.upload_file(filename, file_bytes, file.content_type)
        except Exception as minio_err:
            print(f"[Ingest] MinIO upload failed, using local fallback: {minio_err}")
            s3_key = f"local_fallback/{filename}"

        os.makedirs(STORAGE_DIR, exist_ok=True)
        local_static_path = os.path.join(STORAGE_DIR, f"{filing_id}_document.pdf")
        with open(local_static_path, "wb") as f_out:
            f_out.write(file_bytes)

        file_ext = os.path.splitext(file.filename or "")[1] or ".pdf"
        tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=file_ext)
        try:
            tmp_file.write(file_bytes)
        finally:
            tmp_file.close()

        user_tin = getattr(current_user, "tin_number", None) or None

        db_record = FilingRecord(
            filing_id=filing_id,
            # Placeholder, replaced by the real name only if extraction reads one.
            company_name=f"Processing ({file.filename})...",
            tin_number=user_tin,
            reporting_year=None,
            previous_year=None,
            # Kept as "USD" because the column's nullability is unknown here and
            # the original code wrote this value. It is a placeholder: a
            # successful extraction overwrites it with part_b.currency_used. A
            # failed extraction leaves it, so read this field together with
            # extraction_status rather than on its own.
            currency="USD",
            status=STATUS_PROCESSING,
            audit_notes=(
                f"Uploaded by TIN {user_tin or 'unknown'}. BoT C17 extraction queued."
            ),
            s3_object_key=s3_key,
        )
        db.add(db_record)
        await db.commit()

        background_tasks.add_task(
            process_extraction_background,
            filing_id=filing_id,
            temp_file_path=tmp_file.name,
            s3_key=s3_key,
        )

        return {
            "status": "success",
            "message": "BoT C17 questionnaire submitted and queued for extraction.",
            "data": {
                "filing_id": filing_id,
                "status": STATUS_PROCESSING,
                "submitted_by_tin": user_tin,
            },
        }

    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Upload initialization failed: {e}")
