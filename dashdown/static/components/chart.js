// Dashdown Chart Component
// Self-contained chart rendering with ECharts and async data loading

"use strict";

import { fetchQueryData, recordsOf, queryUsesFilters, esc, readBrandingConfig, bindLiveQuery, isLiveQuery, formatValue, resolveFormatOpts } from "../core.js";
import { showLoading, hideLoading } from "../loading.js";
import { applyChartAnnotations } from "./annotations.js";
import { mountFilterBadge } from "./filter_badge.js";
import {
  currentEChartsTheme,
  currentDefaultPalette,
  currentSurfaceWash,
  currentTextColors,
  onThemeChange,
} from "./echarts_theme.js";
import { mapZoomChrome } from "./_geo.js";

/**
 * Registry of all chart instances
 * Used for resize handling
 * @type {Array<ChartInstance>}
 */
export const chartInstances = [];

// Donut hole center as fractions of the chart's width/height. Offset left of
// center to leave room for the right-side legend. Shared by the pie series
// (`center`) and the center-total label so they stay aligned.
const DONUT_CENTER = [0.38, 0.52];

function getQueryDefs() {
  return (window.Alpine && Alpine.store("queryDefs")) || {};
}

// Reused 2D context for measuring text glyph metrics (see positionDonutCenter).
let _measureCtx = null;
function glyphMetrics(text, fontPx, bold) {
  if (!_measureCtx) _measureCtx = document.createElement("canvas").getContext("2d");
  _measureCtx.font = `${bold ? "bold " : ""}${fontPx}px sans-serif`;
  const m = _measureCtx.measureText(text);
  // actualBoundingBox* give the true ink extents, so we can center on the glyph
  // rather than the (taller, baseline-asymmetric) line box.
  const h = (m.actualBoundingBoxAscent || fontPx * 0.72) + (m.actualBoundingBoxDescent || fontPx * 0.2);
  return { w: m.width, h };
}

/**
 * Draw a donut's center total (big number + "Total") truly centered on the
 * hole. ECharts `title`/`graphic` text anchors by its line box, which leaves
 * the text visibly below the hole; instead we measure each line's glyph box
 * and position it by pixel so the stacked pair is centered on the hole.
 * Re-run on every render and resize, since it depends on the live pixel size.
 * @param {Object} inst - ECharts instance
 * @param {number} total - Summed series value to display
 * @param {((v: any) => string) | null} [fmtFn] - Optional value formatter
 *   (from the chart's format/currency/decimals attrs).
 */
function positionDonutCenter(inst, total, fmtFn) {
  const W = inst.getWidth();
  const H = inst.getHeight();
  if (!W || !H) return;
  const cx = DONUT_CENTER[0] * W;
  const cy = DONUT_CENTER[1] * H;
  const colors = currentTextColors();
  const numText = fmtFn ? fmtFn(total) : Number(total).toLocaleString();
  const numM = glyphMetrics(numText, 20, true);
  const lblM = glyphMetrics("Total", 11, false);
  const gap = 6;
  const blockH = numM.h + gap + lblM.h;
  // Glyph-center Y of each line so the whole block is centered on the hole.
  const numCY = cy - blockH / 2 + numM.h / 2;
  const lblCY = cy + blockH / 2 - lblM.h / 2;
  // ECharts anchors graphic text top-left, so place each by its glyph box.
  const text = (t, cX, cY, m, style) => ({
    type: "text",
    left: cX - m.w / 2,
    top: cY - m.h / 2,
    z: 10,
    silent: true,
    style: { text: t, ...style },
  });
  inst.setOption(
    {
      graphic: [
        text(numText, cx, numCY, numM, {
          fontSize: 20,
          fontWeight: "bold",
          fill: colors.heading,
        }),
        text("Total", cx, lblCY, lblM, { fontSize: 11, fill: colors.muted }),
      ],
    },
    { replaceMerge: ["graphic"] }
  );
}

/**
 * In headless capture mode — PDF export (`window.__dashdownPrint`, set by the
 * `dashdown pdf` runner) or screenshot (`window.__dashdownCapture`, set by
 * `dashdown screenshot`) — disable ECharts animation so the canvas is final the
 * moment setOption returns; otherwise Chromium can rasterize a half-animated
 * chart. A no-op for normal viewing. See print.js.
 */
function forPrint(option) {
  if (window.__dashdownPrint || window.__dashdownCapture) option.animation = false;
  return option;
}

/** True when a chart config should render as a donut (with a center total). A
 * faceted pie (`series=`) is small-multiples filled pies — no single center. */
function isDonutConfig(config) {
  return config.type === "pie" && config.donut !== false && !config.series_by;
}

/** True when a pie config is faceted small-multiples (`series=` present). */
function isFacetedPieConfig(config) {
  return config.type === "pie" && Boolean(config.series_by);
}

/** Grid shape for n faceted pies: a single row up to 5, else near-square. */
function facetGrid(n) {
  const cols = n <= 5 ? Math.max(n, 1) : Math.ceil(Math.sqrt(n));
  return { cols, rows: Math.ceil(n / cols) };
}

/**
 * Size + position faceted pies from the LIVE pixel dimensions. ECharts measures
 * `radius:"%"` against the *smaller* canvas side, so a build-time percent can't
 * fill a wide card — here we know the real width/height, so each pie grows into
 * its grid cell (width-bounded on a tall card, height-bounded on a short one).
 * Re-run on every render + resize, like positionDonutCenter.
 * @param {Object} inst - ECharts instance
 * @param {Object} config - chart config (needs `title` for top spacing)
 * @param {number} n - facet count (number of pie series)
 */
function sizeFacetedPies(inst, config, n) {
  const W = inst.getWidth();
  const H = inst.getHeight();
  if (!W || !H || !n) return;
  const { cols, rows } = facetGrid(n);
  const hasTitle = Boolean(config.title);
  // Vertical band the pies occupy (px): room for the main title (top) and the
  // shared legend (bottom).
  const top = (hasTitle ? 0.14 : 0.08) * H;
  const bandH = 0.88 * H - top;
  const cellW = W / cols;
  const cellH = bandH / rows;
  const titleGap = 22; // px reserved above each pie for its facet label
  // Radius = the smaller of the cell's usable half-width and half-height.
  const radius = Math.max(
    10,
    Math.min(cellW * 0.5 * 0.86, ((cellH - titleGap) * 0.5) * 0.94),
  );
  const seriesPatch = [];
  const titlePatch = hasTitle
    ? [{ text: config.title, left: "left", textStyle: { fontSize: 14 } }]
    : [];
  for (let i = 0; i < n; i++) {
    const cx = (i % cols + 0.5) * cellW;
    const cy = top + (Math.floor(i / cols) + 0.5) * cellH;
    seriesPatch.push({ center: [cx, cy], radius });
    // Patch only the position of each (already-texted) per-pie title.
    titlePatch.push({ top: cy - radius - 18, left: cx, textAlign: "center" });
  }
  // Merge-by-index: updates center/radius + title positions without resending
  // the pie data or the title text.
  inst.setOption({ series: seriesPatch, title: titlePatch });
}

/** Standard left-aligned chart title block, or undefined when no title set. */
function chartTitle(title) {
  return title
    ? { text: title, left: "left", textStyle: { fontSize: 14 } }
    : undefined;
}

/** Distinct values of `key` across records, in first-seen order. */
function distinctValues(records, key) {
  const seen = [];
  records.forEach((r) => {
    const v = String(r[key]);
    if (seen.indexOf(v) === -1) seen.push(v);
  });
  return seen;
}

/**
 * Faceted "small multiples" pie option: one filled pie per distinct `series_by`
 * value, arranged in a grid, each showing the x→y breakdown for that facet, with
 * a per-pie title and a shared slice legend. (ECharts' multiple-pie example.)
 * @param {Object} config - chart config (x = slice label, y = value, series_by = facet)
 * @param {Array<Object>} records - sorted records
 * @param {{fmtFn: ((v:any)=>string)|null}} fmt - the shared value formatter
 * @returns {Object} ECharts option
 */
function facetedPieOption(config, records, { fmtFn }) {
  const { x, y, series_by, title } = config;
  // Group records into facets (first-seen order); collect the union of slice
  // names for a single shared legend.
  const groups = {};
  const order = [];
  const sliceNames = [];
  records.forEach((r) => {
    const g = String(r[series_by]);
    if (!(g in groups)) {
      groups[g] = [];
      order.push(g);
    }
    const name = String(r[x]);
    if (sliceNames.indexOf(name) === -1) sliceNames.push(name);
    groups[g].push({ name, value: Number(r[y]) || 0 });
  });

  const n = order.length;
  const { cols, rows } = facetGrid(n);
  // Initial layout in fractions of the chart box. `sizeFacetedPies` overrides
  // center/radius/title-tops from the live pixel size right after setOption (a %
  // here can't fill a wide card) — these are just the pre-refine fallback.
  const bandTop = title ? 0.16 : 0.1;
  const bandH = 0.86 - bandTop;
  const radiusPct = Math.max(
    8,
    Math.round(Math.min((bandH / rows) * 42, (1 / cols) * 92)),
  );

  const titles = title
    ? [{ text: title, left: "left", textStyle: { fontSize: 14 } }]
    : [];
  const series = order.map((g, i) => {
    const cx = ((i % cols) + 0.5) / cols * 100;
    const cy = (bandTop + (Math.floor(i / cols) + 0.5) / rows * bandH) * 100;
    titles.push({
      text: g,
      left: `${cx}%`,
      top: `${cy - radiusPct - 5}%`,
      textAlign: "center",
      textStyle: { fontSize: 12, fontWeight: "normal" },
    });
    return {
      type: "pie",
      name: g,
      data: groups[g],
      center: [`${cx}%`, `${cy}%`],
      radius: `${radiusPct}%`,
      avoidLabelOverlap: true,
      label: { show: false },
      labelLine: { show: false },
      emphasis: {
        label: { show: true, formatter: "{b}: {d}%", fontSize: 11 },
        itemStyle: { shadowBlur: 10, shadowColor: "rgba(0,0,0,0.3)" },
      },
    };
  });

  return {
    title: titles,
    tooltip: {
      trigger: "item",
      formatter: (p) =>
        `${esc(p.seriesName)}<br/>${esc(p.name)}: ` +
        `${fmtFn ? fmtFn(p.value) : p.value} (${p.percent}%)`,
    },
    legend: { type: "scroll", bottom: 4, data: sliceNames },
    series,
  };
}

