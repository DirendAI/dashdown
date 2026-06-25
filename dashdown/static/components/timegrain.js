// Dashdown TimeGrain Component
// A time-grain picker whose value lives in the central `filters` store as a
// canonical token (`day`/`week`/`month`/…); a chart's `grain={name}` reads it at
// fetch time. The `<select>` reflects/writes the store via Alpine `x-model` in the
// template — this module only registers the control (chip suppression), seeds the
// first-load value (URL > `default` attr > native), and mirrors the store value to
// the URL. The exact minimal role toggle.js / search.js play.

"use strict";

import { parseUrlParams } from "../core.js";

/**
 * Initialize a single time-grain component.
 * @param {HTMLElement} el - element with data-async-component="timegrain"
 */
export function initTimeGrain(el) {
  waitForAlpine(() => {
    let config;
    try {
      config = JSON.parse(el.dataset.config);
    } catch (e) {
      console.error("Failed to parse timegrain config:", e);
      return;
    }

    const { name, default: dflt = "", url_sync = true } = config;

    // Mark this filter as having a visible control so it isn't ALSO rendered as a
    // redundant active-filter chip.
    if (Alpine.store("filterControls")) {
      Alpine.store("filterControls")[name] = true;
    }

    // First-load seed: a URL param wins; else apply the `default` grain. Seeding
    // matters so the chart's `grain={name}` resolves to the displayed selection on
    // load (an unset param would otherwise read empty → native granularity, out of
    // step with the shown option).
    const store = Alpine.store("filters");
    const urlParams = parseUrlParams();
    if (store) {
      if (urlParams[name] != null) {
        if (store[name] == null) store[name] = urlParams[name];
      } else if (store[name] == null) {
        store[name] = dflt;
      }
    }

    if (!url_sync) return;

    // Single reactive path: the store is the source of truth (the select writes it
    // via x-model). Mirror it to the URL whenever it changes — data components
    // already re-fetch off the store.
    Alpine.effect(() => {
      const v = (Alpine.store("filters") || {})[name];
      syncTimeGrainToUrl(name, v == null ? "" : String(v));
    });

    // Back/forward navigation: URL → store (fall back to the default grain, not
    // "", so a defaulted control stays at its grain on popstate).
    window.addEventListener("popstate", () => {
      const params = parseUrlParams();
      const s = Alpine.store("filters");
      if (s) s[name] = params[name] != null ? params[name] : dflt;
    });
  });
}

/**
 * Mirror the grain value to the URL. An empty value (native) removes the param.
 * @param {string} name
 * @param {string} value
 */
export function syncTimeGrainToUrl(name, value) {
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
 * Initialize all time-grain components on the page.
 */
export function initAllTimeGrains() {
  document.querySelectorAll('[data-async-component="timegrain"]').forEach((el) => {
    initTimeGrain(el);
  });
}
