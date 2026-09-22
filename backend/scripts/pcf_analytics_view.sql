-- ===========================================================================
-- scripts/pcf_analytics_view.sql   —  reporting views over the C17 filings table
--
-- HOW TO RUN: open this file in pgAdmin's Query Tool (or `psql -f` it) against
-- the same database the backend writes to, and execute. Nothing here writes or
-- deletes data; it only defines read-only views. Re-running it is safe: each
-- view is dropped and recreated.
--
-- WHAT WAS WRONG WITH THE PREVIOUS VERSION
-- ----------------------------------------
-- It could not run at all. Against this schema it fails on the FIRST name it
-- touches, and there were five of them:
--
--   pcf_filings     -> the table is `filings`            (app/db/models.py)
--   investor_entity -> the column is `company_name`
--   total_usd       -> the column is `total_assets_usd`
--   total_tzs       -> the column is `total_assets_tzs`
--   vlm_score       -> THERE IS NO SUCH COLUMN, AND NO SUCH SCORE
--
-- The last one is the important one. No VLM confidence is computed anywhere in
-- this pipeline, which is why app/db/models.py deliberately has no score column
-- and why the review screen shows "not scored" rather than a number nobody
-- measured. `AVG(vlm_score) AS avg_ai_confidence` would have published an
-- average of nothing — in a financial-stability report. It is replaced by
-- metrics that are actually recorded: how many filings were read cleanly, how
-- many carry extraction warnings, and how many hit a rule in the validation
-- engine (`validation_warnings`, at the ROOT of the payload).
--
-- Two further points, both about honesty of the aggregate:
--
--   * "Sectoral" had no sector. The grouping key was `investor_entity`, i.e.
--     one row per company, which is an entity report, not a sectoral one. The
--     sector the questionnaire actually captures is A5
--     (`part_a.industrial_classifications[].activity`, page 5 — the column that
--     maps a company to its activity code), so that is what is surfaced, as
--     `primary_sector_code`. A filing may declare several activities and their
--     percentage contributions; the FIRST one is used here and the full list
--     stays untouched in `extracted_payload` (see view_pcf_filings_enriched).
--
--   * `created_at` and `reporting_year` are two different things and both are
--     useful: `filing_month` is when the document ARRIVED (operations), while
--     `reporting_year` is the survey period it REPORTS (statistics). The old
--     view grouped on the month alone, which silently mixes two reporting years
--     into one "March 2026" bucket whenever a previous-year re-filing arrives.
--
-- THE THREE VIEWS
-- ---------------
-- 1. view_pcf_filings_enriched   — one row per filing, with the derived
--                                  columns spelled out. The base everything
--                                  else is built from.
-- 2. view_pcf_sectoral_summary   — the requested view, corrected. Sector /
--                                  BPM6 / status totals per month and survey
--                                  year.
-- 3. view_pcf_entity_summary     — the entity grain the old view was really
--                                  producing, so that capability is not lost.
--
-- NOTES THAT MATTER LATER
-- -----------------------
-- * `extracted_payload` is `json`, not `jsonb` (see app/db/models.py). The
--   `->` / `->>` operators work on both and need no cast; the `jsonb_*`
--   functions do, so the cast is written `extracted_payload::jsonb` and stays
--   local to the expression that needs it. If the column is ever migrated to
--   `jsonb` (OPTIONAL_MIGRATIONS.sql), every `::jsonb` here can simply go.
-- * `CREATE OR REPLACE VIEW` refuses to change a column's name or type. Since
--   this revision renames columns (aggregate_usd -> aggregate_total_assets_usd),
--   each view is DROPped first. Do not remove those DROPs.
-- * A view runs with its OWNER's privileges unless it is created
--   `WITH (security_invoker = true)` (PostgreSQL 15+). If an analyst is granted
--   SELECT on these views but not on `filings`, they will still see every row.
--   For a system holding statutory filings that is worth deciding deliberately;
--   add `WITH (security_invoker = true)` to each CREATE VIEW, or grant on the
--   table and rely on the regular permissions.
-- ===========================================================================