/**
 * Build an ECharts hierarchy (array of {name, value?, children?} roots) from a
 * flat adjacency list. `config.node_id`/`config.parent` identify each node and
 * its parent; `config.value`/`config.label` are optional size/display columns.
 * A root is any node whose parent is empty or not itself a node. A `seen` set
 * guards against cycles (a back-edge becomes a leaf rather than recursing).
 * @returns {Array<Object>}
 */
function buildHierarchy(records, config) {
  const { node_id: idCol, parent: parentCol, value: valueCol, label: labelCol } = config;
  const byId = new Map();
  records.forEach((r) => {
    const id = String(r[idCol]);
    if (byId.has(id)) return;
    byId.set(id, {
      id,
      parent: r[parentCol] == null ? "" : String(r[parentCol]),
      name: labelCol ? String(r[labelCol]) : id,
      value: valueCol ? Number(r[valueCol]) || 0 : null,
      kids: [],
    });
  });
  const roots = [];
  byId.forEach((node) => {
    const p = node.parent;
    if (p && byId.has(p) && p !== node.id) byId.get(p).kids.push(node);
    else roots.push(node);
  });
  const seen = new Set();
  const toEChart = (node) => {
    const out = { name: node.name };
    if (node.value != null) out.value = node.value;
    if (!seen.has(node.id)) {
      seen.add(node.id);
      const children = node.kids.map(toEChart);
      if (children.length) out.children = children;
    }
    return out;
  };
  return roots.map(toEChart);
}

/** Sum the donut's value column across records, for the center total. */
function donutTotal(records, config) {
  return records.reduce((s, r) => s + (Number(r[config.y]) || 0), 0);
}

/**
 * Chart instance object
 * @typedef {Object} ChartInstance
 * @property {HTMLElement} el - DOM element
 * @property {Object} config - Chart configuration
 * @property {Object} echartsInstance - ECharts instance
 */

/**
 * Initialize a chart component
 * @param {HTMLElement} el - Chart element with data-async-component="chart"
 */
export function initChart(el) {
  // Wait for Alpine to be available before proceeding
  waitForAlpine(() => {
    const config = JSON.parse(el.dataset.config);
    const queryName = config.query_name;

    // Store reference to config on element
    el._chartConfig = config;

    // Create chart container - keep skeleton until data loads
    let chartContainer = el.querySelector(".dashdown-chart-container");
    if (!chartContainer) {
      const cardBody = el.querySelector(".card-body");
      if (cardBody) {
        // Replace with chart container + skeleton overlay
        cardBody.style.position = "relative";
        cardBody.innerHTML = `
          <div class="w-full h-full dashdown-chart-container"></div>
          <div class="dashdown-chart-skeleton skeleton h-full w-full" style="position:absolute;top:0;left:0"></div>
        `;
        chartContainer = cardBody.querySelector(".dashdown-chart-container");
      } else {
        el.innerHTML = '<div class="card-body p-2" style="position:relative"><div class="w-full h-full dashdown-chart-container"></div><div class="dashdown-chart-skeleton skeleton h-full w-full" style="position:absolute;top:0;left:0"></div></div>';
        chartContainer = el.querySelector(".dashdown-chart-container");
      }
    }

    // Initialize ECharts on the container with the theme matching the current
    // DaisyUI light/dark mode, so titles/axes/legends are readable in both.
    const echartsInstance = echarts.init(chartContainer, currentEChartsTheme());
    el._echarts_instance = echartsInstance;

    // Create chart instance
    const instance = {
      el,
      config,
      echartsInstance,
      async render(filters = {}) {
        if (queryUsesFilters(queryName, filters, getQueryDefs())) {
          // Server-side filtering: re-fetch with filter params
          showLoading(el);
          try {
            const data = await fetchQueryData(queryName, {}, filters);
            const records = recordsOf(data);
            hideLoading(el);
            updateChart(el, records, config);
          } catch (error) {
            hideLoading(el);
            showError(el, error);
          }
        } else {
          // This query references none of the active filters, so its result
          // doesn't change with them — render the full base snapshot. Using
          // fetchQueryData (cached within TTL, in-flight deduped) also fetches
          // once if the base was never loaded; the previous code read the cache
          // wrapper directly and rendered nothing on filter pages.
          showLoading(el);
          try {
            const data = await fetchQueryData(queryName, {}, {});
            hideLoading(el);
            updateChart(el, recordsOf(data), config);
          } catch (error) {
            hideLoading(el);
            showError(el, error);
          }
        }
      },
      // Rebuild the option from the last-rendered records (stashed by
      // updateChart) with NO data round-trip — used by the explain-annotation
      // helpers so applying/clearing/emphasizing marks repaints the current
      // reading instead of re-running render()'s fetch. Falls back to render()
      // if nothing has painted yet.
      repaint() {
        const records = el._chartRecords;
        if (Array.isArray(records) && records.length) {
          updateChart(el, records, config);
        } else {
          const filters =
            window.Alpine && Alpine.store ? { ...(Alpine.store("filters") || {}) } : {};
          this.render(filters);
        }
      },
    };

    // "Filtered by" corner marker (reactive to filter state; self-gates).
    mountFilterBadge(el, queryName);

    // Let the explain-annotation helpers re-render this chart without going
    // through the store (annotations.js::rerenderChart).
    el._chartInstance = instance;

    // Register instance
    chartInstances.push(instance);

    // Register in component store if available
    if (Alpine && Alpine.store && Alpine.store("components")) {
      Alpine.store("components").registerChart(instance);
    }

    // Single reactive path: subscribe to the filters store. The effect runs
    // once immediately (initial render) and re-runs whenever any filter value
    // changes or a new filter key is added. Spreading the store registers a
    // dependency on every key (and on key iteration), so dynamically-added
    // filters are tracked too. No custom events.
    Alpine.effect(() => {
      const filters = { ...(Alpine.store("filters") || {}) };
      // Live queries are WS-first: skip the one-shot data-API fetch (the socket
      // delivers the first payload immediately and is self-healing) so a flaky
      // source can't surface a hard 500 on load. Non-live / static: fetch.
      if (!isLiveQuery(queryName)) instance.render(filters);
      bindLiveQuery(el, queryName, filters, (data) => {
        if (data && !data.error) {
          // WS-first skips render() (which is what normally calls hideLoading),
          // so clear the skeleton overlay here — otherwise the chart draws
          // underneath it and stays invisible.
          hideLoading(el);
          updateChart(el, recordsOf(data), config);
        }
      });
    });

    // Handle window resize. Reference the live instance (not the closure
    // variable) since a theme toggle disposes and re-creates it. Resizing
    // doesn't re-run setOption, so reposition a donut's center total (which is
    // pixel-placed) against the new size.
    window.addEventListener("resize", () => {
      const live = el._echarts_instance;
      if (!live) return;
      live.resize();
      if (el._donutTotal != null)
        positionDonutCenter(live, el._donutTotal, valueFormatter(el._chartConfig || {}));
      if (el._facetCount)
        sizeFacetedPies(live, el._chartConfig || {}, el._facetCount);
    });
  });
}

/**
 * ECharts bakes a theme in at init time, so a light/dark toggle requires
 * disposing the instance and re-initializing with the new theme. Re-render
 * with the current filters afterwards to repaint the (cached) data.
 * @param {ChartInstance} instance
 */
function reinitChartTheme(instance) {
  const el = instance.el;
  const old = el._echarts_instance;
  const container = old ? old.getDom() : el.querySelector(".dashdown-chart-container");
  if (!container) return;
  if (old) old.dispose();
  const next = echarts.init(container, currentEChartsTheme());
  el._echarts_instance = next;
  instance.echartsInstance = next;
  const filters = (window.Alpine && Alpine.store) ? { ...(Alpine.store("filters") || {}) } : {};
  instance.render(filters);
}

// Re-theme every live chart when DaisyUI light/dark mode changes.
onThemeChange(() => {
  chartInstances.forEach(reinitChartTheme);
});

/**
 * Wait for Alpine.js to be available
 * @param {Function} callback - Callback to execute when Alpine is ready
 */
function waitForAlpine(callback) {
  if (window.Alpine) {
    callback();
  } else {
    document.addEventListener("alpine:init", callback);
  }
}

