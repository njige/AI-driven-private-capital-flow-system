from typing import Dict, Any, List, Tuple
from datetime import datetime

# Simulated BoT / IMF Daily FX Rates Engine (Base: TZS)
BOT_DAILY_FX_RATES = {
    "USD": 2680.0,  # 1 USD = 2680 TZS
    "EUR": 2910.0,  # 1 EUR = 2910 TZS
    "GBP": 3420.0,  # 1 GBP = 3420 TZS
    "TZS": 1.0
}

VALID_BPM6_CATEGORIES = [
    "Foreign Direct Investment (FDI) - Equity",
    "Portfolio Investment - Equity",
    "Foreign Debt / Long-term Loans",
    "Foreign Debt / Trade Credits",
    "Other Investment / Trade Credits"
]

class ValidationAndCleansingEngine:
    """
    Layer 3 Processing Pipeline:
    1. Business Rule Engine (TIN format, mandatory fields, math integrity)
    2. Daily FX Converter (Standardizing currency totals)
    3. Evaluation Router (Flagging anomalies vs High-Confidence Routing)
    """

    @staticmethod
    def convert_currency(amount: float, source_currency: str = "USD") -> Tuple[float, float]:
        """Converts financial amounts using official daily BoT exchange rates."""
        rate = BOT_DAILY_FX_RATES.get(source_currency.upper(), 2680.0)
        
        if source_currency.upper() == "TZS":
            total_tzs = amount
            total_usd = amount / BOT_DAILY_FX_RATES["USD"]
        else:
            total_usd = amount
            total_tzs = amount * rate

        return round(total_usd, 2), round(total_tzs, 2)

    @classmethod
    def evaluate_submission(cls, payload: Dict[str, Any]) -> Dict[str, Any]:
        flags: List[str] = []
        is_clean = True

        # --- STEP 1: PYTHON BUSINESS RULE ENGINE ---
        
        # Rule 1.1: TIN Format Validation (TRA standard: 9 digits)
        raw_tin = str(payload.get("tin_number", "")).replace("-", "").strip()
        if not raw_tin or len(raw_tin) != 9 or not raw_tin.isdigit():
            flags.append("ANOMALY: Invalid or missing TRA Taxpayer Identification Number (TIN).")
            is_clean = False
            tin = raw_tin if raw_tin else "000000000"
        else:
            tin = raw_tin

        # Rule 1.2: BPM6 Category Validation
        bpm6_cat = str(payload.get("bpm6_category", "")).strip()
        matched_cat = next((cat for cat in VALID_BPM6_CATEGORIES if cat.lower() in bpm6_cat.lower() or bpm6_cat.lower() in cat.lower()), None)
        
        if matched_cat:
            bpm6_cat = matched_cat
        else:
            flags.append(f"ANOMALY: Unrecognized BPM6 classification '{bpm6_cat}'.")
            is_clean = False

        # Rule 1.3: Non-Zero Capital Injection Check
        declared_usd = float(payload.get("total_usd", 0.0))
        if declared_usd <= 0:
            flags.append("ANOMALY: Declared financial capital return amount is zero or negative.")
            is_clean = False

        # Rule 1.4: Single Transaction Magnitude Check (Threshold: > $50,000,000 USD)
        if declared_usd > 50_000_000:
            flags.append("ALERT: High-value transaction (> $50M USD) requires senior economist verification.")
            is_clean = False

        # --- STEP 2: DAILY FX CONVERTER ---
        currency = payload.get("currency", "USD")
        total_usd, total_tzs = cls.convert_currency(declared_usd, currency)

        # --- STEP 3: EVALUATION ROUTER ---
        if is_clean:
            status = "PROCESSED_BY_AI"
            audit_notes = "Automated Business Rules Passed: High confidence filing."
        else:
            status = "FLAGGED"
            audit_notes = " | ".join(flags)

        # Update payload
        payload.update({
            "tin_number": f"{tin[:3]}-{tin[3:6]}-{tin[6:]}" if len(tin) == 9 else tin,
            "bpm6_category": bpm6_cat if bpm6_cat else "Foreign Direct Investment (FDI) - Equity",
            "total_usd": total_usd,
            "total_tzs": total_tzs,
            "status": status,
            "audit_notes": audit_notes,
            "validated_at": datetime.utcnow().isoformat()
        })

        return payload