-- Group by Teaching_Hospital_ID. Any name the top ID was reported under is accepted.
WITH th AS (
    SELECT teaching_hospital_id, teaching_hospital_name,
           total_amount_of_payment_usdollars::DECIMAL(18, 2) AS amt
    FROM bronze.op_general
    WHERE program_year = '2024'
      AND covered_recipient_type = 'Covered Recipient Teaching Hospital'
),
by_id AS (
    SELECT teaching_hospital_id, sum(amt) AS usd, list(DISTINCT teaching_hospital_name ORDER BY teaching_hospital_name) AS name
    FROM th GROUP BY 1
)
SELECT (SELECT sum(amt) FROM th) AS usd_total,
       name AS top_hospital,
       usd AS top_usd
FROM by_id
ORDER BY usd DESC
LIMIT 1;
