---
title: Growth Answers
description: Monday 9:07 — which campaign drove repeat purchases this week?
---

# Growth Answers

It's Monday, 9:07am. Before the standup, someone asks: **which campaign drove
repeat purchases this week, and what should we change today?** This dashboard
is that question made runnable — the KPIs and charts below are curated
evidence, the same query library and semantic model the header **ask box**
and `dashdown ask` draw on to answer it directly, and the trigger in
`triggers/repeat-rate.yml` watches the same number so nobody has to remember
to ask.

<Dropdown name="channel" data={campaigns.performance} column="channel" label="Channel" bar />

<Grid cols="3">
  <Value data={kpi.repeat_rate} column="repeat_rate" format="percent" label="Repeat-purchase rate (last 7 days)" />
  <Value metric={orders.revenue} format="currency" label="All-time revenue" />
  <Value metric={orders.orders} format="number" label="All-time orders" />
</Grid>

## Which campaign is actually driving repeat business?

`campaigns.performance` (last 30 days) pairs total orders against **repeat**
orders per campaign — the flashiest top-line number and the one that actually
matters rarely belong to the same bar:

<BarChart data={campaigns.performance} x="campaign" y="orders,repeat_orders"
          title="Orders vs. repeat orders by campaign (last 30 days)" explain />

**Summer Referral Push** (email) is the answer: it drives the fewest total
orders of any active campaign but very nearly all of them are repeat
purchases. **Viral Reels Blast** (paid social) is the mirror image — the most
orders by far, essentially none of them repeat. Same channel family
(`paid_social`) as Spring Kickoff, very different job: one campaign brings
people in the door, the other brings them back.

## Is the dip real?

<LineChart data={kpi.weekly_repeat_trend} x="week" y="repeat_rate"
           format="percent" title="Weekly repeat-purchase rate (10 weeks)" explain />

The rate held a steady ~25-30% baseline, cratered when Viral Reels Blast's
first-purchase flood launched in late June, and Summer Referral Push has only
partly clawed it back since. This week still sits a little below the
long-run baseline — not a crisis, but worth a look before it becomes one.

## Who to call

Everyone who made a repeat purchase in the last 7 days, and the campaign
credited for bringing them back yes:

<Table data={campaigns.repeat_purchasers} title="Repeat purchasers, last 7 days"
       format="amount=currency, order_date=date" />

## Ask it directly

<Ask data={kpi.repeat_rate,campaigns.performance,kpi.weekly_repeat_trend}
     ask="Which campaign drove repeat purchases this week, and what should we change today?" />

:::note AI features need an LLM key
The dashboard above needs no API key. The header ask box, `dashdown ask`, the
✨ explain buttons on the charts, and the `<Ask>` block just above all need an
`llm:` provider — set `MISTRAL_API_KEY` and reload (see the README). Until
then each shows a muted "no LLM provider configured" note; nothing else on
this page is affected.
:::

<!-- dashdown:keep id=c5f6f680 kind=query · named query 'campaigns.performance' · 2026-07-19 -->
## per-campaign order volume, revenue, and repeat-purchase share over the last 30 days — which campaign is actually driving repeat business, not just first clicks
<!-- kept from an ask answer · named query 'campaigns.performance' · 2026-07-19 -->
<BarChart data={campaigns.performance} x="campaign" y="orders" title="per-campaign order volume, revenue, and repeat-purchase share over the last 30 days — which campaign is actually driving repeat business, not just first clicks" />
<Table data={campaigns.performance} />
<Ask data={campaigns.performance} ask="per-campaign order volume, revenue, and repeat-purchase share over the last 30 days — which campaign is actually driving repeat business, not just first clicks" />
<!-- /dashdown:keep id=c5f6f680 -->

