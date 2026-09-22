"""
app/services/ai_extractor.py  —  REVISED

Extracts a BoT PCF/C17 questionnaire from a submitted document using Gemini, and
validates the result against the schema in app/schemas/bot_questionnaire.py.

WHAT WAS WRONG IN THE PREVIOUS VERSION
--------------------------------------
1. A failed extraction was stored as a successful one. `extract_and_classify_pcf_doc`
   caught every exception and returned a dict of the same shape as a real result,
   with no marker distinguishing them. The caller had no way to tell, so failed
   filings were persisted as `PROCESSED BY AI` with a confidence score attached.

2. The error text was written into a data field:
       company_name = "[AI Unavailable: Model Busy] Processing (tmpc7itkkdd.pdf)"
   Two problems. It puts an error message where a company name belongs, and it
   always claims "Model Busy" — the real error string was passed in as
   `error_message` and then never used. A 404 and a quota exhaustion produced the
   same message.

3. The fallback hardcoded the survey period as reporting_year 2026 /
   previous_year 2025 while declaring questionnaire_type "PCF/C17/2025" — which
   implies T = 2024. The two statements contradicted each other inside one
   payload. (These are the 2025/2026 years that showed up in the tables.)

4. Retrying on bare `Exception` meant a programming error inside the call was
   retried 7 times with up to 30 s waits — a bug became a three-minute hang — and
   then was swallowed by the fallback anyway.

5. The prompt embedded `BOTQuestionnaireC17.model_json_schema()` verbatim:
   20,145 characters carrying 93 `"default": 0.0` entries. Telling a model that
   every numeric field defaults to zero invites it to return zeros for values it
   did not actually read. The schema is now slimmed to 9,374 characters with the
   defaults removed, and the prompt states explicitly that an omitted key means
   "not reported" while 0.0 means "reported as zero".

6. The prompt omitted whole sections the schema requires: A3 acknowledgement,
   A5's first column, Table C2 exchange rates, the questionnaire type, both A6
   relationship years, the "full units" rule, and the N/A convention.

7. `client = genai.Client()` ran at import time, so a missing GEMINI_API_KEY
   raised during import and took down the whole application rather than failing
   one extraction.

8. The uploaded file was sent to the model immediately after upload without
   checking that processing had finished. Large or scanned PDFs may still be
   PROCESSING, which fails the call intermittently.

9. ONE MODEL IS A SINGLE POINT OF FAILURE. Even with a valid name, the provider
   returns 503 when a model is saturated:

       503 UNAVAILABLE: This model is currently experiencing high demand.

   Observed on a live upload: five attempts, ~41 seconds, then a flagged filing.
   The retry was working — the model was simply unavailable for the whole window.
   A sibling model usually is not, so the extractor now walks a short list of
   candidates and uses the first that answers.

10. THE MODEL NAME WAS HARDCODED. `MODEL_NAME = "gemini-2.5-flash"` and the
   provider has retired that name for new API keys:

       404 NOT_FOUND: This model models/gemini-2.5-flash is no longer available
       to new users.

   Every extraction failed with that error, and the old code relabelled it
   "Model Busy". The name now comes from `GEMINI_MODEL` in .env, defaulting to
   `gemini-flash-latest`, an alias that follows whatever the provider currently
   recommends. A 404 or a 429 also appends an ACTION line naming the fix.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types
from google.genai.errors import APIError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from app.schemas.bot_questionnaire import BOTQuestionnaireC17, parse_questionnaire

# The VLM to call. Overridable, because model names are retired on the provider's
# schedule and not on ours: `gemini-2.5-flash` was the hardcoded value here and it
# now answers new API keys with
#
#     404 NOT_FOUND: This model models/gemini-2.5-flash is no longer available to
#     new users. Please update your code to use models/gemini-3.6-flash
#
# which arrives as an extraction failure and looks like a problem with the
# document. `gemini-flash-latest` is an alias that follows whatever the provider
# currently recommends, so it survives the next retirement. Pin it in .env if you
# need a fixed version for reproducibility:
#
#     # .env
#     GEMINI_MODEL=gemini-flash-latest
#
# Which names your key can actually CALL is not the same as which names it can
# LIST — see check_vlm.py, which probes the candidates and reports which answer.
MODEL_NAME = os.environ.get("GEMINI_MODEL", "").strip() or "gemini-flash-latest"


def candidate_models() -> List[str]:
    """The models to try, in order, for one extraction.

    Structured as a list rather than a single name because a saturated model
    returns 503 for every retry while its siblings keep serving. Override the
    tail of the list with GEMINI_MODEL_FALLBACKS (comma separated); the default
    is a spread across families and generations, so one family-wide incident does
    not take out every candidate at once.
    """
    configured = os.environ.get("GEMINI_MODEL_FALLBACKS", "").strip()
    if configured:
        fallbacks = [name.strip() for name in configured.split(",") if name.strip()]
    else:
        fallbacks = [
            "gemini-3-flash-preview",
            "gemini-flash-lite-latest",
            "gemini-2.5-flash-lite",
        ]
    ordered: List[str] = []
    for name in [MODEL_NAME, *fallbacks]:
        if name not in ordered:
            ordered.append(name)
    return ordered

# Failures are returned inside the payload rather than raised, so the ingestion
# pipeline can still persist the submission (with an honest status) instead of
# losing the upload. Set raise_on_failure=True to get the exception instead.
RETRY_EXCEPTIONS = (APIError, ConnectionError, TimeoutError)

_client: Optional[genai.Client] = None


def get_client() -> genai.Client:
    """Lazily construct the client.

    Previously this ran at module import, so a missing API key broke the whole
    application at startup instead of failing the one extraction that needed it.
    """
    global _client
    if _client is None:
        if not os.environ.get("GEMINI_API_KEY"):
            raise RuntimeError(
                "GEMINI_API_KEY is not set — the extraction service cannot run."
            )
        _client = genai.Client()
    return _client


# ==========================================================================
# PROMPT
# ==========================================================================

def slim_schema() -> str:
    """The Pydantic schema, minus the noise that misleads a model.

    Removes `default` (93 occurrences of 0.0 — removed so the model stops
    echoing zeros for values it never read), plus `title`, `description` and
    `additionalProperties`.

    Serialised compactly: pretty-printing with an indent spends roughly 4,300
    characters on whitespace alone, which is pure token cost in a prompt.
    """
    def prune(node: Any) -> Any:
        if isinstance(node, dict):
            return {
                k: prune(v)
                for k, v in node.items()
                if k not in ("title", "default", "description", "additionalProperties")
            }
        if isinstance(node, list):
            return [prune(v) for v in node]
        return node

    return json.dumps(
        prune(BOTQuestionnaireC17.model_json_schema()),
        separators=(",", ":"),
    )


SECTION_MAP = """
SECTION MAP — read every page; here is where each block belongs:

  page 1  header "QUESTIONNAIRE TYPE: PCF/C17/20xx"  -> metadata.questionnaire_type
  page 1  "A1: COMPANY DETAILS"                      -> part_a.details.*
  page 2  "A2: COMPANY AFFILIATES"                   -> part_a.affiliates.*
  page 2  "A3: ACKNOWLEDGEMENT OF RECEIPT"           -> part_a.acknowledgement.*
  page 5  "A5: INDUSTRIAL CLASSIFICATION"            -> part_a.industrial_classifications[]
  page 5  "A6: SHAREHOLDING STRUCTURE"               -> part_a.shareholding_structure[]
  page 6  "PART B / TABLE B1 / TABLE B2"             -> part_b.*
  page 7  "PART C(I) / TABLE C1"                     -> part_c_liabilities[]
  page 8  "PART D: FATS"                             -> part_d_fats.*
  page 8  "TABLE C2: EXCHANGE RATES (TZS/USD)"       -> metadata.exchange_rates

