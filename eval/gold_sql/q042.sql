-- Every company (making-payment ID) paying a physician with Recipient_State = 'VT' in PY2025.
-- Any name the ID reported under counts.
SELECT list(DISTINCT applicable_manufacturer_or_applicable_gpo_making_payment_name
            ORDER BY applicable_manufacturer_or_applicable_gpo_making_payment_name) AS company,
       sum(total_amount_of_payment_usdollars::DECIMAL(18, 2)) AS usd
FROM bronze.op_general
WHERE program_year = '2025' AND covered_recipient_type = 'Covered Recipient Physician'
  AND recipient_state = 'VT'
GROUP BY applicable_manufacturer_or_applicable_gpo_making_payment_id
ORDER BY usd DESC;
