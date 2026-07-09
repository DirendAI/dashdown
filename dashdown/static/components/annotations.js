// Dashdown Chart Annotations
// Client half of the `explain` chart-annotation feature (see
// dashdown/chart_annotations.py for the server half: vocabulary, validation,
// ref chips). The explain payload carries server-validated annotation objects;
// this module translates them into ECharts markLine/markPoint/markArea and
// hangs them onto the chart's EXISTING series — never a new series, which
// would break faceted-pie index patching, the palette-by-index gradient pass,
// and the legend. Pie/funnel/map annotations are per-datum style overrides on
// the existing series data instead (applyDatumAnnotations) — same principle.
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
const SUPPORTED_TYPES = new Set([
  "line",
  "bar",
  "scatter",
  "combo",
  "pie",
  "funnel",
  "map",
  "candlestick",
  "boxplot",
  "violin",
  "heatmap",
  "calendar",
]);

// Part-of-whole / geo types: no cartesian mark grammar. Their one annotation
// (`item` / `geo_item`) is a per-datum override on the existing series data —
// a dashed outline plus a muted label callout — applied inside the option
// build, so it needs no dispatchAction post-hooks and survives the
// setOption(notMerge) funnel like every other mark.
const DATUM_TYPES = new Set(["pie", "funnel", "map"]);

// Grid charts (matrix heatmap, calendar heatmap): cells are addressed by
// category axes / date, not by datum name, and marks render as a per-datum
// dashed cell outline. `extremum` is resolved here (argmax/argmin over the
// cell values) — the calendar coordinate has no markPoint support, and an
// outlined hottest cell reads better than a pin on a heatmap anyway.
const GRID_TYPES = new Set(["heatmap", "calendar"]);

// Same tolerance the server applies: a value a bit past the observed domain
// (a target line above the max) stays; a wildly-off one is stale — drop it.
const DOMAIN_TOLERANCE = 0.15;

function asNumber(v) {
  if (typeof v === "boolean" || v == null || v === "") return null;
  const n = Number(v);
  return isFinite(n) ? n : null;
}

/** The chart's value columns: config.y ("a,b" for multi-metric), the
 * bars+lines column lists for combo, the OHLC columns for candlestick
 * (config.y is null there), or the cell-value column for a matrix heatmap
 * (config.y is the row *category* column). Mirrors the server's
 * _value_axis_columns. */
function valueColumns(config) {
  if (config.type === "combo") {
    const bars = Array.isArray(config.bars) ? config.bars : [];
    const lines = Array.isArray(config.lines) ? config.lines : [];
    return [...bars, ...lines];
  }
  if (config.type === "candlestick") {
    return [config.open, config.high, config.low, config.close].filter(Boolean);
  }
  if (config.type === "heatmap") {
    return config.value ? [config.value] : [];
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

  if (DATUM_TYPES.has(config.type)) {
    // Faceted pies never carry a chart_context server-side (build_chart_context
    // returns None), so no annotations should reach here — but their series
    // indexes are patched per facet, so skip defensively anyway.
    if (config.type === "pie" && config.series_by) return;
    applyDatumAnnotations(option, config);
    return;
  }

  if (GRID_TYPES.has(config.type)) {
    applyGridAnnotations(option, config);
    return;
  }

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
      const point = {
        type: a.kind,
        symbol: "circle",
        symbolSize: emphasized ? 13 : 9,
        label: markLabel(a, a.kind === "min" && !horizontal ? "bottom" : "top"),
        itemStyle: {
          color: markColor,
          borderColor: labelColor,
          borderWidth: emphasized ? 2 : 1,
        },
      };
      // A candlestick datum is [open, close, low, high] — extremum means the
      // highest high / lowest low, so pin ECharts' recompute to that dim.
      if (config.type === "candlestick")
        point.valueDim = a.kind === "max" ? "highest" : "lowest";
      bucket(idx).points.push(point);
    } else if (a.type === "item") {
      if (config.type === "boxplot") {
        // A boxplot category has no single point value — outline that
        // category's box per-datum instead (index via the category axis).
        decorateBoxDatum(option, a, emphasized);
        continue;
      }
      // A marked bar/session: a muted dot just above that category's value
      // (a candlestick session's high). The value is read from the current
      // records, so the mark tracks filter changes.
      const idx = seriesIndexFor(a, option);
      if (idx === -1) continue;
      const x = findCategory(records, xCol, a.x);
      if (x === undefined) continue;
      const yCols = valueColumns(config);
      const valueCol = config.type === "candlestick" ? config.high : yCols[0];
      const record = records.find((r) => String(r[xCol]) === String(a.x));
      const y = record ? asNumber(record[valueCol]) : null;
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
    if (marks.lines.length)
      mergeMark(series, "markLine", { symbol: "none" }, marks.lines);
    if (marks.areas.length) mergeMark(series, "markArea", {}, marks.areas);
    if (marks.points.length) mergeMark(series, "markPoint", {}, marks.points);
  }
}

