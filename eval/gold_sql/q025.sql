-- General + research per making-payment ID (ownership excluded). Any name the ID used counts.
WITH both_files AS (
    SELECT applicable_manufacturer_or_applicable_gpo_making_payment_id AS id,
           applicable_manufacturer_or_applicable_gpo_making_payment_name AS name,
           total_amount_of_payment_usdollars::DECIMAL(18, 2) AS amt
    FROM bronze.op_general WHERE program_year = '2024'
    UNION ALL
    SELECT applicable_manufacturer_or_applicable_gpo_making_payment_id,
           applicable_manufacturer_or_applicable_gpo_making_payment_name,
           total_amount_of_payment_usdollars::DECIMAL(18, 2)
    FROM bronze.op_research WHERE program_year = '2024'
)
SELECT list(DISTINCT name ORDER BY name) AS company, sum(amt) AS usd
FROM both_files
GROUP BY id
ORDER BY usd DESC
LIMIT 1;
