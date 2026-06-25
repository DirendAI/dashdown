// Dashdown CSV export utility.
//
// Builds a CSV from a query's current (filtered) result and triggers a browser
// download. Used by the per-table export affordance (see table.js): it reads the
// live filter state at click time and fetches the same data the page's tables
// show (shared fetchQueryData path), so it works in static exports (snapshot)
// and authed embeds (token) for free — there's no server-side export endpoint.

"use strict";

import { fetchQueryData } from "../core.js";

/**
 * RFC 4180 escaping: wrap a field in double quotes when it contains the
 * delimiter, a quote, CR, or LF, doubling any internal quotes.
 * @param {*} value
 * @param {string} delimiter
 * @returns {string}
 */
function csvField(value, delimiter) {
  if (value == null) return "";
  const s = String(value);
  if (
    s.indexOf('"') !== -1 ||
    s.indexOf("\r") !== -1 ||
    s.indexOf("\n") !== -1 ||
    s.indexOf(delimiter) !== -1
  ) {
    return '"' + s.replace(/"/g, '""') + '"';
  }
  return s;
}

/**
 * Serialize a `{columns, rows}` dataset to a CSV string.
 * @param {{columns: string[], rows: Array<Array<*>>}} data
 * @param {{headers?: boolean, delimiter?: string}} [opts]
 * @returns {string}
 */
export function toCsv(data, opts = {}) {
  const headers = opts.headers !== false; // default: include the header row
  const delimiter = opts.delimiter || ",";
  const columns = (data && data.columns) || [];
  const rows = (data && data.rows) || [];
  const lines = [];
  if (headers) lines.push(columns.map((c) => csvField(c, delimiter)).join(delimiter));
  for (const row of rows) {
    lines.push(columns.map((_, i) => csvField(row[i], delimiter)).join(delimiter));
  }
  // CRLF line endings — the RFC 4180 default and what Excel expects.
  return lines.join("\r\n");
}

/**
 * Trigger a browser download of `text` as `filename`.
 * @param {string} filename
 * @param {string} text
 */
export function downloadCsv(filename, text) {
  const blob = new Blob([text], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Revoke on the next tick so the download has started.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

/** Current filter values (a plain copy of the filters store). */
function currentFilters() {
  return { ...((window.Alpine && Alpine.store && Alpine.store("filters")) || {}) };
}

/**
 * Fetch a query's current filtered result and download it as CSV.
 * @param {string} queryName
 * @param {{filename?: string, headers?: boolean, delimiter?: string}} [opts]
 * @returns {Promise<void>}
 */
export async function exportQueryCsv(queryName, opts = {}) {
  const data = await fetchQueryData(queryName, {}, currentFilters());
  if (data && data.error) throw new Error(data.error);
  downloadCsv(opts.filename || `${queryName}.csv`, toCsv(data, opts));
}
