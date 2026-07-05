// Dashdown Table Component
// Self-contained table rendering with async data loading, humanized headers,
// per-column formatting, click-to-sort, and client-side pagination.

"use strict";

import { fetchQueryData, recordsOf, queryUsesFilters, esc, bindLiveQuery, isLiveQuery, formatValue, resolveFormatOpts } from "../core.js";
import { showLoading, hideLoading } from "../loading.js";
import { openExportModal } from "./export_modal.js";
import { exportQueryCsv } from "./export.js";
import { mountFilterBadge } from "./filter_badge.js";

// Download glyph (currentColor) — no icon font / external asset.
const EXPORT_ICON =
  '<svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" ' +
  'viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" ' +
  'stroke-linejoin="round" d="M12 3v12m0 0l-4-4m4 4l4-4M4 17v2a2 2 0 002 2h12a2 2 0 ' +
  '002-2v-2"/></svg>';

// Expand glyph (four corner arrows) for the fullscreen button — the delegated
// listener in fullscreen.js catches clicks on `.dashdown-table-fullscreen`.
const FULLSCREEN_ICON =
  '<svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" ' +
  'viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" ' +
  'stroke-linejoin="round" d="M8 3H4a1 1 0 00-1 1v4m0 8v4a1 1 0 001 1h4m8 0h4a1 1 0 ' +
  '001-1v-4m0-8V4a1 1 0 00-1-1h-4"/></svg>';

// Re-export so existing importers of the table-scoped name keep working.
export { formatValue };

/**
 * Registry of all table instances
 * @type {Array<TableInstance>}
 */
export const tableInstances = [];

function getQueryDefs() {
  return (window.Alpine && Alpine.store("queryDefs")) || {};
}

/* ------------------------------------------------------------------ *
 * Pure helpers (presentation only — never mutate the source records) *
 * ------------------------------------------------------------------ */

/** Fill `{column}` placeholders in a link pattern from a record (missing → ""). */
function fillPattern(pattern, row) {
  return String(pattern).replace(/\{(\w+)\}/g, (_, k) => {
    const v = row[k];
    return v == null ? "" : String(v);
  });
}

/**
 * Resolve a drill-down target for the current hosting. `row_link`/`link_pattern`
 * are authored as absolute routes (e.g. `/detail-pages/{id}`), correct as-is on
 * the dev server (served at the origin root). A static build is hosted against a
 * relative `<base>` so it works under a sub-path (project GitHub Pages); there an
 * absolute `/route` bypasses the base and 404s, so re-root it as the same
 * `<route>/index.html` the nav uses (mirrors build.root_link). The presence of a
 * `<base>` marks a static build; external/in-page links are left alone.
 */