-- ---------------------------------------------------------------------------
-- 1. One row per filing.
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS view_pcf_filings_enriched;

CREATE VIEW view_pcf_filings_enriched AS
SELECT
    f.filing_id,
    f.company_name,
    f.tin_number,
    f.reporting_year,
    f.previous_year,
    f.status,
    f.currency,
    f.created_at,
    f.validated_at,

    -- when it arrived vs. when it was validated (same date in the normal case)
    DATE_TRUNC('month', f.created_at)::date                            AS filing_month,
    DATE_TRUNC('month', f.validated_at)::date                          AS validated_month,

    -- dimensions. A NULL or empty category becomes a visible bucket rather
    -- than a blank cell that looks like a reporting error downstream.
    COALESCE(NULLIF(f.bpm6_category, ''), 'UNCLASSIFIED')              AS bpm6_category,

    -- A5 sector: the first declared activity. Empty after a failed extraction,
    -- which is exactly what UNCLASSIFIED should mean.
    COALESCE(
        NULLIF(
            f.extracted_payload::jsonb -> 'part_a'
                                        -> 'industrial_classifications'
                                        -> 0
                                        ->> 'activity',
            ''),
        'UNCLASSIFIED'
    )                                                                  AS primary_sector_code,

    -- A1 "Location of Establishment (Region)" — a real column on page 1, and
    -- the only geographic dimension the questionnaire captures. Kept here and
    -- NOT added to the sectoral summary, which would fragment it into one row
    -- per region per category.
    NULLIF(f.extracted_payload::jsonb -> 'part_a' -> 'details' ->> 'region', '')
                                                                       AS establishment_region,

    -- ---- the amounts, named as the model names them ----------------------
    f.total_assets_usd,
    f.total_assets_tzs,
    f.net_worth_usd,

    -- ---- extraction quality, honestly ------------------------------------
    -- Root of the payload, not metadata: see app/schemas/bot_questionnaire.py.
    COALESCE(f.extracted_payload::jsonb ->> 'extraction_status', 'missing')
                                                                       AS extraction_status,
    COALESCE(jsonb_array_length(
        f.extracted_payload::jsonb -> 'metadata' -> 'extraction_warnings'), 0)
                                                                       AS extraction_warning_count,
    COALESCE(jsonb_array_length(
        f.extracted_payload::jsonb -> 'validation_warnings'), 0)        AS validation_warning_count,
    COALESCE(jsonb_array_length(
        f.extracted_payload::jsonb -> 'not_applicable_fields'), 0)      AS not_applicable_count,

    NOT (COALESCE(f.extracted_payload::jsonb ->> 'extraction_status', 'missing') = 'ok')
                                                                       AS needs_extraction_review,
    f.s3_object_key,
    f.audit_notes
FROM filings f;


-- ---------------------------------------------------------------------------
-- 2. The sectoral summary.
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS view_pcf_sectoral_summary;

