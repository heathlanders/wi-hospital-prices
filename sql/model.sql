-- Analysis model on top of the loaded tables (hospital, item, item_code, rate).
-- Run with: python -m wihp.model   ({config} is replaced with the config/ path.)

CREATE OR REPLACE TABLE payer_map AS
SELECT * FROM read_csv('{config}/payer_map.csv', header = true, all_varchar = true, delim = ',', quote = '"');

CREATE OR REPLACE TABLE shoppable_service AS
SELECT * FROM read_csv('{config}/shoppable_services.csv', header = true, all_varchar = true, delim = ',', quote = '"');

-- One row per payer-specific dollar rate for a CMS shoppable service.
-- Facility rates only (blank billing_class = facility; those files have no
-- professional rows), no modifiers (e.g. TC = technical component only).
CREATE OR REPLACE VIEW shoppable_rate AS
WITH matched AS (
    SELECT DISTINCT ic.slug, ic.row_id, s.service_code
    FROM item_code ic
    JOIN shoppable_service s
      ON s.code = ic.code
     AND (s.code_type = ic.code_type OR (s.code_type = 'CPT' AND ic.code_type = 'HCPCS'))
),
labeled AS (
    SELECT r.*, lower(r.payer_name || ' ' || coalesce(r.plan_name, '')) AS payer_text
    FROM rate r
    WHERE r.negotiated_dollar IS NOT NULL
)
SELECT
    h.slug,
    h.hospital_name,
    m.service_code,
    s.category,
    s.description AS service,
    coalesce(pm.payer_group, r.payer_name) AS payer_group,
    CASE
        WHEN regexp_matches(r.payer_text, 'medicaid|badger|antehm medicaid|\bmco\b|\bmcd\b|\bssi\b|family care')
            THEN 'Medicaid managed care'
        WHEN regexp_matches(r.payer_text, 'medicare|\bpace\b') THEN 'Medicare Advantage'
        WHEN regexp_matches(r.payer_text, 'exchange|marketplace|\bqhp\b') THEN 'Marketplace'
        WHEN regexp_matches(r.payer_text, 'work(ers)? ?comp') THEN 'Workers'' comp'
        ELSE 'Commercial'
    END AS product_line,
    r.payer_name,
    r.plan_name,
    i.setting,
    r.methodology,
    r.negotiated_dollar,
    i.gross_charge,
    i.cash_price
FROM labeled r
JOIN item i USING (slug, row_id)
JOIN matched m USING (slug, row_id)
JOIN shoppable_service s ON s.code = m.service_code
JOIN hospital h USING (slug)
LEFT JOIN payer_map pm USING (payer_name)
WHERE coalesce(i.billing_class, 'facility') = 'facility'
  AND i.modifiers IS NULL
  AND (s.code_type <> 'MS-DRG' OR i.setting IN ('inpatient', 'both'));

-- Median rate per hospital x service x payer group x product line.
CREATE OR REPLACE VIEW hospital_service_payer AS
SELECT slug, hospital_name, service_code, service, category, payer_group, product_line,
       median(negotiated_dollar) AS median_rate,
       count(*) AS n_rates
FROM shoppable_rate
GROUP BY ALL;

-- Per hospital x service: commercial negotiated rates vs cash and gross charge.
CREATE OR REPLACE VIEW hospital_service AS
SELECT slug, hospital_name, service_code, service, category,
       median(negotiated_dollar) FILTER (product_line = 'Commercial') AS commercial_median,
       min(negotiated_dollar) FILTER (product_line = 'Commercial') AS commercial_min,
       max(negotiated_dollar) FILTER (product_line = 'Commercial') AS commercial_max,
       count(DISTINCT payer_group) FILTER (product_line = 'Commercial') AS commercial_payers,
       median(cash_price) AS cash_price,
       median(gross_charge) AS gross_charge,
       median(cash_price) / median(negotiated_dollar) FILTER (product_line = 'Commercial')
           AS cash_to_commercial
FROM shoppable_rate
GROUP BY ALL;

-- Per service: spread of commercial payer-level medians across all hospitals.
CREATE OR REPLACE VIEW service_spread AS
SELECT service_code, service, category,
       count(DISTINCT slug) AS hospitals,
       count(DISTINCT payer_group) AS payer_groups,
       min(median_rate) AS min_rate,
       median(median_rate) AS median_rate,
       max(median_rate) AS max_rate,
       max(median_rate) / nullif(min(median_rate), 0) AS max_to_min
FROM hospital_service_payer
WHERE product_line = 'Commercial'
GROUP BY ALL;