/**
 * Build ECharts option object from config and records, then resolve the series
 * palette. Priority (highest first): a per-chart `color=` attr, the project
 * brand palette (`branding.palette` in dashdown.yaml), then the theme default
 * palette (whose first color follows the DaisyUI `--p` token). Skipped if the
 * chart type already set its own colors.
 * @param {Object} config - Chart configuration
 * @param {Array<Object>} records - Array of record objects
 * @returns {Object} - ECharts option object
 */
export function buildChartOption(config, records) {
  const option = buildChartOptionBase(config, records);
  if (option && !option.color) {
    const attrColors = colorListFromAttr(config.color);
    const branding = readBrandingConfig();
    if (attrColors.length) {
      option.color = attrColors;
    } else if (branding && Array.isArray(branding.palette) && branding.palette.length) {
      option.color = branding.palette;
    } else {
      option.color = currentDefaultPalette();
    }
  }
  if (option) applyAreaGradients(option);
  // Explain-panel annotation marks (config.annotations, set by ask.js). Inside
  // this single funnel so they survive filter refetches, live pushes, theme
  // re-inits, and fullscreen; re-validated against the current records so a
  // stale mark disappears instead of mispointing.
  if (option) applyChartAnnotations(option, config, records);
  return option;
}

/**
 * Convert a hex (`#rgb`/`#rrggbb`) or `rgb()/rgba()` color plus an alpha into an
 * `rgba(...)` string. Returns "transparent" at alpha 0 (so a gradient fades to
 * nothing) and the input unchanged for anything we can't parse (named colors).
 * @param {string} color
 * @param {number} alpha
 * @returns {string}
 */
function colorWithAlpha(color, alpha) {
  if (alpha <= 0) return "transparent";
  if (typeof color !== "string") return color;
  let m = color.match(/^#([0-9a-f]{3})$/i);
  if (m) {
    const h = m[1];
    const r = parseInt(h[0] + h[0], 16);
    const g = parseInt(h[1] + h[1], 16);
    const b = parseInt(h[2] + h[2], 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }
  m = color.match(/^#([0-9a-f]{6})$/i);
  if (m) {
    const h = m[1];
    return `rgba(${parseInt(h.slice(0, 2), 16)}, ${parseInt(h.slice(2, 4), 16)}, ${parseInt(h.slice(4, 6), 16)}, ${alpha})`;
  }
  m = color.match(/^rgba?\(([^)]+)\)$/i);
  if (m) {
    const parts = m[1].split(",").map((s) => s.trim());
    if (parts.length >= 3) return `rgba(${parts[0]}, ${parts[1]}, ${parts[2]}, ${alpha})`;
  }
  return color;
}

/**
 * Upgrade the flat-opacity area fills the line builder sets (`areaStyle:
 * { opacity }`) to a vertical gradient that fades each series' own colour to
 * transparent toward the axis — the signature modern line-chart look. Runs
 * after the palette is resolved so each series' concrete colour is known (the
 * series index maps to option.color exactly as ECharts assigns it). Skips
 * series whose areaStyle already carries an explicit colour, and never touches
 * non-line area fills (e.g. radar polygons, whose areaStyle is per-data-item).
 * @param {Object} option - A resolved ECharts option (with option.color set)
 */
function applyAreaGradients(option) {
  if (!Array.isArray(option.series) || typeof echarts === "undefined") return;
  if (!echarts.graphic || !echarts.graphic.LinearGradient) return;
  const palette = Array.isArray(option.color) ? option.color : [];
  const isAreaLine = (s) =>
    s && s.type === "line" && s.areaStyle &&
    s.areaStyle.color == null && typeof s.areaStyle.opacity === "number";
  // Grouped lines overlap, so keep their fills fainter than a lone trend's.
  const peak = option.series.filter(isAreaLine).length > 1 ? 0.14 : 0.26;
  option.series.forEach((s, i) => {
    if (!isAreaLine(s)) return;
    const color =
      (s.lineStyle && s.lineStyle.color) ||
      (s.itemStyle && s.itemStyle.color) ||
      palette[i % (palette.length || 1)] ||
      palette[0];
    if (!color) return; // no resolvable colour — keep the flat fill
    s.areaStyle = {
      color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
        { offset: 0, color: colorWithAlpha(color, peak) },
        { offset: 1, color: colorWithAlpha(color, 0) },
      ]),
    };
  });
}

/**
 * Parse the per-chart `color=` attr into an array of color strings. Accepts a
 * single color or a comma-separated list ("#f00, #0f0").
 * @param {string | undefined} value
 * @returns {string[]}
 */
