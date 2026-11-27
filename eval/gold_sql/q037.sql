-- Food and beverage to physicians, by program year.
WITH y AS (
    SELECT sum(amt) FILTER (WHERE program_year = '2023') AS usd_2023,
           sum(amt) FILTER (WHERE program_year = '2025') AS usd_2025
    FROM (
        SELECT program_year, total_amount_of_payment_usdollars::DECIMAL(18, 2) AS amt
        FROM bronze.op_general
        WHERE program_year IN ('2023', '2025')
          AND covered_recipient_type = 'Covered Recipient Physician'
          AND nature_of_payment_or_transfer_of_value = 'Food and Beverage'
    )
)
SELECT usd_2023, usd_2025, round(100 * (usd_2025 - usd_2023) / usd_2023, 4) AS percent_change
FROM y;
