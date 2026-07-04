<!-- AUTO-GENERATED from docs/pages/ by tooling/gen-agent-docs.py — do not edit. -->
<!-- Topic: realtime. Regenerate with: python tooling/gen-agent-docs.py -->

<!-- source: docs/pages/realtime.md -->

# Real-time data

A query opts into **live streaming** with the `live` attribute:
`interval=N` sets the poll cadence in seconds (default `5`, floored to `1`). The
component opens a WebSocket to `/_dashdown/ws/data/{query}` and repaints whenever
the result changes — no page reload, no extra wiring.

The demos below use DuckDB's `random()` / `now()` so the result changes on every
poll and the updates are visible offline — watch them tick. A real dashboard would
point a `live` query at a table that actually mutates (order counts, error rates,
queue depth).

:::tip
Streaming is **additive**: a query without `live` behaves exactly as before. And
in a `dashdown build` static export `live` is a no-op — the page falls back to the
one-shot snapshot, so these examples still render (just frozen) with no server.
:::

## Live KPI

A counter that re-reads its query every 2 seconds.

<Counter data={downloads_now} column="downloads_now" label="Downloads (live)" format="number" />

## Live chart

Downloads by channel, re-sampled every 2 seconds.

<BarChart data={channel_live} x="channel" y="downloads" title="Live downloads by channel" format="number" />

## Live table

A "recent activity" feed — five daily rows re-shuffled every 3 seconds.

<Table data={recent_activity} title="Recent activity (live)" />

## Pointing `live` at real data

Nothing about the component changes for genuinely live sources — only the query.
Because a `live` query re-runs on every poll, a query that reads an external
endpoint re-fetches it each tick. DuckDB (which backs the `csv`/`duckdb`
connectors) can read a remote JSON API directly, so no new connector is needed:

````markdown
```sql btc_price live interval=5
SELECT CAST(data.amount AS DOUBLE) AS usd
FROM read_json_auto('https://api.coinbase.com/v2/prices/BTC-USD/spot')
```

<Counter data={btc_price} column="usd" format="currency" decimals=2 label="BTC / USD (live)" />
````

If a poll fails (rate limit, network blip) the server retries on the next tick and
the component keeps showing its last good value — live queries are **self-healing**,
so an occasional upstream hiccup isn't fatal.

:::note
Live streaming needs a running server (a static export snapshots data at build
time, so live queries fall back to that snapshot). See the framework README's
"Real-time" section for the fan-out and caching details.
:::
