-- Typed copy of the Physician Ownership or Investment Interest file, newest release per program year.
with latest as ({{ op_latest_release(source('bronze', 'op_ownership')) }})

select
    o.* exclude (_file_sha256, _run_id, _ingested_at)
    replace (
        cast(o.program_year as integer) as program_year,
        cast(o.record_id as bigint) as record_id,
        cast(o.physician_profile_id as bigint) as physician_profile_id,
        cast(o.applicable_manufacturer_or_applicable_gpo_making_payment_id as bigint)
            as applicable_manufacturer_or_applicable_gpo_making_payment_id,
        cast(o.total_amount_invested_usdollars as decimal(18, 2)) as total_amount_invested_usdollars,
        cast(o.value_of_interest as decimal(18, 2)) as value_of_interest,
        cast(strptime(o.payment_publication_date, '%m/%d/%Y') as date) as payment_publication_date
    )
from {{ source('bronze', 'op_ownership') }} o
join latest using (program_year, _release)
