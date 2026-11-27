-- Every taxonomy an NPI lists in NPPES (up to 15), with the license that goes with it.
select
    npi || '-' || slot as provider_taxonomy_id,
    npi,
    slot,
    taxonomy_code,
    is_primary,
    license_number,
    license_state
from {{ ref('nppes_provider_taxonomies') }}
