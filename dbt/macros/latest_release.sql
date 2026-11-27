{#
  Bronze keeps every release as its own partition (_release). Each Open Payments
  release republishes whole program years, so silver reads, per program year, only
  the newest release that contains it. Release ids look like P06302026_06032026
  (publication date MMDDYYYY first), so they are compared as dates, not text.
#}
{% macro op_latest_release(relation) -%}
    select program_year, arg_max(_release, strptime(substr(_release, 2, 8), '%m%d%Y')) as _release
    from {{ relation }}
    group by program_year
{%- endmacro %}
