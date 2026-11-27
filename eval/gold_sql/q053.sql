-- Transfers = sum of Number_of_Payments_Included, per physician x making-payment ID.
SELECT count(DISTINCT covered_recipient_profile_id) AS count
FROM (
    SELECT covered_recipient_profile_id
    FROM bronze.op_general
    WHERE program_year = '2024' AND covered_recipient_type = 'Covered Recipient Physician'
      AND nature_of_payment_or_transfer_of_value = 'Food and Beverage'
    GROUP BY covered_recipient_profile_id,
             applicable_manufacturer_or_applicable_gpo_making_payment_id
    HAVING sum(number_of_payments_included_in_total_amount::BIGINT) >= 50
);
