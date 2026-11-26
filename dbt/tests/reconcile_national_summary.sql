-- The layer must reproduce CMS's national summary for PY2023-2025, General and Research,
-- Physician and Non-Physician Practitioner: record counts exactly, dollars within $1.
-- CMS's "Total Payment Count" is the number of published records for that recipient
-- type (not the sum of Number_of_Payments_Included_in_Total_Amount).
-- Returns one row per failing cell; a passing test returns nothing.
{{ config(tags=['reconcile']) }}

with summary_release as (
    select arg_max(_release, strptime(substr(_release, 2, 8), '%m%d%Y')) as _release
    from {{ source('bronze', 'op_national_summary') }}
),

cms as (
    select
        cast(s.program_year as integer) as program_year,
        s.payment_type,
        s.recipient_type,
        case when s.recipient_type = 'Covered Recipient Physician'
            then cast(s.total_payment_count_physician as bigint)
            else cast(s.total_payment_count_non_physician_practitioner as bigint)
        end as cms_count,
        case when s.recipient_type = 'Covered Recipient Physician'
            then cast(s.total_payment_amount_physician as decimal(18, 2))
            else cast(s.total_payment_amount_non_physician_practitioner as decimal(18, 2))
        end as cms_amount
    from {{ source('bronze', 'op_national_summary') }} s
    join summary_release using (_release)
    where s.metric_level = 'National'
      and s.program_year in ('2023', '2024', '2025')
),

layer as (
    select program_year, 'General' as payment_type, recipient_type,
        count(*) as layer_count, sum(amount_usd) as layer_amount
    from {{ ref('fct_general_payments') }}
    group by all
    union all
    select program_year, 'Research' as payment_type, recipient_type,
        count(*) as layer_count, sum(amount_usd) as layer_amount
    from {{ ref('fct_research_payments') }}
    group by all
),

expected_cells as (
    select y.program_year, p.payment_type, r.recipient_type
    from (values (2023), (2024), (2025)) y(program_year)
    cross join (values ('General'), ('Research')) p(payment_type)
    cross join (values ('Covered Recipient Physician'),
                       ('Covered Recipient Non-Physician Practitioner')) r(recipient_type)
)

select
    e.program_year,
    e.payment_type,
    e.recipient_type,
    cms.cms_count,
    layer.layer_count,
    cms.cms_amount,
    layer.layer_amount,
    layer.layer_amount - cms.cms_amount as amount_delta
from expected_cells e
left join cms using (program_year, payment_type, recipient_type)
left join layer using (program_year, payment_type, recipient_type)
where cms.cms_count is null
   or layer.layer_count is distinct from cms.cms_count
   or abs(coalesce(layer.layer_amount, 0) - cms.cms_amount) > 1
