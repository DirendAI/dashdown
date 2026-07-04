"""A Python query: monthly downloads with a next-month forecast.

A forecast is awkward in plain SQL but trivial in pandas — this is exactly what
Python queries are for. The function pulls the same monthly totals the SQL
library exposes, then appends a 3-month moving-average forecast row. It's
referenced by name (`data={downloads_forecast}`) from `pages/python-queries.md`,
no differently than a `.sql` query.
"""
from __future__ import annotations

from dashdown import query


@query(cache_ttl=300, description="Monthly downloads + forecast")
def downloads_forecast(params, connect):
    # connect() runs SQL on any project connector and hands back a result with
    # .to_pandas() / .to_arrow(). No params here, so plain SQL is fine.
    df = connect(
        "demo",
        "SELECT month, SUM(downloads) AS downloads "
        "FROM downloads GROUP BY month ORDER BY month",
    ).to_pandas()

    # A simple 3-month moving-average forecast for the month after the last one.
    last = str(df["month"].iloc[-1])
    year, mon = (int(p) for p in last.split("-"))
    nxt = f"{year + (mon // 12)}-{(mon % 12) + 1:02d}"
    forecast = round(float(df["downloads"].tail(3).mean()), 0)

    rows = df.assign(kind="actual").to_dict("records")
    rows.append({"month": nxt, "downloads": forecast, "kind": "forecast"})
    return rows  # a list of dicts is one of the accepted return shapes
