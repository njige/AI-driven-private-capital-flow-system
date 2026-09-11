CREATE OR REPLACE VIEW view_pcf_sectoral_summary AS
SELECT 
    bpm6_category,
    investor_entity,
    status,
    DATE_TRUNC('month', created_at) AS filing_month,
    COUNT(filing_id) AS total_filings,
    SUM(total_usd) AS aggregate_usd,
    SUM(total_tzs) AS aggregate_tzs,
    AVG(vlm_score) AS avg_ai_confidence
FROM pcf_filings
GROUP BY 
    bpm6_category, 
    investor_entity, 
    status, 
    DATE_TRUNC('month', created_at);