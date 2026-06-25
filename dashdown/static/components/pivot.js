// Dashdown PivotTable Component
// Client-side cross-tabulation with drag-and-drop row/column axes.
// The full (filtered) record set lives in memory; re-pivoting on axis or
// aggregation changes is pure re-rendering, no re-fetch.

"use strict";

import { fetchQueryData, recordsOf, queryUsesFilters, applyFilters, queryCache, esc } from "../core.js";
import { showLoading, hideLoading } from "../loading.js";
import { mountFilterBadge } from "./filter_badge.js";

/**
 * Registry of all pivot instances
 * @type {Array<Object>}
 */
export const pivotInstances = [];

const MAX_PIVOT_COLS = 50;

const AGG_FNS = {
  sum: (vals) => numeric(vals).reduce((a, b) => a + b, 0),
  avg: (vals) => {
    const nums = numeric(vals);
    return nums.length ? nums.reduce((a, b) => a + b, 0) / nums.length : null;
  },
  count: (vals) => vals.length,
  min: (vals) => {
    const nums = numeric(vals);
    return nums.length ? Math.min(...nums) : null;
  },
  max: (vals) => {
    const nums = numeric(vals);
    return nums.length ? Math.max(...nums) : null;
  },
};

function numeric(vals) {
  return vals.map(Number).filter((v) => !isNaN(v));
}

function getQueryDefs() {
  return (window.Alpine && Alpine.store("queryDefs")) || {};
}

function waitForAlpine(callback) {
  if (window.Alpine) {
    callback();
  } else {
    document.addEventListener("alpine:init", callback);
  }
}

/**
 * Initialize a pivot component
 * @param {HTMLElement} el - Element with data-async-component="pivot"
 */
export function initPivot(el) {
  waitForAlpine(() => {
    const config = JSON.parse(el.dataset.config);
    const queryName = config.query_name;

    // Mutable pivot state: seeded from the server config, then driven by the
    // drag-and-drop toolbar. records holds the latest filtered rows.
    const state = {
      fields: [],
      rows: config.rows || [],
      cols: config.cols || [],
      values: config.values || null,
      agg: config.agg || "sum",
      records: [],
      seeded: false,
    };
    el._pivotState = state;

    const instance = {
      el,
      config,
      state,
      async render(filters = {}) {
        if (queryUsesFilters(queryName, filters, getQueryDefs())) {
          showLoading(el);
          try {
            const data = await fetchQueryData(queryName, {}, filters);
            hideLoading(el);
            updatePivot(el, recordsOf(data), config, state);
          } catch (error) {
            hideLoading(el);
            showError(el, error);
          }
        } else {
          const baseKey = queryName + JSON.stringify({});
          const baseData = queryCache[baseKey];
          if (!baseData) return;
          const allRecords = recordsOf(baseData);
          const dropdownMeta = (window.Alpine && Alpine.store) ? (Alpine.store("dropdownMeta") || {}) : {};
          updatePivot(el, applyFilters(allRecords, filters, dropdownMeta), config, state);
        }
      },
    };

    pivotInstances.push(instance);

    // "Filtered by" corner marker (reactive to filter state; self-gates).
    mountFilterBadge(el, queryName);

    Alpine.effect(() => {
      const filters = { ...(Alpine.store("filters") || {}) };
      instance.render(filters);
    });
  });
}

/**
 * Seed field assignments from the first batch of records: anything not
 * explicitly configured falls back to the first categorical column(s) for
 * the axes and the first numeric column for the value.
 */
function seedState(state, records) {
  state.fields = records.length ? Object.keys(records[0]) : [];
  const isNumericCol = (c) =>
    records.slice(0, 50).every((r) => r[c] == null || r[c] === "" || !isNaN(Number(r[c])));
  const categorical = state.fields.filter((c) => !isNumericCol(c));
  const numericCols = state.fields.filter(isNumericCol);

  state.rows = state.rows.filter((f) => state.fields.includes(f));
  state.cols = state.cols.filter((f) => state.fields.includes(f));
  if (!state.rows.length && categorical[0]) state.rows = [categorical[0]];
  if (!state.cols.length && categorical[1]) state.cols = [categorical[1]];
  if (!state.values || !state.fields.includes(state.values)) {
    state.values = numericCols.find((c) => !state.rows.includes(c) && !state.cols.includes(c)) || state.fields[0] || null;
  }
  state.seeded = true;
}

/**
 * Update pivot with new records (re-seeds field config on first data).
 */
