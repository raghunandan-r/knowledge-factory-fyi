-- Companies (making-payment IDs) with >= 10,000 PY2024 records, over half CHANGED.
-- Each row lists every name the ID reported under; any of them names the company.
SELECT list(DISTINCT applicable_manufacturer_or_applicable_gpo_making_payment_name
            ORDER BY applicable_manufacturer_or_applicable_gpo_making_payment_name) AS company
FROM bronze.op_general
WHERE program_year = '2024'
GROUP BY applicable_manufacturer_or_applicable_gpo_making_payment_id
HAVING count(*) >= 10000 AND count(*) FILTER (WHERE change_type = 'CHANGED') > 0.5 * count(*);
