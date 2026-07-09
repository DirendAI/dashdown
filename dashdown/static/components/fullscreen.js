// Dashdown fullscreen viewer.
//
// A hover-revealed ⛶ button on every chart card (beside the `explain` sparkle)
// and one in every table toolbar opens a near-fullscreen <dialog>. A chart opens
// with a Chart / Table switcher (see the same data big, or as its rows); a table
// just opens larger. The modal is view-agnostic (a RENDERERS map keyed by view),
// so more views can be re-added later without reworking it.
//
// Pure client-side progressive enhancement: it reuses the *cached*
// fetchQueryData result the card already loaded (no extra request, live filter
// state) and the existing chart/table renderers, so it works on the dev server,
// in static `dashdown build` exports, and in authed embeds with no server
// change. Built on the native <dialog> element (top-layer + ::backdrop +
// Escape-to-close for free), the same idiom as export_modal.js — see the
// `.dashdown-fullscreen-modal` CSS for the styling DaisyUI's un-vendored
// `.modal-box` would otherwise supply.

"use strict";

import { fetchQueryData, recordsOf, esc } from "../core.js";
import { currentEChartsTheme, onThemeChange } from "./echarts_theme.js";
import { updateChart } from "./chart.js";
import { renderTableInto } from "./table.js";
import { getMapRenderer, loadGeometry } from "./_geo.js";

// Live filter snapshot, mirroring export.js — the modal opens against the same
// (queryName, {}, filters) key the card fetched, so fetchQueryData is a cache
// hit and the view matches what's on screen.
function currentFilters() {
  return { ...((window.Alpine && Alpine.store && Alpine.store("filters")) || {}) };
}

function safeParse(json) {
  try {
    return json ? JSON.parse(json) : null;
  } catch (e) {
    return null;
  }
}

const VIEW_LABELS = { chart: "Chart", map: "Map", table: "Table" };

const CLOSE_ICON =
  '<svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" ' +
  'viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" ' +
  'stroke-linejoin="round" d="M6 6l12 12M18 6L6 18"/></svg>';

// The trigger buttons themselves are emitted where each component builds its own
// chrome — the chart's ⛶ server-side in _chart_card (line_chart.py, beside the
// `explain` sparkle), the table's ⛶ client-side in table.js's ensureShell()
// toolbar. Both carry a distinct class that this module's delegated listener
// (initFullscreen) catches, so the component modules never import fullscreen.js.

/**
 * Open the fullscreen modal.
 *
 * @param {Object} opts
 * @param {string} opts.queryName   query to (re)fetch — cached, so free
 * @param {string} [opts.title]     shown in the modal header
 * @param {Object|null} opts.chartConfig  the chart/map's data-config (null for a table)
 * @param {string[]} opts.views     subset of ["chart","map","table"]
 * @param {string} opts.defaultView the initially-active view
 */
