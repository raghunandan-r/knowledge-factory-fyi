-- NPPES lists up to 15 taxonomy/license slots per NPI; one row per filled slot.
{% set slots = range(1, 16) %}

with src as (
    select *
    from {{ source('bronze', 'nppes_npidata') }}
    qualify row_number() over (partition by npi order by _ingested_at desc) = 1
)

{% for i in slots %}
select
    npi,
    {{ i }} as slot,
    healthcare_provider_taxonomy_code_{{ i }} as taxonomy_code,
    healthcare_provider_primary_taxonomy_switch_{{ i }} = 'Y' as is_primary,
    provider_license_number_{{ i }} as license_number,
    provider_license_number_state_code_{{ i }} as license_state,
    healthcare_provider_taxonomy_group_{{ i }} as taxonomy_group
from src
where healthcare_provider_taxonomy_code_{{ i }} is not null
{% if not loop.last %}union all{% endif %}
{% endfor %}
