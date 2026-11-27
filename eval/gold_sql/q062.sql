-- Physicians (by NPI) paid >= $5,000 in PY2024 for consulting or company speaking, on records
-- naming Ozempic in any product slot; "no row" = no 2024 Part D row for Ozempic.
WITH paid AS (
    SELECT covered_recipient_npi AS npi
    FROM bronze.op_general
    WHERE program_year = '2024' AND covered_recipient_type = 'Covered Recipient Physician'
      AND nullif(covered_recipient_npi, '') IS NOT NULL
      AND nature_of_payment_or_transfer_of_value IN ('Consulting Fee', 'Compensation for services other than consulting, including serving as faculty or as a speaker at a venue other than a continuing education program')
      AND 'OZEMPIC' IN (upper(trim(name_of_drug_or_biological_or_device_or_medical_supply_1)), upper(trim(name_of_drug_or_biological_or_device_or_medical_supply_2)), upper(trim(name_of_drug_or_biological_or_device_or_medical_supply_3)), upper(trim(name_of_drug_or_biological_or_device_or_medical_supply_4)), upper(trim(name_of_drug_or_biological_or_device_or_medical_supply_5)))
    GROUP BY 1
    HAVING sum(total_amount_of_payment_usdollars::DECIMAL(18, 2)) >= 5000
),
rx AS (
    SELECT DISTINCT prscrbr_npi AS npi FROM bronze.partd_prescriber_drug
    WHERE _data_year = '2024' AND brnd_name = 'Ozempic'
)
SELECT count(*) AS paid, count(*) FILTER (WHERE npi NOT IN (SELECT npi FROM rx)) AS no_row
FROM paid;