CREATE VIEW view_pcf_sectoral_summary AS
SELECT
    e.filing_month,
    e.reporting_year,
    e.bpm6_category,
    e.primary_sector_code,
    e.status,

    -- ---- volumes ---------------------------------------------------------
    COUNT(*)                                                           AS total_filings,

    -- The status breakdown as columns, so "how much of this month is still
    -- unverified" is one glance instead of a second query. These are the five
    -- values the pipeline and the review screen actually use.
    COUNT(*) FILTER (WHERE e.status = 'PROCESSED')                     AS processed_filings,
    COUNT(*) FILTER (WHERE e.status = 'PROCESSED_WITH_ALERTS')         AS processed_with_alerts_filings,
    COUNT(*) FILTER (WHERE e.status = 'FLAGGED')                       AS flagged_filings,
    COUNT(*) FILTER (WHERE e.status = 'REJECTED')                      AS rejected_filings,
    COUNT(*) FILTER (WHERE e.status = 'PROCESSING')                    AS processing_filings,

    -- ---- amounts ---------------------------------------------------------
    -- Both currencies, each under its own name: they are two columns in the
    -- source, and summing them together would be adding dollars to shillings.
    SUM(e.total_assets_usd)                                            AS aggregate_total_assets_usd,
    SUM(e.total_assets_tzs)                                            AS aggregate_total_assets_tzs,
    SUM(e.net_worth_usd)                                               AS aggregate_net_worth_usd,
    ROUND(AVG(e.total_assets_usd)::numeric, 2)                         AS avg_total_assets_usd,
    ROUND(AVG(e.net_worth_usd)::numeric, 2)                            AS avg_net_worth_usd,

    -- ---- data quality ----------------------------------------------------
    -- This is the honest substitute for the old `AVG(vlm_score)`. It answers
    -- "can this row be used?" rather than "how confident was a model that
    -- never reported a confidence".
    COUNT(*) FILTER (WHERE e.needs_extraction_review)                  AS filings_needing_extraction_review,
    COUNT(*) FILTER (WHERE e.extraction_warning_count > 0)             AS filings_with_extraction_warnings,
    COUNT(*) FILTER (WHERE e.validation_warning_count > 0)             AS filings_with_validation_findings,
    SUM(e.not_applicable_count)                                        AS total_not_applicable_answers,

    MIN(e.created_at)                                                  AS first_filing_at,
    MAX(e.validated_at)                                                AS last_validated_at
FROM view_pcf_filings_enriched e
GROUP BY
    -- Written out, not as ordinals (GROUP BY 1,2,3): with ordinals, inserting a
    -- column above silently changes what the rows mean.
    e.filing_month,
    e.reporting_year,
    e.bpm6_category,
    e.primary_sector_code,
    e.status;


-- ---------------------------------------------------------------------------
-- 3. The entity grain — what the old view was actually producing.
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS view_pcf_entity_summary;

CREATE VIEW view_pcf_entity_summary AS
SELECT
    COALESCE(NULLIF(e.company_name, ''), '[no name extracted]')        AS investor_entity,
    e.tin_number,
    e.reporting_year,
    e.bpm6_category,
    COUNT(*)                                                           AS total_filings,
    COUNT(DISTINCT e.status)                                           AS distinct_statuses,
    STRING_AGG(DISTINCT e.status, ', ' ORDER BY e.status)               AS statuses,
    MIN(e.filing_month)                                                AS first_filing_month,
    MAX(e.filing_month)                                                AS last_filing_month,
    SUM(e.total_assets_usd)                                            AS aggregate_total_assets_usd,
    SUM(e.total_assets_tzs)                                            AS aggregate_total_assets_tzs,
    SUM(e.net_worth_usd)                                               AS aggregate_net_worth_usd,
    COUNT(*) FILTER (WHERE e.needs_extraction_review)                  AS filings_needing_extraction_review
FROM view_pcf_filings_enriched e
GROUP BY
    COALESCE(NULLIF(e.company_name, ''), '[no name extracted]'),
    e.tin_number,
    e.reporting_year,
    e.bpm6_category;


-- ---------------------------------------------------------------------------
-- Quick checks after running this file (read-only, paste into the Query Tool):
--
--   SELECT * FROM view_pcf_sectoral_summary
--   ORDER BY filing_month DESC, total_filings DESC LIMIT 50;
--
--   -- Every filing should be counted exactly once across the status columns:
--   SELECT filing_month, reporting_year,
--          SUM(total_filings) AS filings,
--          SUM(processed_filings + processed_with_alerts_filings
--              + flagged_filings + rejected_filings + processing_filings) AS by_status
--   FROM view_pcf_sectoral_summary
--   GROUP BY filing_month, reporting_year
--   ORDER BY filing_month DESC;
--
--   -- Anything that could not be read should be visible, never silent:
--   SELECT filing_id, status, extraction_status
--   FROM view_pcf_filings_enriched
--   WHERE needs_extraction_review
--   ORDER BY created_at DESC;
-- ---------------------------------------------------------------------------
