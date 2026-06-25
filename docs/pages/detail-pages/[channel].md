---
title: Channel detail
# `dashdown build` pre-renders one static page per row of this query (the
# `getStaticPaths` pattern): each `channel` value becomes `/detail-pages/<channel>`.
# Without this block the dynamic page is skipped by the build (live server only).
static_paths:
  connector: main
  query: SELECT DISTINCT channel FROM downloads ORDER BY channel
---

# Channel detail

:::query name=channel_summary connector=main
SELECT channel,
       SUM(downloads) AS downloads,
       COUNT(DISTINCT month) AS months
FROM downloads
WHERE channel = '${channel}'
GROUP BY channel
:::

:::query name=channel_months connector=main
SELECT month, downloads
FROM downloads
WHERE channel = '${channel}'
ORDER BY month
:::

You are viewing the **<Value data={channel_summary} column="channel" />** channel.
This page is the single template `pages/detail-pages/[channel].md`; the `${channel}`
in its queries is the URL segment, so the same file renders `pip`, `docker`, and
`source`.

<Grid cols=2>
  <Counter data={channel_summary} column="downloads" label="Total downloads" format="number" />
  <Counter data={channel_summary} column="months" label="Months tracked" />
</Grid>

<LineChart data={channel_months} x="month" y="downloads" title="Monthly downloads" />

<Table data={channel_months} format="downloads=number" />

[← Back to all channels](/detail-pages)
