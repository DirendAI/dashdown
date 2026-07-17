---
description: Per-campaign order volume, revenue, and repeat-purchase share over the last 30 days — which campaign is actually driving repeat business, not just first clicks. `repeat_share` is 0-100 (a percentage), matching `format="percent"`.
---
WITH recent AS (
    SELECT order_id, customer_id, campaign_id, order_date, amount
    FROM orders
    WHERE order_date >= (SELECT MAX(order_date) FROM orders) - INTERVAL 29 DAY
),
flagged AS (
    SELECT
        r.*,
        EXISTS (
            SELECT 1 FROM orders o2
            WHERE o2.customer_id = r.customer_id AND o2.order_date < r.order_date
        ) AS is_repeat
    FROM recent r
)
SELECT
    c.name AS campaign,
    c.channel AS channel,
    COUNT(*) AS orders,
    ROUND(SUM(f.amount), 2) AS revenue,
    SUM(CASE WHEN f.is_repeat THEN 1 ELSE 0 END) AS repeat_orders,
    ROUND(AVG(CASE WHEN f.is_repeat THEN 100.0 ELSE 0.0 END), 2) AS repeat_share
FROM flagged f
JOIN campaigns c ON c.campaign_id = f.campaign_id
WHERE ('${channel}' = '' OR c.channel = '${channel}')
GROUP BY c.name, c.channel
ORDER BY repeat_orders DESC
