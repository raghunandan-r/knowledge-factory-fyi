-- Per-physician total of PY2025 general payments on records with Recipient_State = 'CA'.
SELECT count(*) AS count
FROM (
    SELECT covered_recipient_profile_id
    FROM bronze.op_general
    WHERE program_year = '2025' AND covered_recipient_type = 'Covered Recipient Physician'
      AND recipient_state = 'CA'
    GROUP BY 1
    HAVING sum(total_amount_of_payment_usdollars::DECIMAL(18, 2)) > 10000
);
