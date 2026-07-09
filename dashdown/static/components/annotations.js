// Dashdown Chart Annotations
// Client half of the `explain` chart-annotation feature (see
// dashdown/chart_annotations.py for the server half: vocabulary, validation,
// ref chips). The explain payload carries server-validated annotation objects;
// this module translates them into ECharts markLine/markPoint/markArea and
// hangs them onto the chart's EXISTING series — never a new series, which
// would break faceted-pie index patching, the palette-by-index gradient pass,
// and the legend.
//
// The translator runs inside buildChartOption (the single updateChart funnel),
// so annotations automatically survive filter refetches, live WS pushes, theme
// re-inits, and fullscreen (which re-renders through the same funnel with the
// same config object) — and they paint into PDF/screenshot canvases when the
// panel is open.
//
// Defensive posture: every mark is re-validated against the CURRENT records at
// option-build time. A category that vanished after a filter change raced the
// explain response simply doesn't draw — stale marks silently disappear
// instead of pointing at nothing (mirroring the emptyChartOption degrade).

"use strict";

import { currentTextColors } from "./echarts_theme.js";

// Chart types with a translator branch — must stay in sync with the server's
// ANNOTATION_VOCAB (chart_annotations.py). Anything else no-ops.
const SUPPORTED_TYPES = new Set(["line", "bar", "scatter", "combo"]);

// Same tolerance the server applies: a value a bit past the observed domain
// (a target line above the max) stays; a wildly-off one is stale — drop it.
const DOMAIN_TOLERANCE = 0.15;

function asNumber(v) {
  if (typeof v === "boolean" || v == null || v === "") return null;
  const n = Number(v);
  return isFinite(n) ? n : null;
}

/** The chart's value columns: config.y ("a,b" for multi-metric) or, for
 * combo, the bars+lines column lists. */
function valueColumns(config) {
  if (config.type === "combo") {
    const bars = Array.isArray(config.bars) ? config.bars : [];
    const lines = Array.isArray(config.lines) ? config.lines : [];
    return [...bars, ...lines];
  }
  return String(config.y || "")
    .split(",")
    .map((c) => c.trim())
    .filter(Boolean);
}

/** [min, max] over the numeric cells of `cols`, or null when nothing numeric. */
function numericDomain(records, cols) {
  let lo = null;
  let hi = null;
  for (const r of records) {
    for (const col of cols) {
      const n = asNumber(r[col]);
      if (n === null) continue;
      lo = lo === null ? n : Math.min(lo, n);
      hi = hi === null ? n : Math.max(hi, n);
    }
  }
  return lo === null ? null : [lo, hi];
}

function inDomain(value, domain) {
  if (!domain) return false;
  const [lo, hi] = domain;
  const span = hi - lo;
  const pad = DOMAIN_TOLERANCE * (span > 0 ? span : Math.max(Math.abs(hi), 1));
  return value >= lo - pad && value <= hi + pad;
}

/**
 * Find the RAW category value whose string form matches the annotation's
 * (the server normalizes categories to strings; the axis data keeps the raw
 * record values, and ECharts matches mark coords by exact value).
 */
function findCategory(records, xCol, value) {
  const target = String(value);
  for (const r of records) {
    if (String(r[xCol]) === target) return r[xCol];
  }
  return undefined;
}

/**
 * Resolve which option.series a series-targeted mark belongs on. The server
 * validated `series` as a split value or a metric COLUMN name; client series
 * names are split values or the column's last dotted segment — accept both.
 * Returns -1 when the series no longer exists (stale → drop the mark).
 */
function seriesIndexFor(annotation, option) {
  if (!annotation.series) return 0;
  const target = String(annotation.series);
  const short = target.split(".").pop();
  const names = option.series.map((s) => (s.name == null ? "" : String(s.name)));
  let idx = names.indexOf(target);
  if (idx === -1) idx = names.indexOf(short);
  return idx;
}

/**
 * Translate `config.annotations` into marks on `option`'s existing series.
 * Called from buildChartOption after the palette/gradient passes; mutates
 * `option` in place. No-ops on empty/absent annotations, unsupported chart
 * types, and the zero-row path (which never builds an option at all).
 *
 * @param {Object} option - The built ECharts option (mutated)
 * @param {Object} config - The chart config (carries `annotations`, set by
 *   ask.js from the explain payload, and `_annoEmphasis`, the id of the mark
 *   a hovered/focused ref chip is bolding)
 * @param {Array<Object>} records - The records this option was built from
 */
