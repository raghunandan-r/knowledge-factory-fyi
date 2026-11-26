-- Direct: the recipient is a physician. As PI: any PI 1-5 is a physician and the recipient
-- is not; the record amount counts once.
SELECT
    sum(amt) FILTER (WHERE covered_recipient_type = 'Covered Recipient Physician') AS direct_usd,
    sum(amt) FILTER (WHERE covered_recipient_type IS DISTINCT FROM 'Covered Recipient Physician'
                       AND 'Covered Recipient Physician' IN (pi1, pi2, pi3, pi4, pi5)) AS as_pi_usd
FROM (
    SELECT covered_recipient_type,
           total_amount_of_payment_usdollars::DECIMAL(18, 2) AS amt,
           principal_investigator_1_covered_recipient_type AS pi1,
           principal_investigator_2_covered_recipient_type AS pi2,
           principal_investigator_3_covered_recipient_type AS pi3,
           principal_investigator_4_covered_recipient_type AS pi4,
           principal_investigator_5_covered_recipient_type AS pi5
    FROM bronze.op_research
    WHERE program_year = '2025'
);
