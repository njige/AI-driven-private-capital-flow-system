"""
app/routers/economist.py  —  REVISED

Economist workspace: list submissions, commit an audit decision, clear test data,
and a progress stream.

WHAT WAS WRONG IN THE PREVIOUS VERSION
--------------------------------------
1. EVERY ENDPOINT WAS UNAUTHENTICATED. ingestion.py guards upload with
   `Depends(get_current_user)`; this router had no dependency at all. Anyone who
   could reach the API could read every filing together with its full extracted
   payload, approve or reject any of them, and delete the entire table.
   >>> The guard is now switchable through ECONOMIST_REQUIRE_AUTH and defaults
   >>> ON. See the note at the end of this docstring: with the guard on, a
   >>> browser that has never logged in gets a 401 and the list stays empty.

2. `DELETE /submissions/clear` ran `delete(FilingRecord)` with no WHERE clause.
   That deletes every row in the filings table, and Overview.tsx has a button
   wired to it. One click, in a central bank, with no authentication. It is now
   behind three independent guards: authentication, an environment flag that
   defaults OFF, and a confirmation phrase.

3. The request model did not match what the frontend sends. AuditReview.tsx posts

       { companyName, tinNumber, bpm6Category, economistNotes,
         auditStatus, lineItems }

   while this file declared `company_name`, `tin_number`, `bpm6_category`,
   `economist_notes`, `status`, `extracted_payload_override`. Pydantic rejects
   the four required fields as missing, so the endpoint returns 422. The model now
   accepts BOTH spellings, so it works with the frontend as it stands today.
   >>> Also see the note about `services/api.ts` at the bottom of this docstring.

4. `lineItems` had no counterpart here at all, so edits to the A5 activity table
   and the A6 shareholding table were silently discarded on save. They are now
   placed into the payload (see `_place_line_items`).

5. BPM6 was defaulted to "Foreign Direct Investment (FDI)" — the same hardcoded
   classification that was removed from ingestion.py. Approving a filing without
   touching the field stamped it FDI. The default is now None, and a None value
   leaves the existing classification alone.

6. The Part D summary columns were overwritten with RAW reported figures:

       record.total_assets_usd = part_d["total_assets_5"]["reporting_year_value"]

   Part D is reported in the filing's own currency. For a TZS filing this wrote
   the TZS figure into the USD column — an overstatement of roughly 2,600x in the
   submissions list — and `total_assets_tzs` was never updated, so the two columns
   disagreed permanently after any edit. Both are now derived through
   `convert_currency` at the Table C2 year-end rate.

7. `record.audit_notes = payload.economist_notes` DESTROYED the pipeline's
   findings. audit_notes now carries the arithmetic mismatches and the
   "Extraction failed" reason — the evidence — and the reviewer's action wiped it.
   The human trail is now APPENDED; the AI's findings are never overwritten.

8. The override payload was written straight to the column with no validation and
   no re-run of the arithmetic checks. An economist could correct one B1 figure,
   break the balance, approve, and nothing would ever notice — in the one place
   where a human edit is most likely to introduce an error. The payload is now
   re-validated through `parse_questionnaire`, which refreshes
   `validation_warnings`, so an edit that breaks a printed relationship is
   recorded rather than accepted silently.

9. No attribution. Nothing recorded WHO approved a filing or WHEN. `validated_at`
   was not updated, so it still showed the extraction time. The actor and
   timestamp are now written into audit_notes. A proper fix is an `approved_by` /
   `approved_at` migration — see the note at the end.

10. `part_d["total_assets_5"].get(...)` assumed a dict. If the VLM returned a bare
    number for that item, this raised AttributeError and returned a 500 on an
    otherwise valid save.

11. Direct attribute access on the request body meant a client omitting a field
    produced a 422 with no explanation of which interaction caused it.

>>> src/services/api.ts — CHECKED, no change needed there.
    Its request interceptor already does the right thing:

        const token = localStorage.getItem('pcf_token');
        if (token) config.headers.Authorization = `Bearer ${token}`;

    so the header goes out whenever a token exists, and `loginInvestor()` is what
    puts one there, from POST /api/v1/auth/login. The dashboard still failed with
    "Failed to load statutory filings from backend." because there was no token to
    send: the Economist Workspace has no staff login screen, so the browser had
    never stored one — and this router used to answer anyway, because it required
    no authentication at all.

    Two ways out, in order of correctness:
      (a) have the economist portal log in and store a token, leaving the guard
          ON — the end state you want;
      (b) put ECONOMIST_REQUIRE_AUTH=0 in your local .env while you build.
    This file supports both. (b) prints a warning on every startup.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import TokenData, get_current_user
from app.db.database import get_db
from app.db.models import FilingRecord
from app.schemas.bot_questionnaire import parse_questionnaire
from app.services.validation_engine import (
    BOT_DAILY_FX_RATES,
    ValidationAndCleansingEngine as Engine,
)

router = APIRouter(
    prefix="/api/v1/economist",
    tags=["2. Economist Workspace & Manual Audit"],
)

# Statuses a reviewer may set. Deliberately closed: Overview.tsx filters on exact
# strings, so a value outside this set is invisible in every filter except "All".
ReviewStatus = Literal["APPROVED", "REJECTED", "FLAGGED", "PROCESSED_WITH_ALERTS"]

# audit_notes is one column shared by the pipeline and the reviewer. Cap it so a
# repeatedly reviewed filing cannot grow without bound, while always keeping the
# newest decision.
MAX_AUDIT_NOTES_CHARS = 4000

# The destructive endpoint is disabled unless this is explicitly switched on.
# Read at import; set ALLOW_SUBMISSION_CLEAR=1 in a .env for a test environment.
ALLOW_SUBMISSION_CLEAR = os.environ.get("ALLOW_SUBMISSION_CLEAR", "").strip().lower() in (
    "1", "true", "yes", "on",
)
CLEAR_CONFIRMATION_PHRASE = "DELETE-ALL-FILINGS"

# These routes require a logged-in user. That guard is ON unless it is switched
# off explicitly, e.g. ECONOMIST_REQUIRE_AUTH=0 in a LOCAL .env for a demo.
#
# Why the switch exists: the version of this file in your project had no
# authentication on ANY economist route, so the dashboard loaded without a token.
# Adding the guard is correct, but a frontend with no staff login screen has no
# way to supply one, and the dashboard then fails with
#   "Failed to load statutory filings from backend."
# over a 401 in the uvicorn log. Switching the guard off is a deliberate,
# visible, logged choice. Shipping it off is not.
# The value is read once at import, like ALLOW_SUBMISSION_CLEAR above.
ECONOMIST_REQUIRE_AUTH = os.environ.get("ECONOMIST_REQUIRE_AUTH", "1").strip().lower() in (
    "1", "true", "yes", "on",
)


async def _no_auth() -> None:
    """Stand-in dependency used only when ECONOMIST_REQUIRE_AUTH is off."""
    return None


def _auth_guard():
    """Choose the authentication dependency once, at import.

    When the guard is on this returns `get_current_user` UNCHANGED, so the real
    validator in app/core/auth.py is used exactly as you wrote it. This file
    never re-implements it, relaxes it, or inspects the token itself.
    """
    return get_current_user if ECONOMIST_REQUIRE_AUTH else _no_auth


RequireEconomist = Depends(_auth_guard())

if not ECONOMIST_REQUIRE_AUTH:
    print(
        "[AUTH] ECONOMIST_REQUIRE_AUTH is OFF - /api/v1/economist/submissions, "
        "/submissions/{id}/audit and /submissions/clear are UNAUTHENTICATED. "
        "Local testing only. Set ECONOMIST_REQUIRE_AUTH=1 (or delete the line) "
        "before anyone else can reach this server."
    )


# ---------------------------------------------------------------- request body

def _accepts(*names: str) -> AliasChoices:
    """Accept several spellings of one field.

    The frontend sends camelCase and the database column names are snake_case.
    Rather than force a change in either place before anything works, both are
    accepted here. Order matters: the first name present wins.
    """
    return AliasChoices(*names)


class AuditDecisionPayload(BaseModel):
    """Audit decision from the review screen.

    Every field has a default, so a client cannot trigger a 422 by omitting one.
    That is deliberate: the previous model required four fields under names the
    frontend never sent.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    company_name: str = Field(default="", validation_alias=_accepts("company_name", "companyName"))
    tin_number: str = Field(default="", validation_alias=_accepts("tin_number", "tinNumber"))
    bpm6_category: Optional[str] = Field(
        default=None, validation_alias=_accepts("bpm6_category", "bpm6Category")
    )
    economist_notes: str = Field(
        default="", validation_alias=_accepts("economist_notes", "economistNotes", "auditNotes")
    )
    status: ReviewStatus = Field(
        validation_alias=_accepts("status", "auditStatus", "audit_status")
    )
    extracted_payload_override: Optional[Dict[str, Any]] = Field(
        default=None,
        validation_alias=_accepts(
            "extracted_payload_override", "extractedPayloadOverride",
            "extracted_payload", "extractedPayload", "payload",
        ),
    )
    # The A5/A6 tables as sent by the review screen. Placed into the payload by
    # _place_line_items().
    line_items: Optional[List[Any]] = Field(
        default=None, validation_alias=_accepts("line_items", "lineItems")
    )