function colorListFromAttr(value) {
  if (!value) return [];
  return String(value)
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

/**
 * Build a number formatter for value-axis labels / tooltips from the chart's
 * `format`/`currency`/`decimals` attrs, or null when the author set none (so
 * ECharts keeps its raw default). `currency` alone implies the currency format;
 * `decimals` alone implies a thousands-separated number. Gating is on the chart's
 * OWN attrs — a project-wide `format:` default fills currency/locale gaps but
 * never makes an unformatted chart start formatting.
 * @param {Object} config
 * @returns {((v: any) => string) | null}
 */
function valueFormatter(config) {
  const hasFmt =
    config.format || config.currency || config.decimals != null;
  if (!hasFmt) return null;
  const fmt = config.format || (config.currency ? "currency" : "number");
  const opts = resolveFormatOpts(config);
  return (v) => formatValue(v, fmt, opts);
}

/**
 * Tick formatter for value axes. Ticks compact once they reach 1,000
 * (1,500,000 → "1.5M") so a big-number scale stays readable instead of
 * clipping or crowding the plot — tooltips and data labels keep the exact
 * `fmtFn` format. Applies to `number`/`currency`/unformatted axes; any other
 * explicit format (`percent`, `compact` itself, dates) is the author's choice
 * and passes through verbatim. Currency ticks keep their symbol ("$1.2M");
 * an ISO 4217 code takes the locale's own compact currency form.
 * @param {Object} cfg - a format-config fragment (the chart config or `config.right`)
 * @param {((v: any) => string) | null} fmtFn - the exact-value formatter (or null)
 * @returns {(v: any) => string}
 */
function axisTickFormatter(cfg, fmtFn) {
  const fmt = cfg.format || (cfg.currency ? "currency" : null);
  if (fmt && fmt !== "number" && fmt !== "currency") return fmtFn;
  const opts = resolveFormatOpts(cfg);
  return (v) => {
    const n = Number(v);
    if (!isFinite(n) || Math.abs(n) < 1000) return fmtFn ? fmtFn(v) : String(v);
    if (fmt === "currency" && /^[A-Z]{3}$/.test(opts.currency)) {
      try {
        return n.toLocaleString(opts.locale || undefined, {
          style: "currency",
          currency: opts.currency,
          notation: "compact",
          maximumSignificantDigits: 3,
        });
      } catch {
        // Unknown code — fall through to the symbol-prefixed form.
      }
    }
    const compact = formatValue(n, "compact", { locale: opts.locale });
    return fmt === "currency" ? opts.currency + compact : compact;
  };
}

function buildChartOptionBase(config, records) {
  // <Chart auto />: infer a concrete type + axes from the result shape first.
  if (config.type === "auto") {
    config = resolveAutoConfig(config, records);
  }
  const { type, x, y, title, series_by, sort_by } = config;
  // Value-axis / tooltip number formatter from the chart's format/currency/
  // decimals attrs, or null when none set (ECharts keeps its raw default).
  const fmtFn = valueFormatter(config);
  // Axis ticks get their own formatter: big ticks compact ("1.5M") so the
  // scale stays readable, while tooltips/labels keep the exact fmtFn format.
  const axisFmtFn = axisTickFormatter(config, fmtFn);
  // `name: value` formatters honoring fmtFn, falling back to ECharts' raw
  // `{b}: {c}` templates. Labels paint as canvas text (no HTML escaping);
  // tooltips render as HTML (esc the name, matching the violin tooltip).
  const labelNameVal = fmtFn ? (p) => `${p.name}: ${fmtFn(p.value)}` : "{b}: {c}";
  const labelNameValPct = fmtFn
    ? (p) => `${p.name}: ${fmtFn(p.value)} (${p.percent}%)`
    : "{b}: {c} ({d}%)";
  const tipNameVal = fmtFn ? (p) => `${esc(p.name)}: ${fmtFn(p.value)}` : "{b}: {c}";
  const tipNameValPct = fmtFn
    ? (p) => `${esc(p.name)}: ${fmtFn(p.value)} (${p.percent}%)`
    : "{b}: {c} ({d}%)";
  let series;
  let xCategories;

  // Create a sorted copy of records if sort_by is specified
  let sortedRecords = records;
  if (sort_by && records.length > 0 && records[0][sort_by] !== undefined) {
    sortedRecords = [...records].sort((a, b) => {
      const va = a[sort_by];
      const vb = b[sort_by];
      const na = Number(va);
      const nb = Number(vb);
      if (!isNaN(na) && !isNaN(nb)) {
        return na - nb;
      }
      return String(va).localeCompare(String(vb));
    });
  }

  if (type === "pie" && series_by) {
    // Faceted "small multiples": one pie per distinct `series_by` value, each
    // showing the x→y breakdown for that facet (e.g. revenue by region, one pie
    // per quarter; or BSL `metric={m} by={slice} series={facet}`). Mirrors the
    // ECharts multiple-pie example, laid out in a grid.
    return facetedPieOption(config, sortedRecords, { fmtFn, tipNameValPct });
  }

  if (type === "pie") {
    // Pie chart - uses name/value pairs. Defaults to a donut with a center
    // total; `donut=false` falls back to a classic filled pie.
    const pieData = sortedRecords.map((r) => ({
      name: r[x],
      value: Number(r[y]) || 0,
    }));
    const isDonut = config.donut !== false;
    // For a tidy donut, show the legend only when there are few slices and let
    // it carry the labels; with many slices fall back to on-slice percentages.
    const fewSlices = pieData.length <= 6;
    const emphasis = {
      itemStyle: {
        shadowBlur: 10,
        shadowOffsetX: 0,
        shadowColor: "rgba(0, 0, 0, 0.5)",
      },
    };

    const titleOption = title
      ? { text: title, left: "left", textStyle: { fontSize: 14 } }
      : undefined;

    if (isDonut) {
      series = [
        {
          type: "pie",
          data: pieData,
          radius: ["58%", "78%"],
          // Offset left of center to clear the right-side legend. The center
          // total is drawn separately in updateChart() (positionDonutCenter),
          // anchored to this same point — ECharts' own title/graphic text
          // can't be reliably centered on it (they anchor by the line box,
          // leaving the text visibly low), so we place it from glyph metrics.
          center: [`${DONUT_CENTER[0] * 100}%`, `${DONUT_CENTER[1] * 100}%`],
          avoidLabelOverlap: true,
          label: { show: !fewSlices, formatter: "{b}: {d}%" },
          labelLine: { show: !fewSlices },
          emphasis: {
            ...emphasis,
            label: { show: true, fontWeight: "bold" },
          },
        },
      ];
    } else {
      series = [
        {
          type: "pie",
          data: pieData,
          radius: "50%",
          label: { show: true, formatter: labelNameValPct },
          emphasis,
        },
      ];
    }

    return {
      title: titleOption,
      tooltip: { trigger: "item", formatter: tipNameValPct },
      legend: { orient: "vertical", right: 10, top: "middle" },
      series,
    };
  }

  if (type === "treemap" || type === "funnel") {
    // Hierarchical / conversion charts - use name/value pairs (x=label, y=value)
    const data = sortedRecords.map((r) => ({ name: String(r[x]), value: r[y] }));
    const isFunnel = type === "funnel";
    series = [
      isFunnel
        ? {
            type: "funnel",
            data,
            left: "10%",
            right: "10%",
            label: { show: true, formatter: labelNameVal },
            emphasis: { label: { fontSize: 16 } },
          }
        : {
            type: "treemap",
            data,
            label: { show: true, formatter: "{b}" },
            breadcrumb: { show: false },
          },
    ];
    return {
      title: title
        ? { text: title, left: "left", textStyle: { fontSize: 14 } }
        : undefined,
      tooltip: { trigger: "item", formatter: tipNameVal },
      legend: isFunnel ? { orient: "vertical", left: 10, top: "middle" } : undefined,
      series,
    };
  }

  if (type === "scatter") {
    // Scatter plot - x and y are both numeric; optional grouping via series_by
    if (series_by) {
      const groups = {};
      sortedRecords.forEach((r) => {
        const k = String(r[series_by]);
        (groups[k] = groups[k] || []).push([r[x], r[y]]);
      });
      series = Object.keys(groups).map((name) => ({
        name,
        type: "scatter",
        data: groups[name],
      }));
    } else {
      series = [
        { type: "scatter", data: sortedRecords.map((r) => [r[x], r[y]]) },
      ];
    }
    // Format the value (y) axis labels + tooltip when the author set a format.
    const yAxis = { type: "value", name: y, scale: true };
    if (axisFmtFn) yAxis.axisLabel = { formatter: axisFmtFn };
    return {
      title: title
        ? { text: title, left: "left", textStyle: { fontSize: 14 } }
        : undefined,
      tooltip: {
        trigger: "item",
        valueFormatter: fmtFn || undefined,
      },
      legend: series_by ? { top: "bottom" } : undefined,
      grid: {
        left: 50,
        right: 20,
        top: title ? 40 : 20,
        bottom: series_by ? 40 : 30,
      },
      xAxis: {
        type: "value",
        name: x,
        scale: true,
        axisLabel: axisFmtFn ? { formatter: axisFmtFn } : undefined,
      },
      yAxis,
      series,
    };
  }

  if (type === "calendar") {
    // Calendar heatmap - x is a date column (YYYY-MM-DD), y is the value
    const data = sortedRecords
      .map((r) => [String(r[x]).slice(0, 10), Number(r[y])])
      .filter((d) => /^\d{4}-\d{2}-\d{2}$/.test(d[0]) && !isNaN(d[1]));
    const dates = data.map((d) => d[0]).sort();
    const values = data.map((d) => d[1]);
    const min = values.length ? Math.min(...values) : 0;
    let max = values.length ? Math.max(...values) : 1;
    if (max <= min) max = min + 1;
    const range = dates.length
      ? [dates[0], dates[dates.length - 1]]
      : [new Date().getFullYear()];
    return {
      title: title
        ? { text: title, left: "left", textStyle: { fontSize: 14 } }
        : undefined,
      tooltip: {
        formatter: (p) =>
          `${esc(p.value[0])}: ${fmtFn ? fmtFn(p.value[1]) : p.value[1]}`,
      },
      visualMap: {
        min,
        max,
        calculable: true,
        orient: "horizontal",
        left: "center",
        bottom: 0,
        inRange: { color: ["#ebedf0", "#c6e48b", "#7bc96f", "#239a3b", "#196127"] },
      },
      calendar: {
        range,
        cellSize: ["auto", 16],
        top: title ? 60 : 40,
        left: 50,
        right: 20,
        dayLabel: { firstDay: 1 },
        yearLabel: { show: true },
      },
      series: [{ type: "heatmap", coordinateSystem: "calendar", data }],
    };
  }

  if (type === "boxplot" || type === "violin") {
    // Distribution charts - y holds raw values, x optionally groups them.
    // Stats are computed here from the raw rows.
    const groups = groupNumericValues(sortedRecords, x, y);
    const categories = [...groups.keys()];
    if (type === "boxplot") {
      const boxData = [];
      const outliers = [];
      categories.forEach((k, i) => {
        const vals = groups.get(k).sort((a, b) => a - b);
        const q1 = quantileSorted(vals, 0.25);
        const med = quantileSorted(vals, 0.5);
        const q3 = quantileSorted(vals, 0.75);
        const iqr = q3 - q1;
        const inFence = vals.filter((v) => v >= q1 - 1.5 * iqr && v <= q3 + 1.5 * iqr);
        const lo = inFence.length ? Math.min(...inFence) : q1;
        const hi = inFence.length ? Math.max(...inFence) : q3;
        boxData.push([lo, q1, med, q3, hi]);
        vals.forEach((v) => {
          if (v < lo || v > hi) outliers.push([i, v]);
        });
      });
      return {
        title: title
          ? { text: title, left: "left", textStyle: { fontSize: 14 } }
          : undefined,
        tooltip: { trigger: "item" },
        grid: { left: 60, right: 20, top: title ? 40 : 20, bottom: 30 },
        xAxis: { type: "category", data: categories },
        yAxis: {
          type: "value",
          scale: true,
          axisLabel: axisFmtFn ? { formatter: axisFmtFn } : undefined,
        },
        series: [
          { type: "boxplot", data: boxData, itemStyle: { borderWidth: 1.5 } },
          { type: "scatter", data: outliers, symbolSize: 6 },
        ],
      };
    }
    // Violin: kernel density estimate per group, drawn as mirrored polygons
    // on a custom series (x axis is numeric so polygon points can sit between
    // category centers).
    const densities = categories.map((k) => kde(groups.get(k)));
    const maxDensity = Math.max(
      1e-12,
      ...densities.flatMap((pts) => pts.map((p) => p[1]))
    );
    const halfWidth = 0.42;
    return {
      title: title
        ? { text: title, left: "left", textStyle: { fontSize: 14 } }
        : undefined,
      tooltip: {
        trigger: "item",
        formatter: (p) => {
          const vals = groups.get(categories[p.dataIndex]) || [];
          const sorted = [...vals].sort((a, b) => a - b);
          const med = sorted.length ? quantileSorted(sorted, 0.5) : "-";
          const medText = sorted.length && fmtFn ? fmtFn(med) : med;
          return `${esc(categories[p.dataIndex])}<br/>n = ${vals.length}<br/>median = ${medText}`;
        },
      },
      grid: { left: 60, right: 20, top: title ? 40 : 20, bottom: 30 },
      xAxis: {
        type: "value",
        min: -0.7,
        max: categories.length - 0.3,
        interval: 1,
        axisLabel: {
          showMinLabel: false,
          showMaxLabel: false,
          formatter: (v) => categories[Math.round(v)] ?? "",
        },
        splitLine: { show: false },
      },
      yAxis: {
        type: "value",
        scale: true,
        axisLabel: axisFmtFn ? { formatter: axisFmtFn } : undefined,
      },
      series: [
        {
          type: "custom",
          renderItem: (params, api) => {
            const i = params.dataIndex;
            const pts = densities[i];
            const points = pts.map(([v, d]) =>
              api.coord([i - (d / maxDensity) * halfWidth, v])
            );
            for (let j = pts.length - 1; j >= 0; j--) {
              const [v, d] = pts[j];
              points.push(api.coord([i + (d / maxDensity) * halfWidth, v]));
            }
            return {
              type: "polygon",
              shape: { points },
              style: api.style({ opacity: 0.7 }),
            };
          },
          // [index, lo, hi] so axis extents cover the full density support
          data: densities.map((pts, i) => [
            i,
            pts.length ? pts[0][0] : 0,
            pts.length ? pts[pts.length - 1][0] : 0,
          ]),
          encode: { x: 0, y: [1, 2] },
        },
      ],
    };
  }

  if (type === "map") {
    // Choropleth - x is the location/feature name, y is the value.
    // The GeoJSON itself is registered asynchronously in updateChart.
    const data = sortedRecords.map((r) => ({ name: String(r[x]), value: Number(r[y]) }));
    const values = data.map((d) => d.value).filter((v) => !isNaN(v));
    const min = values.length ? Math.min(...values) : 0;
    let max = values.length ? Math.max(...values) : 1;
    if (max <= min) max = min + 1;
    // Title and legend sit on the same translucent card wash the SVG geo maps
    // give their overlaid chrome, so the six map types read as one family.
    const wash = currentSurfaceWash();
    return {
      title: title
        ? {
            text: title,
            left: 6,
            top: 6,
            padding: [4, 8],
            backgroundColor: wash || "transparent",
            borderRadius: 8,
            textStyle: { fontSize: 14, fontWeight: 600 },
          }
        : undefined,
      tooltip: {
        trigger: "item",
        formatter: (p) =>
          p.value == null || isNaN(p.value)
            ? esc(p.name)
            : `${esc(p.name)}: ${fmtFn ? fmtFn(p.value) : p.value}`,
      },
      visualMap: {
        min,
        max,
        calculable: true,
        orient: "horizontal", // bottom-left ramp, like the geo maps' legend
        left: 6,
        bottom: 6,
        padding: [6, 8],
        backgroundColor: wash || "transparent",
        borderRadius: 8,
      },
      series: [
        {
          type: "map",
          map: config.map || "world",
          roam: true,
          data,
          emphasis: { label: { show: true } },
        },
      ],
    };
  }

  if (type === "radar") {
    // Multi-axis comparison: x = indicator (axis) column, y = value, optional
    // series_by splits into multiple overlaid polygons. Each indicator's max is
    // the largest value seen for it, so every axis is scaled to its own range.
    const indicators = distinctValues(sortedRecords, x);
    const maxByInd = {};
    sortedRecords.forEach((r) => {
      const ind = String(r[x]);
      maxByInd[ind] = Math.max(maxByInd[ind] ?? 0, Number(r[y]) || 0);
    });
    const indicatorDefs = indicators.map((name) => ({
      name,
      max: maxByInd[name] > 0 ? maxByInd[name] : 1,
    }));
    let radarData;
    if (series_by) {
      const groups = {};
      sortedRecords.forEach((r) => {
        const k = String(r[series_by]);
        (groups[k] = groups[k] || {})[String(r[x])] = Number(r[y]) || 0;
      });
      radarData = Object.keys(groups).map((name) => ({
        name,
        value: indicators.map((ind) => groups[name][ind] ?? 0),
        areaStyle: { opacity: 0.1 },
      }));
    } else {
      const byInd = {};
      sortedRecords.forEach((r) => {
        byInd[String(r[x])] = Number(r[y]) || 0;
      });
      radarData = [
        {
          name: title || y || "Value",
          value: indicators.map((ind) => byInd[ind] ?? 0),
          areaStyle: { opacity: 0.15 },
        },
      ];
    }
    return {
      title: chartTitle(title),
      tooltip: { trigger: "item" },
      legend: series_by ? { top: "bottom" } : undefined,
      radar: {
        indicator: indicatorDefs,
        radius: "62%",
        center: ["50%", title ? "55%" : "50%"],
      },
      series: [{ type: "radar", data: radarData }],
    };
  }

  if (type === "gauge") {
    // Single KPI value: the first row's y, on a min..max scale (default 0..100).
    const raw = sortedRecords.length ? Number(sortedRecords[0][y]) : 0;
    const value = isFinite(raw) ? raw : 0;
    const min = config.min != null ? Number(config.min) : 0;
    const max = config.max != null ? Number(config.max) : 100;
    const detailFmt = fmtFn
      ? (v) => fmtFn(v)
      : (v) => (Number.isInteger(v) ? String(v) : Number(v).toFixed(1));
    return {
      title: chartTitle(title),
      tooltip: { show: false },
      series: [
        {
          type: "gauge",
          min,
          max,
          // A plain track behind the colored progress arc (the default gauge
          // axisLine is a multi-color band, which fights the brand palette).
          axisLine: { lineStyle: { width: 14, color: [[1, "rgba(128,128,128,0.15)"]] } },
          progress: { show: true, width: 14, roundCap: true },
          pointer: { show: true, length: "60%", width: 5 },
          anchor: { show: true, size: 8, itemStyle: { color: "auto" } },
          axisTick: { show: false },
          splitLine: { length: 10, lineStyle: { width: 1.5 } },
          axisLabel: { distance: 18, fontSize: 10 },
          detail: {
            valueAnimation: true,
            formatter: detailFmt,
            fontSize: 26,
            offsetCenter: [0, "72%"],
          },
          title: { offsetCenter: [0, "95%"], fontSize: 12 },
          data: [{ value, name: title || "" }],
        },
      ],
    };
  }

  if (type === "heatmap") {
    // Matrix heatmap: x and y are both category axes, config.value the cell
    // magnitude (a third column). Distinct from CalendarHeatmap (date grid).
    const valueCol = config.value;
    const xs = distinctValues(sortedRecords, x);
    const ys = distinctValues(sortedRecords, y);
    const data = sortedRecords.map((r) => [
      xs.indexOf(String(r[x])),
      ys.indexOf(String(r[y])),
      Number(r[valueCol]) || 0,
    ]);
    const vals = data.map((d) => d[2]).filter((v) => !isNaN(v));
    const min = vals.length ? Math.min(...vals) : 0;
    let max = vals.length ? Math.max(...vals) : 1;
    if (max <= min) max = min + 1;
    return {
      title: chartTitle(title),
      tooltip: {
        position: "top",
        formatter: (p) =>
          `${esc(xs[p.value[0]])} · ${esc(ys[p.value[1]])}: ` +
          `${fmtFn ? fmtFn(p.value[2]) : p.value[2]}`,
      },
      grid: { left: 80, right: 20, top: title ? 50 : 30, bottom: 60 },
      xAxis: { type: "category", data: xs, splitArea: { show: true } },
      yAxis: { type: "category", data: ys, splitArea: { show: true } },
      visualMap: {
        min,
        max,
        calculable: true,
        orient: "horizontal",
        left: "center",
        bottom: 5,
        inRange: { color: ["#eef2ff", "#a5b4fc", "#6366f1", "#3730a3"] },
      },
      series: [
        {
          type: "heatmap",
          data,
          // Only label small grids; a dense matrix becomes unreadable.
          label: {
            show: data.length <= 200,
            formatter: (p) => (fmtFn ? fmtFn(p.value[2]) : p.value[2]),
          },
          emphasis: {
            itemStyle: { shadowBlur: 10, shadowColor: "rgba(0,0,0,0.3)" },
          },
        },
      ],
    };
  }

  if (type === "sankey") {
    // Flow diagram from an edge list: x = source node, y = target node,
    // config.value = flow magnitude. Nodes are the union of both columns.
    const valueCol = config.value;
    const nodes = [];
    const links = [];
    sortedRecords.forEach((r) => {
      const s = String(r[x]);
      const t = String(r[y]);
      if (!s || !t) return;
      if (nodes.indexOf(s) === -1) nodes.push(s);
      if (nodes.indexOf(t) === -1) nodes.push(t);
      links.push({
        source: s,
        target: t,
        value: valueCol ? Number(r[valueCol]) || 0 : 1,
      });
    });
    return {
      title: chartTitle(title),
      tooltip: {
        trigger: "item",
        triggerOn: "mousemove",
        valueFormatter: fmtFn || undefined,
      },
      series: [
        {
          type: "sankey",
          data: nodes.map((name) => ({ name })),
          links,
          emphasis: { focus: "adjacency" },
          lineStyle: { color: "gradient", curveness: 0.5 },
          label: { fontSize: 11 },
        },
      ],
    };
  }

  if (type === "candlestick") {
    // OHLC financial chart: x = date/category, config.open/high/low/close name
    // the four price columns. ECharts wants [open, close, low, high] per item.
    const { open, high, low, close } = config;
    const cats = sortedRecords.map((r) => String(r[x]));
    const data = sortedRecords.map((r) => [
      Number(r[open]),
      Number(r[close]),
      Number(r[low]),
      Number(r[high]),
    ]);
    const yAxis = { type: "value", scale: true };
    if (axisFmtFn) yAxis.axisLabel = { formatter: axisFmtFn };
    return {
      title: chartTitle(title),
      tooltip: { trigger: "axis", axisPointer: { type: "cross" } },
      grid: { left: 60, right: 20, top: title ? 40 : 20, bottom: 40 },
      xAxis: { type: "category", data: cats, boundaryGap: true },
      yAxis,
      series: [
        {
          type: "candlestick",
          data,
          // Green = close ≥ open (bullish), red = close < open (bearish).
          itemStyle: {
            color: "#22c55e",
            color0: "#ef4444",
            borderColor: "#22c55e",
            borderColor0: "#ef4444",
          },
        },
      ],
    };
  }

  if (type === "themeriver") {
    // Stacked stream over time: x = time, y = value, series_by = category. Each
    // datum is [time, value, category]; ECharts streams them on a time axis.
    const data = sortedRecords.map((r) => [
      String(r[x]),
      Number(r[y]) || 0,
      String(r[series_by]),
    ]);
    const names = distinctValues(sortedRecords, series_by);
    return {
      title: chartTitle(title),
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "line", lineStyle: { color: "rgba(128,128,128,0.4)", width: 1 } },
        valueFormatter: fmtFn || undefined,
      },
      legend: { data: names, top: "bottom" },
      singleAxis: {
        type: "time",
        top: title ? 40 : 20,
        bottom: 50,
        axisPointer: { animation: true, label: { show: true } },
      },
      series: [
        {
          type: "themeRiver",
          data,
          label: { show: false },
          emphasis: { focus: "self" },
        },
      ],
    };
  }

  if (type === "graph") {
    // Force-directed network from an edge list: x = source, y = target,
    // config.value = edge weight (optional, default 1). Node size grows with
    // its total incident weight.
    const valueCol = config.value;
    const nodeWeight = {};
    const links = [];
    sortedRecords.forEach((r) => {
      const s = String(r[x]);
      const t = String(r[y]);
      if (!s || !t) return;
      const w = valueCol ? Number(r[valueCol]) || 0 : 1;
      nodeWeight[s] = (nodeWeight[s] || 0) + w;
      nodeWeight[t] = (nodeWeight[t] || 0) + w;
      links.push(valueCol ? { source: s, target: t, value: w } : { source: s, target: t });
    });
    const names = Object.keys(nodeWeight);
    const maxW = Math.max(1, ...names.map((n) => nodeWeight[n]));
    const nodes = names.map((name) => ({
      name,
      value: nodeWeight[name],
      symbolSize: 14 + 34 * Math.sqrt(nodeWeight[name] / maxW),
    }));
    return {
      title: chartTitle(title),
      tooltip: { trigger: "item", valueFormatter: fmtFn || undefined },
      series: [
        {
          type: "graph",
          layout: "force",
          roam: true,
          data: nodes,
          links,
          force: { repulsion: 140, edgeLength: [40, 120] },
          lineStyle: { color: "source", curveness: 0.1, opacity: 0.6 },
          emphasis: { focus: "adjacency", lineStyle: { width: 3 } },
          label: { show: true, position: "right", fontSize: 11 },
        },
      ],
    };
  }

  if (type === "sunburst" || type === "tree") {
    // Hierarchy from an adjacency list (config.node_id / config.parent, with
    // optional config.value size and config.label name). Sunburst = nested
    // rings; tree = a node-link diagram.
    const roots = buildHierarchy(sortedRecords, config);
    if (type === "sunburst") {
      return {
        title: chartTitle(title),
        tooltip: {
          trigger: "item",
          formatter: (p) =>
            `${esc(p.name)}${p.value != null ? ": " + (fmtFn ? fmtFn(p.value) : p.value) : ""}`,
        },
        series: [
          {
            type: "sunburst",
            data: roots,
            radius: [0, "92%"],
            label: { minAngle: 8 },
            emphasis: { focus: "ancestor" },
          },
        ],
      };
    }
    // `tree` wants a single root; wrap multiple roots under a synthetic one.
    const treeData =
      roots.length === 1 ? roots : [{ name: title || "root", children: roots }];
    return {
      title: chartTitle(title),
      tooltip: { trigger: "item", triggerOn: "mousemove" },
      series: [
        {
          type: "tree",
          data: treeData,
          top: title ? 40 : 20,
          left: 40,
          right: 60,
          bottom: 20,
          symbolSize: 8,
          orient: "LR",
          expandAndCollapse: true,
          initialTreeDepth: 3,
          label: { position: "left", verticalAlign: "middle", align: "right", fontSize: 11 },
          leaves: {
            label: { position: "right", verticalAlign: "middle", align: "left" },
          },
          emphasis: { focus: "descendant" },
        },
      ],
    };
  }

  if (type === "parallel") {
    // Parallel coordinates over several numeric columns (config.dimensions);
    // each row is one polyline. Optional series_by colors lines by group.
    const dims = config.dimensions || [];
    const axes = dims.map((name, i) => ({ dim: i, name }));
    const toLine = (r) =>
      dims.map((d) => {
        const n = Number(r[d]);
        return isFinite(n) ? n : null;
      });
    let series;
    if (series_by) {
      const groups = {};
      sortedRecords.forEach((r) => {
        const k = String(r[series_by]);
        (groups[k] = groups[k] || []).push(toLine(r));
      });
      series = Object.keys(groups).map((name) => ({
        name,
        type: "parallel",
        data: groups[name],
        lineStyle: { width: 1.5, opacity: 0.6 },
      }));
    } else {
      series = [
        {
          type: "parallel",
          data: sortedRecords.map(toLine),
          lineStyle: { width: 1.5, opacity: 0.5 },
        },
      ];
    }
    return {
      title: chartTitle(title),
      tooltip: {},
      legend: series_by ? { top: "bottom" } : undefined,
      parallelAxis: axes,
      parallel: {
        left: 60,
        right: 40,
        top: title ? 60 : 40,
        bottom: series_by ? 50 : 30,
      },
      series,
    };
  }

  // Combo (bar + line, optional second axis) is the one cartesian type that mixes
  // series types and carries two value axes, so it has its own builder rather than
  // the shared yCols path below.
  if (type === "combo") {
    return comboChartOption(config, sortedRecords);
  }

  // `y` may name several metric columns ("revenue,profit") — each becomes its own
  // coloured series with a legend (multi-metric charts, incl. BSL
  // `metric="sales.revenue,sales.profit"`). A single column is the common case.
  const yCols = String(y)
    .split(",")
    .map((c) => c.trim())
    .filter(Boolean);
  const multiMetric = !series_by && yCols.length > 1;
  // Whether the chart carries more than one series (so it needs a legend and a
  // per-series palette rather than per-bar colouring).
  const grouped = Boolean(series_by) || multiMetric;

  if (series_by) {
    // Grouped series (multiple lines/bars) — split one metric (yCols[0]) by a
    // category column.
    const yCol = yCols[0];
    const groups = {};
    const xSet = [];

    sortedRecords.forEach((r) => {
      const k = String(r[series_by]);
      const xv = r[x];
      if (xSet.indexOf(xv) === -1) xSet.push(xv);
      (groups[k] = groups[k] || {})[xv] = r[yCol];
    });

    xCategories = xSet;
    series = Object.keys(groups).map((name) => ({
      name,
      type,
      data: xCategories.map((xv) => groups[name][xv] ?? null),
      smooth: type === "line",
      // `stacked` (Bar/Line) stacks the grouped series on a shared total.
      stack: config.stacked ? "total" : undefined,
    }));
  } else if (multiMetric) {
    // Multiple metrics over a shared x axis — one series per metric column. The
    // legend label is the column's last dotted segment, so a BSL metric column
    // (prefixed `sales.revenue_2024` once a model has joins) reads cleanly as
    // `revenue_2024`; a plain column name is unchanged.
    xCategories = sortedRecords.map((r) => r[x]);
    series = yCols.map((col) => ({
      name: col.split(".").pop(),
      type,
      data: sortedRecords.map((r) => r[col]),
      smooth: type === "line",
      stack: config.stacked ? "total" : undefined,
    }));
  } else {
    // Single series, single metric.
    xCategories = sortedRecords.map((r) => r[x]);
    series = [
      {
        type,
        data: sortedRecords.map((r) => r[yCols[0]]),
        smooth: type === "line",
      },
    ];
  }

  // Soft area fill under line series (8% of the series color), per the
  // mockups' revenue chart. Subtle enough to stay readable when grouped
  // lines overlap.
  if (type === "line") {
    series = series.map((s) => ({ ...s, areaStyle: { opacity: 0.08 } }));
  }

  // Horizontal bars (ECharts "bar-y-category"): the category axis moves to Y
  // and values run along X. Same series data (one value per category) — ECharts
  // infers orientation from which axis carries the categories. `inverse` keeps
  // the first row at the top, so an `ORDER BY value DESC` query reads top-down.
  const horizontal = type === "bar" && config.horizontal;

  // Bar corner rounding. The theme rounds every bar's leading edge ([6,6,0,0]),
  // which is right for a lone vertical column but scallops a *stacked* bar — each
  // segment gets its own rounded corners — and rounds the wrong edge of a
  // *horizontal* bar. Round only the bar's leading tip (right end when
  // horizontal, top when vertical), and for a stack only the outermost segment,
  // so inner segments stay flush and the bar reads as one shape with a rounded
  // end. Series-level itemStyle overrides the theme default.
  if (type === "bar") {
    const tip = horizontal ? [0, 6, 6, 0] : [6, 6, 0, 0];
    const lastIdx = series.length - 1;
    series = series.map((s, i) => {
      const outer = !config.stacked || i === lastIdx;
      return { ...s, itemStyle: { ...(s.itemStyle || {}), borderRadius: outer ? tip : 0 } };
    });
  }

  const categoryAxis = { type: "category", data: xCategories };
  // A line/area trend should span the full plot width: pin the first and last
  // points to the axis edges instead of ECharts' default half-band padding,
  // which leaves conspicuous dead space at both ends. Bars keep the default gap
  // so columns stay centered within their band. A lone point (degenerate line,
  // handled below) keeps the centered gap so it isn't pinned to the far edge.
  if (type === "line" && xCategories.length > 1) categoryAxis.boundaryGap = false;
  const valueAxis = { type: "value" };
  // Honor the chart's format/currency/decimals attrs on the value axis labels
  // (so `63712.895` reads as `$63,712.90`) and the shared axis tooltip; big
  // ticks compact ("1.5M") via axisFmtFn while the tooltip stays exact.
  if (axisFmtFn) valueAxis.axisLabel = { formatter: axisFmtFn };

  // A line chart with a single x-category is just a lone floating dot — useless
  // as a "trend". Enlarge + label the point so its value reads, and add an
  // explicit empty-state hint. Applies to single and grouped series alike (one
  // x value means every line collapses to one point).
  const degenerateLine = type === "line" && xCategories.length === 1;
  if (degenerateLine) {
    // Explicit label color: with the theme's transparent canvas, ECharts'
    // auto-contrast picks a white-stroked label that garbles on dark cards.
    const labelColor = currentTextColors().heading;
    series = series.map((s) => ({
      ...s,
      smooth: false,
      symbol: "circle",
      symbolSize: 10,
      label: {
        show: true,
        position: "top",
        formatter: fmtFn ? (p) => fmtFn(p.value) : lineValueLabel,
        color: labelColor,
        textBorderWidth: 0,
      },
    }));
  }

  return {
    title: title
      ? { text: title, left: "left", textStyle: { fontSize: 14 } }
      : undefined,
    tooltip: { trigger: "axis", valueFormatter: fmtFn || undefined },
    legend: grouped ? { top: "bottom" } : undefined,
    grid: {
      // Horizontal bars need more left room for the category labels.
      left: horizontal ? 90 : 50,
      right: 20,
      top: title ? 40 : 20,
      bottom: grouped ? 40 : 30,
    },
    xAxis: horizontal ? valueAxis : categoryAxis,
    yAxis: horizontal ? { ...categoryAxis, inverse: true } : valueAxis,
    graphic: degenerateLine ? trendHintGraphic() : undefined,
    series,
  };
}

