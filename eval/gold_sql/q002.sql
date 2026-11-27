-- Median of per-physician totals, not of records.
WITH per_physician AS (
    SELECT covered_recipient_profile_id,
           sum(total_amount_of_payment_usdollars::DECIMAL(18, 2)) AS total
    FROM bronze.op_general
    WHERE program_year = '2024'
      AND covered_recipient_type = 'Covered Recipient Physician'
    GROUP BY 1
)
SELECT round(median(total), 2) AS usd FROM per_physician;
