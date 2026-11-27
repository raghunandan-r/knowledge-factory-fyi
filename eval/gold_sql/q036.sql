-- Dispute_Status_for_Publication = 'Yes', all recipient types.
SELECT count(*) AS records, sum(total_amount_of_payment_usdollars::DECIMAL(18, 2)) AS usd
FROM bronze.op_general
WHERE program_year = '2025' AND dispute_status_for_publication = 'Yes';
