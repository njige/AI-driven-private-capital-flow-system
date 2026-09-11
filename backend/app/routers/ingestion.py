import os
import tempfile
from datetime import datetime
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models import FilingRecord
from app.services.validation_engine import ValidationAndCleansingEngine
from app.services.minio_service import minio_service
from app.services.lineage_service import lineage_service
from app.services.ai_extractor import extract_and_classify_pcf_doc
from app.core.auth import get_current_user, TokenData  # <-- Imported TokenData model

router = APIRouter(prefix="/api/v1/ingest", tags=["1. Ingestion"])


async def process_extraction_background(filing_id: str, temp_file_path: str, s3_key: str):
    """Background task handler for processing Qwen extraction without freezing the HTTP response."""
    from app.db.database import AsyncSessionLocal
    
    async with AsyncSessionLocal() as db:
        try:
            # 1. Run Vision Language Model Extraction
            extracted_ai_data = extract_and_classify_pcf_doc(temp_file_path)

            line_items = extracted_ai_data.get("line_items", [])
            sum_usd = sum(float(item.get("amount_usd", 0.0)) for item in line_items)
            sum_tzs = sum(float(item.get("amount_tzs", 0.0)) for item in line_items)

            raw_usd = sum_usd if sum_usd > 0 else float(extracted_ai_data.get("total_usd", 0.0))
            raw_tzs = sum_tzs if sum_tzs > 0 else float(extracted_ai_data.get("total_tzs", 0.0))

            extracted_payload = {
                "filing_id": filing_id,
                "company_name": extracted_ai_data.get("company_name", "Unknown Entity"),
                "tin_number": extracted_ai_data.get("tin_number", "000000000"),
                "bpm6_category": extracted_ai_data.get("bpm6_category", "Foreign Direct Investment (FDI) - Equity"),
                "total_usd": raw_usd,
                "total_tzs": raw_tzs,
                "currency": "USD"
            }

            # 2. Business Rules Validation
            validated = ValidationAndCleansingEngine.evaluate_submission(extracted_payload)

            # 3. Update Record in PostgreSQL
            from sqlalchemy import select
            result = await db.execute(select(FilingRecord).where(FilingRecord.filing_id == filing_id))
            db_record = result.scalars().first()

            if db_record:
                db_record.company_name = validated["company_name"]
                db_record.tin_number = validated["tin_number"]
                db_record.bpm6_category = validated["bpm6_category"]
                db_record.total_usd = validated["total_usd"]
                db_record.total_tzs = validated["total_tzs"]
                db_record.status = validated["status"]
                db_record.audit_notes = validated["audit_notes"]
                await db.commit()

            # 4. Record Lineage
            await lineage_service.log_data_lineage(
                db=db,
                job_name="ingest_and_validate_filing",
                event_type="COMPLETE",
                inputs=[{"namespace": "minio", "name": s3_key}],
                outputs=[{"namespace": "postgresql.public", "name": "filings"}],
                facets={"status": validated["status"], "tin": validated["tin_number"]}
            )

        except Exception as err:
            await db.rollback()
            print(f"[Background Task Error]: {str(err)}")
        finally:
            if os.path.exists(temp_file_path):
                os.remove(temp_file_path)


@router.post("/upload")
async def process_statutory_upload(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user)  # <-- TokenData model parameter
):
    try:
        file_bytes = await file.read()
        timestamp = int(datetime.utcnow().timestamp())
        filing_id = f"SUB-A{timestamp}"
        filename = f"{timestamp}_{file.filename}"

        # Save to S3 / MinIO
        try:
            s3_key = minio_service.upload_file(filename, file_bytes, file.content_type)
        except Exception:
            s3_key = f"local_fallback/{filename}"

        # Write to temporary file on disk
        file_ext = os.path.splitext(file.filename)[1]
        tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=file_ext)
        tmp_file.write(file_bytes)
        tmp_file.close()

        # Safely resolve user TIN number attribute
        user_tin = current_user.tin_number or "000-000-000"

        # Create initial pending record in DB immediately
        db_record = FilingRecord(
            filing_id=filing_id,
            company_name=f"Processing ({file.filename})...",
            tin_number=user_tin,
            bpm6_category="Foreign Direct Investment (FDI) - Equity",
            total_usd=0.0,
            total_tzs=0.0,
            currency="USD",
            status="PROCESSING",
            audit_notes=f"Uploaded by authenticated user (TIN: {user_tin}). VLM background extraction in progress...",
            s3_object_key=s3_key
        )
        db.add(db_record)
        await db.commit()

        # Dispatch AI task to run asynchronously in background
        background_tasks.add_task(
            process_extraction_background,
            filing_id=filing_id,
            temp_file_path=tmp_file.name,
            s3_key=s3_key
        )

        # Return instant success response to unlock UI spinner
        return {
            "status": "success",
            "message": "File submitted successfully and queued for AI verification.",
            "data": {
                "filing_id": filing_id,
                "status": "PROCESSING",
                "submitted_by_tin": user_tin
            }
        }

    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Upload initialization failed: {str(e)}")