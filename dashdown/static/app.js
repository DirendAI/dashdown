// Dashdown Application Initialization
// Main entry point that orchestrates all components

"use strict";

import { readBuildConfig } from "./core.js";
import { initStores } from "./store.js";
import { initAllTables } from "./components/table.js";
import { initAllCharts, resizeAllCharts } from "./components/chart.js";
import { initAllDropdowns } from "./components/dropdown.js";
import { initAllSearches } from "./components/search.js";
import { initAllToggles } from "./components/toggle.js";
import { initAllButtonGroups } from "./components/button_group.js";
import { initAllComboboxes } from "./components/combobox.js";
import { initAllTimeGrains } from "./components/timegrain.js";
import { initAllTabs } from "./components/tabs.js";
import { initAllValues } from "./components/value.js";
import { initAllCounters } from "./components/counter.js";
import { initAllPivots } from "./components/pivot.js";
import { initAllChoroplethTimes } from "./components/choropleth_time.js";
import { initAllChoroplethFacets } from "./components/choropleth_facets.js";
import { initAllBivariateMaps } from "./components/bivariate_map.js";
import { initAllBubbleMaps } from "./components/bubble_map.js";
import { initAllDotDensityMaps } from "./components/dot_density_map.js";
import { initAllAsks, initAllExplains } from "./components/ask.js";
import { initPageHeader, initBuildStamp } from "./components/page_header.js";
import { initEmbedFrame } from "./components/embed_frame.js";
import { initEmbedUI } from "./components/embed_ui.js";
import { initAllMermaid } from "./components/mermaid.js";
import { initAllCopyCode } from "./components/copy_code.js";
import { initFullscreen } from "./components/fullscreen.js";
import { initAllSiteSearches } from "./components/site_search.js";
import { initPrint, initPdfButton } from "./components/print.js";
import { initLegacy } from "./legacy.js";

// Global flag to prevent double initialization
let initialized = false;

/**
 * Initialize the Dashdown application
 */
export function initDashdown() {
  if (initialized) return;
  initialized = true;

  // Initialize Alpine stores first - this now polls until Alpine is ready
  initStores(() => {
    // Initialize all async components after stores are ready
    initComponents();
  });

  // Set up live reload
  setupLiveReload();

  // Set up global resize handler
  setupResizeHandler();
}

/**
 * Initialize all components on the page
 */
function initComponents() {
  // Process dropdowns first (they may be needed for filtering)
  initAllDropdowns();

  // Process search filters (URL sync + chip suppression registration)
  initAllSearches();

  // Process toggle (boolean) filters (URL sync + chip suppression + default seed)
  initAllToggles();

  // Process button-group (segmented single-select) filters
  initAllButtonGroups();

  // Process combobox (searchable high-cardinality single-select) filters
  initAllComboboxes();

  // Process time-grain pickers (URL sync + chip suppression + default seed)
  initAllTimeGrains();

  // Process tabs before charts, so a chart in the initially-active panel is
  // visible (and measurable) when ECharts initializes.
  initAllTabs();

  // Process charts
  initAllCharts();

  // Process tables
  initAllTables();

  // Process value components
  initAllValues();

  // Process counter components
  initAllCounters();

  // Process pivot tables
  initAllPivots();

  // Process the SVG geo maps (choropleths, bubble, dot-density)
  initAllChoroplethTimes();
  initAllChoroplethFacets();
  initAllBivariateMaps();
  initAllBubbleMaps();
  initAllDotDensityMaps();

  // Process ask (LLM commentary) components
  initAllAsks();

  // Wire chart `explain` buttons (their ask footers initialize on first open)
  initAllExplains();
}

/**
 * Set up SSE for live reload during development.
 *
 * The EventSource must be explicitly closed on `pagehide`: when you navigate
 * away, Chrome freezes the page into the back/forward cache (bfcache) WITHOUT
 * closing open connections, so the reload stream keeps occupying one of the
 * browser's ~6 connections-per-host (HTTP/1.1). After ~6 navigations every slot
 * is held by a frozen page's stream and the next click stalls until one times
 * out. Closing on `pagehide` frees the slot immediately; `pageshow` re-opens it
 * if the page is later restored from bfcache.
 */
