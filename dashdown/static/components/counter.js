// Dashdown Counter Component
// Displays a single value as a large KPI-style counter, with an optional
// delta badge (▲/▼ vs. a comparison value) and an inline trend sparkline.

"use strict";

import { fetchQueryData, recordsOf, queryUsesFilters, bindLiveQuery, isLiveQuery, formatValue, resolveFormatOpts, readBrandingConfig } from "../core.js";
import { showLoading, hideLoading } from "../loading.js";
import { mountFilterBadge } from "./filter_badge.js";
import { currentDefaultPalette, onThemeChange } from "./echarts_theme.js";

function getQueryDefs() {
  return (window.Alpine && Alpine.store("queryDefs")) || {};
}

/** Format the headline number per the config's format/currency/decimals attrs,
 * passing the "-"/"Error" sentinels through untouched. */
function displayNumber(value, cfg) {
  if (value === null || value === undefined) return "-";
  return formatValue(value, cfg.format, resolveFormatOpts(cfg));
}

/** Pull a single value out of a record set by column name / index / position. */
function extractValue(records, rowIndex, column, colIndex) {
  const row = records[rowIndex];
  if (!row) return undefined;
  if (column) return row[column];
  const keys = Object.keys(row);
  if (colIndex !== undefined) return row[keys[colIndex]];
  return row[keys[0]];
}

/** Count the decimal places in a finite number (0 for integers). Used to keep
 * the count-up animation's frames at the same precision as the final value. */
function decimalPlaces(n) {
  if (!isFinite(n)) return 0;
  const i = String(n).indexOf(".");
  return i === -1 ? 0 : String(n).length - i - 1;
}

/** Percent change from `previous` to `current`, or null if not computable. */
function computeDelta(current, previous) {
  const c = Number(current);
  const p = Number(previous);
  if (!isFinite(c) || !isFinite(p) || p === 0) return null;
  return ((c - p) / Math.abs(p)) * 100;
}

/** Numeric series for the sparkline, from a chosen (or first numeric) column. */
function sparkValues(records, column) {
  if (!records.length) return [];
  let col = column;
  if (!col) {
    const keys = Object.keys(records[0]);
    col =
      keys.find((k) => typeof records[0][k] === "number") ||
      keys.find((k) => isFinite(Number(records[0][k])));
  }
  if (!col) return [];
  return records.map((r) => Number(r[col])).filter((v) => isFinite(v));
}

/**
 * Initialize a counter component
 * @param {HTMLElement} el - Element with data-async-component="counter"
 */
