from pydantic import BaseModel, Field
from typing import Optional, List
from enum import Enum

class BPM6Category(str, Enum):
    FDI_EQUITY = "Foreign Direct Investment (FDI) - Equity"
    PORTFOLIO_EQUITY = "Portfolio Investment - Equity"
    FOREIGN_DEBT = "Foreign Debt / Long-term Loans"
    OTHER_INVESTMENT = "Other Investment / Trade Credits"
    UNKNOWN = "Unclassified / Manual Review Required"

class FinancialLineItem(BaseModel):
    description: str = Field(description="Description of the financial line item or instrument")
    amount_tzs: Optional[float] = Field(default=0.0, description="Extracted amount in TZS")
    amount_usd: Optional[float] = Field(default=0.0, description="Extracted amount in USD or foreign currency")

class PCFAuditExtraction(BaseModel):
    company_name: Optional[str] = Field(default="UNKNOWN", description="Name of the reporting entity")
    tin_number: Optional[str] = Field(default=None, description="Taxpayer Identification Number if present")
    reporting_period: Optional[str] = Field(default=None, description="Financial year or period (e.g. 2025/2026)")
    bpm6_category: BPM6Category = Field(default=BPM6Category.UNKNOWN)
    line_items: List[FinancialLineItem] = Field(default_factory=list)
    confidence_score: float = Field(default=0.5, description="Extraction confidence score between 0.0 and 1.0")
    audit_notes: Optional[str] = Field(default=None, description="Notes on anomalies, missing seals, or low clarity")