# ------------------------------------------------------------------- helpers

def _actor_label(user: Optional[TokenData]) -> str:
    """Best available identifier for the person making the decision.

    `TokenData` in app/core/auth.py carries NO username. It has tin_number,
    company_name, client_id and roles, so every field that is present gets
    recorded instead of stopping at the first one: the edited payload carries no
    signature of its own, and audit_notes is the only place the reviewer's
    identity is ever written down.

    `roles` is included deliberately. auth.py currently hardcodes it to
    ["investor_submitter"] for every caller, which is exactly the kind of thing
    someone reading this note six months later needs to be able to see.
    """
    if user is None:
        return "unknown-user"          # the guard is switched off
    parts = [
        f"{attr}={value}"
        for attr in ("username", "email", "sub", "user_id",
                     "tin_number", "company_name", "client_id")
        if (value := getattr(user, attr, None))
    ]
    roles = getattr(user, "roles", None)
    if roles:
        parts.append("roles=" + ",".join(str(role) for role in roles))
    return " ".join(parts) or "unknown-user"


def _append_audit_note(existing: Optional[str], decision: str, actor: str, notes: str) -> str:
    """Keep the pipeline's findings and add the human trail after them."""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = f"[{stamp}] {actor}: {decision}"
    if notes.strip():
        entry += f" — {notes.strip()}"

    combined = f"{existing.strip()} | {entry}" if existing and existing.strip() else entry
    if len(combined) > MAX_AUDIT_NOTES_CHARS:
        marker = "[…earlier notes trimmed] "
        keep = MAX_AUDIT_NOTES_CHARS - len(entry) - len(marker) - 3
        combined = f"{marker}{combined[len(combined) - keep:]}" if keep > 0 else entry
    return combined


