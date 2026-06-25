// Dashdown Counter Component
// Displays a single value as a large KPI-style counter, with an optional
// delta badge (▲/▼ vs. a comparison value) and an inline trend sparkline.

"use strict";

import { fetchQueryData, recordsOf, queryUsesFilters, bindLiveQuery, isLiveQuery, formatValue, resolveFormatOpts } from "../core.js";
import { mountFilterBadge } from "./filter_badge.js";

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
    if (!isLiveQuery(queryName))
      fetchQueryData(queryName, {}, filters)
        .then((data) => {
        const records = recordsOf(data);
        const value = extractValue(records, rowIndex, column, colIndex);
        updateCounter(el, displayNumber(value, config), prefix, suffix);

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
      .catch(() => updateCounter(el, "Error", prefix, suffix));

    // Sparkline fetches independently of the headline value.
    if (config.sparkline_query) {
      fetchQueryData(config.sparkline_query, {}, filters)
        .then((sd) => updateSparkline(el, sparkValues(recordsOf(sd), config.sparkline_column)))
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
 * Render an inline SVG sparkline into the counter's spark container.
 * Uses currentColor (set by the container's text-* class) for line + fill.
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

  const W = 120;
  const H = 32;
  const PAD = 2;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const n = values.length;

  const pts = values.map((v, i) => {
    const x = (i / (n - 1)) * W;
    const y = H - PAD - ((v - min) / range) * (H - PAD * 2);
    return `${x.toFixed(1)} ${y.toFixed(1)}`;
  });
  const line = "M" + pts.join(" L");
  const area = `${line} L${W} ${H} L0 ${H} Z`;

  host.innerHTML =
    `<svg class="w-full h-8" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">` +
    `<path d="${area}" fill="currentColor" fill-opacity="0.08" stroke="none"/>` +
    `<path d="${line}" fill="none" stroke="currentColor" stroke-width="2" vector-effect="non-scaling-stroke"/>` +
    `</svg>`;
}

/**
 * Initialize all counter components on the page
 */
export function initAllCounters() {
  document.querySelectorAll('[data-async-component="counter"]').forEach((el) => {
    initCounter(el);
  });
}
