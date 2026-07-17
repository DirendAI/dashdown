---
description: Customers who made a repeat purchase in the last 7 days, with the campaign credited and the order amount — the "who to call" list behind the Monday question.
---
WITH recent AS (
    SELECT order_id, customer_id, campaign_id, order_date, amount
    FROM orders
    WHERE order_date >= (SELECT MAX(order_date) FROM orders) - INTERVAL 6 DAY
)
SELECT
    cu.name AS customer,
    cu.city AS city,
    c.name AS campaign,
    c.channel AS channel,
    r.order_date AS order_date,
    r.amount AS amount
FROM recent r
JOIN customers cu ON cu.customer_id = r.customer_id
JOIN campaigns c ON c.campaign_id = r.campaign_id
WHERE EXISTS (
    SELECT 1 FROM orders o2
    WHERE o2.customer_id = r.customer_id AND o2.order_date < r.order_date
)
ORDER BY r.order_date DESC, r.amount DESC
