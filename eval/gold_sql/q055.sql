-- Deactivated before Date_of_Payment and not reactivated by then. Records with a blank NPI
-- are excluded; all recipient types count.
WITH g AS (
    SELECT covered_recipient_npi AS npi, strptime(date_of_payment, '%m/%d/%Y') AS paid_on,
           total_amount_of_payment_usdollars::DECIMAL(18, 2) AS amt
    FROM bronze.op_general
    WHERE program_year = '2025' AND nullif(covered_recipient_npi, '') IS NOT NULL
),
n AS (
    SELECT npi, strptime(npi_deactivation_date, '%m/%d/%Y') AS d,
           strptime(nullif(npi_reactivation_date, ''), '%m/%d/%Y') AS r
    FROM bronze.nppes_npidata
    WHERE nullif(npi_deactivation_date, '') IS NOT NULL
)
SELECT count(*) AS records, coalesce(sum(amt), 0) AS usd
FROM g JOIN n USING (npi)
WHERE n.d < g.paid_on
  -- A reactivation on or before the payment date (same-day ones included) ends it.
  AND (n.r IS NULL OR n.r > g.paid_on);