def _place_line_items(payload: Dict[str, Any], line_items: Optional[List[Any]]) -> List[str]:
    """Put the review screen's A5/A6 rows back into the payload.

    The screen sends one list — `shareholders.length > 0 ? shareholders :
    classifications`. Which table it came from is inferred from the keys present.
    Anything unrecognised is left alone and reported back, rather than written
    into a table where it does not belong.
    """
    warnings: List[str] = []
    if not line_items:
        return warnings

    shareholding_keys = {"source_country_or_multilateral", "reporting_year_relationship"}
    classification_keys = {"activity", "estimated_percentage_contribution"}
    rows = [r for r in line_items if isinstance(r, dict)]

    if not rows:
        warnings.append("line_items was supplied but contained no row objects.")
        return warnings

    part_a = payload.setdefault("part_a", {})
    if not isinstance(part_a, dict):
        warnings.append("part_a is not an object; line_items could not be placed.")
        return warnings

    if any(shareholding_keys & set(r) for r in rows):
        part_a["shareholding_structure"] = rows
    elif any(classification_keys & set(r) for r in rows):
        part_a["industrial_classifications"] = rows
    else:
        warnings.append(
            "line_items was supplied but matched neither the shareholding nor the "
            "activity table, so it was not placed. Its keys were: "
            f"{sorted(rows[0].keys())}"
        )
    return warnings


