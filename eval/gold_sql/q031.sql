-- Companies = distinct making-payment IDs; names = distinct raw name strings.
SELECT count(DISTINCT applicable_manufacturer_or_applicable_gpo_making_payment_id) AS companies,
       count(DISTINCT applicable_manufacturer_or_applicable_gpo_making_payment_name) AS names
FROM bronze.op_general
WHERE program_year = '2025';