<!-- dashdown:keep id=63de5810 kind=query · named query 'campaigns.repeat_purchasers' · 2026-07-19 -->
## customers who made a repeat purchase in the last 7 days, with the campaign credited and the order amount — the "who to call" list behind the Monday question
<!-- kept from an ask answer · named query 'campaigns.repeat_purchasers' · 2026-07-19 -->
<LineChart data={campaigns.repeat_purchasers} x="order_date" y="amount" title="customers who made a repeat purchase in the last 7 days, with the campaign credited and the order amount — the &quot;who to call&quot; list behind the Monday question" />
<Ask data={campaigns.repeat_purchasers} ask="customers who made a repeat purchase in the last 7 days, with the campaign credited and the order amount — the &quot;who to call&quot; list behind the Monday question" />
<!-- /dashdown:keep id=63de5810 -->
<!-- dashdown:keep id=9ff3ceb4 kind=semantic · semantic: orders.revenue by city · 2026-07-19 -->
## City Share
<!-- kept from an ask answer · semantic: orders.revenue by city · 2026-07-19 -->
<PieChart metric={orders.revenue} by={orders.city} title="maybe pie chart" />
<Ask inline metric={orders.revenue} by={orders.city} ask="Can you tell me which city has most potential or which one the least?" />
<!-- /dashdown:keep id=9ff3ceb4 -->

<!-- dashdown:keep id=ec5dd019 kind=query · named query 'campaigns.performance' · 2026-07-19 -->
## Per campaign
<!-- kept from an ask answer · named query 'campaigns.performance' · 2026-07-19 -->
<BarChart data={campaigns.performance} x="campaign" y="orders" title="per-campaign order volume, revenue, and repeat-purchase share over the last 30 days — which campaign is actually driving repeat business, not just first clicks" />
<Ask inline data={campaigns.performance} ask="Which campaing has the most potential? Also annotate it." />
<!-- /dashdown:keep id=ec5dd019 -->

<!-- dashdown:keep id=d1d0efc1 kind=semantic · semantic: orders.revenue by channel · 2026-07-19 -->
## can you show me funnel chart of the revenue by channel
<!-- kept from an ask answer · semantic: orders.revenue by channel · 2026-07-19 -->
<Table metric={orders.revenue} by={orders.channel} />
<Ask metric={orders.revenue} by={orders.channel} ask="can you show me funnel chart of the revenue by channel" />
<!-- /dashdown:keep id=d1d0efc1 -->

<!-- dashdown:keep id=bb82b4e4 kind=semantic · semantic: orders.revenue by channel · 2026-07-19 -->
## Revenue by channel
<!-- kept from an ask answer · asked: “can you show me funnel chart of the revenue by channel” · semantic: orders.revenue by channel · 2026-07-19 -->
<FunnelChart metric={orders.revenue} by={orders.channel} title="Revenue by channel" />
<Ask metric={orders.revenue} by={orders.channel} ask="Revenue by channel" />
<!-- /dashdown:keep id=bb82b4e4 -->

<!-- dashdown:keep id=f967402a kind=semantic · semantic: orders.revenue · 2026-07-19 -->
## All-time revenue
<!-- kept from an ask answer · asked: “what about all time” · semantic: orders.revenue · 2026-07-19 -->
<Counter metric={orders.revenue} />
<Ask metric={orders.revenue} ask="All-time revenue" />
<!-- /dashdown:keep id=f967402a -->

<!-- dashdown:keep id=5b45151f kind=semantic · semantic: orders.n by order_date per channel (week) · 2026-07-19 -->
## Order channel counts
<!-- kept from an ask answer · asked: “can you show me themeriver chart of order channel counts” · semantic: orders.n by order_date per channel (week) · 2026-07-19 -->
<ThemeRiver metric={orders.n} by={orders.order_date} series={orders.channel} grain="week" title="Order channel counts" />
<Ask inline metric={orders.n} by={orders.order_date} series={orders.channel} ask="tell a bit more about this data" />
<!-- /dashdown:keep id=5b45151f -->

<!-- dashdown:keep id=a5a72719 kind=semantic · semantic: orders.orders · 2026-07-19 · filters not carried over -->
## Orders this month
<!-- kept from an ask answer · asked: “can you show counter of orders this month” · semantic: orders.orders · 2026-07-19 · filters not carried over -->
<Counter metric={orders.orders} label="Orders" />
<Ask metric={orders.orders} ask="Orders this month" />
<!-- /dashdown:keep id=a5a72719 -->
