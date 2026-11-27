-- Records naming two or more products across slots 1-5. Slot 1 can be empty while a later
-- slot is filled (9,907 PY2025 records), so "a name in slot 2+" alone over-counts.
SELECT count(*) AS count
FROM bronze.op_general
WHERE program_year = '2025'
  AND (nullif(trim(name_of_drug_or_biological_or_device_or_medical_supply_1), '') IS NOT NULL)::INT
    + (nullif(trim(name_of_drug_or_biological_or_device_or_medical_supply_2), '') IS NOT NULL)::INT
    + (nullif(trim(name_of_drug_or_biological_or_device_or_medical_supply_3), '') IS NOT NULL)::INT
    + (nullif(trim(name_of_drug_or_biological_or_device_or_medical_supply_4), '') IS NOT NULL)::INT
    + (nullif(trim(name_of_drug_or_biological_or_device_or_medical_supply_5), '') IS NOT NULL)::INT
    >= 2;