export function openFullscreenView(opts) {
  const { queryName, title, chartConfig, views, defaultView } = opts;

  const dialog = document.createElement("dialog");
  dialog.className = "modal dashdown-fullscreen-modal";

  const tabs =
    views.length > 1
      ? '<div class="dashdown-fs-switch" role="tablist">' +
        views
          .map(
            (v) =>
              `<button type="button" class="dashdown-fs-tab${
                v === defaultView ? " is-active" : ""
              }" role="tab" data-view="${v}" aria-selected="${v === defaultView}">${
                VIEW_LABELS[v]
              }</button>`
          )
          .join("") +
        "</div>"
      : "";

  dialog.innerHTML =
    '<div class="modal-box dashdown-fullscreen-box">' +
    '<div class="dashdown-fs-header">' +
    `<div class="dashdown-fs-title">${esc(title || "")}</div>` +
    tabs +
    '<button type="button" class="dashdown-fs-close btn btn-ghost btn-sm btn-square" ' +
    `data-action="close" aria-label="Close">${CLOSE_ICON}</button>` +
    "</div>" +
    '<div class="dashdown-fs-body"></div>' +
    "</div>";

  const body = dialog.querySelector(".dashdown-fs-body");

  // --- Chart-view lifecycle (the only view that owns disposable resources) ----
  let echartsInstance = null;
  let unsubscribeTheme = null;
  let resizeRaf = 0;

  function disposeChart() {
    window.removeEventListener("resize", onResize);
    if (unsubscribeTheme) {
      unsubscribeTheme();
      unsubscribeTheme = null;
    }
    if (echartsInstance) {
      try {
        echartsInstance.dispose();
      } catch (e) {
        /* already disposed */
      }
      echartsInstance = null;
    }
  }

  function onResize() {
    if (!echartsInstance) return;
    cancelAnimationFrame(resizeRaf);
    resizeRaf = requestAnimationFrame(() => {
      if (echartsInstance) echartsInstance.resize();
    });
  }

  function renderChart(records) {
    body.innerHTML = '<div class="dashdown-fs-chart"></div>';
    const container = body.querySelector(".dashdown-fs-chart");
    const paint = () => {
      const inst = echarts.init(container, currentEChartsTheme());
      // updateChart reads el._echarts_instance and handles empty/map/donut/facet.
      container._echarts_instance = inst;
      echartsInstance = inst;
      updateChart(container, records, chartConfig);
      // ECharts measures its container, so size it after it's laid out visibly.
      requestAnimationFrame(() => inst.resize());
    };
    paint();
    window.addEventListener("resize", onResize);
    // Re-theme the modal chart on a light/dark toggle (dispose + re-init, the
    // same contract chart.js follows for the on-page instances).
    unsubscribeTheme = onThemeChange(() => {
      if (echartsInstance) {
        try {
          echartsInstance.dispose();
        } catch (e) {
          /* noop */
        }
      }
      paint();
    });
  }

  // --- Map-view lifecycle -----------------------------------------------------
  // The geo draw()s are one-shot DOM builders; the only disposable resource is
  // ChoroplethTime's animation interval, parked on the host element.
  let mapHost = null;

  function disposeMap() {
    if (mapHost && mapHost._dashdownMapTimer) {
      clearInterval(mapHost._dashdownMapTimer);
      mapHost._dashdownMapTimer = null;
    }
    mapHost = null;
  }

  function renderMap(records) {
    // `dashdown-map` scopes the shared map-shell layout CSS; the geo draw()
    // renders its normal card body into this host, just modal-sized.
    body.innerHTML = '<div class="dashdown-map dashdown-fs-map"></div>';
    const host = body.querySelector(".dashdown-fs-map");
    mapHost = host;
    const draw = getMapRenderer(chartConfig.type);
    loadGeometry(chartConfig)
      .then((world) => {
        // The modal chrome already shows the title — don't repeat it in the card.
        draw(host, world, records, { ...chartConfig, title: "" });
      })
      .catch((err) => {
        body.innerHTML = `<div class="alert alert-error">${esc(
          (err && err.message) || "Failed to load map"
        )}</div>`;
      });
  }

  function renderTable(records) {
    body.innerHTML = '<div class="dashdown-fs-table"></div>';
    const host = body.querySelector(".dashdown-fs-table");
    renderTableInto(host, records, {
      query_name: queryName,
      // Header lives in the modal chrome; keep the table's own toolbar for
      // search + CSV export, but no nested fullscreen button.
      title: "",
      search: true,
      export: true,
      fullscreen: false,
      // Fullscreen is where you go to see everything — lift the on-card 100-row cap.
      limit: 5000,
    });
  }

  const RENDERERS = { chart: renderChart, map: renderMap, table: renderTable };

  function showView(view, records) {
    disposeChart(); // tear down a prior chart/map view before swapping content
    disposeMap();
    dialog
      .querySelectorAll(".dashdown-fs-tab")
      .forEach((t) => {
        const on = t.dataset.view === view;
        t.classList.toggle("is-active", on);
        t.setAttribute("aria-selected", on ? "true" : "false");
      });
    (RENDERERS[view] || renderTable)(records);
  }

  // --- Data (cached) then first paint --------------------------------------
  let records = [];
  function loadAndRender(view) {
    body.innerHTML = '<div class="dashdown-fs-loading skeleton"></div>';
    fetchQueryData(queryName, {}, currentFilters())
      .then((data) => {
        if (data && data.error) throw new Error(data.error);
        records = recordsOf(data);
        showView(view, records);
      })
      .catch((err) => {
        body.innerHTML = `<div class="alert alert-error">${esc(
          (err && err.message) || "Failed to load data"
        )}</div>`;
      });
  }

  // --- Wiring ---------------------------------------------------------------
  dialog.addEventListener("click", (e) => {
    // Click in the empty area around the box closes (box fills most of the view).
    if (e.target === dialog) return dialog.close();
    if (e.target.closest('[data-action="close"]')) return dialog.close();
    const tab = e.target.closest(".dashdown-fs-tab");
    if (tab && !tab.classList.contains("is-active")) {
      showView(tab.dataset.view, records);
    }
  });
  dialog.addEventListener("close", () => {
    disposeChart();
    disposeMap();
    dialog.remove();
  });

  document.body.appendChild(dialog);
  dialog.showModal();
  loadAndRender(defaultView);
}