/**
 * Combo (bar + line) chart. Each column in `config.bars` is drawn as a bar and
 * each in `config.lines` as a line over a shared category x-axis; columns listed
 * in `config.right_axis` are plotted against a secondary (right-hand) value axis —
 * the canonical "revenue $ as bars, margin % as a line" two-scale chart. Bars are
 * emitted first (drawn behind the lines). The left axis is formatted from the
 * chart's own `format`/`currency`/`decimals`; the right axis from the nested
 * `config.right` fragment (its `right_*` twins). Both modes (a plain `data={query}`
 * with column names and a semantic `bars={model.metric}`) reach here identically —
 * the columns are already resolved to result-column names by the Python component.
 * @param {Object} config
 * @param {Array<Object>} records
 * @returns {Object} ECharts option
 */
function comboChartOption(config, records) {
  const { x, title } = config;
  const bars = Array.isArray(config.bars) ? config.bars : [];
  const lines = Array.isArray(config.lines) ? config.lines : [];
  const rightCols = new Set(Array.isArray(config.right_axis) ? config.right_axis : []);
  const lineSet = new Set(lines);
  // Bars first so the lines paint on top; `cols` order also indexes the tooltip's
  // per-series formatter lookup (param.seriesIndex → cols[i]).
  const cols = [...bars, ...lines];

  const fmtFn = valueFormatter(config);
  const rightFmtFn = config.right ? valueFormatter(config.right) : null;
  const useRight = cols.some((c) => rightCols.has(c));

  // Optional per-series colour overrides — one list for bars, one for lines, each
  // cycled if there are more series than colours. Empty → the shared palette
  // (config.color / theme) still applies in buildChartOption.
  const barColors = Array.isArray(config.bar_colors) ? config.bar_colors : [];
  const lineColors = Array.isArray(config.line_colors) ? config.line_colors : [];
  let barIdx = 0;
  let lineIdx = 0;

  const xCategories = records.map((r) => r[x]);
  const series = cols.map((col) => {
    const isLine = lineSet.has(col);
    const onRight = rightCols.has(col);
    const s = {
      // Last dotted segment, so a BSL metric column reads cleanly (matches the
      // multi-metric legend labels elsewhere); a plain column name is unchanged.
      name: String(col).split(".").pop(),
      type: isLine ? "line" : "bar",
      yAxisIndex: useRight && onRight ? 1 : 0,
      data: records.map((r) => r[col]),
    };
    if (isLine) {
      s.smooth = true;
      if (lineColors.length) {
        const c = lineColors[lineIdx % lineColors.length];
        s.itemStyle = { color: c };  // legend marker + symbols
        s.lineStyle = { color: c };  // the line itself
      }
      lineIdx += 1;
    } else if (barColors.length) {
      s.itemStyle = { color: barColors[barIdx % barColors.length] };
      barIdx += 1;
    } else {
      barIdx += 1;
    }
    return s;
  });

  const leftAxis = { type: "value" };
  const leftAxisFmt = axisTickFormatter(config, fmtFn);
  if (leftAxisFmt) leftAxis.axisLabel = { formatter: leftAxisFmt };
  const yAxis = [leftAxis];
  if (useRight) {
    // Right axis: no split lines so the two grids don't visually fight.
    const right = { type: "value", splitLine: { show: false } };
    const rf = config.right
      ? axisTickFormatter(config.right, rightFmtFn)
      : leftAxisFmt;
    if (rf) right.axisLabel = { formatter: rf };
    yAxis.push(right);
  }

  return {
    title: title
      ? { text: title, left: "left", textStyle: { fontSize: 14 } }
      : undefined,
    // Axis tooltip with a per-series formatter, so a left ($) and a right (%)
    // series each render in their own axis's format (ECharts' single
    // `valueFormatter` can't tell the two axes apart).
    tooltip: {
      trigger: "axis",
      formatter: (params) => {
        const arr = Array.isArray(params) ? params : [params];
        if (!arr.length) return "";
        const head = esc(arr[0].axisValueLabel ?? arr[0].name ?? "");
        const rows = arr.map((p) => {
          const f = rightCols.has(cols[p.seriesIndex]) ? rightFmtFn || fmtFn : fmtFn;
          const val = f ? f(p.value) : p.value;
          return `${p.marker}${esc(p.seriesName)}: ${esc(val)}`;
        });
        return [head, ...rows].join("<br/>");
      },
    },
    legend: { top: "bottom" },
    grid: {
      left: 55,
      right: useRight ? 55 : 20,
      top: title ? 40 : 20,
      bottom: 40,
    },
    xAxis: { type: "category", data: xCategories },
    yAxis,
    series,
  };
}

