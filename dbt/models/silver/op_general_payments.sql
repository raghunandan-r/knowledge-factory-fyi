-- Typed copy of the General Payments file, newest release per program year.
-- Column names are CMS's field names (snake_case), so they match the data dictionary.
with latest as ({{ op_latest_release(source('bronze', 'op_general')) }})

select
    g.* exclude (_file_sha256, _run_id, _ingested_at)
    replace (
        cast(g.program_year as integer) as program_year,
        cast(g.record_id as bigint) as record_id,
        cast(g.covered_recipient_profile_id as bigint) as covered_recipient_profile_id,
        cast(g.teaching_hospital_id as bigint) as teaching_hospital_id,
        cast(g.applicable_manufacturer_or_applicable_gpo_making_payment_id as bigint)
            as applicable_manufacturer_or_applicable_gpo_making_payment_id,
        cast(g.total_amount_of_payment_usdollars as decimal(18, 2)) as total_amount_of_payment_usdollars,
        cast(g.number_of_payments_included_in_total_amount as integer)
            as number_of_payments_included_in_total_amount,
        cast(strptime(g.date_of_payment, '%m/%d/%Y') as date) as date_of_payment,
        cast(strptime(g.payment_publication_date, '%m/%d/%Y') as date) as payment_publication_date
    )
from {{ source('bronze', 'op_general') }} g
join latest using (program_year, _release)