export function initCounter(el) {
  const config = JSON.parse(el.dataset.config);
  const queryName = config.query_name;
  const rowIndex = config.row || 0;
  const column = config.column;
  const colIndex = config.index;
  const prefix = config.prefix || "";
  const suffix = config.suffix || "";
  const invert = !!config.invert_delta;

  function render(filters = {}) {
    if (!queryUsesFilters(queryName, filters, getQueryDefs())) return;
    // Live queries are WS-first: the headline value comes from the socket
    // (below), so skip the one-shot fetch — it can't surface a hard error on a
    // flaky source, and avoids a redundant request. (A live counter's delta
    // badge therefore comes from a static `delta=` only; compare-query deltas
    // need the non-live fetch.) The sparkline still fetches independently.
    if (!isLiveQuery(queryName)) {
      // First load: the server-rendered skeleton is the loading state
      // (showLoading self-gates on it). Re-fetches float the shared spinner
      // overlay over the card, so a slow query is visibly in flight instead
      // of silently showing the stale value.
      showLoading(el);
      fetchQueryData(queryName, {}, filters)
        .then((data) => {
          hideLoading(el);
          const records = recordsOf(data);
          const value = extractValue(records, rowIndex, column, colIndex);
          setCounterValue(el, value, config, prefix, suffix);

          // Delta badge: explicit value wins, else derive from the compare query.
          if (config.delta !== undefined) {
            updateDelta(el, parseFloat(config.delta), invert);
          } else if (config.compare_query) {
            fetchQueryData(config.compare_query, {}, filters)
              .then((cmp) => {
                const prev = extractValue(
                  recordsOf(cmp),
                  config.compare_row || 0,
                  config.compare_column || column,
                  config.compare_index !== undefined ? config.compare_index : colIndex,
                );
                const pct = computeDelta(value, prev);
                if (pct !== null) updateDelta(el, pct, invert);
              })
              .catch(() => {});
          }
        })
        .catch(() => {
          hideLoading(el);
          updateCounter(el, "Error", prefix, suffix);
        });
    }

    // Sparkline fetches independently of the headline value.
    if (config.sparkline_query) {
      fetchQueryData(config.sparkline_query, {}, filters)
        .then((sd) => updateSparkline(el, sparkValues(recordsOf(sd), config.sparkline_column)))
        .catch(() => {});
    }

    // Breakdown strip fetches independently too. The records are kept on the
    // element so a theme toggle can repaint with the other theme's palette
    // without refetching.
    if (config.breakdown_query) {
      fetchQueryData(config.breakdown_query, {}, filters)
        .then((bd) => {
          el._dashdownBreakdownRecords = recordsOf(bd);
          updateBreakdown(el, el._dashdownBreakdownRecords, config);
        })
        .catch(() => {});
    }

    // Live mode: push fresh headline values without a refetch. (Delta/sparkline
    // stay on the filter-driven path — they're typically separate queries.)
    // No-op for non-live queries / static builds.
    bindLiveQuery(el, queryName, filters, (data) => {
      if (!data || data.error) return;
      const value = extractValue(recordsOf(data), rowIndex, column, colIndex);
      updateCounter(el, displayNumber(value, config), prefix, suffix);
    });
  }

  // Single reactive path: subscribe to the filters store via an Alpine effect.
  // The effect runs once immediately (initial render) and re-runs whenever any
  // filter value changes or a new filter key is added. No custom events.
  const subscribe = () => {
    Alpine.effect(() => {
      const filters = { ...(Alpine.store("filters") || {}) };
      render(filters);
    });
  };
  if (window.Alpine) {
    subscribe();
  } else {
    document.addEventListener("alpine:init", subscribe);
  }

  // The palettes differ per theme (dark hues are lighter), so a theme toggle
  // repaints the strip from the cached records — same reason chart.js
  // re-inits its charts on toggle.
  if (config.breakdown_query) {
    onThemeChange(() => {
      if (el.isConnected && el._dashdownBreakdownRecords) {
        updateBreakdown(el, el._dashdownBreakdownRecords, config);
      }
    });
  }

  // "Filtered by" corner marker (reactive to filter state; self-gates).
  mountFilterBadge(el, queryName);
}

/**
 * Update counter display
 * @param {HTMLElement} el - Counter element
 * @param {string} value - Value to display
 * @param {string} prefix - Prefix text
 * @param {string} suffix - Suffix text
 */
function updateCounter(el, value, prefix, suffix) {
  const valueEl = el.querySelector(".dashdown-counter-value");
  if (!valueEl) return;
  valueEl.textContent = `${prefix}${value}${suffix}`;
}

/**
 * Set the headline value, animating a count-up on the *first* reveal of a
 * finite number (a brief ease-out from 0 → value, re-formatted every frame so
 * currency/percent/separators stay correct). Sentinels ("-"/Error), non-numeric
 * values, prefers-reduced-motion, and every subsequent update render instantly —
 * so a filter change or a live tick snaps rather than re-animating.
 * @param {HTMLElement} el - Counter element
 * @param {*} value - Raw value pulled from the record set
 * @param {Object} cfg - Counter config (format/currency/decimals…)
 * @param {string} prefix
 * @param {string} suffix
 */
function setCounterValue(el, value, cfg, prefix, suffix) {
  const valueEl = el.querySelector(".dashdown-counter-value");
  if (!valueEl) return;
  const num = Number(value);
  const prefersReduced =
    window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const setText = (v) => {
    valueEl.textContent = `${prefix}${displayNumber(v, cfg)}${suffix}`;
  };
  // A compact headline ("3.34B") keeps the exact value one hover away.
  if (cfg.format === "compact" && isFinite(num)) {
    const full = formatValue(num, "number", resolveFormatOpts(cfg));
    valueEl.title = `${prefix}${full}${suffix}`;
  }

  // Instant path: anything not a fresh finite number, or motion is unwanted.
  if (
    value === null ||
    value === undefined ||
    !isFinite(num) ||
    prefersReduced ||
    el._dashdownCounted
  ) {
    if (el._dashdownCountRaf) cancelAnimationFrame(el._dashdownCountRaf);
    el._dashdownCounted = true;
    setText(value);
    return;
  }

  el._dashdownCounted = true;
  const DURATION = 750;
  // Round every in-between frame to the final value's precision: an explicit
  // `decimals=`, else the target's own decimal places (capped to shrug off
  // float noise). Without this the eased float renders a long, jittery decimal
  // tail that's wider than the final number.
  const explicit = cfg.decimals;
  const stepDecimals =
    explicit != null && explicit !== ""
      ? Number(explicit)
      : Math.min(decimalPlaces(num), 4);
  const now = () =>
    window.performance && performance.now ? performance.now() : Date.now();
  const start = now();
  const ease = (t) => 1 - Math.pow(1 - t, 3); // ease-out cubic
  const frame = () => {
    const t = Math.min(1, (now() - start) / DURATION);
    if (t < 1) {
      setText(Number((num * ease(t)).toFixed(stepDecimals)));
      el._dashdownCountRaf = requestAnimationFrame(frame);
    } else {
      setText(num); // land exactly on the final formatted value
    }
  };
  if (el._dashdownCountRaf) cancelAnimationFrame(el._dashdownCountRaf);
  el._dashdownCountRaf = requestAnimationFrame(frame);
}

