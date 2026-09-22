# app/schemas/bot_questionnaire.py
"""
BOT — Questionnaire for the Survey of Companies with Foreign Assets and Liabilities
Instrument: PCF/C17.  Issued jointly by TISEZA, Bank of Tanzania and NBS.

This revision reconciles the model against the printed 8-page instrument
(page-by-page review in SCHEMA_REVIEW.md). Changes from the previous revision are
marked "CHANGED", "ADDED" or "NOTE" so they can be reviewed individually.

Two design decisions govern everything below:

1. extra="allow" everywhere. The payload is produced by a vision model. The
   previous revision silently discarded any key it did not declare — a written
   `tin_number` vanished at the validation boundary with no error. For a
   statistical return, silently destroying captured data is worse than carrying
   an unexpected field. Extras now survive and are surfaced as warnings.

2. Warnings, not hard failures, for arithmetic. A return that does not sum is
   not a malformed document — it is a finding. The model records the mismatch in
   `validation_warnings` instead of rejecting the submission, so the audit trail
   keeps both the figure and the fact that it did not reconcile.
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ==========================================
# BASE MODEL
# ==========================================

class ContractModel(BaseModel):
    """Base for every model in this module. See module docstring, point 1."""

    model_config = ConfigDict(extra="allow")


# ==========================================
# ENUMS & CONSTANTS
# ==========================================

DEFAULT_QUESTIONNAIRE_TYPE = "PCF/C17/2025"

# Last-resort values only, used when a year is neither present nor derivable
# from the questionnaire type. Any use of these is recorded in
# `extraction_warnings` so it is never silent. See _resolve_years().
DEFAULT_PREVIOUS_YEAR = 2023
DEFAULT_REPORTING_YEAR = 2024


class CurrencyType(str, Enum):
    TZS = "TZS"
    USD = "USD"


class RelationshipType(str, Enum):
    """Guidelines (page 4) define all seven. See note on the tables below."""
    DI = "DI"          # Direct Investor (>= 10%)
    DIE = "DIE"        # Direct Investment Entity (reverse investment)
    FE = "FE"          # Fellow Enterprise
    PI = "PI"          # Portfolio Investment (< 10%)
    IFS = "IFS"        # Investment Fund Shares
    OTHER = "OTHER"
    RESIDENT = "RESIDENT"


class A6RelationshipType(str, Enum):
    """A6 prints: 'DI, FE, PI, Other, IFS and Resident'.

    NOTE: DIE is defined in the guidelines but is NOT printed as an option here.
    It is retained because reverse investment must be reportable; the printed
    omission looks like a defect in the instrument. Raise with BoT/NBS — if they
    confirm DIE is invalid for A6, remove it from this enum and the discrepancy
    disappears.

    (Declared standalone rather than subclassing RelationshipType: Python enums
    with members cannot be extended.)
    """
    DI = "DI"
    DIE = "DIE"
    FE = "FE"
    PI = "PI"
    IFS = "IFS"
    OTHER = "OTHER"
    RESIDENT = "RESIDENT"


class C1RelationshipType(str, Enum):
    """C1 prints: 'DI, FE, Other'.

    CHANGED: this table previously used the full seven-value enum, so the API
    accepted PI / IFS / RESIDENT on a row whose printed form has no such box.
    Restricting it to the three printed options makes the API reject what the
    form cannot express.
    """
    DI = "DI"
    FE = "FE"
    OTHER = "OTHER"


class MaturityType(str, Enum):
    LT = "LT"  # Long Term (12 months or more)
    ST = "ST"  # Short Term (Less than 12 months)


class LiabilityCategoryType(str, Enum):
    """Verbatim from Table C1's row labels — backend matches on these strings."""
    LOANS = "Loans (Including Financial Leases, Repos)"
    DEBT_SECURITIES = "Debt securities (Including Money Market Instruments, Bonds and notes)"
    TRADE_CREDITS = "Suppliers/Trade Credits & Advances"
    INSURANCE_RESERVES = "Life & Non-Life Insurance Technical Reserves"
    PENSION_CLAIMS = "Pension Entitlements/Claims"
    OTHER_ACCOUNTS_PAYABLE = "Other Accounts Payable"


# ==========================================
# DYNAMIC PERIOD METADATA
# ==========================================

_YEAR_IN_TEXT = re.compile(r"(?:19|20)\d{2}")


