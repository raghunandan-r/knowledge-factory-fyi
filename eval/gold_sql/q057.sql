-- Clinical = Preclinical_Research_Indicator 'No'; no identifier = blank or not NCT + 8 digits.
SELECT round(100 * sum(amt) FILTER (WHERE NOT regexp_full_match(coalesce(trim(nct), ''),
                                                                 'NCT[0-9]{8}'))
             / sum(amt), 4) AS percent
FROM (
    SELECT clinicaltrials_gov_identifier AS nct,
           total_amount_of_payment_usdollars::DECIMAL(18, 2) AS amt
    FROM bronze.op_research
    WHERE program_year = '2025' AND preclinical_research_indicator = 'No'
);