Survey period: the type on page 1 is PCF/C17/<survey year>. The tables are
labelled T-1 and T, where T = survey year - 1 and T-1 = survey year - 2. Read the
years off the column headings and put them in metadata.previous_year and
metadata.reporting_year. Do NOT assume any particular year.
"""

EXTRACTION_RULES = """
MANDATORY RULES

1. NEVER INVENT A VALUE. If a box is blank or unreadable, OMIT the key entirely.
   An omitted key means "not reported". 0.0 means "reported as zero". These are
   different facts and the distinction is used in audit review.

2. "N/A" — the form instructs respondents to write N/A rather than leave a box
   empty. When you see N/A, add that field's dotted path to
   `not_applicable_fields` (e.g. "part_b.table_b1.share_premium") and omit the
   value. Do not record N/A as zero.

3. AMOUNTS ARE IN FULL UNITS. The form says: "report all values in TZS or USD
   and in full units (e.g. ten million units as 10,000,000 and NOT 10m)".
   Strip thousands separators, currency symbols and any "m"/"bn" suffixes.

4. Currency: the box ticked at the top of Part B is part_b.currency_used.
   Values in Parts B and C are in that currency.

5. RELATIONSHIP CODES — use only these literals, and choose carefully:
     DI   direct investor — holds 10% or more of equity or voting rights
     DIE  direct investment entity — reverse investment: its non-resident
          subsidiary/associate holds 10% or more of THIS company
     FE   fellow enterprise — under the SAME non-resident direct investor as
          this company, but the holding here is under 10%
     PI   portfolio investment — under 10%, and NOT under the same direct
          investor as this company
     IFS  investment fund shares
     OTHER, RESIDENT
   A6 accepts: DI, FE, PI, IFS, OTHER, RESIDENT.
   C1 accepts: DI, FE, OTHER only.
   Do not treat FE and PI as interchangeable. A related company under the same
   non-resident parent is FE even though its holding is below 10%.

