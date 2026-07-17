---
description: >-
  Share of orders in the last 7 days placed by a repeat customer (an earlier
  order exists for that customer) — the single number the Monday-morning
  question turns on. Scale is 0-100 (a percentage, not a 0-1 fraction) so
  format="percent" renders it correctly and triggers/repeat-rate.yml's
  condition reads in the same units.
---
WITH recent AS (
    SELECT order_id, customer_id, order_date
    FROM orders
    WHERE order_date >= (SELECT MAX(order_date) FROM orders) - INTERVAL 6 DAY
),
flagged AS (
    SELECT
        r.order_id,
        EXISTS (
            SELECT 1 FROM orders o2
            WHERE o2.customer_id = r.customer_id AND o2.order_date < r.order_date
        ) AS is_repeat
    FROM recent r
)
SELECT
    ROUND(AVG(CASE WHEN is_repeat THEN 100.0 ELSE 0.0 END), 2) AS repeat_rate
FROM flagged