/**
 * Render the ▲/▼ delta badge.
 * @param {HTMLElement} el - Counter element
 * @param {number} pct - Percentage change (signed)
 * @param {boolean} invert - Treat a decrease as the "good" direction
 */
function updateDelta(el, pct, invert) {
  const badge = el.querySelector(".dashdown-counter-delta");
  if (!badge || !isFinite(pct)) return;

  let dir = 0;
  if (pct > 0.05) dir = 1;
  else if (pct < -0.05) dir = -1;
  const good = invert ? -dir : dir;

  let tone;
  if (good > 0) tone = "text-success bg-success/10";
  else if (good < 0) tone = "text-error bg-error/10";
  else tone = "text-base-content/60 bg-base-200";

  const arrow = dir > 0 ? "▲" : dir < 0 ? "▼" : "—";
  badge.className =
    "dashdown-counter-delta text-xs font-medium rounded-full px-2 py-0.5 whitespace-nowrap " +
    tone;
  badge.textContent = `${arrow} ${Math.abs(pct).toFixed(1)}%`;
}

/**
 * Render an inline SVG sparkline into the counter's spark container — the
 * full-bleed background layer pinned to the card's bottom edge (layout in
 * dashdown.css). Uses currentColor (set by the container's text-* class) for
 * line + fill.
 *
 * The geometry is deliberately size-independent, so the drawing stays correct
 * through any later resize (grid reflow, window resize, print) with no
 * redraw/observer: line + area live in a normalized viewBox stretched to the
 * layer (`preserveAspectRatio="none"`); `non-scaling-stroke` keeps the line
 * 2px. (No endpoint marker — see dashdown.css.)
 * @param {HTMLElement} el - Counter element
 * @param {number[]} values - Numeric series
 */
function updateSparkline(el, values) {
  const host = el.querySelector(".dashdown-counter-spark");
  if (!host) return;
  if (!values || values.length < 2) {
    host.innerHTML = "";
    return;
  }

  const W = 100; // normalized x — rendered edge-to-edge at any card width
  const H = 40; // matches the layer's design height (vertical scale ~1:1)
  const TOP = 3; // headroom so a peak doesn't kiss the text above
  const BOT = 2; // keep the 2px line's lower half inside the layer
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const n = values.length;

  const yAt = (v) => TOP + (1 - (v - min) / range) * (H - TOP - BOT);
  const pts = values.map((v, i) => {
    const x = (i / (n - 1)) * W;
    return `${x.toFixed(2)} ${yAt(v).toFixed(1)}`;
  });
  const line = "M" + pts.join(" L");
  const area = `${line} L${W} ${H} L0 ${H} Z`;

  host.innerHTML =
    `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">` +
    `<path d="${area}" fill="currentColor" fill-opacity="0.1" stroke="none"/>` +
    `<path d="${line}" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>` +
    `</svg>`;
}

/** The breakdown strip's palette — same priority as chart.js resolves series
 * colors (project `branding.palette`, else the theme default), so a counter's
 * segments match the chart next to it showing the same dimension. */
function breakdownPalette() {
  const branding = readBrandingConfig();
  if (branding && Array.isArray(branding.palette) && branding.palette.length) {
    return branding.palette;
  }
  return currentDefaultPalette();
}

/** `{label, value}` segments from the breakdown records: label from a chosen
 * (or first non-numeric) column, value from a chosen (or first numeric) column.
 * Non-finite and non-positive values are dropped — negatives don't compose
 * into a part-to-whole strip. Data order is kept (color follows the entity). */
