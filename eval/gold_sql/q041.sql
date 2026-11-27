-- Individual providers (entity type 1). Primary taxonomy = the slot whose primary switch is 'Y'. Active = never deactivated, or
-- reactivated on or after the deactivation date. Paid = NPI on any PY2025 general record.
WITH slots AS (
    SELECT npi, entity_type_code, npi_deactivation_date AS d, npi_reactivation_date AS r,
           unnest([healthcare_provider_taxonomy_code_1, healthcare_provider_taxonomy_code_2,
                   healthcare_provider_taxonomy_code_3, healthcare_provider_taxonomy_code_4,
                   healthcare_provider_taxonomy_code_5, healthcare_provider_taxonomy_code_6,
                   healthcare_provider_taxonomy_code_7, healthcare_provider_taxonomy_code_8,
                   healthcare_provider_taxonomy_code_9, healthcare_provider_taxonomy_code_10,
                   healthcare_provider_taxonomy_code_11, healthcare_provider_taxonomy_code_12,
                   healthcare_provider_taxonomy_code_13, healthcare_provider_taxonomy_code_14,
                   healthcare_provider_taxonomy_code_15]) AS code,
           unnest([healthcare_provider_primary_taxonomy_switch_1,
                   healthcare_provider_primary_taxonomy_switch_2,
                   healthcare_provider_primary_taxonomy_switch_3,
                   healthcare_provider_primary_taxonomy_switch_4,
                   healthcare_provider_primary_taxonomy_switch_5,
                   healthcare_provider_primary_taxonomy_switch_6,
                   healthcare_provider_primary_taxonomy_switch_7,
                   healthcare_provider_primary_taxonomy_switch_8,
                   healthcare_provider_primary_taxonomy_switch_9,
                   healthcare_provider_primary_taxonomy_switch_10,
                   healthcare_provider_primary_taxonomy_switch_11,
                   healthcare_provider_primary_taxonomy_switch_12,
                   healthcare_provider_primary_taxonomy_switch_13,
                   healthcare_provider_primary_taxonomy_switch_14,
                   healthcare_provider_primary_taxonomy_switch_15]) AS sw
    FROM bronze.nppes_npidata
),
ic AS (
    SELECT DISTINCT npi FROM slots
    WHERE code = '207RI0011X' AND sw = 'Y' AND entity_type_code = '1'
      AND (nullif(d, '') IS NULL
           OR strptime(r, '%m/%d/%Y') >= strptime(d, '%m/%d/%Y'))
),
paid AS (
    SELECT DISTINCT covered_recipient_npi AS npi FROM bronze.op_general
    WHERE program_year = '2025' AND nullif(covered_recipient_npi, '') IS NOT NULL
)
SELECT (SELECT count(*) FROM ic) AS npis,
       (SELECT count(*) FROM ic WHERE npi IN (SELECT npi FROM paid)) AS paid;
