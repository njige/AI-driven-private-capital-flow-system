"""
app/services/validation_engine.py  —  REVISED

Rules engine for the BoT PCF/C17 questionnaire: arithmetic checks, shareholding
consistency, currency standardisation and the status router.

WHAT WAS WRONG IN THE PREVIOUS VERSION
--------------------------------------
1. The endorsement. When no rule fired the engine returned

       status      = "PROCESSED_BY_AI"
       audit_notes = "Automated BoT Business Rules Passed: High confidence filing."

   Every rule is an arithmetic comparison, and an empty payload is all zeros —
   so 0 = 0 passed everything and a filing where nothing had been extracted was
   routed as a high-confidence pass. This is the sentence the economist saw over
   an empty record.

2. A fabricated fallback. When schema validation failed, the engine returned a
   hand-written dict containing reporting_year 2026 and previous_year 2025,
   company_name "Unextracted / Invalid Payload" and telephone "+255000000000".
   Three invented values — including a telephone number — written into a data
   record for a central-bank return. (These hardcoded years are also one of the
   sources of the 2025/2026 table years.) It is replaced by an empty payload.

3. The two engines disagreed, and both were stored. This file compared with a
   tolerance of 1.0; the schema compares with 0.01. A Part D net-worth
   discrepancy of 0.50 therefore produced, on the same record:

       validation_warnings: ["D.7: net worth is 3,000,000.50 but item 5 - item 6 = 3,000,000.00."]
       audit_notes:         "Automated BoT Business Rules Passed: High confidence filing."

   The arithmetic B1/B2/D.7 rules are now removed from this file and delegated
   to the schema, so there is exactly one implementation and one tolerance.

4. Table B1 was checked for the FIRST TAX YEAR ONLY. The old code read
   `form.part_b.table_b1.paid_up_share_capital` — one row of five. The other four
   rows (share premium, reserves, other equity, accumulated retained earnings)
   were never validated. Delegating to the schema fixes this as a side effect,
   because the schema iterates every row.

5. Duplicate warnings. Both this file and ingestion called `model_validate`, and
   the schema's validator re-appended its findings on every pass, so a single
   finding was stored two, four, six times over. Fixed in the schema; this file
   no longer adds a third pass on top.

6. `convert_currency` converted nothing except TZS. The rate table was consulted
   and then ignored:

       if currency == "TZS": ... else: total_usd = amount; total_tzs = amount * rate

   Any non-TZS amount was returned as if it were already USD, so a EUR or GBP
   filing was reported in USD at face value — an overstatement of roughly 12%
   for EUR and 32% for GBP. Worse, the rate used, 2597.4, is Table C2's
   *average reporting-year* rate, which is the correct rate for flows but the
   wrong rate for stocks; a balance-sheet item converted at an average rate is
   understated by roughly 9% against the year-end rate the instrument publishes.

7. The instrument's own Table C2 was ignored. The questionnaire asks the filer
   to state the rates used; the engine preferred a hardcoded dict instead. The
   rule is now: use what the filing declared, fall back to the table, and say in
   the audit note which source was used.

8. Dead guards. `hasattr(form.part_b, 'table_b1') and form.part_b.table_b1` is
   always true on a validated Pydantic model — `table_b1` is a declared field
   with a default factory, and a model instance is always truthy. The checks read
   as conditionals but never skipped anything.

WHAT THIS FILE NOW OWNS
-----------------------
The schema owns field-level arithmetic and consistency. This file owns:
  * the population check — whether a document was read at all;
  * Table C2 rate resolution and currency standardisation;
  * the high-value alert;
  * the A6 shareholding total;
  * the status router.

STATUS VOCABULARY
-----------------
FLAGGED               needs a human: extraction failed, nothing was extracted, or
                      a printed arithmetic relationship does not hold.
PROCESSED_WITH_ALERTS parsed and arithmetically sound, but carrying an advisory
                      (currently only the high-value threshold). NOTE: Overview.tsx
                      offers ALL / PROCESSED_BY_AI / APPROVED / REJECTED /
                      FLAGGED, so a filing in this state is visible only under
                      "All Submissions". One <option> needs adding there.
PROCESSED_BY_AI       parsed, arithmetically sound, nothing advisory.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.schemas.bot_questionnaire import BOTQuestionnaireC17, parse_questionnaire

# Fallback FX table: TZS per ONE unit of the currency.
# Table C2 (metadata.exchange_rates) overrides the USD entry whenever the filing
# declares it — see rates_for_period(). Keep the fallback current per survey
# cycle; it is a backstop, not the source of truth.
BOT_DAILY_FX_RATES: Dict[str, float] = {
    "USD": 2597.4,
    "EUR": 2910.0,
    "GBP": 3420.0,
    "TZS": 1.0,
}

# Which Table C2 column applies, by the economic nature of the figure.
#   stocks (balance-sheet items) -> end of period
#   flows  (profit, dividends, compensation) -> period average
# Matching the wrong one is a silent misstatement, not a rounding error.
C2_RATE_FIELDS: Dict[str, Tuple[str, ...]] = {
    "reporting_end": ("end_of_reporting_year", "average_reporting_year"),
    "reporting_average": ("average_reporting_year", "end_of_reporting_year"),
    "previous_end": ("end_of_previous_year", "average_previous_year"),
    "previous_average": ("average_previous_year", "end_of_previous_year"),
}

HIGH_VALUE_USD_THRESHOLD = 50_000_000.0

STATUS_PROCESSED = "PROCESSED_BY_AI"
STATUS_ALERTS = "PROCESSED_WITH_ALERTS"
STATUS_FLAGGED = "FLAGGED"

# audit_notes is a single column and the reader is an economist, not a log file.
MAX_AUDIT_NOTE_CHARS = 1000


class ValidationAndCleansingEngine:
    """Layer 3 of the BoT PCF/C17 pipeline."""

    # ---------------------------------------------------------------- currency

    @staticmethod
    def convert_currency(
        amount: Optional[float],
        source_currency: str = "USD",
        rates: Optional[Dict[str, float]] = None,
    ) -> Tuple[Optional[float], Optional[float]]:
        """Convert an amount to (USD, TZS) using TZS-per-unit rates.

        Returns (None, None) when the amount or the currency cannot be resolved.
        Writing no figure is safer than writing a wrongly-converted one: a
        central-bank aggregate that silently contains 1.12x its true USD value is
        harder to detect later than a NULL.
        """
        if amount is None:
            return None, None

        table = rates or BOT_DAILY_FX_RATES
        code = str(source_currency or "").strip().upper()
        usd_rate = table.get("USD") or BOT_DAILY_FX_RATES["USD"]
        rate = table.get(code)

        if not rate or not usd_rate:
            return None, None

        try:
            value = float(amount)
        except (TypeError, ValueError):
            return None, None

        total_tzs = value * rate
        total_usd = total_tzs / usd_rate
        return round(total_usd, 2), round(total_tzs, 2)

    @classmethod
    def rates_for_period(cls, form: Any, period: str = "reporting_end") -> Dict[str, float]:
        """TZS-per-unit rates, preferring the rates the filing itself declared.

        `period` selects the Table C2 column, because a stock and a flow must not
        be converted at the same rate.
        """
        table = dict(BOT_DAILY_FX_RATES)
        exchange = getattr(getattr(form, "metadata", None), "exchange_rates", None)

        for field_name in C2_RATE_FIELDS.get(period, ()):
            declared = getattr(exchange, field_name, None)
            try:
                declared = float(declared) if declared else None
            except (TypeError, ValueError):
                declared = None
            if declared:
                table["USD"] = declared
                return table

        return table

    # --------------------------------------------------------------- utilities

    @staticmethod
    def _unwrap(data: Any) -> Any:
        """Accept a bare payload, a {"questionnaire": {...}} wrapper, or a model."""
        wrapped = getattr(data, "questionnaire", None)
        if isinstance(wrapped, dict):
            return wrapped
        if isinstance(data, dict) and isinstance(data.get("questionnaire"), dict):
            return data["questionnaire"]
        return data

    @classmethod
    def _count_populated(cls, form: Any) -> int:
        """Count non-zero figures in the ANSWER sections.

        Metadata is deliberately excluded: reporting_year is always populated from
        a default, so including it would make an empty document look like a
        populated one — which is the failure this check exists to catch.
        """
        def numerics(node: Any) -> int:
            if isinstance(node, dict):
                return sum(numerics(v) for v in node.values())
            if isinstance(node, (list, tuple)):
                return sum(numerics(v) for v in node)
            if isinstance(node, bool) or node is None:
                return 0
            if isinstance(node, (int, float)):
                return 1 if node else 0
            return 0

        try:
            sections: List[Any] = [
                form.part_b.model_dump(exclude={"currency_used"}),
                [r.model_dump() for r in form.part_c_liabilities],
                [r.model_dump() for r in form.part_c_assets],
                form.part_c_memorandum.model_dump(),
                form.part_d_fats.model_dump(),
                [s.model_dump() for s in form.part_a.shareholding_structure],
            ]
        except Exception:
            return 0

        return sum(numerics(section) for section in sections)

    @staticmethod
    def _note(findings: List[str]) -> str:
        joined = " | ".join(findings)
        if len(joined) > MAX_AUDIT_NOTE_CHARS:
            joined = joined[:MAX_AUDIT_NOTE_CHARS] + " | [truncated — full list in validation_warnings]"
        return joined

    # ------------------------------------------------------------- entry point

    @classmethod
    def evaluate_questionnaire(cls, data: Any) -> Dict[str, Any]:
        """Validate an extracted payload and route it for review.

        Never raises and never fabricates content. The returned `questionnaire` is
        always a schema-valid, fully-populated payload — on failure it is empty
        rather than invented, and the reason is in `audit_notes`.
        """
        raw_payload = cls._unwrap(data)

        try:
            form = parse_questionnaire(raw_payload)
        except Exception as exc:  # schema could not accept it at all
            return cls._failed_result(exc)

        # --- what did the pipeline say about its own extraction? -------------
        # Root level only: that is where the schema declares it and where
        # AuditReview.tsx reads it. Default "partial", never "ok" — an absent
        # status must not be read as a clean extraction.
        extraction_status = str(form.extraction_status or "partial").lower()
        extraction_failed = extraction_status == "failed"

        # --- findings already computed by the schema -------------------------
        # Single source of truth for B1 (all five rows), B2, C1, D.7, D.8, D.11,
        # A5 and the all-zero check.
        arithmetic = list(form.validation_warnings)
        extraction_warnings = list(form.metadata.extraction_warnings)

        findings: List[str] = []
        anomalies: List[str] = []
        advisories: List[str] = []

        populated = cls._count_populated(form)
        currency = getattr(form.part_b.currency_used, "value", None) or str(
            form.part_b.currency_used
        )

        # --- engine-specific checks -----------------------------------------

        # A6 shareholding: the instrument asks for the ownership split, which
        # should account for the whole company.
        try:
            entries = form.part_a.shareholding_structure
            if entries:
                total_pct = sum(
                    float(getattr(s, "reporting_year_shareholding_pct", 0.0) or 0.0)
                    for s in entries
                )
                if total_pct > 100.0:
                    anomalies.append(
                        f"A6: reporting-year shareholding totals {total_pct:.2f}%, "
                        f"which exceeds 100%."
                    )
                elif 0.0 < total_pct < 99.99:
                    advisories.append(
                        f"A6: reporting-year shareholding totals {total_pct:.2f}% — "
                        f"the remainder is unaccounted for."
                    )
        except Exception:
            pass

        # High-value alert, converted at the year-end rate (a stock).
        rates = cls.rates_for_period(form, "reporting_end")
        assets_rep = getattr(form.part_d_fats.total_assets_5, "reporting_year_value", None)
        assets_usd, _ = cls.convert_currency(assets_rep, currency, rates)

        if assets_rep and assets_usd is None:
            advisories.append(
                f"Total assets could not be converted: currency {currency!r} is not "
                f"in the rate table. USD-equivalent figures were not computed."
            )
        elif assets_usd is not None and assets_usd > HIGH_VALUE_USD_THRESHOLD:
            rate_source = "Table C2" if rates["USD"] != BOT_DAILY_FX_RATES["USD"] else "fallback table"
            advisories.append(
                f"High-value enterprise: total assets of {assets_usd:,.2f} USD "
                f"(> {HIGH_VALUE_USD_THRESHOLD:,.0f}) at the year-end rate from "
                f"{rate_source} — senior economist review."
            )

        if not extraction_failed and populated == 0:
            anomalies.append(
                "No figures were extracted: every value in Parts B, C and D is zero "
                "or absent. This is either a document that could not be read or a "
                "nil return, and the two must be told apart before approval."
            )

        # --- routing ---------------------------------------------------------
        # A pass is claimed only when the extraction reported ok, the document
        # actually contained figures, and nothing was found.
        if extraction_failed:
            status = STATUS_FLAGGED
            is_valid = False
            reason = (
                next((w for w in extraction_warnings if "Extraction failed" in w), None)
                or "the extraction reported failure"
            )
            findings = [f"Extraction failed: {reason}"]
        elif anomalies:
            status = STATUS_FLAGGED
            is_valid = False
            findings = arithmetic + anomalies + advisories
        elif arithmetic:
            status = STATUS_FLAGGED
            is_valid = False
            findings = arithmetic + advisories
        elif advisories or extraction_status != "ok":
            status = STATUS_ALERTS
            is_valid = True
            findings = advisories + arithmetic
            if extraction_status != "ok":
                findings.append(f"extraction_status={extraction_status}")
        else:
            status = STATUS_PROCESSED
            is_valid = True
            findings = []

        if status == STATUS_PROCESSED:
            audit_notes = (
                f"Automated BoT business rules passed. All printed arithmetic "
                f"relationships hold ({populated} figures read)."
            )
        else:
            audit_notes = cls._note(findings) or "No specific finding recorded."

        # Make the extraction's own caveats visible without duplicating the whole
        # list — the payload carries them in full and the review screen shows them.
        if extraction_warnings and not extraction_failed:
            audit_notes = cls._note(
                [audit_notes, f"[{len(extraction_warnings)} extraction warning(s) recorded in the payload]"]
            )

        return {
            "is_valid": is_valid,
            "status": status,
            "audit_notes": audit_notes,
            "questionnaire": form.model_dump(mode="json"),
            "validated_at": datetime.now(timezone.utc).isoformat(),
            # Additive fields. Existing callers that read only the five keys above
            # are unaffected.
            "extraction_status": extraction_status,
            "findings": findings,
            "populated_value_count": populated,
        }

    # ------------------------------------------------------------ failure path

    @classmethod
    def _failed_result(cls, exc: Exception) -> Dict[str, Any]:
        """Schema validation failed: return an EMPTY payload, not an invented one.

        The previous fallback hardcoded reporting_year 2026, previous_year 2025,
        company_name "Unextracted / Invalid Payload" and telephone
        "+255000000000", and carried no extraction_status — so a record whose
        parse had failed persisted with a fabricated telephone number and a
        schema-default status of "ok".
        """
        message = f"schema validation failed: {type(exc).__name__}: {exc}"

        try:
            # The status is supplied UP FRONT so the schema sees it while it runs
            # its checks. Validating first and relabelling afterwards left the
            # payload carrying the finding "every headline FATS figure is zero
            # while extraction_status is 'ok'" on a record whose status had just
            # been set to "failed" — a self-contradicting audit trail.
            empty = parse_questionnaire({"extraction_status": "failed"})
        except Exception:  # the schema itself is unusable; do not compound it
            return {
                "is_valid": False,
                "status": STATUS_FLAGGED,
                "audit_notes": f"ANOMALY: {message} (and the empty-payload fallback also failed)",
                "questionnaire": {},
                "validated_at": datetime.now(timezone.utc).isoformat(),
                "extraction_status": "failed",
                "findings": [message],
                "populated_value_count": 0,
            }

        if message not in empty.metadata.extraction_warnings:
            empty.metadata.extraction_warnings.append(message)

        return {
            "is_valid": False,
            "status": STATUS_FLAGGED,
            "audit_notes": f"ANOMALY: {message}",
            "questionnaire": empty.model_dump(mode="json"),
            "validated_at": datetime.now(timezone.utc).isoformat(),
            "extraction_status": "failed",
            "findings": [message],
            "populated_value_count": 0,
        }