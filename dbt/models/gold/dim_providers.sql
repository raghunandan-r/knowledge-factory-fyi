-- One row per NPI in NPPES: the lookup for any NPI found in Open Payments or Part D.
select
    npi,
    entity_type,
    case
        when entity_type = 'Organization' then organization_name
        else concat_ws(' ', first_name, middle_name, last_name, name_suffix)
    end as provider_name,
    first_name,
    middle_name,
    last_name,
    organization_name,
    credential,
    sex_code,
    primary_taxonomy_code,
    practice_address_line1,
    practice_city,
    practice_state,
    left(practice_postal_code, 5) as practice_zip5,
    practice_country_code,
    enumeration_date,
    last_update_date,
    deactivation_date,
    reactivation_date,
    case
        when deactivation_date is not null
            and (reactivation_date is null or reactivation_date < deactivation_date)
            then 'Deactivated'
        else 'Active'
    end as npi_status,
    replacement_npi
from {{ ref('nppes_providers') }}