/** Data-label formatter for a degenerate single-point line: the value itself. */
function lineValueLabel(p) {
  const v = p.value;
  if (v == null || v === "") return "";
  const n = Number(v);
  return isFinite(n) ? n.toLocaleString() : String(v);
}

/**
 * A muted top-center caption for a degenerate (single-point) line chart, so the
 * lone dot reads as "no trend yet" rather than a stray data glitch. Color comes
 * from the live theme so it stays legible in light/dark.
 * @returns {Array<Object>} - ECharts `graphic` elements
 */
/**
 * True when a query returned no rows — every chart type funnels through
 * updateChart, so this one check drives the shared empty state below.
 */
function isEmptyChartData(records) {
  return !Array.isArray(records) || records.length === 0;
}

/**
 * Universal empty-state option. When a query returns zero rows, any `*Chart`
 * (they all share updateChart→buildChartOption) shows a centered message
 * instead of bare axes or a "0" donut. The text is the component's
 * `empty_message` attr (defaulted to "No data available" in _chart_html); the
 * title still paints so the card stays identifiable.
 */
function emptyChartOption(config) {
  const colors = currentTextColors();
  return {
    title: config.title
      ? { text: config.title, left: "left", textStyle: { fontSize: 14 } }
      : undefined,
    xAxis: { show: false },
    yAxis: { show: false },
    graphic: [
      {
        type: "text",
        left: "center",
        top: "middle",
        silent: true,
        style: {
          text: config.empty_message || "No data available",
          fontSize: 13,
          fill: colors.muted,
        },
      },
    ],
  };
}

