import uuid
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import OpenLineageAuditLog

class OpenLineageService:
    @staticmethod
    async def log_data_lineage(
        db: AsyncSession, 
        job_name: str, 
        event_type: str, 
        inputs: list, 
        outputs: list, 
        facets: dict
    ):
        """
        Emits OpenLineage compliant event tracking data transformations from MinIO -> PostgreSQL.
        """
        event_payload = {
            "eventType": event_type,
            "eventTime": datetime.utcnow().isoformat(),
            "job": {"namespace": "bot.pcf.pipeline", "name": job_name},
            "inputs": inputs,
            "outputs": outputs,
            "facets": facets
        }

        audit_entry = OpenLineageAuditLog(
            event_id=str(uuid.uuid4()),
            event_type=event_type,
            job_name=job_name,
            producer="https://github.com/OpenLineage/pcf-engine",
            payload=event_payload
        )

        db.add(audit_entry)
        await db.commit()

lineage_service = OpenLineageService()