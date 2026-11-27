-- Rank physicians by their PY2025 total; top 1% = ceil(0.01 x paid physicians).
WITH per AS (
    SELECT covered_recipient_profile_id,
           sum(total_amount_of_payment_usdollars::DECIMAL(18, 2)) AS total
    FROM bronze.op_general
    WHERE program_year = '2025' AND covered_recipient_type = 'Covered Recipient Physician'
    GROUP BY 1
),
ranked AS (
    SELECT total, row_number() OVER (ORDER BY total DESC) AS rn, count(*) OVER () AS n
    FROM per
)
SELECT round(100 * sum(total) FILTER (WHERE rn <= ceil(0.01 * n)) / sum(total), 4) AS percent
FROM ranked;
