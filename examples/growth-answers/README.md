# Growth Answers

A runnable version of the essay's opening scene: it's Monday, 9:07am, and
someone asks **"which campaign drove repeat purchases this week, and what
should we change today?"** This demo shows Dashdown's answer to that — the
*ask → answer → action* inversion: a page is curated evidence, but the engine
underneath (connectors, the query library, the semantic layer, the `llm:`
gateway) can answer the question directly, and a trigger can ask it on your
behalf before anyone remembers to.

## What's here

- `data/*.csv` — six campaigns, ~310 orders, ~240 customers, deterministic and
  hand-seeded to tell one story (below).
- `semantic/growth.yml` — a boring-semantic-layer model over the
  orders/campaigns/customers join (`revenue`, `orders`, `avg_order_value`;
  `campaign`, `channel`, `city`, `order_date` dimensions) — the KPI row's
  revenue/order counters read straight from it.
- `queries/kpi/repeat_rate.sql`, `queries/kpi/weekly_repeat_trend.sql`,
  `queries/campaigns/performance.sql`, `queries/campaigns/repeat_purchasers.sql`
  — the shared query library the page, the header ask box, and the trigger all
  resolve by name. Repeat-purchase logic (an earlier order exists for that
  customer) needs a correlated subquery BSL can't express, so it lives here
  rather than in the semantic model.
- `pages/index.md` — the dashboard: KPI row, a campaign performance bar chart
  (`explain`-annotated), the weekly repeat-rate trend, the "who to call" table,
  a channel filter, and an authored `<Ask>`.
- `triggers/repeat-rate.yml` — watches `kpi.repeat_rate` on the shared
  streaming poll loop; ships `enabled: false` (see "Enable the trigger" below).

## The data story

**Summer Referral Push** (email, launched June 15) drives almost nothing but
repeat business: 28 orders in the last 30 days, 27 of them repeat (96%).
**Viral Reels Blast** (paid social, launched June 20) is the mirror image: 140
orders in the same window, essentially all first-time purchases, 0 repeat.
Both ramp hard through July, so the overall repeat-purchase rate — a steady
~25–30% baseline through May and early June — dips to **~21.6%** in the most
recent 7 days as Viral Reels Blast's first-purchase flood outpaces what
Summer Referral Push claws back; the "who to call" list for the week is,
unsurprisingly, almost entirely Summer Referral Push customers.

Run the numbers yourself:

```bash
uv run dashdown query "SELECT * FROM campaigns" -p examples/growth-answers
```

## 2-minute setup

```bash
# from the repo root
pip install -e .                       # or: uv sync
pip install 'dashdown-md[mistral]'     # optional — only needed for the AI surfaces
export MISTRAL_API_KEY=...             # optional — see "Without a key" below
uv run dashdown serve examples/growth-answers
```

Open <http://127.0.0.1:8000>.

## The demo script

1. **Open the page.** The KPI row, the campaign bar chart, the weekly trend
   line, and the repeat-purchasers table are all live queries — no key
   needed for any of this.
2. **Ask it.** Type into the header ask box (top of every page):

   > Which campaign drove repeat purchases this week?

   The engine resolves the question onto the query library (never raw SQL —
   `allow_sql: false` in `dashdown.yaml`), returns an answer with a chart,
   the backing rows, and a provenance line describing exactly which query
   answered it.
3. **Same question from the CLI:**

   ```bash
   uv run dashdown ask "Which campaign drove repeat purchases this week and what should we change today?" -p examples/growth-answers
   ```
4. **Click explain.** Hover the "Orders vs. repeat orders by campaign" chart
   and click the ✨ sparkle — the model annotates the bar it thinks matters
   most, generated on demand.
5. **The trigger.** `triggers/repeat-rate.yml` watches `kpi.repeat_rate` every
   60 seconds and would fire a webhook when it drops below 20 (the query is
   scaled 0-100, a percentage) — the seeded data sits at ~21.6, close enough
   that trimming a few of the recent `Summer Referral Push` rows out of
   `data/orders.csv` (or adding a burst of `Viral Reels Blast` rows) trips it.
   See **Enable the trigger** below.
6. **The ask log.** Every runtime question — resolution, query, answer —
   appends a line to `.dashdown/ask_log.jsonl` in this project directory. It's
   plain JSON lines, so it's itself queryable by Dashdown later.

## Without a key

No `MISTRAL_API_KEY`? The dashboard above works exactly the same — every
number, chart, and table is a plain query. `dashdown/llm.py::parse_llm_config`
degrades a broken/missing `llm:` block to *disabled* rather than failing the
server (the same policy `<Ask />` already relies on elsewhere in Dashdown), so
`dashdown serve` and `dashdown build` both run keyless. The header ask box,
`dashdown ask`, every chart's ✨ explain button, and the page's `<Ask>` block
just show a muted "no LLM provider configured" note instead.

## Enable the trigger

`triggers/repeat-rate.yml` ships with `enabled: false` and its webhook
pointing at `${DEMO_HOOK_URL}`. A **disabled** trigger's actions are not
built — and their `${VAR}` references not resolved — until it's enabled, so
the project loads cleanly with no environment set up at all. The moment you
flip `enabled: true`, the fail-hard checks run: an unset `DEMO_HOOK_URL`
then stops the server at load with a clear error rather than silently doing
nothing.

To point it at a real endpoint (e.g. <https://webhook.site> for a quick
look, or your own sink):

  ```yaml
  # triggers/repeat-rate.yml
  enabled: true
  actions:
    - type: webhook
      url: ${DEMO_HOOK_URL}   # export DEMO_HOOK_URL=https://... first
  ```

  ```bash
  export DEMO_HOOK_URL=https://webhook.site/your-unique-id
  uv run dashdown serve examples/growth-answers
  ```

  **Restart the server, don't hot-flip.** A running server's environment is
  frozen — an `export` in another shell is invisible to it, and if the
  dev-watcher reloads a trigger whose env var the server can't see, the
  reload fails with only a stderr log line while the old (disabled) config
  silently stays live. Set `DEMO_HOOK_URL`, flip `enabled: true`, then start
  a fresh `dashdown serve` in that same shell.

  With `interval: 60` and the seeded value at ~21.6 (below the 20 line only
  after you trim a few repeat rows — see "The trigger" above), the first fire
  should land within a minute of crossing the threshold, then again every
  `cooldown` (3600s) while it stays breached.
