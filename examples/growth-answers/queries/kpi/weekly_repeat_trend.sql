---
description: Repeat-purchase rate by week over the last 10 weeks — the trend behind the Monday KPI, so a dip in the latest week reads in context instead of in isolation. `repeat_rate` is 0-100 (a percentage), matching `format="percent"`.
---
WITH flagged AS (
    SELECT
        order_id,
        customer_id,
        order_date,
        EXISTS (
            SELECT 1 FROM orders o2
            WHERE o2.customer_id = orders.customer_id AND o2.order_date < orders.order_date
        ) AS is_repeat
    FROM orders
    WHERE order_date >= (SELECT MAX(order_date) FROM orders) - INTERVAL 69 DAY
)
SELECT
    DATE_TRUNC('week', order_date) AS week,
    COUNT(*) AS orders,
    ROUND(AVG(CASE WHEN is_repeat THEN 100.0 ELSE 0.0 END), 2) AS repeat_rate
FROM flagged
GROUP BY 1
ORDER BY 1
