// Dashdown Slider Component
// A single-value numeric threshold filter. The one-handle sibling of RangeSlider:
// one native <input type="range"> over a track with a fill from the minimum to
// the handle. This Alpine component clamps the handle, paints the fill, mirrors
// the value into the central `filters` store (the single reactive path data
// components re-fetch off) and, optionally, the URL — the same minimal role
// rangeSliderComponent plays.

"use strict";

import { parseUrlParams, formatValue, resolveFormatOpts, debounce } from "../core.js";

/**
 * Mirror the value to the URL (always present — a threshold is always a concrete
 * number; the author's `'${x}' = '' OR …` guard covers the pre-seed instant).
 * @param {string} name
 * @param {string} value
 */
export function syncSliderToUrl(name, value) {
  const params = new URLSearchParams(window.location.search);
  if (value !== "") params.set(name, value);
  else params.delete(name);
  const qs = params.toString();
  const newUrl = window.location.pathname + (qs ? "?" + qs : "");
  if (newUrl !== window.location.pathname + window.location.search) {
    window.history.replaceState({}, "", newUrl);
  }
}

/**
 * Coerce a value to a finite number, or fall back.
 * @param {*} v
 * @param {number} fallback
 * @returns {number}
 */
function toNum(v, fallback) {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

// Defined globally so the template's x-data="sliderComponent('name')" can reach
// it once this module has loaded (before Alpine initializes) — exactly how
// range_slider.js exposes rangeSliderComponent.
window.sliderComponent = function (name) {
  return {
    value: 0,
    min: 0,
    max: 100,
    step: 1,
    urlSync: true,
    format: null,
    syncDebounced: null,

    init() {
      let config = {};
      try {
        config = JSON.parse(this.$el.dataset.config || "{}");
      } catch (e) {
        console.error("Slider: failed to parse data-config", e);
      }

      this.min = toNum(config.min, 0);
      this.max = toNum(config.max, 100);
      this.step = toNum(config.step, 1) || 1;
      this.urlSync = this.$el.dataset.urlSync === "true";
      this.format = config.format || null;
      // Debounce the store write (data re-fetch) so a drag settles into one fetch;
      // the handle + readout (bound to `value`) stay live. The seed push below is
      // immediate so the first fetch is already filtered.
      const debounceMs = toNum(config.debounce, 300);
      this.syncDebounced = debounce(() => {
        this.pushToFilters();
        if (this.urlSync) this.syncUrl();
      }, debounceMs);

      // First-load seed: URL param wins over the author's default.
      const urlParams = parseUrlParams();
      const seeded = urlParams[name] != null;
      this.value = this.clamp(
        seeded ? toNum(urlParams[name], config.default) : toNum(config.default, this.min)
      );

      // Mark the key as a visible control so it isn't also shown as a chip.
      if (window.Alpine && Alpine.store && Alpine.store("filterControls")) {
        Alpine.store("filterControls")[name] = true;
      }

      // Push the seeded value into the store so the first fetch is filtered.
      this.pushToFilters();
      if (this.urlSync) this.syncUrl();

      // Back/forward navigation: URL → control (then the watcher re-pushes).
      if (this.urlSync) {
        window.addEventListener("popstate", () => {
          const p = parseUrlParams();
          this.value = this.clamp(toNum(p[name], config.default));
        });
      }

      // Single reactive path: the handle writes `value`, this watcher mirrors it
      // to the store + URL — debounced so a drag coalesces into one re-fetch.
      this.$watch("value", () => {
        this.syncDebounced();
      });
    },

    /** Clamp a number to the track. */
    clamp(v) {
      const n = toNum(v, this.min);
      return Math.min(this.max, Math.max(this.min, n));
    },

    onInput() {
      // x-model.number already wrote `value`; nothing else to coordinate (no
      // second handle to keep ordered). Kept as a seam for symmetry/clarity.
    },

    /** Percent position of a value along the track (0–100). */
    pct(v) {
      const span = this.max - this.min;
      if (span <= 0) return 0;
      return ((this.clamp(v) - this.min) / span) * 100;
    },

    /** Inline style for the fill from the minimum to the handle. */
    fillStyle() {
      return `left:0;right:${100 - this.pct(this.value)}%`;
    },

    /** Format the value for the readout via the shared formatter (if configured). */
    fmt(v) {
      if (this.format && this.format.format) {
        return formatValue(v, this.format.format, resolveFormatOpts(this.format));
      }
      return String(v);
    },

    /** Mirror the value into the central filters store (always a concrete number). */
    pushToFilters() {
      const store = window.Alpine && Alpine.store ? Alpine.store("filters") : null;
      if (store) store[name] = String(this.value);
    },

    syncUrl() {
      syncSliderToUrl(name, String(this.value));
    },
  };
};