// ---------------------------------------------------------------------------
// Delegated open-triggers — one document-level listener wires BOTH the chart
// corner button and the table toolbar button. Living here (not in chart.js /
// table.js) keeps those modules free of a fullscreen import cycle.
// ---------------------------------------------------------------------------

function openChartFullscreen(hostEl) {
  const config = hostEl._chartConfig || safeParse(hostEl.dataset.config) || {};
  const queryName = config.query_name || hostEl.dataset.queryName;
  if (!queryName) return;
  openFullscreenView({
    queryName,
    title: config.title || "",
    chartConfig: config,
    views: ["chart", "table"],
    defaultView: "chart",
  });
}

function openMapFullscreen(hostEl) {
  // Prefer the live config object (the geo modules stash it, like initChart):
  // it carries runtime state the dataset snapshot lacks — open-explain
  // annotations, the active metric toggle — so the modal matches the card.
  const config = hostEl._chartConfig || safeParse(hostEl.dataset.config) || {};
  const queryName = config.query_name || hostEl.dataset.queryName;
  if (!queryName) return;
  openFullscreenView({
    queryName,
    title: config.title || "",
    chartConfig: config,
    views: ["map", "table"],
    defaultView: "map",
  });
}

function openTableFullscreen(hostEl) {
  const config = hostEl._tableConfig || safeParse(hostEl.dataset.config) || {};
  const queryName = config.query_name || hostEl.dataset.queryName;
  if (!queryName) return;
  openFullscreenView({
    queryName,
    title: config.title || "",
    chartConfig: null,
    views: ["table"],
    defaultView: "table",
  });
}

/**
 * Install the single delegated click listener for every fullscreen trigger.
 * Called from app.js init() outside the async-component gate (like mermaid /
 * copy-code), so a prose page with a lone chart still wires up.
 */
export function initFullscreen() {
  if (document.__dashdownFullscreenWired) return;
  document.__dashdownFullscreenWired = true;
  document.addEventListener("click", (e) => {
    const chartBtn = e.target.closest(".dashdown-chart-expand-btn");
    if (chartBtn) {
      // The same ⛶ sits on charts and the SVG geo maps — dispatch on the
      // host's async-component type (a map iff a geo module registered it).
      const host = chartBtn.closest("[data-async-component]");
      if (!host) return;
      if (getMapRenderer(host.dataset.asyncComponent)) openMapFullscreen(host);
      else if (host.dataset.asyncComponent === "chart") openChartFullscreen(host);
      return;
    }
    const tableBtn = e.target.closest(".dashdown-table-fullscreen");
    if (tableBtn) {
      const host = tableBtn.closest('[data-async-component="table"]');
      if (host) openTableFullscreen(host);
    }
  });
}
