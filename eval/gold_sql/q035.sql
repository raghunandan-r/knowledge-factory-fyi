-- Month of Date_of_Payment (MM/DD/YYYY text) within PY2024, physicians only.
SELECT month(strptime(date_of_payment, '%m/%d/%Y')) AS month_number,
       sum(total_amount_of_payment_usdollars::DECIMAL(18, 2)) AS usd
FROM bronze.op_general
WHERE program_year = '2024'
  AND covered_recipient_type = 'Covered Recipient Physician'
GROUP BY 1
ORDER BY usd DESC
LIMIT 1;
