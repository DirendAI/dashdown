// Per-chart PNG download — the ⬇ button beside the fullscreen ⛶ on every
// ECharts card (emitted by line_chart.py::_chart_card). One delegated
// listener; reads the live ECharts instance off the card at click time, so a
// theme toggle's dispose/re-init never leaves a stale handler. Works in
// static exports and embeds for free (pure client-side, no server endpoint).

"use strict";

/** Sanitize a chart title / query name into a safe download filename stem. */
function pngFilename(config) {
  const stem = String(config.title || config.query_name || "chart")
    .replace(/[^\w.-]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return `${stem || "chart"}.png`;
}

export function initChartPng() {
  document.addEventListener("click", (e) => {
    const btn = e.target && e.target.closest && e.target.closest(".dashdown-chart-png-btn");
    if (!btn) return;
    const card = btn.closest(".dashdown-chart");
    const inst = card && card._echarts_instance;
    if (!inst) return;
    const config = card._chartConfig || {};
    // A solid theme-matching background: the canvas itself is transparent, and
    // a transparent PNG pasted into chat/docs is unreadable in the other theme.
    const bg = getComputedStyle(card).backgroundColor || "#ffffff";
    let url;
    try {
      url = inst.getDataURL({ type: "png", pixelRatio: 2, backgroundColor: bg });
    } catch (err) {
      console.error("Chart PNG export failed:", err);
      return;
    }
    const a = document.createElement("a");
    a.href = url;
    a.download = pngFilename(config);
    document.body.appendChild(a);
    a.click();
    a.remove();
  });
}
