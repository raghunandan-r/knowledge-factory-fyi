-- Individual clinicians = physicians + NPPs; teaching hospitals excluded.
SELECT round(100 * sum(amt) FILTER (WHERE covered_recipient_type
                                    = 'Covered Recipient Non-Physician Practitioner')
             / sum(amt), 4) AS percent
FROM (
    SELECT covered_recipient_type, total_amount_of_payment_usdollars::DECIMAL(18, 2) AS amt
    FROM bronze.op_general
    WHERE program_year = '2025'
      AND covered_recipient_type IN ('Covered Recipient Physician',
                                     'Covered Recipient Non-Physician Practitioner')
);