function trendHintGraphic() {
  const colors = currentTextColors();
  return [
    {
      type: "text",
      left: "center",
      top: 6,
      z: 5,
      silent: true,
      style: {
        text: "Not enough data for a trend",
        fontSize: 12,
        fill: colors.muted,
      },
    },
  ];
}

/**
 * Group the numeric values of column y by column x (or a single "All" group).
 * @returns {Map<string, number[]>}
 */
function groupNumericValues(records, x, y) {
  const groups = new Map();
  records.forEach((r) => {
    const v = Number(r[y]);
    if (isNaN(v)) return;
    const k = x ? String(r[x]) : "All";
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(v);
  });
  return groups;
}

/**
 * Quantile of an ascending-sorted numeric array (linear interpolation).
 */
function quantileSorted(sorted, q) {
  if (!sorted.length) return 0;
  const pos = (sorted.length - 1) * q;
  const base = Math.floor(pos);
  const rest = pos - base;
  const next = sorted[base + 1];
  return next === undefined ? sorted[base] : sorted[base] + rest * (next - sorted[base]);
}

/**
 * Gaussian kernel density estimate, sampled over the value range.
 * @returns {Array<[number, number]>} - [value, density] pairs, ascending
 */
function kde(vals, samples = 40) {
  if (!vals.length) return [];
  const n = vals.length;
  const mean = vals.reduce((a, b) => a + b, 0) / n;
  const std = Math.sqrt(vals.reduce((s, v) => s + (v - mean) ** 2, 0) / n);
  const bw = 1.06 * (std || Math.abs(mean) * 0.1 || 1) * Math.pow(n, -0.2);
  const lo = Math.min(...vals) - bw;
  const hi = Math.max(...vals) + bw;
  const pts = [];
  for (let i = 0; i <= samples; i++) {
    const t = lo + (i * (hi - lo)) / samples;
    let d = 0;
    vals.forEach((v) => {
      const z = (t - v) / bw;
      d += Math.exp(-0.5 * z * z);
    });
    pts.push([t, d / (n * bw * Math.sqrt(2 * Math.PI))]);
  }
  return pts;
}

