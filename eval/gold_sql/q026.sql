-- Value = Value_of_Interest (current value), not Total_Amount_Invested.
SELECT count(DISTINCT physician_profile_id) AS physicians,
       sum(value_of_interest::DECIMAL(18, 2)) AS usd
FROM bronze.op_ownership
WHERE program_year = '2025';