6. A6 has a relationship column for BOTH years. Capture
   previous_year_relationship AND reporting_year_relationship for every
   shareholder. Capture every printed row, not just the first.

7. A5 has THREE columns. The first ("Activity/Industrial Classification") is the
   sector itself and goes in `activity`; the second is
   `activity_description`; the third is `estimated_percentage_contribution`.

8. ARITHMETIC — the form defines these; your output should satisfy them:
     Table B1:  E = A + B - C + D1 + D2 + D3   (for each of the 5 equity types)
     Table B2:  D = A - B
     Table C1:  E = A + B - C + D1 + D2 + D3   (for each liability record)
     Part D:    item 7 = item 5 - item 6
                item 8 = item 9 + item 10
                item 11 = item 12 + item 13 + item 14

9. Table B1 has FIVE equity types (paid-up share capital, share premium,
   reserves, other equity, accumulated retained earnings). Return all five.
   Table B2 has FOUR columns A-D. Table C1 rows repeat per liability category —
   one record in part_c_liabilities for each filled row of the printed table.

10. A3 acknowledgement: capture recipient_name, recipient_company, title,
    tel_mobile, date and researcher name/mobile. Set signature_present = true
    ONLY if a signature is actually visible. Do not guess.

11. "extraction_status" — a TOP-LEVEL key of the JSON object, not inside
    metadata. It drives review priority, so be honest:
      "ok"      every printed section was found and populated
      "partial" some sections were blank, unreadable or absent
      "failed"  the document could not be read as a questionnaire at all
    Never report "ok" for a document you could not fully read.
"""


def build_prompt() -> str:
    return f"""You are a Bank of Tanzania (BoT) statistical analyst operating as an
