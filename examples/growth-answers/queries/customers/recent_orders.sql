---
description: >-
  Detail list of the most recent orders, newest first (top 50) — order date,
  customer name, city, amount, and the campaign/channel credited. Use for
  "latest / last N orders" or "which customers ordered recently" style list
  questions; this is a row listing, not an aggregate.
---
SELECT
  o.order_date,
  c.name  AS customer,
  c.city,
  o.amount,
  ca.name AS campaign,
  ca.channel
FROM orders o
JOIN customers c  ON o.customer_id = c.customer_id
JOIN campaigns ca ON o.campaign_id = ca.campaign_id
ORDER BY o.order_date DESC, o.order_id DESC
LIMIT 50
