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
