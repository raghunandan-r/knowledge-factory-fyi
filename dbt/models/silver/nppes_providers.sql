-- One row per NPI from the NPPES main file. Deactivated NPIs stay in the file with
-- (often) only the NPI and deactivation date populated.
{% set taxonomy_slots = range(1, 16) %}

with src as (
    select *
    from {{ source('bronze', 'nppes_npidata') }}
    qualify row_number() over (partition by npi order by _ingested_at desc) = 1
)

select
    npi,
    cast(entity_type_code as integer) as entity_type_code,
    case entity_type_code when '1' then 'Individual' when '2' then 'Organization' end as entity_type,
    replacement_npi,
    provider_organization_name_legal_business_name as organization_name,
    provider_last_name_legal_name as last_name,
    provider_first_name as first_name,
    provider_middle_name as middle_name,
    provider_name_prefix_text as name_prefix,
    provider_name_suffix_text as name_suffix,
    provider_credential_text as credential,
    provider_other_organization_name as other_organization_name,
    provider_other_last_name as other_last_name,
    provider_other_first_name as other_first_name,
    provider_sex_code as sex_code,
    provider_first_line_business_practice_location_address as practice_address_line1,
    provider_second_line_business_practice_location_address as practice_address_line2,
    provider_business_practice_location_address_city_name as practice_city,
    provider_business_practice_location_address_state_name as practice_state,
    provider_business_practice_location_address_postal_code as practice_postal_code,
    provider_business_practice_location_address_country_code_if_outside_us as practice_country_code,
    provider_business_practice_location_address_telephone_number as practice_phone,
    provider_business_mailing_address_city_name as mailing_city,
    provider_business_mailing_address_state_name as mailing_state,
    provider_business_mailing_address_postal_code as mailing_postal_code,
    cast(strptime(provider_enumeration_date, '%m/%d/%Y') as date) as enumeration_date,
    cast(strptime(last_update_date, '%m/%d/%Y') as date) as last_update_date,
    cast(strptime(npi_deactivation_date, '%m/%d/%Y') as date) as deactivation_date,
    cast(strptime(npi_reactivation_date, '%m/%d/%Y') as date) as reactivation_date,
    cast(strptime(certification_date, '%m/%d/%Y') as date) as certification_date,
    is_sole_proprietor,
    is_organization_subpart,
    parent_organization_lbn,
    coalesce(
        {%- for i in taxonomy_slots %}
        case when healthcare_provider_primary_taxonomy_switch_{{ i }} = 'Y'
            then healthcare_provider_taxonomy_code_{{ i }} end,
        {%- endfor %}
        healthcare_provider_taxonomy_code_1
    ) as primary_taxonomy_code,
    _file as _source_file
from src
