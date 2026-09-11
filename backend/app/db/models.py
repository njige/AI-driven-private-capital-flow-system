from sqlalchemy import Column, String, Float, DateTime, Text, JSON
from datetime import datetime
from app.db.database import Base

class FilingRecord(Base):
    __tablename__ = "filings"

    filing_id = Column(String, primary_key=True, index=True)
    company_name = Column(String, nullable=False)
    tin_number = Column(String, index=True)
    bpm6_category = Column(String)
    total_usd = Column(Float)
    total_tzs = Column(Float)
    currency = Column(String, default="USD")
    status = Column(String, default="PROCESSED_BY_AI")
    audit_notes = Column(Text, nullable=True)
    s3_object_key = Column(String, nullable=True)  # Reference to MinIO S3 bucket object
    created_at = Column(DateTime, default=datetime.utcnow)
    validated_at = Column(DateTime, nullable=True)

class OpenLineageAuditLog(Base):
    __tablename__ = "lineage_audit_events"

    event_id = Column(String, primary_key=True)
    event_type = Column(String, nullable=False) # e.g., START, COMPLETE, FAIL
    job_name = Column(String, nullable=False)
    producer = Column(String, nullable=False)
    payload = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)