function setupLiveReload() {
  // A static export has no dev server / reload endpoint — skip it so the page
  // doesn't endlessly retry a dead EventSource. A live *production* server
  // (create_app(dev=False)) sets __dashdownNoLiveReload for the same reason:
  // there's no file watcher, so the stream would only waste a connection.
  if (window.__dashdownNoLiveReload) return;
  const build = readBuildConfig();
  if (build && build.static) return;

  let es = null;

  function open() {
    if (es) return;
    try {
      es = new EventSource("/_dashdown/reload");
      es.onmessage = () => {
        // Save scroll position
        const scrollY = window.scrollY;
        window.location.reload();
        // Restore scroll position (best-effort, after reload)
        window.addEventListener(
          "load",
          () => {
            window.scrollTo(0, scrollY);
          },
          { once: true }
        );
      };
    } catch (e) {
      // Ignore errors (SSE not supported or endpoint not available)
      es = null;
    }
  }

  function close() {
    if (es) {
      try {
        es.close();
      } catch (e) {
        /* already closed */
      }
      es = null;
    }
  }

  open();
  // Release the connection before the page is frozen/unloaded so it can't
  // linger in bfcache and exhaust the connection pool.
  window.addEventListener("pagehide", close);
  // Re-open if this page is restored from bfcache (back/forward navigation).
  window.addEventListener("pageshow", (e) => {
    if (e.persisted) open();
  });
}

/**
 * Set up window resize handler for charts
 */
function setupResizeHandler() {
  // Use a debounced resize handler
  let resizeTimeout;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimeout);
    resizeTimeout = setTimeout(() => {
      resizeAllCharts();
    }, 100);
  });
}

/**
 * Check if there are any async components on the page
 * @returns {boolean}
 */
function hasAsyncComponents() {
  return document.querySelector("[data-async-component]") !== null;
}

/**
 * Check if there are legacy components on the page
 * @returns {boolean}
 */
function hasLegacyComponents() {
  return (
    document.querySelector("[data-dashdown-chart], [data-dashdown-table]") !== null
  );
}

/**
 * Initialize based on available components
 */
function init() {
  // Independent of component init so the listener is registered before any
  // data fetch fires the `dashdown:data-loaded` event it stamps from.
  initPageHeader();
  // Localize the static-build "Generated <time>" footer (no-op off the build).
  initBuildStamp();

  // Embed: report content height to a framing host (no-op unless embedded), and
  // wire the "Embed" copy button (no-op unless present, i.e. full-shell view).
  initEmbedFrame();
  initEmbedUI();

  // Mermaid diagrams are static HTML (no data API), so init them independently
  // of the async-component path — a docs page with only a diagram and no
  // charts/tables still needs them rendered. Self-gates + lazy-loads.
  initAllMermaid();

  // Copy buttons on fenced code blocks — static HTML enhancement (no data API),
  // so init independently of the async-component path for prose-only docs pages.
  // Self-gates when the page has no code blocks.
  initAllCopyCode();

  // Fullscreen viewer: one delegated listener for every chart ⛶ button and
  // table ⛶ button. Independent of the async-component gate — it opens a modal
  // on demand and reuses the cached query data, so it wires up regardless of
  // when charts/tables initialize (and is a no-op until a button is clicked).
  initFullscreen();

  // Site search is plain DOM (no Alpine, no data API), so init it independently
  // of the async-component path — a prose-only docs page must still get search.
  initAllSiteSearches();

  // PDF export support: dress the page for print + expose a readiness signal.
  // No-op unless opened for export, so ordinary viewers pay nothing. Outside
  // the async-component gate so a prose/diagram-only page still prints + signals.
  initPrint();
  // Header "Export PDF" button (client-side window.print over the print styles).
  initPdfButton();

  if (hasAsyncComponents()) {
    initDashdown();
  }

  // Also initialize legacy components if they exist
  // (for backward compatibility - pages with old data-dashdown-* attributes)
  if (hasLegacyComponents()) {
    // Wait a bit to ensure Alpine is loaded before initializing legacy
    setTimeout(() => {
      initLegacy();
    }, 100);
  }
}

// Initialize when DOM is ready
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}

// Export for use in other modules
export { init, hasAsyncComponents };