export function applyChartAnnotations(option, config, records) {
  const annotations = Array.isArray(config.annotations) ? config.annotations : [];
  if (!option || !annotations.length) return;
  if (!SUPPORTED_TYPES.has(config.type)) return;
  if (!Array.isArray(option.series) || !option.series.length) return;
  if (!Array.isArray(records) || !records.length) return;

  const xCol = config.x;
  const horizontal = !!config.horizontal;
  const scatter = config.type === "scatter";
  const yDomain = numericDomain(records, valueColumns(config));
  const xDomain = scatter && xCol ? numericDomain(records, [xCol]) : null;
  const emphasisId = config._annoEmphasis || null;

  // Marks are muted and dashed — deliberately quieter than the data series.
  // Colors resolve at option-build time from the live theme (the canvas is
  // transparent, so labels need explicit fills; zrender can't read oklch).
  const colors = currentTextColors();
  const markColor = colors.muted;
  const labelColor = colors.heading;
  const markLabel = (a, position) => ({
    show: !!a.label,
    formatter: (a.label || "").replace(/[{}]/g, ""), // never an ECharts template
    position,
    color: labelColor,
    fontSize: 11,
  });

  // On the value axis a mark is {yAxis: v} — unless the bar chart is
  // horizontal, where the value runs along X and category marks live on Y.
  const valueKey = horizontal ? "xAxis" : "yAxis";
  const categoryKey = horizontal ? "yAxis" : "xAxis";
  const axisKeyFor = (axis) => (axis === "y" ? valueKey : categoryKey);

  // markPoint coord order is [xAxisValue, yAxisValue] regardless of which
  // axis carries the categories.
  const pointCoord = (cat, val) => (horizontal ? [val, cat] : [cat, val]);

  /** Resolve an axis_line/range endpoint; undefined → stale, drop the mark. */
  function resolveAxisValue(axis, value) {
    if (axis === "x" && !scatter) return findCategory(records, xCol, value);
    const n = asNumber(value);
    const domain = axis === "x" ? xDomain : yDomain;
    return n !== null && inDomain(n, domain) ? n : undefined;
  }

  // Accumulate marks per target series (axis-level marks sit on series 0 —
  // they span the grid; a series-targeted point rides its own series).
  const buckets = new Map(); // seriesIdx -> {lines, areas, points}
  const bucket = (idx) => {
    if (!buckets.has(idx)) buckets.set(idx, { lines: [], areas: [], points: [] });
    return buckets.get(idx);
  };

  for (const a of annotations) {
    const emphasized = emphasisId !== null && a.id === emphasisId;

    if (a.type === "axis_line") {
      const v = resolveAxisValue(a.axis, a.value);
      if (v === undefined) continue;
      bucket(0).lines.push({
        [axisKeyFor(a.axis)]: v,
        label: markLabel(a, "insideEndTop"),
        lineStyle: {
          type: "dashed",
          color: markColor,
          width: emphasized ? 2.5 : 1.2,
          opacity: emphasized ? 1 : 0.85,
        },
      });
    } else if (a.type === "range") {
      const from = resolveAxisValue(a.axis, a.from);
      const to = resolveAxisValue(a.axis, a.to);
      if (from === undefined || to === undefined) continue;
      const key = axisKeyFor(a.axis);
      bucket(0).areas.push([
        {
          [key]: from,
          label: markLabel(a, "insideTop"),
          itemStyle: { color: markColor, opacity: emphasized ? 0.22 : 0.1 },
        },
        { [key]: to },
      ]);
    } else if (a.type === "point") {
      const idx = seriesIndexFor(a, option);
      if (idx === -1) continue;
      const y = asNumber(a.y);
      if (y === null || !inDomain(y, yDomain)) continue;
      let x;
      if (scatter) {
        x = asNumber(a.x);
        if (x === null || !inDomain(x, xDomain)) continue;
      } else {
        x = findCategory(records, xCol, a.x);
        if (x === undefined) continue;
      }
      bucket(idx).points.push({
        coord: pointCoord(x, y),
        symbol: "circle",
        symbolSize: emphasized ? 13 : 9,
        label: markLabel(a, "top"),
        itemStyle: {
          color: markColor,
          borderColor: labelColor,
          borderWidth: emphasized ? 2 : 1,
        },
      });
    } else if (a.type === "extremum") {
      const idx = seriesIndexFor(a, option);
      if (idx === -1) continue;
      if (a.kind !== "max" && a.kind !== "min") continue;
      bucket(idx).points.push({
        type: a.kind,
        symbol: "circle",
        symbolSize: emphasized ? 13 : 9,
        label: markLabel(a, a.kind === "min" && !horizontal ? "bottom" : "top"),
        itemStyle: {
          color: markColor,
          borderColor: labelColor,
          borderWidth: emphasized ? 2 : 1,
        },
      });
    } else if (a.type === "item") {
      // A marked bar: a muted dot just above that category's value. The value
      // is read from the current records, so the mark tracks filter changes.
      const idx = seriesIndexFor(a, option);
      if (idx === -1) continue;
      const x = findCategory(records, xCol, a.x);
      if (x === undefined) continue;
      const yCols = valueColumns(config);
      const record = records.find((r) => String(r[xCol]) === String(a.x));
      const y = record ? asNumber(record[yCols[0]]) : null;
      if (y === null) continue;
      bucket(idx).points.push({
        coord: pointCoord(x, y),
        symbol: "circle",
        symbolSize: emphasized ? 13 : 9,
        label: markLabel(a, horizontal ? "right" : "top"),
        itemStyle: {
          color: markColor,
          borderColor: labelColor,
          borderWidth: emphasized ? 2 : 1,
        },
      });
    }
    // Unknown types: server-validated payloads never carry them, but a newer
    // server against an older client must degrade silently — skip.
  }

  for (const [idx, marks] of buckets) {
    const series = option.series[idx];
    if (!series) continue;
    if (marks.lines.length) {
      series.markLine = {
        silent: true,
        symbol: "none",
        animation: false,
        data: marks.lines,
      };
    }
    if (marks.areas.length) {
      series.markArea = { silent: true, animation: false, data: marks.areas };
    }
    if (marks.points.length) {
      series.markPoint = { silent: true, animation: false, data: marks.points };
    }
  }
}

