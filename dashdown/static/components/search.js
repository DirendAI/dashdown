// Dashdown Search Component
// A text filter whose value lives in the central `filters` store (the input
// binds to it directly via x-model in the template). This module only mirrors
// that store value to the URL and registers the filter as a visible control.

"use strict";

import { parseUrlParams } from "../core.js";

/**
 * Initialize a search component
 * @param {HTMLElement} el - Search element with data-async-component="search"
 */
export function initSearch(el) {
  waitForAlpine(() => {
    let config;
    try {
      config = JSON.parse(el.dataset.config);
    } catch (e) {
      console.error("Failed to parse search config:", e);
      return;
    }

    const { name, url_sync = true } = config;

    // Mark this filter as having a visible control so it isn't also shown as a
    // redundant active-filter chip.
    if (Alpine.store("filterControls")) {
      Alpine.store("filterControls")[name] = true;
    }

    if (!url_sync) return;

    // Seed the store from the URL (parseUrlParams already does this at store
    // init; this is a harmless idempotent guard if a Search mounts later).
    const urlParams = parseUrlParams();
    const store = Alpine.store("filters");
    if (store && urlParams[name] != null && store[name] == null) {
      store[name] = urlParams[name];
    }

    // Single reactive path: the store is the source of truth (the input writes
    // it via x-model). Mirror it to the URL whenever it changes — data
    // components already re-fetch off the store, so no custom broadcast needed.
    Alpine.effect(() => {
      const v = (Alpine.store("filters") || {})[name];
      syncSearchToUrl(name, v == null ? "" : String(v));
    });

    // Back/forward navigation: URL → store.
    window.addEventListener("popstate", () => {
      const params = parseUrlParams();
      const s = Alpine.store("filters");
      if (s) s[name] = params[name] || "";
    });
  });
}

/**
 * Sync search value to the URL (no event broadcast — the store is the single
 * reactive path; this only keeps the address bar shareable/bookmarkable).
 * @param {string} name - Filter name
 * @param {string} value - Search value
 */
export function syncSearchToUrl(name, value) {
  const params = new URLSearchParams(window.location.search);
  if (value && value.trim()) {
    params.set(name, value.trim());
  } else {
    params.delete(name);
  }
  const qs = params.toString();
  const newUrl = window.location.pathname + (qs ? "?" + qs : "");
  if (newUrl !== window.location.pathname + window.location.search) {
    window.history.replaceState({}, "", newUrl);
  }
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
 * Initialize all search components on the page
 */
export function initAllSearches() {
  document.querySelectorAll('[data-async-component="search"]').forEach((el) => {
    initSearch(el);
  });
}
