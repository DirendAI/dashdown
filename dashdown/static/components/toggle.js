// Dashdown Toggle Component
// A boolean filter whose value lives in the central `filters` store as a string
// (the checkbox reflects it via :checked and writes it back via @change in the
// template). This module only: registers the control (chip suppression), seeds
// the first-load value (URL > default attr > off), and mirrors the store value
// to the URL — the same minimal role search.js plays.

"use strict";

import { parseUrlParams } from "../core.js";

/**
 * Initialize a toggle component.
 * @param {HTMLElement} el - element with data-async-component="toggle"
 */
export function initToggle(el) {
  waitForAlpine(() => {
    let config;
    try {
      config = JSON.parse(el.dataset.config);
    } catch (e) {
      console.error("Failed to parse toggle config:", e);
      return;
    }

    const {
      name,
      on_value = "true",
      off_value = "",
      default: defaultOn = false,
      url_sync = true,
    } = config;

    // Mark this filter as having a visible control so it isn't also shown as a
    // redundant active-filter chip.
    if (Alpine.store("filterControls")) {
      Alpine.store("filterControls")[name] = true;
    }

    // First-load seed: URL param wins; else apply the `default` attr (on → the
    // on value, off → the off value). Seeding the off value matters for the
    // two-state case (off_value="No"/"false"), where an unset param would
    // otherwise substitute to '' and match nothing. The empty-string default
    // (all-guard) seeds "" harmlessly.
    const store = Alpine.store("filters");
    const urlParams = parseUrlParams();
    if (store) {
      if (urlParams[name] != null) {
        if (store[name] == null) store[name] = urlParams[name];
      } else if (store[name] == null) {
        store[name] = defaultOn ? on_value : off_value;
      }
    }

    if (!url_sync) return;

    // Single reactive path: the store is the source of truth (the checkbox writes
    // it via @change). Mirror it to the URL whenever it changes — data components
    // already re-fetch off the store.
    Alpine.effect(() => {
      const v = (Alpine.store("filters") || {})[name];
      syncToggleToUrl(name, v == null ? "" : String(v));
    });

    // Back/forward navigation: URL → store (fall back to the off value, not "",
    // so a non-empty off_value stays correct on popstate).
    window.addEventListener("popstate", () => {
      const params = parseUrlParams();
      const s = Alpine.store("filters");
      if (s) s[name] = params[name] != null ? params[name] : off_value;
    });
  });
}

/**
 * Mirror the toggle value to the URL. An empty value (the all-guard "off")
 * removes the param; a non-empty value (incl. a custom off_value) sets it.
 * @param {string} name
 * @param {string} value
 */
export function syncToggleToUrl(name, value) {
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
 * Initialize all toggle components on the page.
 */
export function initAllToggles() {
  document.querySelectorAll('[data-async-component="toggle"]').forEach((el) => {
    initToggle(el);
  });
}