/** Re-render the chart hosting `el` with the current filter state. The data
 * fetch is a cache hit within TTL, so this repaints without a network round
 * trip. (initChart stashes the instance on the element.) */
function rerenderChart(el) {
  const instance = el && el._chartInstance;
  if (!instance) return;
  const filters =
    window.Alpine && Alpine.store ? { ...(Alpine.store("filters") || {}) } : {};
  instance.render(filters);
}

/**
 * Apply an explain payload's annotations to a chart card and repaint. The
 * marks live on the same config object fullscreen re-renders from, so the
 * modal view inherits them for free.
 * @param {HTMLElement} el - The chart card (data-async-component="chart")
 * @param {Array<Object>} annotations - Server-validated annotation objects
 */
export function setChartAnnotations(el, annotations) {
  if (!el || !el._chartConfig) return;
  el._chartConfig.annotations = Array.isArray(annotations) ? annotations : [];
  delete el._chartConfig._annoEmphasis;
  rerenderChart(el);
}

/**
 * Remove all annotations (explain panel closed / params changed) and repaint
 * back to the chart's clean reading. No-op when nothing was applied.
 * @param {HTMLElement} el - The chart card
 */
export function clearChartAnnotations(el) {
  if (!el || !el._chartConfig) return;
  const had = Array.isArray(el._chartConfig.annotations)
    ? el._chartConfig.annotations.length
    : 0;
  delete el._chartConfig.annotations;
  delete el._chartConfig._annoEmphasis;
  if (had) rerenderChart(el);
}

/**
 * Bold one mark (a ref chip is hovered/focused) or restore them all
 * (`id = null`). Bolding only — no dim-the-rest layer, by design.
 * @param {HTMLElement} el - The chart card
 * @param {string|null} id - The annotation id ("a1"…), or null to restore
 */
export function emphasizeChartAnnotation(el, id) {
  if (!el || !el._chartConfig) return;
  const config = el._chartConfig;
  if (!Array.isArray(config.annotations) || !config.annotations.length) return;
  if ((config._annoEmphasis || null) === (id || null)) return;
  if (id) config._annoEmphasis = id;
  else delete config._annoEmphasis;
  rerenderChart(el);
}
