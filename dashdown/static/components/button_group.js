// Dashdown ButtonGroup Component
// A single-select segmented control whose value lives in the central `filters`
// store as a string (each segment writes it via @click and reflects active state
// via :class in the template). This module only: registers the control (chip
// suppression), seeds the first-load value (URL > default attr > "All"), and
// mirrors the store value to the URL — the same minimal role toggle.js plays.

"use strict";

import { parseUrlParams } from "../core.js";

/**
 * Initialize a button-group component.
 * @param {HTMLElement} el - element with data-async-component="buttongroup"
 */
export function initButtonGroup(el) {
  waitForAlpine(() => {
    let config;
    try {
      config = JSON.parse(el.dataset.config);
    } catch (e) {
      console.error("Failed to parse button-group config:", e);
      return;
    }

    const { name, default: defaultValue = "", url_sync = true } = config;

    // Mark this filter as having a visible control so it isn't also shown as a
    // redundant active-filter chip.
    if (Alpine.store("filterControls")) {
      Alpine.store("filterControls")[name] = true;
    }

    // First-load seed: URL param wins; else apply the `default` attr (or "" =
    // the "All" segment when there's no default). Matches toggle.js.
    const store = Alpine.store("filters");
    const urlParams = parseUrlParams();
    if (store) {
      if (urlParams[name] != null) {
        if (store[name] == null) store[name] = urlParams[name];
      } else if (store[name] == null) {
        store[name] = defaultValue;
      }
    }

    if (!url_sync) return;

    // Single reactive path: the store is the source of truth (segments write it
    // via @click). Mirror it to the URL whenever it changes — data components
    // already re-fetch off the store.
    Alpine.effect(() => {
      const v = (Alpine.store("filters") || {})[name];
      syncButtonGroupToUrl(name, v == null ? "" : String(v));
    });

    // Back/forward navigation: URL → store (fall back to "" = the All segment).
    window.addEventListener("popstate", () => {
      const params = parseUrlParams();
      const s = Alpine.store("filters");
      if (s) s[name] = params[name] != null ? params[name] : "";
    });
  });
}

/**
 * Mirror the selected value to the URL. The empty "All" value removes the param;
 * any concrete value sets it.
 * @param {string} name
 * @param {string} value
 */
export function syncButtonGroupToUrl(name, value) {
  const params = new URLSearchParams(window.location.search);
  if (value !== "") {
    params.set(name, value);
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
 * Wait for Alpine.js to be available.
 * @param {Function} callback
 */
function waitForAlpine(callback) {
  if (window.Alpine) {
    callback();
  } else {
    document.addEventListener("alpine:init", callback);
  }
}

/**
 * Initialize all button-group components on the page.
 */
export function initAllButtonGroups() {
  document
    .querySelectorAll('[data-async-component="buttongroup"]')
    .forEach((el) => initButtonGroup(el));
}