/**
 * Classify a column's sampled values as temporal / numeric / categorical.
 */
function classifyValues(values) {
  let numeric = 0;
  let temporal = 0;
  let nonNull = 0;
  values.forEach((v) => {
    if (v == null || v === "") return;
    nonNull++;
    if (typeof v === "number") {
      numeric++;
      return;
    }
    const s = String(v);
    if (/^\d{4}-\d{2}(-\d{2})?([T ].*)?$/.test(s)) {
      temporal++;
      return;
    }
    if (s.trim() !== "" && !isNaN(Number(s))) {
      numeric++;
    }
  });
  if (!nonNull) return "categorical";
  if (temporal / nonNull > 0.8) return "temporal";
  if (numeric / nonNull > 0.8) return "numeric";
  return "categorical";
}

/**
 * Resolve a `type: "auto"` config to a concrete chart config from the
 * shape of the records: temporal x -> line, categorical x -> bar,
 * numeric x + numeric y -> scatter. Explicit x/y attributes win.
 */
export function resolveAutoConfig(config, records) {
  if (!records.length) {
    return { ...config, type: "bar", x: config.x || "", y: config.y || "" };
  }
  const cols = Object.keys(records[0]);
  const sample = records.slice(0, 50);
  const kind = {};
  cols.forEach((c) => {
    kind[c] = classifyValues(sample.map((r) => r[c]));
  });

  const firstOf = (k, exclude) => cols.find((c) => kind[c] === k && c !== exclude);
  const x =
    config.x || firstOf("temporal") || firstOf("categorical") || cols[0];
  const y = config.y || cols.find((c) => c !== x && kind[c] === "numeric");
  if (!y) {
    throw new Error(
      "Chart auto: no numeric column to plot — specify `y` explicitly"
    );
  }
  let type = "bar";
  if (kind[x] === "temporal") type = "line";
  else if (kind[x] === "numeric") type = "scatter";
  const resolved = { ...config, type, x, y };
  // Time series read wrong when unordered; sort by the temporal axis.
  if (kind[x] === "temporal" && !resolved.sort_by) resolved.sort_by = x;
  return resolved;
}

/**
 * GeoJSON registration for MapChart. Built-in source for "world"; anything
 * else needs a `geojson` attribute. Registrations are cached per map name.
 */
const DEFAULT_GEOJSON_URLS = {
  // Vendored locally (no CDN). Resolved against this module's served URL so it
  // works on the dev server and in base-resolved static exports alike.
  world: new URL("../vendor/world.json", import.meta.url).href,
};
const mapRegistrations = {};

function ensureMapRegistered(config) {
  const name = config.map || "world";
  if (echarts.getMap(name)) return Promise.resolve();
  if (!mapRegistrations[name]) {
    const url = config.geojson || DEFAULT_GEOJSON_URLS[name];
    if (!url) {
      return Promise.reject(
        new Error(`MapChart: unknown map "${name}" — pass a geojson="..." URL`)
      );
    }
    mapRegistrations[name] = fetch(url)
      .then((resp) => {
        if (!resp.ok) {
          throw new Error(`Failed to load GeoJSON for map "${name}" (HTTP ${resp.status})`);
        }
        return resp.json();
      })
      .then((geojson) => {
        echarts.registerMap(name, geojson);
      })
      .catch((err) => {
        delete mapRegistrations[name]; // allow a retry on the next render
        throw err;
      });
  }
  return mapRegistrations[name];
}

/**
 * Tame MapChart's ECharts roam to the geo maps' control scheme (enableMapZoom
 * parity): a plain wheel stays with the page (the shared hint flashes),
 * ⌘/Ctrl + scroll zooms, and the bottom-center "Reset view" pill restores the
 * full extent. The wheel is intercepted in the capture phase so zrender never
 * sees an unmodified scroll. Wired once per host element — updateChart calls
 * it on every map render, and it covers the fullscreen modal too (fullscreen
 * sets `_echarts_instance` on its own host); reads the instance live so a
 * theme-change re-init keeps working.
 */
function wireMapRoamGuard(el) {
  if (el._mapZoomChrome) return;
  const inst = el._echarts_instance;
  if (!inst) return;
  const container = inst.getDom();
  const chrome = mapZoomChrome(container);
  el._mapZoomChrome = chrome;

  const zoomed = () => {
    const live = el._echarts_instance;
    if (!live) return false;
    const s = ((live.getOption() || {}).series || [])[0] || {};
    const z = s.zoom == null ? 1 : Number(s.zoom);
    return Math.abs(z - 1) > 1e-6 || Array.isArray(s.center);
  };
  // Roam state lives in the ECharts option — re-read it after the interaction
  // the browser is still dispatching (hence the rAF).
  const sync = () => requestAnimationFrame(() => chrome.setZoomed(zoomed()));

  container.addEventListener(
    "wheel",
    (e) => {
      if (e.ctrlKey || e.metaKey) {
        sync(); // zrender zooms; reflect the new state
      } else {
        e.stopPropagation(); // plain scroll stays with the page
        if (!zoomed()) chrome.flashHint();
      }
    },
    { capture: true, passive: true }
  );
  ["mouseup", "touchend"].forEach((t) => container.addEventListener(t, sync));

  chrome.resetBtn.addEventListener("click", () => {
    const live = el._echarts_instance;
    if (live) live.dispatchAction({ type: "restore" });
    sync();
  });
}

/**
 * Update chart with new records
 * @param {HTMLElement} el - Chart element
 * @param {Array<Object>} records - Array of record objects
 * @param {Object} config - Chart configuration
 */
export function updateChart(el, records, config) {
  const echartsInstance = el._echarts_instance;
  if (!echartsInstance) {
    console.error("Chart update failed: no ECharts instance found");
    return;
  }

  // No rows → shared empty state for every chart type (incl. map: skip the
  // GeoJSON registration there's nothing to plot on). Clear any donut/facet
  // bookkeeping the resize handler would otherwise act on.
  if (isEmptyChartData(records)) {
    try {
      echartsInstance.setOption(forPrint(emptyChartOption(config)), true);
    } catch (error) {
      showError(el, error);
    }
    delete el._donutTotal;
    delete el._facetCount;
    delete el._chartRecords;
    return;
  }

  // Stash the drawn records so instance.repaint() (explain-annotation marks)
  // can rebuild the option without a data round-trip.
  el._chartRecords = records;

  if (config.type === "map") {
    wireMapRoamGuard(el);
    ensureMapRegistered(config)
      .then(() =>
        echartsInstance.setOption(forPrint(buildChartOption(config, records)), true)
      )
      .catch((error) => showError(el, error));
    return;
  }

  try {
    const option = forPrint(buildChartOption(config, records));
    echartsInstance.setOption(option, true);
    // Donuts get a glyph-centered total drawn over the hole. Stash it on the
    // element so the resize handler can reposition it without re-fetching.
    if (isDonutConfig(config)) {
      const total = donutTotal(records, config);
      el._donutTotal = total;
      positionDonutCenter(echartsInstance, total, valueFormatter(config));
    } else {
      delete el._donutTotal;
    }
    // Faceted pies are sized from the live pixel box (see sizeFacetedPies); stash
    // the facet count so the resize handler can re-fit without re-fetching.
    if (isFacetedPieConfig(config)) {
      const facetCount = (option.series || []).length;
      el._facetCount = facetCount;
      sizeFacetedPies(echartsInstance, config, facetCount);
    } else {
      delete el._facetCount;
    }
  } catch (error) {
    showError(el, error);
  }
}

/**
 * Show error state for a chart
 * @param {HTMLElement} el - Chart element
 * @param {Error} error - Error object
 */
export function showError(el, error) {
  const cardBody = el.querySelector(".card-body");
  const chartContainer = el.querySelector(".w-full.h-full");
  const message = error.message || "Failed to load chart data";
  const html = `<div class="alert alert-error">${esc(message)}</div>`;

  if (cardBody) {
    cardBody.innerHTML = html;
  } else if (chartContainer) {
    chartContainer.innerHTML = html;
  } else {
    el.innerHTML = html;
  }
}

/**
 * Resize all charts
 */
export function resizeAllCharts() {
  chartInstances.forEach((instance) => {
    if (instance.echartsInstance) {
      instance.echartsInstance.resize();
    }
  });
}

/**
 * Initialize all charts on the page
 */
export function initAllCharts() {
  document.querySelectorAll('[data-async-component="chart"]').forEach((el) => {
    initChart(el);
  });
}
