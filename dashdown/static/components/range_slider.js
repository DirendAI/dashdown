// Dashdown RangeSlider Component
// A numeric two-handle range filter. Like DateRange it owns a low/high *pair* of
// filter keys (min_param/max_param). The two handles are plain overlaid
// `<input type="range">`s (no slider lib); this Alpine component clamps them so
// they can't cross, paints the fill between them, mirrors the pair into the
// central `filters` store (the single reactive path data components re-fetch off)
// and, optionally, the URL — the same minimal role dateRangeComponent plays.

"use strict";

import { parseUrlParams, formatValue, resolveFormatOpts, debounce } from "../core.js";

/**
 * Mirror the range pair to the URL. A value equal to the track bound is treated
 * as "unset" and removes its param (a clean URL when the slider is wide open);
 * any narrower value sets it.
 * @param {string} minParam
 * @param {string} maxParam
 * @param {string} loValue - "" to delete, else the stringified number
 * @param {string} hiValue - "" to delete, else the stringified number
 */
export function syncRangeToUrl(minParam, maxParam, loValue, hiValue) {
  const params = new URLSearchParams(window.location.search);

  if (loValue !== "") params.set(minParam, loValue);
  else params.delete(minParam);

  if (hiValue !== "") params.set(maxParam, hiValue);
  else params.delete(maxParam);

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

// Defined globally so the template's x-data="rangeSliderComponent('name')" can
// reach it once this module has loaded (before Alpine initializes) — exactly how
// daterange.js exposes dateRangeComponent.
window.rangeSliderComponent = function (name) {
  return {
    lo: 0,
    hi: 100,
    min: 0,
    max: 100,
    step: 1,
    minParam: "",
    maxParam: "",
    urlSync: true,
    format: null,
    syncDebounced: null,

    init() {
      let config = {};
      try {
        config = JSON.parse(this.$el.dataset.config || "{}");
      } catch (e) {
        console.error("RangeSlider: failed to parse data-config", e);
      }

      this.min = toNum(config.min, 0);
      this.max = toNum(config.max, 100);
      this.step = toNum(config.step, 1) || 1;
      this.minParam = config.min_param || `${name}_min`;
      this.maxParam = config.max_param || `${name}_max`;
      this.urlSync = this.$el.dataset.urlSync === "true";
      this.format = config.format || null;
      // Debounce the store write (data re-fetch) so dragging either handle settles
      // into one fetch; the handles + readout (bound to lo/hi) stay live. The seed
      // push below is immediate so the first fetch is already filtered.
      const debounceMs = toNum(config.debounce, 300);
      this.syncDebounced = debounce(() => {
        this.pushToFilters();
        if (this.urlSync) this.syncUrl();
      }, debounceMs);

      // First-load seed: URL params win over the author's default (a shared deep
      // link must override), and the default falls back to the full track.
      const urlParams = parseUrlParams();
      const seededLo = urlParams[this.minParam] != null;
      const seededHi = urlParams[this.maxParam] != null;
      this.lo = this.clamp(
        seededLo ? toNum(urlParams[this.minParam], config.default_lo) : toNum(config.default_lo, this.min)
      );
      this.hi = this.clamp(
        seededHi ? toNum(urlParams[this.maxParam], config.default_hi) : toNum(config.default_hi, this.max)
      );
      if (this.lo > this.hi) {
        const t = this.lo;
        this.lo = this.hi;
        this.hi = t;
      }

      // Mark both keys as visible controls so they aren't also shown as chips.
      if (window.Alpine && Alpine.store && Alpine.store("filterControls")) {
        Alpine.store("filterControls")[this.minParam] = true;
        Alpine.store("filterControls")[this.maxParam] = true;
      }

      // Push the seeded values into the store so the first fetch is filtered.
      this.pushToFilters();
      if (this.urlSync) this.syncUrl();

      // Back/forward navigation: URL → control (then the watchers re-push).
      if (this.urlSync) {
        window.addEventListener("popstate", () => {
          const p = parseUrlParams();
          this.lo = this.clamp(toNum(p[this.minParam], this.min));
          this.hi = this.clamp(toNum(p[this.maxParam], this.max));
          if (this.lo > this.hi) this.hi = this.lo;
        });
      }

      // Single reactive path: the handles write lo/hi (clamped in onLoInput/
      // onHiInput), and these watchers mirror them to the store + URL — debounced
      // so a drag (or a lo/hi pair moving together) coalesces into one re-fetch.
      this.$watch("lo", () => {
        this.syncDebounced();
      });
      this.$watch("hi", () => {
        this.syncDebounced();
      });
    },

    /** Clamp a number to the track and snap nothing else (the input owns step). */
    clamp(v) {
      const n = toNum(v, this.min);
      return Math.min(this.max, Math.max(this.min, n));
    },

    /** Lo handle moved: never let it pass the hi handle. */
    onLoInput() {
      if (this.lo > this.hi) this.lo = this.hi;
    },

    /** Hi handle moved: never let it drop below the lo handle. */
    onHiInput() {
      if (this.hi < this.lo) this.hi = this.lo;
    },

    /** Percent position of a value along the track (0–100). */
    pct(v) {
      const span = this.max - this.min;
      if (span <= 0) return 0;
      return ((this.clamp(v) - this.min) / span) * 100;
    },

    /** Inline style for the coloured fill between the two handles. */
    fillStyle() {
      const a = this.pct(this.lo);
      const b = this.pct(this.hi);
      return `left:${a}%;right:${100 - b}%`;
    },

    /** Format a value for the readout via the shared formatter (if configured). */
    fmt(v) {
      if (this.format && this.format.format) {
        return formatValue(v, this.format.format, resolveFormatOpts(this.format));
      }
      return String(v);
    },

    /**
     * Mirror the pair into the central filters store. A handle sitting on its
     * track bound writes "" (unset) — empty-means-all, the same convention every
     * other dashdown filter uses. The author's query guards each bound
     * (`'${..._min}' = '' OR price >= CAST(${..._min} AS DOUBLE)`), so an empty
     * value (untouched/wide-open slider, OR the very first fetch before this
     * seeds) shows everything instead of blowing up `CAST('' AS DOUBLE)`.
     */
    pushToFilters() {
      const store = window.Alpine && Alpine.store ? Alpine.store("filters") : null;
      if (!store) return;
      store[this.minParam] = this.lo <= this.min ? "" : String(this.lo);
      store[this.maxParam] = this.hi >= this.max ? "" : String(this.hi);
    },

    /**
     * Mirror the pair to the URL, but only when a handle has moved OFF its track
     * bound — an untouched / wide-open slider keeps the URL clean. On reload a
     * missing param falls back to the bound, so store and URL stay consistent.
     */
    syncUrl() {
      syncRangeToUrl(
        this.minParam,
        this.maxParam,
        this.lo <= this.min ? "" : String(this.lo),
        this.hi >= this.max ? "" : String(this.hi)
      );
    },
  };
};
