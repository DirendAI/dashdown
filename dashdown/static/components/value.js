// Dashdown Value Component
// Displays a single value from a query result

"use strict";

import { fetchQueryData, recordsOf, bindLiveQuery, isLiveQuery, formatValue, resolveFormatOpts } from "../core.js";
import { showLoading, hideLoading } from "../loading.js";

/** Pull the single display value out of a query payload, per the component's
 * row/column/index config, then format it. Returns "-" when there's nothing
 * to show. `cfg` carries the format/currency/decimals/prefix/suffix attrs. */
function displayValue(data, rowIndex, column, colIndex, cfg) {
  const records = recordsOf(data);
  const row = records[rowIndex];
  if (!row) return "-";

  let value;
  if (column) {
    value = row[column];
  } else if (colIndex !== undefined) {
    const keys = Object.keys(row);
    value = row[keys[colIndex]];
  } else {
    // Default: first column of first row
    const keys = Object.keys(row);
    value = row[keys[0]];
  }
  if (value === null || value === undefined) return "-";
  const formatted = formatValue(value, cfg.format, resolveFormatOpts(cfg));
  return `${cfg.prefix || ""}${formatted}${cfg.suffix || ""}`;
}

/**
 * Initialize a value component
 * @param {HTMLElement} el - Element with data-async-component="value"
 */
export function initValue(el) {
  const config = JSON.parse(el.dataset.config);
  const queryName = config.query_name;
  const rowIndex = config.row || 0;
  const column = config.column;
  const colIndex = config.index;

  function render(filters = {}) {
    // Live queries are WS-first (skip the one-shot fetch — the socket delivers
    // and self-heals); non-live / static builds fetch with the current filters.
    if (!isLiveQuery(queryName))
      fetchQueryData(queryName, {}, filters)
        .then((data) => {
          el.textContent = displayValue(data, rowIndex, column, colIndex, config);
        })
        .catch((error) => {
          console.error(`Value component error for ${queryName}:`, error);
          el.textContent = "Error";
        });

    // Live mode: replace the value on each push. No-op for non-live / static.
    bindLiveQuery(el, queryName, filters, (data) => {
      if (data && !data.error) {
        el.textContent = displayValue(data, rowIndex, column, colIndex, config);
      }
    });
  }

  // Single reactive path: subscribe to the filters store via an Alpine effect, so
  // a semantic metric (or any filtered query) re-fetches when a filter changes —
  // the same pattern counter.js / chart.js use. Runs once immediately.
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
}

/**
 * Initialize all value components on the page
 */
export function initAllValues() {
  document.querySelectorAll('[data-async-component="value"]').forEach((el) => {
    initValue(el);
  });
}
