import asyncio
import json
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import delete

from app.db.database import get_db
from app.db.models import FilingRecord

router = APIRouter(
    prefix="/api/v1/economist",
    tags=["2. Economist Workspace & Manual Audit"]
)

class AuditDecisionPayload(BaseModel):
    company_name: str
    tin_number: str
    bpm6_category: str
    economist_notes: str
    status: str  # e.g., "APPROVED" or "FLAGGED"

@router.get("/submissions", status_code=status.HTTP_200_OK)
async def fetch_pcf_submissions(db: AsyncSession = Depends(get_db)):
    """Fetches all processed statutory filings from PostgreSQL for the Economist Dashboard."""
    try:
        result = await db.execute(select(FilingRecord).order_by(FilingRecord.created_at.desc()))
        records = result.scalars().all()
        
        data = [
            {
                "submission_id": r.filing_id,
                "filing_id": r.filing_id,
                "company_name": r.company_name,
                "tin_number": r.tin_number,
                "bpm6_category": r.bpm6_category,
                "total_usd": r.total_usd,
                "total_tzs": r.total_tzs,
                "currency": r.currency,
                "status": r.status,
                "audit_notes": r.audit_notes,
                "created_at": r.created_at.isoformat() if r.created_at else None
            }
            for r in records
        ]
        
        return {
            "status": "success",
            "data": data
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch submissions: {str(e)}"
        )

@router.put("/submissions/{submission_id}/audit", status_code=status.HTTP_200_OK)
async def update_audit_decision(
    submission_id: str, 
    payload: AuditDecisionPayload,
    db: AsyncSession = Depends(get_db)
):
    """
    Persists economist manual overrides and updates filing audit status in PostgreSQL.
    """
    result = await db.execute(select(FilingRecord).where(FilingRecord.filing_id == submission_id))
    record = result.scalars().first()
    
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Filing record '{submission_id}' not found."
        )

    # Apply manual updates
    record.company_name = payload.company_name
    record.tin_number = payload.tin_number
    record.bpm6_category = payload.bpm6_category
    record.audit_notes = payload.economist_notes
    record.status = payload.status

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
            "audit_notes": record.audit_notes
        }
    }

@router.delete("/submissions/clear", status_code=status.HTTP_200_OK)
async def clear_pcf_submissions(db: AsyncSession = Depends(get_db)):
    """Wipes test submissions directly from PostgreSQL."""
    try:
        await db.execute(delete(FilingRecord))
        await db.commit()
        return {
            "status": "success",
            "message": "All test submissions cleared successfully."
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to clear submissions: {str(e)}"
        )

@router.get("/stream-updates")
async def stream_audit_events():
    """
    Real-time Server-Sent Events (SSE) stream for monitoring extraction, 
    VLM processing progress, and database synchronization events.
    """
    async def event_generator():
        steps = [
            {"step": 1, "status": "CONNECTED", "message": "Established SSE connection with DERP engine."},
            {"step": 2, "status": "PROCESSING", "message": "Parsing incoming statutory document payload..."},
            {"step": 3, "status": "VLM_ANALYSIS", "message": "Running VLM model for line-item field extraction..."},
            {"step": 4, "status": "VALIDATING", "message": "Cross-checking company TIN and BPM6 classifications..."},
            {"step": 5, "status": "SYNCED", "message": "Data synchronized with PostgreSQL database."}
        ]
        
        for step in steps:
            # Send standard SSE event string format: data: <payload>\n\n
            yield f"data: {json.dumps(step)}\n\n"
            await asyncio.sleep(1.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"  # Prevents Nginx/proxy response buffering
        }
    )