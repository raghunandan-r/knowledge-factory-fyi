-- Per transfer: one record can bundle several transfers.
SELECT round(sum(total_amount_of_payment_usdollars::DECIMAL(18, 2))
             / sum(number_of_payments_included_in_total_amount::BIGINT), 2) AS usd
FROM bronze.op_general
WHERE program_year = '2024'
  AND covered_recipient_type = 'Covered Recipient Physician'
  AND nature_of_payment_or_transfer_of_value = 'Food and Beverage';
