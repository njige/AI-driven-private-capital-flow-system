from typing import Optional, Dict, Any
from pydantic import BaseModel, Field

class PCFAuditExtraction(BaseModel):
    """
    Wrapper model maintaining backward compatibility while enforcing 
    the full nested Bank of Tanzania C17 Questionnaire schema.
    """
    filing_id: Optional[str] = Field(default="PCF-2026-0042")
    s3_object_key: Optional[str] = Field(default="")
    confidence_score: float = Field(default=0.95, description="Extraction confidence score between 0.0 and 1.0")
    status: str = Field(default="PENDING", description="Validation status (VALID, FLAGGED, etc.)")
    audit_notes: Optional[str] = Field(default=None, description="Notes on anomalies or rule violations")
    
    # The full nested questionnaire payload matching BOTQuestionnaireC17
    questionnaire: Optional[Dict[str, Any]] = Field(
        default_factory=dict,
        description="Full hierarchical BoT C17 questionnaire containing part_a, part_b, part_c_liabilities, and part_d_fats"
    )