export function updatePivot(el, records, config, state) {
  state.records = records;
  if (!state.seeded && records.length) seedState(state, records);
  renderPivot(el, config, state);
}

/**
 * Cross-tabulate records by the current state.
 * Returns row/col keys plus aggregated cell, total and grand values.
 */
export function computePivot(records, state) {
  const agg = AGG_FNS[state.agg] || AGG_FNS.sum;
  const keyOf = (r, fields) =>
    fields.length ? fields.map((f) => String(r[f] ?? "")).join(" / ") : "All";

  const cells = new Map(); // rowKey -> colKey -> raw values
  const rowVals = new Map();
  const colVals = new Map();
  const allVals = [];
  records.forEach((r) => {
    const v = r[state.values];
    if (v == null) return;
    const rk = keyOf(r, state.rows);
    const ck = keyOf(r, state.cols);
    if (!cells.has(rk)) cells.set(rk, new Map());
    const row = cells.get(rk);
    if (!row.has(ck)) row.set(ck, []);
    row.get(ck).push(v);
    if (!rowVals.has(rk)) rowVals.set(rk, []);
    rowVals.get(rk).push(v);
    if (!colVals.has(ck)) colVals.set(ck, []);
    colVals.get(ck).push(v);
    allVals.push(v);
  });

  const naturalSort = (a, b) => {
    const na = Number(a);
    const nb = Number(b);
    if (!isNaN(na) && !isNaN(nb)) return na - nb;
    return String(a).localeCompare(String(b));
  };
  const rowKeys = [...cells.keys()].sort(naturalSort);
  let colKeys = [...colVals.keys()].sort(naturalSort);
  let truncatedCols = 0;
  if (colKeys.length > MAX_PIVOT_COLS) {
    truncatedCols = colKeys.length - MAX_PIVOT_COLS;
    colKeys = colKeys.slice(0, MAX_PIVOT_COLS);
  }

  return {
    rowKeys,
    colKeys,
    truncatedCols,
    cell: (rk, ck) => {
      const vals = cells.get(rk) && cells.get(rk).get(ck);
      return vals && vals.length ? agg(vals) : null;
    },
    rowTotal: (rk) => agg(rowVals.get(rk) || []),
    colTotal: (ck) => agg(colVals.get(ck) || []),
    grandTotal: () => (allVals.length ? agg(allVals) : null),
  };
}

function formatCell(v) {
  if (v == null || (typeof v === "number" && isNaN(v))) return "";
  if (typeof v === "number") {
    return Number.isInteger(v) ? v.toLocaleString() : v.toLocaleString(undefined, { maximumFractionDigits: 2 });
  }
  return String(v);
}

function chipHTML(field) {
  return (
    `<span class="badge badge-outline badge-sm cursor-move select-none" ` +
    `draggable="true" data-pivot-field="${esc(field)}">${esc(field)}</span>`
  );
}

function zoneHTML(label, zone, fields) {
  return (
    `<div class="flex flex-col gap-1">` +
    `<span class="text-xs font-semibold opacity-60">${esc(label)}</span>` +
    `<div class="dashdown-pivot-zone flex flex-wrap gap-1 items-center rounded-lg border border-dashed border-base-300 p-2" ` +
    `data-pivot-zone="${zone}" style="min-width:7rem;min-height:2.2rem">` +
    fields.map(chipHTML).join("") +
    `</div></div>`
  );
}

function toolbarHTML(state) {
  const assigned = new Set([...state.rows, ...state.cols]);
  const unassigned = state.fields.filter((f) => !assigned.has(f));
  const aggOptions = Object.keys(AGG_FNS)
    .map((a) => `<option value="${a}"${a === state.agg ? " selected" : ""}>${a}</option>`)
    .join("");
  const valueOptions = state.fields
    .map((f) => `<option value="${esc(f)}"${f === state.values ? " selected" : ""}>${esc(f)}</option>`)
    .join("");
  return (
    `<div class="dashdown-pivot-toolbar flex flex-wrap gap-3 items-end mb-3 text-sm">` +
    zoneHTML("Fields", "fields", unassigned) +
    zoneHTML("Rows", "rows", state.rows) +
    zoneHTML("Columns", "cols", state.cols) +
    `<div class="flex flex-col gap-1">` +
    `<span class="text-xs font-semibold opacity-60">Value</span>` +
    `<select class="select select-bordered select-sm" data-pivot-values>${valueOptions}</select>` +
    `</div>` +
    `<div class="flex flex-col gap-1">` +
    `<span class="text-xs font-semibold opacity-60">Aggregation</span>` +
    `<select class="select select-bordered select-sm" data-pivot-agg>${aggOptions}</select>` +
    `</div>` +
    `</div>`
  );
}