automated extraction engine for the PCF/C17 questionnaire ("Questionnaire for the
Survey of Companies with Foreign Assets and Liabilities").

Read every page of the attached submission and return the questionnaire as JSON.

{SECTION_MAP}

{EXTRACTION_RULES}

Return EXCLUSIVELY a raw JSON object conforming to this structure. Omit any key
you could not read — do not fill it with a default:

{slim_schema()}
"""


# ==========================================================================
# VLM CALL
# ==========================================================================

def _generation_config() -> "types.GenerateContentConfig":
    """The generate_content config, built defensively.

    `automatic_function_calling` is disabled only if this SDK version exposes the
    symbol. The SDK prints a long advisory banner on every call that does not
    disable it, which is noise in an extraction log. Guarded with hasattr because
    pinning the code to a symbol from one SDK release is how a dependency bump
    turns into a 500 on upload.
    """
    kwargs: Dict[str, Any] = {
        "response_mime_type": "application/json",
        "temperature": 0.0,
    }
    if hasattr(types, "AutomaticFunctionCallingConfig"):
        try:
            kwargs["automatic_function_calling"] = types.AutomaticFunctionCallingConfig(disable=True)
        except Exception:      # noqa: BLE001 — cosmetic only, never fatal
            pass
    return types.GenerateContentConfig(**kwargs)


# Narrowed from (APIError, Exception): retrying a bare Exception meant a coding
# error was retried seven times with 30 s backoff before being swallowed.
#
# Two attempts per model, not five. Measured on a real 503 — "high demand" — five
# attempts of the SAME saturated model spent ~41 seconds and returned nothing.
# Switching model is the better use of that budget, so the attempts are few and
# the candidates are several.
@retry(
    stop=stop_after_attempt(2),
    wait=wait_random_exponential(multiplier=2, max=15),
    retry=retry_if_exception_type(RETRY_EXCEPTIONS),
    reraise=True,
)
def _retrying_call(uploaded_file: Any, prompt: str, model: str) -> Any:
    """One model, with a short retry budget."""
    return get_client().models.generate_content(
        model=model,
        contents=[uploaded_file, prompt],
        config=_generation_config(),
    )


def _call_gemini_vlm_with_retry(uploaded_file: Any, prompt: str) -> Any:
    """Try each candidate model until one answers.

    A provider that is out of capacity for one model is usually still serving
    another, so the failure that matters here — 503 UNAVAILABLE on every attempt
    — is handled by switching model rather than by waiting longer. Errors that
    are NOT capacity-related (a bad key, a malformed request) fail fast: trying
    three more models would only turn one clear error into four.
    """
    last_error: Optional[Exception] = None

    for index, model in enumerate(candidate_models()):
        try:
            if index:
                print(f"[PCF extraction] falling back to model {model!r}")
            return _retrying_call(uploaded_file, prompt, model)
        except APIError as err:
            last_error = err
            status = str(getattr(err, "status", "") or "").upper()
            code = getattr(err, "code", None)
            retryable = (
                status in ("UNAVAILABLE", "RESOURCE_EXHAUSTED", "NOT_FOUND", "INTERNAL")
                or code in (404, 429, 500, 503)
                or "high demand" in str(err).lower()
            )
            if not retryable:
                raise
            print(
                f"[PCF extraction] model {model!r} returned {code} {status or ''} — "
                f"{'trying the next candidate' if index + 1 < len(candidate_models()) else 'no candidates left'}"
            )

    assert last_error is not None
    raise last_error


def _upload_and_wait(file_path: str, timeout_s: int = 120) -> Any:
    """Upload the document and wait until Gemini has finished processing it.

    The previous version passed the just-uploaded handle straight to the model.
    For a large or scanned PDF the file can still be PROCESSING, which makes the
    call fail intermittently — and that failure then disappeared into the
    silent fallback.
    """
    client = get_client()
    uploaded = client.files.upload(file=file_path)

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        state = getattr(uploaded, "state", None)
        state_name = getattr(state, "name", str(state)) if state is not None else ""
        if state_name in ("ACTIVE", "FileState.ACTIVE"):
            return uploaded
        if state_name in ("FAILED", "FileState.FAILED"):
            raise RuntimeError(f"Gemini could not process {file_path!r} (state={state_name}).")
        time.sleep(2)
        uploaded = client.files.get(name=uploaded.name)

    raise TimeoutError(
        f"Gemini was still processing {file_path!r} after {timeout_s}s."
    )


def _delete_quietly(client: Any, uploaded: Any) -> None:
    if not uploaded:
        return
    try:
        client.files.delete(name=uploaded.name)
    except Exception:
        pass


# ==========================================================================
# PUBLIC API
# ==========================================================================

def build_failed_questionnaire(
    filename: str = "",
    error_message: str = "",
    partial: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """An explicitly-failed questionnaire.

    Carries no fabricated data. The company name is left empty — the previous
    version wrote the error text into `company_name`, which is why filings
    appeared in the list as "[AI Unavailable: Model Busy] Processing (...)".
    The real error goes to metadata.extraction_warnings where the review UI
    displays it, and extraction_status marks the record as failed.
    """
    base: Dict[str, Any] = partial if partial else {}
    base.setdefault("metadata", {})
    base["metadata"]["extraction_warnings"] = (
        list(base["metadata"].get("extraction_warnings") or [])
        + ([f"Extraction failed for {filename}: {error_message}"] if error_message else [])
    )
    # Root level: this is the field the schema declares and the review UI reads.
    base["extraction_status"] = "failed"
    return base


# Backwards-compatible alias. The old name is likely referenced by ingestion.py
# and by tests; it now returns a failed-but-honest payload instead of a
# plausible-looking empty one, so a caller that ignores the status is no longer
# able to mistake it for success.
def build_default_questionnaire_dict(
    company_name: str = "Unknown Entity",
    filename: str = "",
    error_message: str = "",
) -> Dict[str, Any]:
    payload = build_failed_questionnaire(
        filename=filename or company_name,
        error_message=error_message,
    )
    validated = parse_questionnaire(payload)
    return validated.model_dump(mode="json")


def extract_and_classify_pcf_doc(
    file_path: str,
    raise_on_failure: bool = False,
) -> Dict[str, Any]:
    """Extract a PCF/C17 questionnaire from a document.

    Returns the validated payload as a JSON-safe dict, always with
    `metadata.extraction_status` set to "ok" | "partial" | "failed" and any
    problems recorded in `metadata.extraction_warnings` and
    `validation_warnings`.

    Callers SHOULD branch on extraction_status. A returned dict is not by itself
    evidence that extraction succeeded — that was the defect this revision fixes.
    """
    filename = os.path.basename(file_path)
    uploaded_file = None
    client = None

    try:
        client = get_client()
        uploaded_file = _upload_and_wait(file_path)

        response = _call_gemini_vlm_with_retry(uploaded_file, build_prompt())

        # response.text raises if the candidate was blocked or returned nothing.
        raw_text = getattr(response, "text", None)
        if not raw_text:
            reason = getattr(response, "prompt_feedback", None) or "empty response"
            raise ValueError(f"Model returned no JSON content ({reason}).")

        try:
            parsed_data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Model returned malformed JSON at line {exc.lineno}: {exc.msg}"
            ) from exc

        if not isinstance(parsed_data, dict):
            raise ValueError(f"Model returned {type(parsed_data).__name__}, expected an object.")

        # A model that emits an explicit null for a `str` field produces a hard
        # failure that reads like a document problem:
        #     ValidationError: extraction_status
        #     Input should be a valid string [input_value=None, input_type=NoneType]
        # (Same failure as the questionnaire_type one fixed above.) An explicit
        # null carries no more information than an absent key, so drop it and let
        # the schema's own default and inference paths run.
        for container in (parsed_data, parsed_data.get("metadata")):
            if isinstance(container, dict):
                for key in ("extraction_status", "questionnaire_type"):
                    if key in container and container[key] is None:
                        container.pop(key)

        # What the MODEL claimed about its own read, taken from the raw answer
        # before validation. The declared field defaults to "ok", so reading the
        # claim back out of the validated payload cannot tell "the model said ok"
        # from "the model said nothing" — and a claim nested under metadata is
        # then shadowed by that default, which is not what the note above the
        # reconciliation promises.
        def _claimed(where: dict) -> str:
            value = (where or {}).get("extraction_status")
            return value.strip().lower() if isinstance(value, str) else ""

        claimed_root = _claimed(parsed_data)
        claimed_meta = _claimed(parsed_data.get("metadata") or {})

        # Let the schema fill unreadable sections. Do NOT substitute a fake
        # company name — an empty company_name is a truthful statement that the
        # name was not read.
        parsed_data.setdefault("part_a", {})
        parsed_data["part_a"].setdefault("details", {})

        parsed_data.setdefault("metadata", {})
        # Do NOT seed `questionnaire_type` here with None. `setdefault` inserts the
        # value whenever the model omits the key, and the field is a `str`: the
        # schema's own path — "questionnaire_type was not supplied; inferred
        # 'PCF/C17/<year>'" plus a warning — was replaced by
        #     ValidationError: metadata.questionnaire_type
        #     Input should be a valid string [input_type=NoneType]
        # i.e. an extraction that failed for a reason that reads like a document
        # problem. Verified: parse_questionnaire handles A) no metadata key, B)
        # empty metadata and D) a supplied type; only the injected None (C) raises.
        # `extraction_status` stays at the root: that is where the schema declares
        # it and where AuditReview.tsx reads it. The model is asked for the root
        # key, but a model may still nest it under metadata, so both are honoured
        # below and the result is written to the root only.

        # Validate. parse_questionnaire applies the schema's defaults, resolves
        # the survey period, runs the arithmetic checks and collects warnings.
        validated = parse_questionnaire(parsed_data)
        payload = validated.model_dump(mode="json")

        # Reconcile whatever the model said about its own confidence. The root
        # key is the primary statement; a claim nested under metadata is honoured
        # when the root was not used. Silence keeps the schema's default ("ok").
        candidates = [s for s in (claimed_root, claimed_meta) if s]
        # A stated failure wins over any other claim: understating a failure is
        # the unsafe direction, and it is the failure the reviewer must see.
        status = "failed" if "failed" in candidates else (candidates[0] if candidates
                                                          else payload.get("extraction_status") or "ok")
        # Do not let the model claim a clean run over a document that failed
        # the schema's own arithmetic or consistency checks.
        if status == "ok" and payload.get("validation_warnings"):
            status = "partial"
        payload["extraction_status"] = status

        return payload

    except Exception as err:
        error_str = f"{type(err).__name__}: {err}"

        # A retired or unavailable model name is the one failure that looks like a
        # document problem but is not, so it gets a line saying what to do. The
        # provider's own message is always kept — never replaced with a guess like
        # the old "Model Busy", which was hardcoded text that made a 404 and a
        # quota exhaustion indistinguishable.
        lowered = error_str.lower()
        if "not_found" in lowered or "no longer available" in lowered or "404" in lowered:
            error_str += (
                f" | ACTION: the model {MODEL_NAME!r} is not available to this API key. "
                f"Set GEMINI_MODEL in .env to a model the key can call — run "
                f"`python check_vlm.py` to see which ones answer, e.g. "
                f"GEMINI_MODEL=gemini-flash-latest"
            )
        elif "resource_exhausted" in lowered or "429" in lowered or "quota" in lowered:
            error_str += (
                " | ACTION: quota exhausted. An eight-page scanned questionnaire "
                "costs a lot of a free-tier allowance — check the plan and limits."
            )

        print(f"[PCF extraction failed for {filename}]: {error_str}")

        if raise_on_failure:
            raise

        return build_failed_questionnaire(filename=filename, error_message=error_str)

    finally:
        if client is not None:
            _delete_quietly(client, uploaded_file)


def extraction_status(payload: Dict[str, Any]) -> str:
    """Convenience accessor for callers that only need the status."""
    if not isinstance(payload, dict):
        return "failed"
    meta = payload.get("metadata") or {}
    return str(meta.get("extraction_status") or payload.get("extraction_status") or "failed")