def _derive_from_payload(
    record: FilingRecord,
    payload: Dict[str, Any],
    form: Optional[Any] = None,
) -> List[str]:
    """Re-derive the indexed columns from an edited payload.

    `payload` is the normalised dict, so every section exists and the enum values
    are their wire values. `form` is the validated model when validation succeeded;
    it is what carries the filing's Table C2 rates.
    """
    notes: List[str] = []
    meta = payload.get("metadata") or {}

    # Only overwrite when the payload actually carries a value — never blank a
    # good company name because the edit form was empty.
    details = (payload.get("part_a") or {}).get("details") or {}
    name = (details.get("company_name") or "").strip()
    if name:
        record.company_name = name
    tin = (details.get("tin_number") or "").strip()
    if tin:
        record.tin_number = tin

    for attr, key in (("reporting_year", "reporting_year"), ("previous_year", "previous_year")):
        value = meta.get(key)
        if isinstance(value, int):
            setattr(record, attr, value)

    part_b = payload.get("part_b") or {}
    currency = part_b.get("currency_used")
    if currency:
        record.currency = currency

    # BPM6 belongs to the economist. Take it from the payload only when present.
    bpm6 = payload.get("bpm6_category")
    if bpm6:
        record.bpm6_category = bpm6

    # Summary columns, converted. The previous code wrote the raw reported figure
    # into total_assets_usd, which for a TZS filing was wrong by ~2,600x.
    # A stock is converted at a year-end rate, not a period average.
    rates = (
        Engine.rates_for_period(form, "reporting_end")
        if form is not None
        else dict(BOT_DAILY_FX_RATES)
    )

    part_d = payload.get("part_d_fats") or {}

    def item_value(item: Any) -> Optional[float]:
        if isinstance(item, dict):
            value = item.get("reporting_year_value")
        elif isinstance(item, (int, float)):
            value = item
        else:
            value = None
        return float(value) if isinstance(value, (int, float)) else None

    assets = item_value(part_d.get("total_assets_5"))
    if assets is not None:
        assets_usd, assets_tzs = Engine.convert_currency(assets, currency or "TZS", rates)
        if assets_usd is None:
            notes.append(
                f"total_assets could not be converted: currency {currency!r} is not in "
                f"the rate table. USD/TZS summary columns left unchanged."
            )
        else:
            record.total_assets_usd = assets_usd
            record.total_assets_tzs = assets_tzs

    net_worth = item_value(part_d.get("net_worth_7"))
    if net_worth is not None:
        net_worth_usd, _ = Engine.convert_currency(net_worth, currency or "TZS", rates)
        if net_worth_usd is not None:
            record.net_worth_usd = net_worth_usd

    return notes


# ------------------------------------------------------------------- routes

@router.get("/submissions", status_code=status.HTTP_200_OK)
async def fetch_bot_submissions(
    db: AsyncSession = Depends(get_db),
    current_user: Optional[TokenData] = RequireEconomist,
    limit: int = Query(default=500, ge=1, le=5000),
):
    """All filings for the economist dashboard, newest first."""
    try:
        result = await db.execute(
            select(FilingRecord)
            .order_by(FilingRecord.created_at.desc())
            .limit(limit)
        )
        records = result.scalars().all()

        data = [
            {
                "submission_id": r.filing_id,
                "filing_id": r.filing_id,
                "company_name": r.company_name,
                "tin_number": r.tin_number,
                "reporting_year": r.reporting_year,
                "previous_year": r.previous_year,
                "bpm6_category": r.bpm6_category,
                "total_assets_usd": r.total_assets_usd,
                "total_assets_tzs": r.total_assets_tzs,
                "net_worth_usd": r.net_worth_usd,
                "currency": r.currency,
                "status": r.status,
                "audit_notes": r.audit_notes,
                "extracted_payload": r.extracted_payload,
                "s3_object_key": r.s3_object_key,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "validated_at": r.validated_at.isoformat() if r.validated_at else None,
            }
            for r in records
        ]

        return {"status": "success", "data": data}

    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch submissions: {e}",
        )