function tableHTML(state) {
  if (!state.records.length) {
    return `<div class="text-sm opacity-70 py-4">${esc(state.emptyMessage || "No data available")}</div>`;
  }
  const pivot = computePivot(state.records, state);
  const corner = state.rows.length ? state.rows.join(" / ") : "";

  let html = '<div class="overflow-x-auto"><table class="table table-zebra table-sm w-full">';
  html += `<thead><tr><th class="px-3 py-2">${esc(corner)}</th>`;
  pivot.colKeys.forEach((ck) => {
    html += `<th class="px-3 py-2 text-right">${esc(ck)}</th>`;
  });
  html += `<th class="px-3 py-2 text-right font-bold">Total</th></tr></thead><tbody>`;
  pivot.rowKeys.forEach((rk) => {
    html += `<tr><td class="px-3 py-2 font-medium">${esc(rk)}</td>`;
    pivot.colKeys.forEach((ck) => {
      html += `<td class="px-3 py-2 text-right">${formatCell(pivot.cell(rk, ck))}</td>`;
    });
    html += `<td class="px-3 py-2 text-right font-semibold">${formatCell(pivot.rowTotal(rk))}</td></tr>`;
  });
  html += `<tr class="font-semibold"><td class="px-3 py-2">Total</td>`;
  pivot.colKeys.forEach((ck) => {
    html += `<td class="px-3 py-2 text-right">${formatCell(pivot.colTotal(ck))}</td>`;
  });
  html += `<td class="px-3 py-2 text-right">${formatCell(pivot.grandTotal())}</td></tr>`;
  html += "</tbody></table></div>";
  if (pivot.truncatedCols) {
    html += `<div class="text-sm text-base-content/70 mt-2">Showing first ${MAX_PIVOT_COLS} columns (${pivot.truncatedCols} more hidden)</div>`;
  }
  return html;
}

/**
 * Render toolbar + pivot table into the card body and wire up interactions.
 */
function renderPivot(el, config, state) {
  state.emptyMessage = config.empty_message;
  const body = el.querySelector(".card-body") || el;
  let html = "";
  if (config.title) {
    html += `<div class="dashdown-table-title">${esc(config.title)}</div>`;
  }
  html += toolbarHTML(state);
  html += tableHTML(state);
  body.innerHTML = html;
  attachToolbarHandlers(el, config, state);
}

function attachToolbarHandlers(el, config, state) {
  const rerender = () => renderPivot(el, config, state);

  el.querySelectorAll("[data-pivot-field]").forEach((chip) => {
    chip.addEventListener("dragstart", (e) => {
      e.dataTransfer.setData("text/plain", chip.dataset.pivotField);
      e.dataTransfer.effectAllowed = "move";
    });
  });

  el.querySelectorAll("[data-pivot-zone]").forEach((zone) => {
    zone.addEventListener("dragover", (e) => {
      e.preventDefault();
      zone.classList.add("border-primary");
    });
    zone.addEventListener("dragleave", () => zone.classList.remove("border-primary"));
    zone.addEventListener("drop", (e) => {
      e.preventDefault();
      const field = e.dataTransfer.getData("text/plain");
      if (!field || !state.fields.includes(field)) return;
      state.rows = state.rows.filter((f) => f !== field);
      state.cols = state.cols.filter((f) => f !== field);
      const target = zone.dataset.pivotZone;
      if (target === "rows") state.rows.push(field);
      if (target === "cols") state.cols.push(field);
      rerender();
    });
  });

  const valuesSel = el.querySelector("[data-pivot-values]");
  if (valuesSel) {
    valuesSel.addEventListener("change", () => {
      state.values = valuesSel.value;
      rerender();
    });
  }
  const aggSel = el.querySelector("[data-pivot-agg]");
  if (aggSel) {
    aggSel.addEventListener("change", () => {
      state.agg = aggSel.value;
      rerender();
    });
  }
}

/**
 * Show error state for a pivot
 */
export function showError(el, error) {
  const cardBody = el.querySelector(".card-body");
  const message = error.message || "Failed to load pivot data";
  const html = `<div class="alert alert-error">${esc(message)}</div>`;
  if (cardBody) {
    cardBody.innerHTML = html;
  } else {
    el.innerHTML = html;
  }
}

/**
 * Initialize all pivots on the page
 */
export function initAllPivots() {
  document.querySelectorAll('[data-async-component="pivot"]').forEach((el) => {
    initPivot(el);
  });
}
