from __future__ import annotations

from dashdown.components.base import Component, register_component
from dashdown.components.builtin._util import attr_str, ref_str, resolve_semantic_query
from dashdown.components.builtin.line_chart import _chart_html, _chart_placeholder

_ROLES = ("open", "high", "low", "close")


@register_component("CandlestickChart")
class CandlestickChart(Component):
    """Candlestick (OHLC) chart for price/range series.

    Two input modes, like every chart:

    * ``data={prices} x="day" open="open" high="high" low="low" close="close"`` —
      the four price columns of a query.
    * **semantic** — ``by={stock.day} open={stock.open} high={stock.high}
      low={stock.low} close={stock.close}``: each role is a **measure** of one
      semantic model, combined into a single synthetic query grouped by ``by``.
      (This is how a BI tool binds an OHLC visual to a semantic model — four
      measures grouped by a date dimension; the model author defines `open` as a
      first()/`close` as a last()/`high` as a max()/`low` as a min() measure.)

    Bullish candles (close ≥ open) render green, bearish red.
    """

    def render(self, attrs, ctx, inner: str | None = None):
        if attrs.get("data") is None:
            # --- semantic mode: open/high/low/close are measure refs on one model ---
            refs = {k: ref_str(attrs, k) for k in _ROLES}
            missing = [k for k, v in refs.items() if not v]
            if missing:
                raise ValueError(
                    "CandlestickChart requires measure (or `data={query}` column) "
                    "attribute(s): " + ", ".join(missing)
                )
            sem = resolve_semantic_query(
                attrs, ctx,
                measures=[refs[k] for k in _ROLES],
                by_ref=ref_str(attrs, "by") or ref_str(attrs, "x"),
            )
            cols = {k: sem["columns"][refs[k]] for k in _ROLES}
            # OHLC share one price scale → default the value-axis format to `close`.
            sem_format = sem["formats"].get(cols["close"]) or None
            return _chart_placeholder(
                "candlestick", attrs, ctx,
                name=sem["query_name"], x=sem["by"], y=None,
                extra=cols, sem_format=sem_format,
            )
        # --- data={query} mode: open/high/low/close are column names ---
        cols = {k: attr_str(attrs, k) for k in _ROLES}
        missing = [k for k, v in cols.items() if not v]
        if missing:
            raise ValueError(
                "CandlestickChart requires column attribute(s): " + ", ".join(missing)
            )
        return _chart_html("candlestick", attrs, ctx, require_y=False, extra=cols)