function navHref(href) {
  if (!href || href[0] !== "/" || href.startsWith("//")) return href;
  if (!document.querySelector("base[href]")) return href; // dev server
  const i = href.search(/[#?]/);
  const path = i === -1 ? href : href.slice(0, i);
  const tail = i === -1 ? "" : href.slice(i);
  const route = path.replace(/^\/+|\/+$/g, "");
  return new URL((route ? `${route}/index.html` : "index.html") + tail, document.baseURI).href;
}

/** snake_case / kebab-case / camelCase column name → "Title Case". */
export function humanizeHeader(name) {
  return String(name)
    .replace(/[_-]+/g, " ")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

/** A column is numeric when every non-null value parses as a finite number. */
function isNumericColumn(records, col) {
  let seen = false;
  for (const r of records) {
    const v = r[col];
    if (v == null || v === "") continue;
    if (typeof v === "number") {
      if (!isFinite(v)) return false;
    } else if (!isFinite(Number(v))) {
      return false;
    }
    seen = true;
  }
  return seen;
}

/** Stable-ish sort by a column; numeric columns compare numerically, nulls last. */
function sortRecords(records, sort, numericCols) {
  if (!sort || !sort.col) return records;
  const { col, dir } = sort;
  const mul = dir === "desc" ? -1 : 1;
  const numeric = numericCols.has(col);
  return [...records].sort((a, b) => {
    const av = a[col];
    const bv = b[col];
    const an = av == null || av === "";
    const bn = bv == null || bv === "";
    if (an && bn) return 0;
    if (an) return 1; // nulls always last, regardless of direction
    if (bn) return -1;
    if (numeric) return (Number(av) - Number(bv)) * mul;
    return String(av).localeCompare(String(bv)) * mul;
  });
}

/**
 * Per-column {min, max} over the full record set, for heatmap shading. Computed
 * from all records (not the current page) so a cell's color is stable as the user
 * sorts/searches/paginates. Only numeric, finite values count.
 */
function heatmapStats(records, cols) {
  const stats = {};
  cols.forEach((col) => {
    let min = Infinity;
    let max = -Infinity;
    for (const r of records) {
      const v = r[col];
      if (v == null || v === "") continue;
      const n = Number(v);
      if (!isFinite(n)) continue;
      if (n < min) min = n;
      if (n > max) max = n;
    }
    if (min !== Infinity) stats[col] = { min, max };
  });
  return stats;
}

/**
 * Inline `background-color` for a heatmap cell, or "" when the value isn't
 * numeric. Colors are pulled from the active DaisyUI theme (so they follow the
 * project's `--p`/`--su`/`--er` and any `custom.css` override) as translucent
 * overlays over the card surface, so cell text stays legible in light and dark.
 *   - sequential: low→high in the theme's primary color (`--p`).
 *   - diverging: the theme's error→success colors (`--er`/`--su`), centered at 0
 *     when the column spans sign, else at the column midpoint.
 */
function heatmapStyle(value, stat, scheme) {
  if (!stat) return "";
  const n = Number(value);
  if (value == null || value === "" || !isFinite(n)) return "";
  const { min, max } = stat;
  if (scheme === "diverging") {
    const center = min < 0 && max > 0 ? 0 : (min + max) / 2;
    const span = Math.max(max - center, center - min) || 1;
    const t = (n - center) / span; // -1 … 1
    const alpha = Math.min(0.5, Math.abs(t) * 0.5);
    const tone = t >= 0 ? "--su" : "--er"; // theme success / error
    return `background-color: oklch(var(${tone}) / ${alpha.toFixed(3)})`;
  }
  const span = max - min || 1;
  const t = (n - min) / span; // 0 … 1
  const alpha = 0.06 + t * 0.44; // faint floor so even the min cell is tinted
  return `background-color: oklch(var(--p) / ${alpha.toFixed(3)})`;
}

/** Page numbers to render, with `"…"` gaps when there are many pages. */
function paginationItems(current, pages) {
  if (pages <= 7) return Array.from({ length: pages }, (_, i) => i);
  const items = [];
  const window = new Set([0, pages - 1, current - 1, current, current + 1]);
  let prev = -1;
  for (let i = 0; i < pages; i++) {
    if (!window.has(i)) continue;
    if (i - prev > 1) items.push("…");
    items.push(i);
    prev = i;
  }
  return items;
}

/* ------------------------------------------------------------------ *
 * Init + lifecycle                                                   *
 * ------------------------------------------------------------------ */

/**
 * Initialize a table component
 * @param {HTMLElement} el - Table element with data-async-component="table"
 */
export function initTable(el) {
  // Wait for Alpine to be available before proceeding
  waitForAlpine(() => {
    const config = JSON.parse(el.dataset.config);
    const queryName = config.query_name;
    const linkColumn = el.dataset.linkColumn || config.link_column || null;
    const linkPattern = el.dataset.linkPattern || config.link_pattern || null;

    // Store config + view state on the element so sort/page clicks can re-render
    // without re-fetching.
    el._tableConfig = config;
    el._records = [];
    el._page = 0;
    el._search = "";
    el._sort = config.sort ? { col: config.sort, dir: config.sort_dir || "asc" } : null;

    wireInteractions(el);

    // Keep skeleton until data is loaded - no need to modify DOM yet

    // Create table instance
    const instance = {
      el,
      config,
      linkColumn,
      linkPattern,
      async render(filters = {}) {
        if (queryUsesFilters(queryName, filters, getQueryDefs())) {
          // Server-side filtering: re-fetch with filter params
          showLoading(el);
          try {
            const data = await fetchQueryData(queryName, {}, filters);
            const records = recordsOf(data);
            hideLoading(el);
            updateTable(el, records, config);
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
            updateTable(el, recordsOf(data), config);
          } catch (error) {
            hideLoading(el);
            showError(el, error);
          }
        }
      },
    };

    // Register instance
    tableInstances.push(instance);

    // "Filtered by" marker. The table's top-right corner already holds the
    // search/export controls, so host it inline in that toolbar (it migrates
    // there once the toolbar is built, else falls back to the corner).
    mountFilterBadge(el, queryName, { inlineInto: ".dashdown-table-actions" });

    // Register in component store if available
    if (Alpine && Alpine.store && Alpine.store("components")) {
      Alpine.store("components").registerTable(instance);
    }

    // Single reactive path: subscribe to the filters store. The effect runs
    // once immediately (initial render) and re-runs whenever any filter value
    // changes or a new filter key is added. No custom events.
    Alpine.effect(() => {
      const filters = { ...(Alpine.store("filters") || {}) };
      // Live queries are WS-first (skip the one-shot fetch — the socket delivers
      // and self-heals). updateTable resets to page 1, fine for the live
      // latest-rows tables this targets. No-op for non-live / static builds.
      if (!isLiveQuery(queryName)) instance.render(filters);
      bindLiveQuery(el, queryName, filters, (data) => {
        if (data && !data.error) updateTable(el, recordsOf(data), config);
      });
    });
  });
}

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
 * Delegate sort-header and pagination clicks once per table element.
 * @param {HTMLElement} el - Table element
 */
function wireInteractions(el) {
  if (el._tableWired) return;
  el._tableWired = true;

  // Quick-filter box: lives in the persistent toolbar (not re-rendered), so the
  // input keeps focus while only the view region below it re-renders.
  el.addEventListener("input", (e) => {
    const box = e.target.closest(".dashdown-table-search");
    if (!box || !el.contains(box)) return;
    el._search = box.value || "";
    el._page = 0;
    renderTable(el);
  });

  el.addEventListener("click", (e) => {
    const exportBtn = e.target.closest(".dashdown-table-export");
    if (exportBtn && el.contains(exportBtn)) {
      onExportClick(el, exportBtn);
      return;
    }
    const th = e.target.closest("[data-sort-col]");
    if (th && el.contains(th)) {
      const col = th.getAttribute("data-sort-col");
      if (el._sort && el._sort.col === col) {
        el._sort.dir = el._sort.dir === "asc" ? "desc" : "asc";
      } else {
        el._sort = { col, dir: "asc" };
      }
      el._page = 0;
      renderTable(el);
      return;
    }
    const pageBtn = e.target.closest("[data-page]");
    if (pageBtn && el.contains(pageBtn) && !pageBtn.disabled) {
      const p = pageBtn.getAttribute("data-page");
      const pages = el._pages || 1;
      if (p === "prev") el._page = Math.max(0, el._page - 1);
      else if (p === "next") el._page = Math.min(pages - 1, el._page + 1);
      else el._page = Math.min(pages - 1, Math.max(0, parseInt(p, 10) || 0));
      renderTable(el);
      return;
    }

    // Whole-row drill-down (row_link): a row carrying data-row-href navigates on
    // click. Clicks on a real link/button inside the row keep their own behavior,
    // so the accessible anchor (and any cell link) isn't double-fired. Cmd/Ctrl
    // opens a new tab, matching normal link expectations.
    const rowEl = e.target.closest("tr[data-row-href]");
    if (rowEl && el.contains(rowEl) && !e.target.closest("a, button")) {
      const href = rowEl.getAttribute("data-row-href");
      if (href) {
        if (e.metaKey || e.ctrlKey) window.open(href, "_blank");
        else window.location.href = href;
      }
    }
  });
}

/**
 * Open the CSV export modal for this table, then download the current filtered
 * result with the chosen settings (header row + delimiter).
 * @param {HTMLElement} el - Table element
 * @param {HTMLButtonElement} btn - The clicked export button
 */
async function onExportClick(el, btn) {
  const config = el._tableConfig || {};
  const settings = await openExportModal({
    title: "Export CSV",
    submitLabel: "Download",
    fields: [
      { name: "headers", label: "Include header row", type: "checkbox", default: true },
      {
        name: "delimiter",
        label: "Delimiter",
        type: "select",
        default: ",",
        options: [
          { value: ",", label: "Comma ( , )" },
          { value: ";", label: "Semicolon ( ; )" },
          { value: "\t", label: "Tab" },
        ],
      },
    ],
  });
  if (!settings) return; // cancelled
  btn.disabled = true;
  try {
    await exportQueryCsv(config.query_name, {
      filename: config.export_filename || `${config.query_name}.csv`,
      headers: settings.headers,
      delimiter: settings.delimiter,
    });
  } catch (err) {
    console.error("CSV export failed:", err);
  } finally {
    btn.disabled = false;
  }
}

/* ------------------------------------------------------------------ *
 * Rendering                                                          *
 * ------------------------------------------------------------------ */

/** True if any cell in the row (raw or formatted) contains the query substring. */
function rowMatches(r, q, cols, formats, fmtOpts) {
  for (const col of cols) {
    const v = r[col];
    if (v != null && String(v).toLowerCase().includes(q)) return true;
    if (formats[col]) {
      const f = formatValue(v, formats[col], fmtOpts);
      if (f && f.toLowerCase().includes(q)) return true;
    }
  }
  return false;
}

/**
 * Build the table + pagination markup (the part that re-renders on sort/page/
 * search). The title and search box live in the persistent shell, not here.
 * @param {HTMLElement} el - Table element (reads `_tableConfig`, `_records`, `_sort`, `_page`, `_search`)
 * @returns {string} - HTML string
 */
export function buildTableView(el) {
  const config = el._tableConfig || {};
  const allRecords = el._records || [];
  const headers = config.headers || {};
  const formats = config.formats || {};
  const fmtOpts = resolveFormatOpts(config);
  const linkColumn = config.link_column;
  const linkPattern = config.link_pattern;
  const rowLink = config.row_link;

  if (allRecords.length === 0) {
    el._pages = 1;
    return `<div class="text-sm text-base-content/60 py-6 text-center">${esc(config.empty_message || "No data available")}</div>`;
  }

  // Column metadata is derived from the full set so alignment stays stable as
  // the user searches/sorts.
  const cols = Object.keys(allRecords[0]);
  const numericCols = new Set(cols.filter((c) => isNumericColumn(allRecords, c)));
  const alignRight = (c) => numericCols.has(c) && c !== linkColumn;

  // Heatmap shading: `config.heatmap` is `true` (every numeric column) or a list
  // of column names; only numeric columns are ever shaded. Stats come from the
  // full set so colors don't shift between pages.
  const heatmapScheme = config.heatmap_scheme || "sequential";
  let heatmapCols = null;
  let heatmapStatsMap = null;
  if (config.heatmap) {
    const requested = config.heatmap === true ? cols : config.heatmap;
    heatmapCols = new Set((requested || []).filter((c) => numericCols.has(c)));
    if (heatmapCols.size) heatmapStatsMap = heatmapStats(allRecords, heatmapCols);
    else heatmapCols = null;
  }

  // Quick-filter (client-side substring match), then sort, then paginate.
  const q = (el._search || "").trim().toLowerCase();
  const records = q
    ? allRecords.filter((r) => rowMatches(r, q, cols, formats, fmtOpts))
    : allRecords;

  if (records.length === 0) {
    el._pages = 1;
    return `<div class="text-sm text-base-content/60 py-6 text-center">No rows match “${esc(el._search.trim())}”</div>`;
  }

  let html = "";
  const sorted = sortRecords(records, el._sort, numericCols);
  const total = sorted.length;
  const pageSize = config.page_size > 0 ? config.page_size : total;
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const page = Math.min(el._page || 0, pages - 1);
  el._page = page;
  el._pages = pages;
  const start = page * pageSize;
  const view = sorted.slice(start, start + pageSize);

  html += '<div class="overflow-x-auto"><table class="table w-full">';

  // Headers — humanized + sortable, numeric columns right-aligned.
  html += '<thead><tr>';
  cols.forEach((col) => {
    const label = headers[col] || humanizeHeader(col);
    const isSorted = el._sort && el._sort.col === col;
    const align = alignRight(col) ? "text-right" : "text-left";
    const tone = isSorted ? "text-primary" : "text-base-content/60 hover:text-base-content";
    const arrow = isSorted ? (el._sort.dir === "desc" ? " ▼" : " ▲") : "";
    html +=
      `<th data-sort-col="${esc(col)}" ` +
      `class="px-4 py-3 text-xs font-medium uppercase tracking-wide cursor-pointer select-none ${align} ${tone}">` +
      `${esc(label)}${arrow}</th>`;
  });
  html += "</tr></thead><tbody>";

  // The column that carries a real <a> for keyboard / screen-reader users. An
  // explicit link_column wins; otherwise, when the whole row is clickable
  // (row_link), the first column gets the anchor so the drill-down stays
  // reachable without a mouse.
  const anchorCol = linkColumn || (rowLink ? cols[0] : null);

  // Rows — hover state, formatted + aligned cells, optional cell link / clickable
  // row. A row_link row carries data-row-href; wireInteractions navigates on click.
  view.forEach((r) => {
    const rowHref = rowLink ? navHref(fillPattern(rowLink, r)) : null;
    const trAttrs = rowHref
      ? ` class="hover cursor-pointer" data-row-href="${esc(rowHref)}"`
      : ' class="hover"';
    html += `<tr${trAttrs}>`;
    cols.forEach((col) => {
      const raw = r[col];
      const display = esc(formatValue(raw, formats[col], fmtOpts));
      const tdClass = "px-4 py-2.5" + (alignRight(col) ? " text-right tabular-nums" : "");
      const bg =
        heatmapCols && heatmapCols.has(col)
          ? heatmapStyle(raw, heatmapStatsMap[col], heatmapScheme)
          : "";
      const styleAttr = bg ? ` style="${bg}"` : "";

      // A cell renders as a link when it's the configured link_column (using
      // link_pattern) or the accessible anchor for a clickable row (row_link).
      let href = null;
      if (linkColumn && linkPattern && col === linkColumn) href = navHref(fillPattern(linkPattern, r));
      else if (rowHref && col === anchorCol) href = rowHref;

      if (href != null) {
        html += `<td class="${tdClass}"${styleAttr}><a href="${esc(href)}" class="text-primary hover:underline">${display}</a></td>`;
      } else {
        html += `<td class="${tdClass}"${styleAttr}>${display}</td>`;
      }
    });
    html += "</tr>";
  });

  html += "</tbody></table></div>";

  // Pagination footer — only when there's more than one page.
  if (pages > 1) {
    const from = start + 1;
    const to = start + view.length;
    const btn = "px-2.5 py-1 rounded text-sm border border-base-300 hover:bg-base-200";
    let nav = "";
    nav += `<button data-page="prev" class="${btn}${page === 0 ? " opacity-40" : ""}"${page === 0 ? " disabled" : ""}>‹</button>`;
    paginationItems(page, pages).forEach((it) => {
      if (it === "…") {
        nav += `<span class="px-1 text-base-content/40">…</span>`;
        return;
      }
      const active = it === page;
      const cls = active
        ? "px-2.5 py-1 rounded text-sm bg-primary text-primary-content font-medium"
        : btn;
      nav += `<button data-page="${it}" class="${cls}">${it + 1}</button>`;
    });
    nav += `<button data-page="next" class="${btn}${page === pages - 1 ? " opacity-40" : ""}"${page === pages - 1 ? " disabled" : ""}>›</button>`;

    html +=
      '<div class="flex flex-wrap items-center justify-between gap-2 px-1 pt-3 mt-1 text-xs text-base-content/60">' +
      `<span>Showing <span class="font-medium text-base-content/80">${from}–${to}</span> of ` +
      `<span class="font-medium text-base-content/80">${total}</span></span>` +
      `<div class="flex items-center gap-1">${nav}</div></div>`;
  }

  return html;
}

/**
 * Build the persistent shell (title + optional search box + an empty view
 * container) into the card body, once. The search input must survive view
 * re-renders so it keeps focus while the user types.
 * @param {HTMLElement} el - Table element
 */
function ensureShell(el) {
  const host = el.querySelector(".card-body") || el;
  if (host.querySelector(".dashdown-table-view")) return host;

  const config = el._tableConfig || {};
  // Auto-search (the default) only appears once a table has enough rows to be
  // worth filtering; an explicit `search` always shows. `search=false` → off.
  const showSearch =
    config.search && (!config.search_auto || (el._records || []).length >= 2);
  const showExport = config.export && (el._records || []).length > 0;
  // Fullscreen viewer button — on by default; `fullscreen=false` opts out. The
  // modal's own (re-rendered) table passes fullscreen:false so it isn't nested.
  const showFullscreen =
    config.fullscreen !== false && (el._records || []).length > 0;
  let html = "";
  if (config.title || showSearch || showExport || showFullscreen) {
    html += '<div class="dashdown-table-toolbar flex items-center justify-between gap-3 mb-3">';
    html += config.title
      ? `<div class="dashdown-table-title font-semibold">${esc(config.title)}</div>`
      : "<div></div>";
    html += '<div class="dashdown-table-actions flex items-center gap-2">';
    if (showSearch) {
      html +=
        `<input type="text" class="dashdown-table-search input input-sm input-bordered w-full max-w-xs" ` +
        `placeholder="${esc(config.search_placeholder || "Search…")}" aria-label="Search table">`;
    }
    if (showExport) {
      html +=
        `<button type="button" class="dashdown-table-export btn btn-ghost btn-sm btn-square" ` +
        `title="Export CSV" aria-label="Export CSV">${EXPORT_ICON}</button>`;
    }
    if (showFullscreen) {
      html +=
        `<button type="button" class="dashdown-table-fullscreen btn btn-ghost btn-sm btn-square" ` +
        `title="View fullscreen" aria-label="View fullscreen">${FULLSCREEN_ICON}</button>`;
    }
    html += "</div></div>";
  }
  html += '<div class="dashdown-table-view"></div>';
  host.innerHTML = html;
  return host;
}

/**
 * Re-render the view region (table + pagination) from current sort/page/search
 * state, leaving the persistent toolbar (and its focused search input) intact.
 * @param {HTMLElement} el - Table element
 */
export function renderTable(el) {
  const host = ensureShell(el);
  const view = host.querySelector(".dashdown-table-view");
  view.innerHTML = buildTableView(el);
}

/**
 * Render a table into an arbitrary host element from a records array + a plain
 * config object — the reuse hook for the fullscreen modal's "view as table"
 * (fullscreen.js). Wires the delegated sort/search/export handlers on the host
 * (a clone/detached node is otherwise inert) and paints via the normal
 * updateTable path, so the fullscreen table gets sort, pagination, search and
 * CSV export for free.
 * @param {HTMLElement} host - A detached/standalone element to render into
 * @param {Array<Object>} records - Array of record objects
 * @param {Object} config - Table configuration (must carry query_name for CSV)
 */
export function renderTableInto(host, records, config) {
  wireInteractions(host);
  updateTable(host, records, config);
}

/**
 * Store a fresh record set and render from page 1.
 * @param {HTMLElement} el - Table element
 * @param {Array<Object>} records - Array of record objects
 * @param {Object} config - Table configuration
 */
export function updateTable(el, records, config) {
  const limit = config && config.limit ? config.limit : 100;
  el._tableConfig = config || el._tableConfig;
  el._records = (records || []).slice(0, limit);
  el._page = 0;

  // Remove any skeleton loaders before first render.
  el.querySelectorAll(".skeleton").forEach((s) => s.remove());

  renderTable(el);
}

/**
 * Show error state for a table
 * @param {HTMLElement} el - Table element
 * @param {Error} error - Error object
 */
export function showError(el, error) {
  const cardBody = el.querySelector(".card-body");
  const message = error.message || "Failed to load table data";
  const html = `<div class="alert alert-error">${esc(message)}</div>`;

  if (cardBody) {
    cardBody.innerHTML = html;
  } else {
    el.innerHTML = html;
  }
}

/**
 * Initialize all tables on the page
 */
export function initAllTables() {
  document.querySelectorAll('[data-async-component="table"]').forEach((el) => {
    initTable(el);
  });
}