def _coerce_year(value: Union[str, int, float, None]) -> Optional[int]:
    """Best-effort parse of a year. Returns None when nothing usable is present,
    so the caller can decide — never invents a value here."""
    if value is None:
        return None
    if isinstance(value, bool):          # bool is an int subclass; reject it
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned or cleaned.upper() == "N/A":
            return None
        if cleaned.isdigit():
            return int(cleaned)
        try:
            return int(float(cleaned))       # "2024.0"
        except ValueError:
            pass
        found = _YEAR_IN_TEXT.search(cleaned)  # "December 2024", "FY2025"
        if found:
            return int(found.group(0))
    return None


class ExchangeRatesTZSUsd(ContractModel):
    """ADDED — Table C2, printed on page 8.

    The instrument requires a currency choice in Parts B and C and instructs the
    respondent to "refer to a table of exchange rates in the last page". Without
    the applied rate, a TZS figure cannot be re-derived from its USD equivalent,
    and column D2 (exchange-rate change, "Official Use Only") cannot be
    independently verified. Values below are the printed 2023/2024 rates; they
    change every survey cycle and must be loaded per cycle, not hardcoded in
    business logic.
    """
    end_of_previous_year: Optional[float] = None      # Dec 2023: 2506.0
    average_previous_year: Optional[float] = None     # Dec 2023: 2382.1
    end_of_reporting_year: Optional[float] = None     # Dec 2024: 2374.7
    average_reporting_year: Optional[float] = None    # Dec 2024: 2597.4
    source: str = "Table C2"


