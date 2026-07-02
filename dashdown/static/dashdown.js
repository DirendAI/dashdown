// Dashdown Client - Modular Entry Point
//
// This is the main entry point loaded by page.html.
// It imports all modules and initializes the application.
//
// Load order:
//   1. ECharts (blocking, <head>)
//   2. DaisyUI CSS + Tailwind CSS (blocking, <head>)
//   3. This file (module script)
//   4. Alpine.js (defer, end of <body>)
//
// The modular structure:
//   - core.js: Shared utilities (API client, caching, data transformation)
//   - store.js: Alpine.js stores (filters, theme, queryDefs)
//   - loading.js: Loading state indicators
//   - components/: Individual component modules (table.js, chart.js, dropdown.js)
//   - legacy.js: Backward compatibility for old component attributes
//   - app.js: Application initialization and orchestration

"use strict";

// Polyfill check for older browsers
if (!window.Promise) {
  console.error("Dashdown requires Promise support. Please use a modern browser.");
}

// Global namespace for Dashdown
window.Dashdown = window.Dashdown || {};

// Load all modules - browser will cache and execute them
// The actual initialization happens in app.js which runs on DOM ready
import "./core.js";
import "./store.js";
import "./loading.js";
import "./components/table.js";
import "./components/chart.js";
import "./components/pivot.js";
import "./components/dropdown.js";
import "./components/daterange.js";
import "./components/range_slider.js";
import "./components/slider.js";
import "./components/search.js";
import "./components/toggle.js";
import "./components/button_group.js";
import "./components/combobox.js";
import "./components/site_search.js";
import "./components/tabs.js";
import "./components/ask.js";
import "./components/export.js";
import "./components/mermaid.js";
import "./components/filter_bar.js";
import "./components/embed_frame.js";
import "./components/embed_ui.js";
import "./components/echarts_theme.js";
import "./legacy.js";
import "./app.js";