/**
 * The pie/funnel/map translator: each `item`/`geo_item` annotation becomes a
 * per-datum override on series 0's matching data entry — a dashed outline in
 * the mark color, plus a label callout (pie shows the annotation label with a
 * leader line even when slice labels are off; a map labels the region; funnel
 * keeps its native stage label — the chip tooltip carries the annotation
 * text). A name that no longer matches the current data (filter drift) simply
 * doesn't decorate anything — same stale-mark posture as the cartesian path.
 */
function applyDatumAnnotations(option, config) {
  const series = option.series[0];
  if (!series || !Array.isArray(series.data)) return;

  const colors = currentTextColors();
  const emphasisId = config._annoEmphasis || null;
  // Never let a label become an ECharts template ("{b}" etc.).
  const cleanLabel = (s) => String(s).replace(/[{}]/g, "");

  for (const a of annotationTargets(config.annotations)) {
    const item = series.data.find(
      (d) => d && String(d.name) === String(a.target)
    );
    if (!item) continue; // stale: slice/stage/region gone after a filter change
    const emphasized = emphasisId !== null && a.id === emphasisId;

    item.itemStyle = {
      ...(item.itemStyle || {}),
      borderType: "dashed",
      borderColor: colors.heading,
      borderWidth: emphasized ? 3 : 1.8,
    };
    const label = a.label
      ? {
          show: true,
          formatter: cleanLabel(a.label),
          color: colors.heading,
          fontSize: 11,
          fontWeight: emphasized ? "bold" : "normal",
        }
      : null;
    if (config.type === "pie" && label) {
      item.label = label;
      item.labelLine = { show: true };
    } else if (config.type === "map") {
      // Label the region even without an annotation label — the region name
      // is the callout then (and still bolds on chip focus).
      item.label = label || {
        show: true,
        color: colors.heading,
        fontSize: 11,
        fontWeight: emphasized ? "bold" : "normal",
      };
    }
  }
}

/**
 * The grid-chart translator (matrix heatmap, calendar heatmap): `item` and
 * `extremum` become a dashed outline on the matching cell — a per-datum
 * itemStyle override, same principle as applyDatumAnnotations. A heatmap cell
 * is addressed by both category axes (datum = [xi, yi, value] against
 * option.xAxis/yAxis.data); a calendar day by its date (datum =
 * ["YYYY-MM-DD", value], matched on the same 10-char slice the option builder
 * uses). `extremum` is resolved here from the drawn data — the calendar
 * coordinate has no markPoint support, and an outlined hottest cell reads
 * better than a pin on a heatmap anyway. Stale addresses (a category/date
 * gone after a filter change) decorate nothing, silently.
 */
