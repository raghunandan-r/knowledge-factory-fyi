-- Unpivot the five slots, keep Drug/Biological slots, count each record once per product.
WITH slots AS (
    SELECT record_id, total_amount_of_payment_usdollars::DECIMAL(18, 2) AS amt,
           unnest([indicate_drug_or_biological_or_device_or_medical_supply_1,
                   indicate_drug_or_biological_or_device_or_medical_supply_2,
                   indicate_drug_or_biological_or_device_or_medical_supply_3,
                   indicate_drug_or_biological_or_device_or_medical_supply_4,
                   indicate_drug_or_biological_or_device_or_medical_supply_5]) AS kind,
           unnest([name_of_drug_or_biological_or_device_or_medical_supply_1,
                   name_of_drug_or_biological_or_device_or_medical_supply_2,
                   name_of_drug_or_biological_or_device_or_medical_supply_3,
                   name_of_drug_or_biological_or_device_or_medical_supply_4,
                   name_of_drug_or_biological_or_device_or_medical_supply_5]) AS name
    FROM bronze.op_general
    WHERE program_year = '2025'
),
per_record AS (
    SELECT DISTINCT record_id, amt, upper(trim(name)) AS product
    FROM slots
    WHERE kind IN ('Drug', 'Biological') AND nullif(trim(name), '') IS NOT NULL
)
SELECT product, sum(amt) AS usd
FROM per_record
GROUP BY product
ORDER BY usd DESC
LIMIT 10;
