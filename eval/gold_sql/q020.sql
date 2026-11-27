-- Professions by Covered_Recipient_Primary_Type_1 on NPP records.
SELECT program_year::INT AS program_year,
       sum(amt) FILTER (WHERE ptype = 'Physician Assistant') AS pa_usd,
       sum(amt) FILTER (WHERE ptype = 'Nurse Practitioner') AS np_usd
FROM (
    SELECT program_year, covered_recipient_primary_type_1 AS ptype,
           total_amount_of_payment_usdollars::DECIMAL(18, 2) AS amt
    FROM bronze.op_general
    WHERE program_year IN ('2023', '2025')
      AND covered_recipient_type = 'Covered Recipient Non-Physician Practitioner'
)
GROUP BY 1
ORDER BY 1;
