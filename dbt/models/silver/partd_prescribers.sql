-- Typed Part D Prescribers by Provider (one row per NPI per year), newest release per data
-- year. Unlike the by-drug file, these totals include drugs with fewer than 11 claims.
with latest as (
    select _data_year, max(_release) as _release
    from {{ source('bronze', 'partd_prescriber') }}
    group by _data_year
)

select
    cast(p._data_year as integer) as data_year,
    p.prscrbr_npi,
    p.prscrbr_last_org_name,
    p.prscrbr_first_name,
    p.prscrbr_mi,
    p.prscrbr_crdntls,
    p.prscrbr_ent_cd,
    p.prscrbr_city,
    p.prscrbr_state_abrvtn,
    p.prscrbr_state_fips,
    p.prscrbr_zip5,
    p.prscrbr_ruca,
    p.prscrbr_ruca_desc,
    p.prscrbr_cntry,
    p.prscrbr_type,
    p.prscrbr_type_src,
    cast(p.tot_clms as bigint) as tot_clms,
    cast(p.tot_30day_fills as decimal(18, 1)) as tot_30day_fills,
    cast(p.tot_drug_cst as decimal(18, 2)) as tot_drug_cst,
    cast(p.tot_day_suply as bigint) as tot_day_suply,
    cast(p.tot_benes as bigint) as tot_benes,
    p.brnd_sprsn_flag,
    cast(p.brnd_tot_clms as bigint) as brnd_tot_clms,
    cast(p.brnd_tot_drug_cst as decimal(18, 2)) as brnd_tot_drug_cst,
    p.gnrc_sprsn_flag,
    cast(p.gnrc_tot_clms as bigint) as gnrc_tot_clms,
    cast(p.gnrc_tot_drug_cst as decimal(18, 2)) as gnrc_tot_drug_cst,
    cast(p.opioid_tot_clms as bigint) as opioid_tot_clms,
    cast(p.opioid_tot_drug_cst as decimal(18, 2)) as opioid_tot_drug_cst,
    cast(p.opioid_tot_benes as bigint) as opioid_tot_benes,
    cast(p.opioid_prscrbr_rate as double) as opioid_prscrbr_rate,
    cast(p.opioid_la_tot_clms as bigint) as opioid_la_tot_clms,
    cast(p.antbtc_tot_clms as bigint) as antbtc_tot_clms,
    cast(p.antbtc_tot_drug_cst as decimal(18, 2)) as antbtc_tot_drug_cst,
    cast(p.bene_avg_age as double) as bene_avg_age,
    cast(p.bene_avg_risk_scre as double) as bene_avg_risk_scre,
    p._release,
    p._source_file
from {{ source('bronze', 'partd_prescriber') }} p
join latest using (_data_year, _release)
