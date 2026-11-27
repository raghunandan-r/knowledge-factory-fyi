-- Typed Part D Prescribers by Provider and Drug, newest release per data year.
-- Blanks in the source are suppressed values (1-10) and stay NULL.
with latest as (
    select _data_year, max(_release) as _release
    from {{ source('bronze', 'partd_prescriber_drug') }}
    group by _data_year
)

select
    cast(d._data_year as integer) as data_year,
    d.prscrbr_npi,
    d.prscrbr_last_org_name,
    d.prscrbr_first_name,
    d.prscrbr_city,
    d.prscrbr_state_abrvtn,
    d.prscrbr_state_fips,
    d.prscrbr_type,
    d.prscrbr_type_src,
    d.brnd_name,
    d.gnrc_name,
    cast(d.tot_clms as bigint) as tot_clms,
    cast(d.tot_30day_fills as decimal(18, 1)) as tot_30day_fills,
    cast(d.tot_day_suply as bigint) as tot_day_suply,
    cast(d.tot_drug_cst as decimal(18, 2)) as tot_drug_cst,
    cast(d.tot_benes as bigint) as tot_benes,
    d.ge65_sprsn_flag,
    cast(d.ge65_tot_clms as bigint) as ge65_tot_clms,
    cast(d.ge65_tot_30day_fills as decimal(18, 1)) as ge65_tot_30day_fills,
    cast(d.ge65_tot_drug_cst as decimal(18, 2)) as ge65_tot_drug_cst,
    cast(d.ge65_tot_day_suply as bigint) as ge65_tot_day_suply,
    d.ge65_bene_sprsn_flag,
    cast(d.ge65_tot_benes as bigint) as ge65_tot_benes,
    d._release,
    d._source_file
from {{ source('bronze', 'partd_prescriber_drug') }} d
join latest using (_data_year, _release)