function breakdownSegments(records, cfg) {
  if (!records.length) return [];
  const keys = Object.keys(records[0]);
  let valueCol = cfg.breakdown_column;
  if (!valueCol) {
    valueCol =
      keys.find((k) => typeof records[0][k] === "number") ||
      keys.find((k) => isFinite(Number(records[0][k])));
  }
  if (!valueCol) return [];
  let labelCol = cfg.breakdown_label;
  if (!labelCol) {
    labelCol =
      keys.find((k) => k !== valueCol && typeof records[0][k] !== "number") ||
      keys.find((k) => k !== valueCol);
  }
  const segments = [];
  records.forEach((r, i) => {
    const value = Number(r[valueCol]);
    if (!isFinite(value) || value <= 0) return;
    const label = labelCol ? String(r[labelCol]) : `#${i + 1}`;
    segments.push({ label, value });
  });
  return segments;
}

/** Share of total as compact display text ("45%", "<1%"). */
function pctText(value, total) {
  const pct = (value / total) * 100;
  return pct < 1 ? "<1%" : `${Math.round(pct)}%`;
}

/**
 * Render the composition strip ("one-row treemap") + its compact legend into
 * the counter's breakdown shells. Segment widths are proportional flex-grow
 * shares; the 2px flex gap lets the card surface do the separating (no
 * strokes), and the track's own rounding + overflow clip round only the outer
 * ends. Categorical hues are assigned in data order, never cycled: segments
 * beyond the palette fold into a single neutral "Other". Values live in
 * per-segment tooltips (formatted like the headline) and identity in the
 * legend — never text inside the slim segments.
 * @param {HTMLElement} el - Counter element
 * @param {Object[]} records - Breakdown query records
 * @param {Object} cfg - Counter config
 */
function updateBreakdown(el, records, cfg) {
  const bar = el.querySelector(".dashdown-counter-breakdown-bar");
  const legend = el.querySelector(".dashdown-counter-breakdown-legend");
  if (!bar || !legend) return;

  const palette = breakdownPalette();
  let segments = breakdownSegments(records || [], cfg);
  if (segments.length > palette.length) {
    const head = segments.slice(0, palette.length - 1);
    const rest = segments.slice(palette.length - 1);
    head.push({
      label: "Other",
      value: rest.reduce((sum, s) => sum + s.value, 0),
      other: true,
    });
    segments = head;
  }
  const total = segments.reduce((sum, s) => sum + s.value, 0);

  bar.textContent = "";
  legend.textContent = "";
  if (!segments.length || total <= 0) {
    bar.classList.add("is-empty");
    bar.title = "No data";
    return;
  }
  bar.classList.remove("is-empty");
  bar.removeAttribute("title");

  const fmtOpts = resolveFormatOpts(cfg);
  // What the legend prints beside each category name — the share (default),
  // the value (formatted like the headline), or both. The tooltip always
  // carries both, so this only chooses what's visible without hovering.
  const legendText = (seg) => {
    const value = formatValue(seg.value, cfg.format, fmtOpts);
    const pct = pctText(seg.value, total);
    if (cfg.breakdown_values === "value") return value;
    if (cfg.breakdown_values === "both") return `${value} · ${pct}`;
    return pct;
  };
  segments.forEach((seg, i) => {
    const tip = `${seg.label} — ${formatValue(seg.value, cfg.format, fmtOpts)} (${pctText(seg.value, total)})`;
    const piece = document.createElement("span");
    piece.className =
      "dashdown-counter-breakdown-seg" + (seg.other ? " dashdown-counter-breakdown-seg--other" : "");
    piece.style.flexGrow = String(seg.value / total);
    if (!seg.other) piece.style.background = palette[i];
    piece.title = tip;
    bar.appendChild(piece);

    if (cfg.breakdown_legend === false) return;
    const key = document.createElement("span");
    key.className = "dashdown-counter-breakdown-key";
    key.title = tip;
    const dot = document.createElement("i");
    if (seg.other) dot.className = "is-other";
    else dot.style.background = palette[i];
    key.appendChild(dot);
    key.appendChild(document.createTextNode(seg.label + " "));
    const pct = document.createElement("span");
    pct.className = "dashdown-counter-breakdown-pct";
    pct.textContent = legendText(seg);
    key.appendChild(pct);
    legend.appendChild(key);
  });
}

/**
 * Initialize all counter components on the page
 */
export function initAllCounters() {
  document.querySelectorAll('[data-async-component="counter"]').forEach((el) => {
    initCounter(el);
  });
}