function applyGridAnnotations(option, config) {
  const series = option.series[0];
  if (!series || !Array.isArray(series.data)) return;

  const colors = currentTextColors();
  const emphasisId = config._annoEmphasis || null;
  const calendar = config.type === "calendar";
  const valueSlot = calendar ? 1 : 2;
  const datumValue = (d) => (Array.isArray(d) ? d : d && d.value);

  const xs =
    !calendar && option.xAxis && Array.isArray(option.xAxis.data)
      ? option.xAxis.data
      : [];
  const ys =
    !calendar && option.yAxis && Array.isArray(option.yAxis.data)
      ? option.yAxis.data
      : [];

  const findIndex = (a) => {
    if (a.type === "item") {
      if (calendar) {
        const target = String(a.x).slice(0, 10);
        return series.data.findIndex((d) => {
          const v = datumValue(d);
          return v && String(v[0]) === target;
        });
      }
      const xi = xs.indexOf(String(a.x));
      const yi = ys.indexOf(String(a.y));
      if (xi === -1 || yi === -1) return -1;
      return series.data.findIndex((d) => {
        const v = datumValue(d);
        return v && v[0] === xi && v[1] === yi;
      });
    }
    if (a.type === "extremum" && (a.kind === "max" || a.kind === "min")) {
      let best = -1;
      let bestValue = null;
      series.data.forEach((d, i) => {
        const v = datumValue(d);
        const n = v ? asNumber(v[valueSlot]) : null;
        if (n === null) return;
        if (best === -1 || (a.kind === "max" ? n > bestValue : n < bestValue)) {
          best = i;
          bestValue = n;
        }
      });
      return best;
    }
    return -1; // unknown type: newer server, older client — degrade silently
  };

  for (const a of config.annotations) {
    const idx = findIndex(a);
    if (idx === -1) continue;
    const datum = series.data[idx];
    const emphasized = emphasisId !== null && a.id === emphasisId;
    series.data[idx] = {
      value: datumValue(datum),
      itemStyle: {
        borderType: "dashed",
        borderColor: colors.heading,
        borderWidth: emphasized ? 3 : 1.8,
      },
    };
  }
}

/**
 * Outline one boxplot category's box: a per-datum itemStyle override on the
 * box series (series 0 of [box, outliers]), indexed via the category axis.
 * The label rides the chip tooltip — boxes have no leader-line grammar.
 */
function decorateBoxDatum(option, a, emphasized) {
  const xs =
    option.xAxis && Array.isArray(option.xAxis.data) ? option.xAxis.data : [];
  const idx = xs.indexOf(String(a.x));
  const series = option.series[0];
  if (idx === -1 || !series || !Array.isArray(series.data)) return;
  const datum = series.data[idx];
  if (datum == null) return;
  const colors = currentTextColors();
  series.data[idx] = {
    value: Array.isArray(datum) ? datum : datum.value,
    itemStyle: {
      borderType: "dashed",
      borderColor: colors.heading,
      borderWidth: emphasized ? 3 : 2.2,
    },
  };
}

/** The item/geo_item annotations with their datum name resolved, other types
 * skipped (a newer server against an older client degrades silently). */
function annotationTargets(annotations) {
  const out = [];
  for (const a of annotations) {
    const target = a.type === "geo_item" ? a.name : a.type === "item" ? a.x : null;
    if (target != null) out.push({ id: a.id, label: a.label, target });
  }
  return out;
}

/**
 * Append explain marks to a series' mark* channel without clobbering any the
 * chart drew natively. No supported chart type currently sets markLine/
 * markPoint/markArea itself, but a future one might — appending to the existing
 * `.data` (keeping its wrapper config) keeps this forward-safe.
 */
function mergeMark(series, key, extra, data) {
  const existing = series[key];
  if (existing && Array.isArray(existing.data)) {
    existing.data = existing.data.concat(data);
  } else {
    series[key] = { silent: true, animation: false, ...extra, data };
  }
}

/** Repaint the chart hosting `el` so a mark change (apply / clear / emphasize)
 * shows. Prefers `instance.repaint()`, which rebuilds from the last-rendered
 * records with no data round-trip — so bolding a mark on chip hover doesn't
 * re-run the fetch path every mouseenter. Falls back to a full filter-driven
 * render before the chart's first paint. (initChart stashes the instance.) */
function rerenderChart(el) {
  const instance = el && el._chartInstance;
  if (!instance) return;
  if (typeof instance.repaint === "function") {
    instance.repaint();
    return;
  }
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
