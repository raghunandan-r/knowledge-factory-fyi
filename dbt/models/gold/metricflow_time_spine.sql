-- Daily calendar MetricFlow needs to place measures on metric_time.
select cast(range as date) as date_day
from range(date '2013-01-01', date '2031-01-01', interval 1 day)