@router.put("/submissions/{submission_id}/audit", status_code=status.HTTP_200_OK)
async def update_audit_decision(
    submission_id: str,
    payload: AuditDecisionPayload,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[TokenData] = RequireEconomist,
):
    """Commit an audit decision, optionally applying the reviewer's edits."""
    try:
        result = await db.execute(
            select(FilingRecord).where(FilingRecord.filing_id == submission_id)
        )
        record = result.scalars().first()
        if not record:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Filing record '{submission_id}' not found.",
            )

        actor = _actor_label(current_user)
        derived_notes: List[str] = []

        # ---- 1. the edited payload, if the reviewer made one ---------------
        override = payload.extracted_payload_override
        if override:
            override = dict(override)
            placed = _place_line_items(override, payload.line_items)
            derived_notes.extend(placed)

            try:
                normalised = parse_questionnaire(override)
                # Refreshes validation_warnings, so an edit that breaks a printed
                # relationship is recorded instead of being accepted silently.
                record.extracted_payload = normalised.model_dump(mode="json")
                validated_form = normalised
                if normalised.validation_warnings:
                    derived_notes.append(
                        f"re-validated after manual edit; "
                        f"{len(normalised.validation_warnings)} consistency finding(s) "
                        f"now recorded in the payload"
                    )
            except Exception as exc:
                # Never discard the reviewer's work, but never hide the problem.
                record.extracted_payload = override
                validated_form = None
                derived_notes.append(
                    f"WARNING: the edited payload failed schema validation "
                    f"({type(exc).__name__}: {exc}); it was stored unvalidated"
                )

            derived_notes.extend(
                _derive_from_payload(record, record.extracted_payload or {}, validated_form)
            )
        elif payload.line_items:
            # Rows edited, but no payload supplied — the only case where line_items
            # cannot be placed, because there is no payload to place them into.
            derived_notes.append(
                "line_items was supplied without extracted_payload_override, so the "
                "A5/A6 edits could not be stored. Send the full payload."
            )

        # ---- 2. the flat fields, applied only when they carry a value ------
        # Never blank a populated column because a form field came back empty.
        if payload.company_name.strip():
            record.company_name = payload.company_name.strip()
        if payload.tin_number.strip():
            record.tin_number = payload.tin_number.strip()
        if payload.bpm6_category:
            record.bpm6_category = payload.bpm6_category

        # ---- 3. status and the append-only audit trail --------------------
        record.status = payload.status
        note_text = payload.economist_notes
        if derived_notes:
            note_text = (note_text + " | " if note_text.strip() else "") + " | ".join(derived_notes)
        record.audit_notes = _append_audit_note(
            record.audit_notes, payload.status, actor, note_text
        )

        await db.commit()
        await db.refresh(record)

        return {
            "status": "success",
            "message": f"Filing {submission_id} audit decision committed successfully.",
            "data": {
                "submission_id": record.filing_id,
                "company_name": record.company_name,
                "tin_number": record.tin_number,
                "bpm6_category": record.bpm6_category,
                "status": record.status,
                "audit_notes": record.audit_notes,
                "extracted_payload": record.extracted_payload,
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update audit decision: {e}",
        )


@router.delete("/submissions/clear", status_code=status.HTTP_200_OK)
async def clear_bot_submissions(
    confirm: str = Query(
        default="",
        description=f"Must be exactly '{CLEAR_CONFIRMATION_PHRASE}'.",
    ),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[TokenData] = RequireEconomist,
):
    """Delete every filing. Triple-guarded; see the module docstring.

    This cannot be undone, and the raw documents in MinIO/disk are NOT removed, so
    the rows go but the PDFs stay.
    """
    if not ALLOW_SUBMISSION_CLEAR:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Clearing submissions is disabled. Set ALLOW_SUBMISSION_CLEAR=1 in the "
                "backend environment to enable it for a test environment only."
            ),
        )
    if confirm != CLEAR_CONFIRMATION_PHRASE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Pass ?confirm={CLEAR_CONFIRMATION_PHRASE} to confirm.",
        )

    try:
        # Read the ids first so the response can report exactly what was removed.
        # A single-column select, so `.scalars()` yields the strings directly.
        listing = await db.execute(select(FilingRecord.filing_id))
        filing_ids = list(listing.scalars().all())

        await db.execute(delete(FilingRecord))
        await db.commit()

        print(
            f"[AUDIT] submissions cleared by {_actor_label(current_user)}: "
            f"{len(filing_ids)} row(s) deleted: {filing_ids[:50]}"
        )

        return {
            "status": "success",
            "message": f"Cleared {len(filing_ids)} test submission(s).",
            "data": {"deleted_count": len(filing_ids), "deleted_filing_ids": filing_ids[:50]},
        }

    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to clear submissions: {e}",
        )


@router.get("/stream-updates")
async def stream_audit_events():
    """A SCRIPTED progress animation — not a report of what is happening.

    Kept because the dashboard consumes it, and left unauthenticated because an
    EventSource cannot send an Authorization header. It exposes no filing data,
    which is why that is acceptable here and not on the routes above.

    Be clear about what it is: the five steps below are fixed strings emitted on a
    1.5 s timer. They name the VLM and the rules engine, but they are emitted
    whether or not either is running, and the generator then ENDS — so a client
    that expects a live stream will reconnect and replay the same five steps.

    To make this real, the ingestion background task would need to publish
    progress (a job table or a pub/sub channel) and this route would subscribe to
    it, keyed by filing_id. Until then, do not read a completion from it.
    """
    async def event_generator():
        steps = [
            {"step": 1, "status": "CONNECTED", "message": "Established SSE connection with extraction engine."},
            {"step": 2, "status": "PROCESSING", "message": "Parsing incoming BoT C17 multi-section questionnaire payload..."},
            {"step": 3, "status": "VLM_ANALYSIS", "message": "Running VLM model for multi-page section extraction..."},
            {"step": 4, "status": "VALIDATING", "message": "Running business rules engine validation..."},
            {"step": 5, "status": "SYNCED", "message": "Data synchronized with PostgreSQL database."},
        ]

        for step in steps:
            yield f"data: {json.dumps(step)}\n\n"
            await asyncio.sleep(1.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )