-- Any of the five product slots; a record counts once, in full.
SELECT sum(total_amount_of_payment_usdollars::DECIMAL(18, 2)) AS usd
FROM bronze.op_general
WHERE program_year = '2024'
  AND 'OZEMPIC' IN (
      upper(trim(name_of_drug_or_biological_or_device_or_medical_supply_1)),
      upper(trim(name_of_drug_or_biological_or_device_or_medical_supply_2)),
      upper(trim(name_of_drug_or_biological_or_device_or_medical_supply_3)),
      upper(trim(name_of_drug_or_biological_or_device_or_medical_supply_4)),
      upper(trim(name_of_drug_or_biological_or_device_or_medical_supply_5)));
