-- Every record in the research file, all recipient types.
SELECT sum(total_amount_of_payment_usdollars::DECIMAL(18, 2)) AS usd
FROM bronze.op_research
WHERE program_year = '2025';
