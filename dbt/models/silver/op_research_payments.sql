-- Typed copy of the Research Payments file, newest release per program year.
with latest as ({{ op_latest_release(source('bronze', 'op_research')) }})

select
    r.* exclude (_file_sha256, _run_id, _ingested_at)
    replace (
        cast(r.program_year as integer) as program_year,
        cast(r.record_id as bigint) as record_id,
        cast(r.covered_recipient_profile_id as bigint) as covered_recipient_profile_id,
        cast(r.teaching_hospital_id as bigint) as teaching_hospital_id,
        cast(r.principal_investigator_1_profile_id as bigint) as principal_investigator_1_profile_id,
        cast(r.applicable_manufacturer_or_applicable_gpo_making_payment_id as bigint)
            as applicable_manufacturer_or_applicable_gpo_making_payment_id,
        cast(r.total_amount_of_payment_usdollars as decimal(18, 2)) as total_amount_of_payment_usdollars,
        cast(strptime(r.date_of_payment, '%m/%d/%Y') as date) as date_of_payment,
        cast(strptime(r.payment_publication_date, '%m/%d/%Y') as date) as payment_publication_date
    )
from {{ source('bronze', 'op_research') }} r
join latest using (program_year, _release)
