select * from (values
    (1, 2024, date '2024-01-15', 'Physician', 100.00::decimal(18, 2)),
    (2, 2024, date '2024-02-20', 'Physician', 250.00::decimal(18, 2)),
    (3, 2024, date '2024-02-21', 'Nurse Practitioner', 50.00::decimal(18, 2)),
    (4, 2025, date '2025-03-01', 'Physician', 1000.00::decimal(18, 2))
) as t(payment_id, program_year, paid_date, recipient_type, amount_usd)
