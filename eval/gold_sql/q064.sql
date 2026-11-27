-- National total vs the sum of per-prescriber rows (rows under 11 claims are suppressed).
WITH national AS (
    SELECT sum(tot_clms::BIGINT) AS n
    FROM bronze.partd_geo_drug
    WHERE _data_year = '2024' AND prscrbr_geo_lvl = 'National' AND brnd_name = 'Ozempic'
),
prescriber AS (
    SELECT sum(tot_clms::BIGINT) AS n
    FROM bronze.partd_prescriber_drug
    WHERE _data_year = '2024' AND brnd_name = 'Ozempic'
)
SELECT national.n AS national_claims, prescriber.n AS prescriber_claims,
       national.n - prescriber.n AS missing
FROM national, prescriber;