class SurveyPeriodMetadata(ContractModel):
    questionnaire_type: str = Field(
        default=DEFAULT_QUESTIONNAIRE_TYPE,
        description="Extract from the top right of page 1.",
    )
    previous_year: int = Field(
        default=DEFAULT_PREVIOUS_YEAR,
        description="The older year referenced in the tables (e.g., 2023).",
    )
    reporting_year: int = Field(
        default=DEFAULT_REPORTING_YEAR,
        description="The newer year referenced in the tables (e.g., 2024).",
    )

    # ADDED — page 1 prints "RESEARCHER: ......" in the header block.
    researcher: Optional[str] = None

    # ADDED — Table C2.
    exchange_rates: ExchangeRatesTZSUsd = Field(default_factory=ExchangeRatesTZSUsd)

    # ADDED — anything the pipeline had to guess or coerce.
    extraction_warnings: List[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _resolve_years(cls, data: Any) -> Any:
        """Resolve the survey period — and the questionnaire type — from whatever
        the extractor supplied.

        Resolution order (no hard-coded year is ever invented without a warning):

          1. an explicit `questionnaire_type` with a survey year inside it
             ("PCF/C17/2027") -> reporting = survey_year - 1, previous = survey_year - 2;
          2. explicit `reporting_year` / `previous_year` values;
          3. the module default, recorded as a warning.

        The year is never taken from the system clock. Re-auditing a 2023 return
        in 2026 must still show 2023 and 2024; a clock-derived period would
        silently relabel every column of every historical filing.
        """
        if not isinstance(data, dict):
            return data

        d = dict(data)
        warnings = list(d.get("extraction_warnings") or [])

        # --- what did the extractor actually give us? ---------------------
        supplied_rep = _coerce_year(d.get("reporting_year"))
        supplied_prev = _coerce_year(d.get("previous_year"))
        qtype_raw = str(d.get("questionnaire_type") or "").strip()
        type_was_supplied = bool(qtype_raw)

        qtype = qtype_raw or DEFAULT_QUESTIONNAIRE_TYPE
        # Only trust a survey year from a type that was actually supplied. The
        # default constant contains "2025", so treating it as evidence would make
        # a survey year always derivable and the fallback below unreachable —
        # which is exactly how a filing whose period was never captured ended up
        # rendering 2023/2024 headers without a word of warning.
        survey_year: Optional[int] = None
        if type_was_supplied:
            match = _YEAR_IN_TEXT.search(qtype)
            survey_year = int(match.group(0)) if match else None
            if survey_year is None:
                warnings.append(
                    f"questionnaire_type {qtype!r} contains no recognisable survey year."
                )

        # --- infer the survey year from the years, when the type is absent ---
        # CHANGED: previously a correctly-supplied 2026 filing with no
        # questionnaire_type was compared against the *default* type and flagged
        # as a mismatch, so a 2027 form was reported as an anomaly.
        inferred_type = False
        if survey_year is None and supplied_rep is not None:
            survey_year = supplied_rep + 1
            d["questionnaire_type"] = f"PCF/C17/{survey_year}"
            inferred_type = True
        elif survey_year is None and supplied_prev is not None:
            # Only T-1 given ("as at December 2025"): survey year = T-1 + 2.
            survey_year = supplied_prev + 2
            d["questionnaire_type"] = f"PCF/C17/{survey_year}"
            inferred_type = True

        # --- resolve each year -------------------------------------------
        for field_name, offset, default, supplied in (
            ("reporting_year", 1, DEFAULT_REPORTING_YEAR, supplied_rep),
            ("previous_year", 2, DEFAULT_PREVIOUS_YEAR, supplied_prev),
        ):
            raw_value = d.get(field_name)

            if supplied is not None:
                d[field_name] = supplied
                # Cross-check only when the type was given by the extractor.
                # Comparing against an inferred or defaulted type would flag a
                # perfectly good future-dated return.
                if (
                    type_was_supplied
                    and survey_year is not None
                    and supplied != survey_year - offset
                ):
                    warnings.append(
                        f"{field_name}: supplied {supplied} disagrees with the survey "
                        f"year implied by questionnaire_type {qtype!r} "
                        f"(expected {survey_year - offset}). Keeping the supplied value; "
                        f"confirm which is correct."
                    )
                continue

            if raw_value not in (None, ""):
                # Supplied but unparseable.
                if survey_year is not None:
                    d[field_name] = survey_year - offset
                    warnings.append(
                        f"{field_name}: could not interpret {raw_value!r}; derived "
                        f"{survey_year - offset} from the survey year {survey_year}."
                    )
                else:
                    d.pop(field_name, None)
                    warnings.append(
                        f"{field_name}: could not interpret {raw_value!r} and no survey "
                        f"year is available; fell back to the module default ({default})."
                    )
                continue

            # Not supplied at all.
            if survey_year is not None:
                d[field_name] = survey_year - offset
            else:
                # CHANGED: this used to happen silently, so a filing whose period
                # was never captured rendered 2023/2024 column headers with no
                # indication that nothing had been read off the document.
                d.pop(field_name, None)
                warnings.append(
                    f"{field_name}: not present in the payload and no survey year is "
                    f"available; fell back to the module default ({default}). The "
                    f"generated column headers may not match the submitted document."
                )

        if inferred_type:
            warnings.append(
                f"questionnaire_type was not supplied; inferred 'PCF/C17/{survey_year}' "
                f"from reporting_year {supplied_rep}."
            )

        # De-duplicate, for the same reason `validation_warnings` is de-duplicated
        # at the end of the model validator: this block is a `mode="before"` hook, so
        # it runs on EVERY validation of the payload, and each run appended another
        # copy of every message it had already written. The raw payload arrives with
        # none of these strings, but the extractor's own parse, the rules engine and
        # ingestion each validate the payload again — three passes over a document
        # with a real disagreement produced six identical lines in the stored
        # payload and a "[4 extraction warning(s)]" count that matched neither.
        # Order is preserved: the first occurrence is the one that stays.
        seen_warnings: set = set()
        deduped_warnings: List[str] = []
        for warning in warnings:
            if warning not in seen_warnings:
                seen_warnings.add(warning)
                deduped_warnings.append(warning)

        d["extraction_warnings"] = deduped_warnings
        return d


# ==========================================
# PART A: GENERAL INFORMATION
# ==========================================

class ContactPerson(ContractModel):
    name: Optional[str] = None
    position: Optional[str] = None
    mobile: Optional[str] = None
    email: Optional[str] = None


class PartA1CompanyDetails(ContractModel):
    """A1: COMPANY DETAILS (page 1).

    CHANGED: `company_name` was required, while every sibling had a default. A
    required field in a vision-extracted pipeline forces *something* into the box
    when the model returns nothing — which is how this record ended up with
    company_name = "[AI Unavailable: Model Busy] Processing (tmpc7itkkdd.pdf)".
    A partial record with an honest status is better than a fabricated name.
    Extraction success is now tracked by `extraction_status` on the root.
    """
    company_name: str = ""
    previous_name: Optional[str] = None
    date_completed: Optional[str] = None
    po_box: Optional[str] = None
    district: Optional[str] = None
    region: Optional[str] = None          # printed as "Location of Establishment (Region)"
    area: Optional[str] = None
    street_plot: Optional[str] = None
    telephone: Optional[str] = None
    fax: Optional[str] = None
    email: Optional[str] = None
    website: Optional[str] = None
    date_established: Optional[str] = None
    date_commenced_ops: Optional[str] = None
    primary_contact: Optional[ContactPerson] = None
    alternative_contact: Optional[ContactPerson] = None

    # ADDED — the printed A1 block has no TIN box, but TIN is the reporting
    # entity's key identifier and the review UI captures it. Declaring it here
    # stops Pydantic silently discarding the written value.
    # Alternative: keep TIN only on the submission record and drop the payload
    # copy. Either is defensible; carrying it here avoids the loss.
    tin_number: Optional[str] = None


class PartA2CompanyAffiliates(ContractModel):
    has_subsidiaries_in_tz: Optional[bool] = None          # Question 2.1
    is_supplying_consolidated_info: Optional[bool] = None  # Question 2.2
    # Question 2.3 ("if no, fill separate questionnaires per company") is an
    # instruction to the respondent, not a data field. Not modelled.


class ResearcherInfo(ContractModel):
    """From the A3 block: 'Researcher: Name / Mob'."""
    name: Optional[str] = None
    mobile: Optional[str] = None


class AcknowledgementOfReceipt(ContractModel):
    """ADDED — A3: ACKNOWLEDGEMENT OF RECEIPT OF QUESTIONNAIRE (page 2).

    Entirely absent from the previous revision. This section is evidence-bearing:
    it records who acknowledged receipt, when, and whether a signature exists.
    For a compliance return, an unsigned or unacknowledged filing is itself a
    control exception, so the system must be able to see it.
    """
    recipient_name: Optional[str] = None
    recipient_company: Optional[str] = None
    title: Optional[str] = None
    tel_mobile: Optional[str] = None
    # The vision model can establish that a signature is present, but it cannot
    # transcribe one. Record presence, not content.
    signature_present: Optional[bool] = None
    date: Optional[str] = None
    researcher: ResearcherInfo = Field(default_factory=ResearcherInfo)


class IndustrialClassification(ContractModel):
    """A5: INDUSTRIAL CLASSIFICATION (page 5).

    CHANGED — the printed table has THREE columns; the previous model captured
    only two. The first column, "Activity/Industrial Classification", is the one
    that maps the company to a sector code, so its loss was material.
    """
    activity: str = ""                   # ADDED — A5 column 1
    activity_description: str = ""       # CHANGED — was required
    estimated_percentage_contribution: float = Field(0.0, ge=0.0, le=100.0)


class ShareholderRecord(ContractModel):
    """A6: SHAREHOLDING STRUCTURE (page 5). All six printed columns verified."""
    source_country_or_multilateral: str = ""   # CHANGED — was required
    shareholder_name: str = ""                 # CHANGED — was required
    previous_year_shareholding_pct: float = 0.0
    previous_year_relationship: A6RelationshipType = A6RelationshipType.OTHER
    reporting_year_shareholding_pct: float = 0.0
    reporting_year_relationship: A6RelationshipType = A6RelationshipType.OTHER


class PartAGeneralInfo(ContractModel):
    details: PartA1CompanyDetails = Field(default_factory=PartA1CompanyDetails)
    affiliates: PartA2CompanyAffiliates = Field(default_factory=PartA2CompanyAffiliates)
    # ADDED
    acknowledgement: AcknowledgementOfReceipt = Field(default_factory=AcknowledgementOfReceipt)
    industrial_classifications: List[IndustrialClassification] = Field(default_factory=list)
    shareholding_structure: List[ShareholderRecord] = Field(default_factory=list)


# ==========================================
# PART B: EQUITY INVESTMENT
# ==========================================

class EquityRow(ContractModel):
    previous_year_closing_A: float = 0.0       # Closing T-1
    purchase_increase_B: float = 0.0           # Increase during T
    sales_decrease_C: float = 0.0              # Decrease during T
    other_changes_price_D1: float = 0.0        # Official use only
    other_changes_exchange_rate_D2: float = 0.0  # Official use only
    other_changes_volume_D3: float = 0.0       # Official use only
    reporting_year_closing_E: float = 0.0      # Closing T (E = A + B - C + D)


class TableB1DirectInvestment(ContractModel):
    paid_up_share_capital: EquityRow = Field(default_factory=EquityRow)
    share_premium: EquityRow = Field(default_factory=EquityRow)
    reserves_capital_statutory_revaluation_other: EquityRow = Field(default_factory=EquityRow)
    # Printed as "Other Equity (e.g. Equity Debt Swaps, Shareholders Deposits)" —
    # the schema name correctly merges both examples.
    other_equity_debt_swaps_deposits: EquityRow = Field(default_factory=EquityRow)
    accumulated_retained_earnings_loss: EquityRow = Field(default_factory=EquityRow)


class TableB2ProfitsDividends(ContractModel):
    net_profit_or_loss_after_tax_A: float = 0.0   # Net profit during T
    dividends_declared_B: float = 0.0             # Declared during T
    dividends_paid_or_profits_remitted_C: float = 0.0
    retained_earnings_D: float = 0.0              # Official use only. D = A - B


class PartBEquityInvestment(ContractModel):
    currency_used: CurrencyType = CurrencyType.TZS
    table_b1: TableB1DirectInvestment = Field(default_factory=TableB1DirectInvestment)
    table_b2: TableB2ProfitsDividends = Field(default_factory=TableB2ProfitsDividends)
    # NOTE: the form instructs "report all values in TZS or USD and in full units
    # (e.g. ten million units as 10,000,000 and NOT 10m)". There is no field that
    # can enforce this — it is a validation concern, not a schema concern. Left
    # to the extraction prompt and to review.


# ==========================================
# PART C: NON-EQUITY INVESTMENTS
# ==========================================

class NonEquityLiabilityRecord(ContractModel):
    """TABLE C1: NON EQUITY LIABILITIES (page 7) — foreign investment IN the company.

    Printed note on columns A and E: "including accrued interest not paid".
    """
    liability_category: LiabilityCategoryType = LiabilityCategoryType.LOANS  # CHANGED — was required
    source_country_or_multilateral: str = ""                                 # CHANGED — was required
    relationship: C1RelationshipType = C1RelationshipType.OTHER             # CHANGED — 3-value enum
    original_maturity: MaturityType = MaturityType.LT
    previous_year_closing_A: float = 0.0     # Closing T-1 (incl. accrued interest not paid)
    amount_received_B: float = 0.0           # Received during T
    principal_repayment_C: float = 0.0       # Repaid during T
    other_changes_price_D1: float = 0.0      # Official use only
    other_changes_exchange_rate_D2: float = 0.0  # Official use only
    other_changes_volume_D3: float = 0.0     # Official use only
    reporting_year_closing_E: float = 0.0    # Closing T (incl. accrued interest not paid)
    interest_paid_G: float = 0.0             # Interest paid during T
    # NOTE: the printed columns are lettered A, B, C, D, E, G — there is no F.
    # Presumed a legacy numbering artefact; retained here so the letter mapping
    # matches the paper form exactly. Confirm if a page defining F exists.


class NonEquityAssetRecord(ContractModel):
    """RESERVED — Part C(II), the asset-side table.

    Page 7 is headed "PART C(I): NON EQUITY INVESTMENTS IN YOUR COMPANY". The
    Roman numeral implies a second part, but no C(II) page appears in the
    instrument supplied: the scan runs C1 then straight to Part D.

    The survey is titled "Survey of Companies with Foreign Assets AND
    Liabilities", while Parts B and C(I) both capture the *liability* side. If
    C(II) exists it is therefore almost certainly the *asset* side (the
    reporting company's own claims on non-residents).

    This model is deliberately an empty-by-default placeholder rather than a
    guess: it mirrors C1's column structure, but `asset_category` is a free
    string because inventing an enum for a table nobody has seen would be
    fabrication. Nothing is written here unless that page is supplied — at which
    point only `asset_category` should need to become an enum.
    """
    asset_category: str = ""
    source_country_or_multilateral: str = ""
    relationship: C1RelationshipType = C1RelationshipType.OTHER
    original_maturity: MaturityType = MaturityType.LT
    previous_year_closing_A: float = 0.0
    amount_received_B: float = 0.0
    principal_repayment_C: float = 0.0
    other_changes_price_D1: float = 0.0
    other_changes_exchange_rate_D2: float = 0.0
    other_changes_volume_D3: float = 0.0
    reporting_year_closing_E: float = 0.0
    interest_paid_G: float = 0.0


class PartCMemorandum(ContractModel):
    """ADDED — items defined in the guidelines but absent from C1's rows.

    Page 4 defines "Standardised Guarantee" at length ("...guarantees not provided
    by means of a financial derivative ... issued by governments on export credit
    or student loans"). C1 has no row for it, and off-balance-sheet guarantees
    are a classic exposure that goes unrecorded when there is nowhere to put it.

    CONFIRM with BoT/NBS whether guarantees are meant to be folded into "Other
    Accounts Payable" or reported separately. Until then this memorandum field
    gives the figure somewhere to live instead of being dropped.
    """
    standardised_guarantees: Optional[float] = None
    memorandum_note: Optional[str] = None


# ==========================================
# PART D: FATS STATISTICS
# ==========================================

class FATSItem(ContractModel):
    previous_year_value: float = 0.0          # T-1 Value
    reporting_year_value: float = 0.0         # T Value


class FATSCountItem(ContractModel):
    previous_year_count: int = 0              # T-1 Employee Count
    reporting_year_count: int = 0             # T Employee Count


class PartDFATS(ContractModel):
    """PART D: INFORMATION ON FOREIGN AFFILIATE TRADE STATISTICS (page 8).

    All 14 printed items verified. Items 9/10 and 12/13/14 are "o/w" (of which)
    breakdowns — their relationships to the totals are checked in
    _validate_arithmetic() below.
    """
    opening_stock_inventory_1: FATSItem = Field(default_factory=FATSItem)
    closing_stock_inventory_2: FATSItem = Field(default_factory=FATSItem)
    sales_turnover_3: FATSItem = Field(default_factory=FATSItem)
    tax_on_income_4: FATSItem = Field(default_factory=FATSItem)
    total_assets_5: FATSItem = Field(default_factory=FATSItem)
    total_liabilities_6: FATSItem = Field(default_factory=FATSItem)
    net_worth_7: FATSItem = Field(default_factory=FATSItem)         # Item 5 - Item 6
    total_number_of_employees_8: FATSCountItem = Field(default_factory=FATSCountItem)
    professionals_count_9: FATSCountItem = Field(default_factory=FATSCountItem)      # o/w
    non_professionals_count_10: FATSCountItem = Field(default_factory=FATSCountItem)  # o/w
    total_employee_compensation_11: FATSItem = Field(default_factory=FATSItem)
    compensation_short_term_foreign_12: FATSItem = Field(default_factory=FATSItem)   # o/w
    compensation_long_term_foreign_13: FATSItem = Field(default_factory=FATSItem)    # o/w
    compensation_local_14: FATSItem = Field(default_factory=FATSItem)                # o/w


# ==========================================
# MASTER QUESTIONNAIRE ROOT MODEL
# ==========================================

class BOTQuestionnaireC17(ContractModel):
    metadata: SurveyPeriodMetadata = Field(default_factory=SurveyPeriodMetadata)
    part_a: PartAGeneralInfo = Field(default_factory=PartAGeneralInfo)
    part_b: PartBEquityInvestment = Field(default_factory=PartBEquityInvestment)

    # CHANGED — the UI and this model now agree on `part_c_liabilities`.
    part_c_liabilities: List[NonEquityLiabilityRecord] = Field(default_factory=list)
    # ADDED — reserved for C(II); empty unless that page is supplied.
    part_c_assets: List[NonEquityAssetRecord] = Field(default_factory=list)
    # ADDED — items defined by the guidelines with no column in C1.
    part_c_memorandum: PartCMemorandum = Field(default_factory=PartCMemorandum)

    part_d_fats: PartDFATS = Field(default_factory=PartDFATS)

    # ---- pipeline / audit metadata -------------------------------------
    # ADDED. BPM6 is assigned by BoT during review, not supplied by the
    # respondent, but it belongs with the filing. Declaring it stops the value
    # being dropped when the payload round-trips through validation.
    bpm6_category: Optional[str] = None

    # ADDED. The questionnaire mandates "N/A" for inapplicable questions
    # (page 3), but every numeric field defaults to 0.0 — so "not applicable"
    # and "reported as nil" are currently indistinguishable. Rather than migrate
    # every numeric to Optional[float] (which would ripple through aggregation
    # and the review UI), list the dotted paths that were answered "N/A".
    # That migration remains the better long-term fix; this captures the signal
    # now, while the corpus is small.
    #
    # Two path forms are used, matching the granularity of the printed rows:
    #   field level  — "part_b.table_b1.share_premium"
    #                  "part_b.table_b2.retained_earnings_D"
    #                  "part_d_fats.total_assets_5"
    #                  "part_c_liabilities[0].interest_paid_G"
    #   record level — "part_c_liabilities[2]"   (a whole C1 line that does not apply)
    # The review UI emits field-level paths for B and D, and record-level paths
    # for C1. Both forms are accepted; consumers should prefix-match rather than
    # compare for equality.
    not_applicable_fields: List[str] = Field(default_factory=list)

    # ADDED. "failed" | "partial" | "ok" | "manual" — set by the ingest pipeline.
    # A record whose extraction failed must not present itself as verified data.
    extraction_status: str = "ok"

    # ADDED. Arithmetic and consistency findings. These are audit findings, not
    # parse errors, so the record is never rejected for them.
    validation_warnings: List[str] = Field(default_factory=list)

    # ---- consistency checks --------------------------------------------
    @model_validator(mode="before")
    @classmethod
    def _ensure_metadata(cls, data: Any) -> Any:
        """Guarantee the metadata block is validated even when the payload omits
        it. Without this, a payload with no `metadata` key took the declared
        default_factory and the survey-period validator never ran — so the
        defaulted years were applied with no warning recorded anywhere."""
        if isinstance(data, dict) and not isinstance(data.get("metadata"), dict):
            data = dict(data)
            data["metadata"] = {}
        return data

    @model_validator(mode="after")
    def _validate_arithmetic(self) -> "BOTQuestionnaireC17":
        """The instrument prints these relationships; a mismatch is a finding.

        Every check compares the stored figure against the arithmetic the form
        itself defines, and records the difference. Nothing is auto-corrected —
        silently overwriting a reported figure would destroy the exception that
        the reviewer needs to see.
        """
        warnings = list(self.validation_warnings)
        tol = 0.01

        def close(a: float, b: float) -> bool:
            return abs(a - b) < tol

        # --- Table B1:  E = A + B - C + D1 + D2 + D3
        for name, row in self.part_b.table_b1.model_dump().items():
            expected = (
                row["previous_year_closing_A"]
                + row["purchase_increase_B"]
                - row["sales_decrease_C"]
                + row["other_changes_price_D1"]
                + row["other_changes_exchange_rate_D2"]
                + row["other_changes_volume_D3"]
            )
            actual = row["reporting_year_closing_E"]
            if not close(expected, actual):
                warnings.append(
                    f"B1.{name}: closing E is {actual:,.2f} but A+B-C+D1+D2+D3 = "
                    f"{expected:,.2f} (difference {actual - expected:,.2f})."
                )

        # --- Table B2:  D = A - B
        b2 = self.part_b.table_b2
        expected_d = b2.net_profit_or_loss_after_tax_A - b2.dividends_declared_B
        if not close(expected_d, b2.retained_earnings_D):
            warnings.append(
                f"B2: retained earnings D is {b2.retained_earnings_D:,.2f} but "
                f"A-B = {expected_d:,.2f} (difference "
                f"{b2.retained_earnings_D - expected_d:,.2f})."
            )

        # --- Table C1:  E = A + B - C + D1 + D2 + D3
        for idx, rec in enumerate(self.part_c_liabilities):
            expected = (
                rec.previous_year_closing_A
                + rec.amount_received_B
                - rec.principal_repayment_C
                + rec.other_changes_price_D1
                + rec.other_changes_exchange_rate_D2
                + rec.other_changes_volume_D3
            )
            if not close(expected, rec.reporting_year_closing_E):
                warnings.append(
                    f"C1[{idx}] {rec.liability_category.value[:40]}...: closing E is "
                    f"{rec.reporting_year_closing_E:,.2f} but A+B-C+D1+D2+D3 = "
                    f"{expected:,.2f}."
                )

        # --- Part D:  item 7 = item 5 - item 6
        d = self.part_d_fats
        expected_net_worth = (
            d.total_assets_5.reporting_year_value
            - d.total_liabilities_6.reporting_year_value
        )
        if not close(expected_net_worth, d.net_worth_7.reporting_year_value):
            warnings.append(
                f"D.7: net worth is {d.net_worth_7.reporting_year_value:,.2f} but "
                f"item 5 - item 6 = {expected_net_worth:,.2f}."
            )

        # --- Part D:  item 8 = item 9 + item 10   (o/w professionals + non-)
        for period in ("previous_year_count", "reporting_year_count"):
            expected_emp = (
                getattr(d.professionals_count_9, period)
                + getattr(d.non_professionals_count_10, period)
            )
            actual_emp = getattr(d.total_number_of_employees_8, period)
            # Only flag when the split is actually populated — an all-zero
            # section is "not reported", which is a different matter.
            if expected_emp > 0 and actual_emp != expected_emp:
                warnings.append(
                    f"D.8 ({period}): total employees is {actual_emp:,} but "
                    f"item 9 + item 10 = {expected_emp:,}."
                )

        # --- Part D:  item 11 = item 12 + item 13 + item 14  (o/w compensation)
        for period in ("previous_year_value", "reporting_year_value"):
            expected_comp = (
                getattr(d.compensation_short_term_foreign_12, period)
                + getattr(d.compensation_long_term_foreign_13, period)
                + getattr(d.compensation_local_14, period)
            )
            actual_comp = getattr(d.total_employee_compensation_11, period)
            if expected_comp > 0 and not close(expected_comp, actual_comp):
                warnings.append(
                    f"D.11 ({period}): total compensation is {actual_comp:,.2f} but "
                    f"item 12 + item 13 + item 14 = {expected_comp:,.2f}."
                )

        # --- A5:  contributions should total ~100%
        if self.part_a.industrial_classifications:
            total_pct = sum(
                c.estimated_percentage_contribution
                for c in self.part_a.industrial_classifications
            )
            if total_pct > 0 and not close(total_pct, 100.0):
                warnings.append(
                    f"A5: estimated percentage contributions total {total_pct:.2f}%, "
                    f"not 100%."
                )

        # --- Part D:  the report cannot be extracted as all zeros
        if (
            self.extraction_status == "ok"
            and d.total_assets_5.reporting_year_value == 0
            and d.sales_turnover_3.reporting_year_value == 0
            and d.total_number_of_employees_8.reporting_year_count == 0
        ):
            warnings.append(
                "D: every headline FATS figure is zero while extraction_status is "
                "'ok' — verify the section was actually extracted."
            )

        # De-duplicate. Validators re-run on every `model_validate()` call, and
        # this payload is validated at least twice — once by the rules engine and
        # again by ingestion before it is persisted — so without this a single
        # finding is stored two, four, six times over and the review screen's
        # data-quality panel lists it once per duplicate.
        seen: set = set()
        deduped: List[str] = []
        for warning in warnings:
            if warning not in seen:
                seen.add(warning)
                deduped.append(warning)

        self.validation_warnings = deduped
        return self

    # ---- unexpected key detection --------------------------------------
    @model_validator(mode="after")
    def _note_unexpected_keys(self) -> "BOTQuestionnaireC17":
        """extra="allow" preserves unmodelled keys instead of destroying them.
        Surface them, so a typo or a renamed VLM field is visible rather than
        quietly captured under the wrong name."""
        extras = set(self.model_extra or {})
        if extras:
            message = (
                f"unrecognised top-level key(s) preserved but not modelled: "
                f"{sorted(extras)}"
            )
            # Guarded because extras survive every round-trip, so re-validating
            # would otherwise append this same line again each time.
            if message not in self.metadata.extraction_warnings:
                self.metadata.extraction_warnings.append(message)
        return self


# ---- public convenience --------------------------------------------------

def parse_questionnaire(payload: Any) -> BOTQuestionnaireC17:
    """Validate an extracted payload, tolerating partial extractions.

    Returns a fully-populated model. Callers should inspect
    `metadata.extraction_warnings` and `validation_warnings` rather than assume
    a clean pass — a record with warnings is still usable, and the warnings are
    part of the audit trail.
    """
    return BOTQuestionnaireC17.model_validate(payload or {})
