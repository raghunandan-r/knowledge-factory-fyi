-- Specialty = full Specialty_1 string; average = dollars / distinct paid physicians.
SELECT covered_recipient_specialty_1 AS specialty,
       round(sum(total_amount_of_payment_usdollars::DECIMAL(18, 2))
             / count(DISTINCT covered_recipient_profile_id), 2) AS usd_per_physician
FROM bronze.op_general
WHERE program_year = '2025'
  AND covered_recipient_type = 'Covered Recipient Physician'
  AND nullif(trim(covered_recipient_specialty_1), '') IS NOT NULL
GROUP BY 1
HAVING count(DISTINCT covered_recipient_profile_id) >= 100
ORDER BY usd_per_physician DESC
LIMIT 10;
