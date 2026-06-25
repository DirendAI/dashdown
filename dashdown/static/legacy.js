// Dashdown Legacy Component Support
// Handles old component attributes (data-dashdown-chart, data-dashdown-table)
// for backward compatibility

"use strict";

import { recordsOf, applyFilters, readDatasets } from "./core.js";
import { showLoading, hideLoading } from "./loading.js";

/**
 * Dropdown metadata for legacy mode
 */
let dropdownMeta = {};

/**
 * Registry for legacy components
 */
let legacyCharts = [];
let legacyTables = [];

/**
 * Local reference to datasets for legacy mode
 */
let allDatasets = {};

/**
 * Collect dropdown metadata from legacy dropdowns
 */
function collectDropdownMeta() {
  const ddEls = Array.from(document.querySelectorAll("[data-dashdown-dropdown]"));
  ddEls.forEach(function (el) {
    try {
      const meta = JSON.parse(el.dataset.dashdownDropdown);
      dropdownMeta[meta.name] = meta;
    } catch (e) {
      // Ignore
    }
  });
}

/**
 * Initialize legacy charts
 */
function initLegacyCharts() {
  const chartEls = Array.from(document.querySelectorAll("[data-dashdown-chart]"));

  legacyCharts = chartEls.map(function (el) {
    const cfg = JSON.parse(el.dataset.dashdownChart);
    const container = el.querySelector(".w-full.h-full") || el;

    // Check if echarts is available
    if (typeof echarts !== "undefined") {
      const inst = echarts.init(container);
      return { el: el, cfg: cfg, inst: inst };
    }
    return null;
  }).filter(Boolean);

  // Set up resize handler
  window.addEventListener("resize", function () {
    legacyCharts.forEach(function (c) {
      if (c.inst) c.inst.resize();
    });
  });
}

/**
 * Initialize legacy tables
 */
function initLegacyTables() {
  legacyTables = Array.from(document.querySelectorAll("[data-dashdown-table]"));
}

/**
 * Build chart option (same as chart.js)
 */
function buildChartOption(config, records) {
  const { type, x, y, title, series_by, sort_by } = config;
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

  if (series_by) {
    const groups = {};
    const xSet = [];

    sortedRecords.forEach((r) => {
      const k = String(r[series_by]);
      const xv = r[x];
      if (xSet.indexOf(xv) === -1) xSet.push(xv);
      (groups[k] = groups[k] || {})[xv] = r[y];
    });

    xCategories = xSet;
    series = Object.keys(groups).map((name) => ({
      name,
      type,
      data: xCategories.map((xv) => groups[name][xv] ?? null),
      smooth: type === "line",
    }));
  } else {
    xCategories = sortedRecords.map((r) => r[x]);
    series = [
      {
        type,
        data: sortedRecords.map((r) => r[y]),
        smooth: type === "line",
      },
    ];
  }

  return {
    title: title
      ? { text: title, left: "left", textStyle: { fontSize: 14 } }
      : undefined,
    tooltip: { trigger: "axis" },
    legend: series_by ? { top: "bottom" } : undefined,
    grid: {
      left: 50,
      right: 20,
      top: title ? 40 : 20,
      bottom: series_by ? 40 : 30,
    },
    xAxis: { type: "category", data: xCategories },
    yAxis: { type: "value" },
    series,
  };
}

/**
 * Render legacy charts with filters
 */
function renderLegacyCharts(filters) {
  legacyCharts.forEach(function (c) {
    const records = applyFilters(
      recordsOf(allDatasets[c.cfg.dataset]),
      filters,
      dropdownMeta
    );
    if (c.inst) {
      const option = buildChartOption(c.cfg, records);
      c.inst.setOption(option, true);
    }
  });
}

/**
 * Render legacy tables with filters
 */
function renderLegacyTables(filters) {
  legacyTables.forEach(function (el) {
    const name = el.dataset.dashdownTable;
    const records = applyFilters(
      recordsOf(allDatasets[name]),
      filters,
      dropdownMeta
    );

    const linkCol = el.dataset.linkColumn || null;
    const linkPattern = el.dataset.linkPattern || null;
    const tbody = el.querySelector("tbody");
    if (!tbody) return;

    const cols = Array.from(el.querySelectorAll("thead th")).map((th) => th.textContent);
    tbody.innerHTML = records
      .map((r) => {
        const cells = cols
          .map((c) => {
            const v = r[c] == null ? "" : String(r[c]);
            if (linkCol && linkPattern && c === linkCol) {
              const href = linkPattern.replace(/\{(\w+)\}/g, (_, k) => {
                const rv = r[k];
                return rv == null ? "" : String(rv);
              });
              return `<td><a href="${href}">${v}</a></td>`;
            }
            return `<td>${v}</td>`;
          })
          .join("");
        return "<tr>" + cells + "</tr>";
      })
      .join("");
  });
}

/**
 * Re-render all legacy components
 */
function rerenderLegacy(filters) {
  renderLegacyCharts(filters);
  renderLegacyTables(filters);
}

/**
 * Initialize legacy mode
 */
export function initLegacy() {
  allDatasets = readDatasets();

  // Collect metadata
  collectDropdownMeta();

  // Initialize components
  initLegacyCharts();
  initLegacyTables();

  // Set up filter change listener
  document.addEventListener("change", function (e) {
    const select = e.target.closest("select");
    if (!select) return;

    const label = select.closest("[data-dashdown-dropdown]");
    if (!label) return;

    const filters = {};
    document.querySelectorAll("[data-dashdown-dropdown] select").forEach(function (sel) {
      const metaEl = sel.closest("[data-dashdown-dropdown]");
      if (!metaEl) return;

      let meta;
      try {
        meta = JSON.parse(metaEl.dataset.dashdownDropdown);
      } catch (_) {
        return;
      }
      if (meta && meta.name) filters[meta.name] = sel.value;
    });

    rerenderLegacy(filters);
  });

  // Initial render
  rerenderLegacy({});
}

/**
 * Check if there are legacy components on the page
 */
export function hasLegacyComponents() {
  return (
    document.querySelector("[data-dashdown-chart], [data-dashdown-table]") !== null
  );
}
