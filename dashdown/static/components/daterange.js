// Dashdown DateRange Component
// Date range picker with preset ranges and URL sync

"use strict";

import { parseUrlParams } from "../core.js";

/**
 * Sync date range to URL
 * @param {string} startParam - Start date parameter name
 * @param {string} endParam - End date parameter name
 * @param {string} startDate - Start date value
 * @param {string} endDate - End date value
 */
export function syncDateRangeToUrl(startParam, endParam, startDate, endDate) {
  const params = new URLSearchParams(window.location.search);

  if (startDate) {
    params.set(startParam, startDate);
  } else {
    params.delete(startParam);
  }

  if (endDate) {
    params.set(endParam, endDate);
  } else {
    params.delete(endParam);
  }

  const newUrl = `${window.location.pathname}?${params.toString()}`;
  window.history.pushState({}, "", newUrl);

  // Trigger custom event for other components
  window.dispatchEvent(new CustomEvent("dashdown:url-updated", {
    detail: { params: Object.fromEntries(params.entries()) }
  }));
}

/**
 * Format a Date as YYYY-MM-DD in local time (not UTC, unlike toISOString).
 * @param {Date} d - Date to format
 * @returns {string} - Local date string
 */
function formatLocalDate(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/**
 * Resolve a preset config to a concrete {start, end} date pair, or null for
 * presets that carry no fixed range (custom / unknown). Single source of preset
 * math — both setPreset (apply) and updateActivePreset (detect) call this so the
 * two can never drift. All arithmetic is in LOCAL time to match formatLocalDate.
 * @param {object} preset - A preset config from preset_configs
 * @param {Date} today - Local midnight of the current day
 * @returns {{start: Date, end: Date}|null}
 */
function presetRange(preset, today) {
  if (!preset) return null;
  if (preset.kind === "rolling") {
    const start = new Date(today);
    start.setDate(today.getDate() + preset.days_start);
    const end = new Date(today);
    end.setDate(today.getDate() + preset.days_end);
    return { start, end };
  }
  if (preset.kind === "calendar") {
    return calendarRange(preset.unit, preset.offset, today);
  }
  return null;
}

/**
 * Compute a calendar-aligned range. offset 0 = current period start → today
 * (period-to-date, so it never selects future days with no data); offset -1 =
 * the whole previous period (its start → its end). Weeks start Monday (ISO).
 * @param {string} unit - "week" | "month" | "year"
 * @param {number} offset - 0 = current period, -1 = previous period
 * @param {Date} today - Local midnight of the current day
 * @returns {{start: Date, end: Date}|null}
 */
function calendarRange(unit, offset, today) {
  if (unit === "week") {
    // getDay(): 0=Sun..6=Sat. Days elapsed since Monday (0=Mon..6=Sun):
    const sinceMonday = (today.getDay() + 6) % 7;
    const start = new Date(today);
    start.setDate(today.getDate() - sinceMonday + offset * 7);
    if (offset === 0) return { start, end: new Date(today) };
    const end = new Date(start);
    end.setDate(start.getDate() + 6);
    return { start, end };
  }
  if (unit === "month") {
    const start = new Date(today.getFullYear(), today.getMonth() + offset, 1);
    if (offset === 0) return { start, end: new Date(today) };
    // Day 0 of the next month = last day of the target month.
    const end = new Date(today.getFullYear(), today.getMonth() + offset + 1, 0);
    return { start, end };
  }
  if (unit === "year") {
    const start = new Date(today.getFullYear() + offset, 0, 1);
    if (offset === 0) return { start, end: new Date(today) };
    const end = new Date(today.getFullYear() + offset, 11, 31);
    return { start, end };
  }
  return null;
}

// Define the Alpine component function globally so it can be used in HTML
// This will be called when Alpine initializes the component
window.dateRangeComponent = function(name) {
  return {
    startDate: "",
    endDate: "",
    activePreset: "",
    presets: {},
    persist: false,
    defaultPreset: "",
    storageKey: "",

    init() {
      // Read config from data-config attribute (properly HTML-escaped JSON)
      let config = {};
      try {
        config = JSON.parse(this.$el.dataset.config || "{}");
      } catch (e) {
        console.error("DateRange: failed to parse data-config", e);
      }

      // Load preset configs from config
      this.presets = config.preset_configs || {};

      const startParam = config.start_param || `${name}_start`;
      const endParam = config.end_param || `${name}_end`;

      // Persistence (localStorage) + default preset power the project-wide global
      // date filter: remember the selection across navigation, and seed it on the
      // first ever visit. Keyed by the param pair so every page sharing the same
      // global filter reads the same value.
      this.persist = !!config.persist;
      this.defaultPreset = config.default || "";
      this.storageKey = `dashdown:date-filter:${startParam}|${endParam}`;

      // Load from URL on initialization
      const urlParams = parseUrlParams();
      let seededFromUrl = false;
      if (urlParams[startParam]) {
        this.startDate = urlParams[startParam];
        seededFromUrl = true;
      }
      if (urlParams[endParam]) {
        this.endDate = urlParams[endParam];
        seededFromUrl = true;
      }

      // Mark both date keys as visible controls so they aren't shown as chips.
      if (window.Alpine && Alpine.store && Alpine.store("filterControls")) {
        Alpine.store("filterControls")[startParam] = true;
        Alpine.store("filterControls")[endParam] = true;
      }

      this.updateActivePreset();
      this.setupUrlSync(startParam, endParam);

      // URL wins over the remembered/default range (a shared deep link must
      // override). With nothing in the URL: restore the persisted value, else
      // apply the default preset. Both set the dates *after* setupUrlSync, so the
      // watchers push them to the filters store + URL (the single reactive path).
      if (!seededFromUrl) {
        const stored = this.persist ? this.readStored() : null;
        if (stored && (stored.start || stored.end)) {
          this.startDate = stored.start || "";
          this.endDate = stored.end || "";
          this.updateActivePreset();
        } else if (this.defaultPreset) {
          this.setPreset(this.defaultPreset);
        }
      } else if (this.persist) {
        // Mirror a URL-provided range into storage so other pages inherit it.
        this.writeStored();
      }
    },

    readStored() {
      try {
        return JSON.parse(window.localStorage.getItem(this.storageKey) || "null");
      } catch (e) {
        return null;
      }
    },

    writeStored() {
      if (!this.persist) return;
      try {
        if (this.startDate || this.endDate) {
          window.localStorage.setItem(
            this.storageKey,
            JSON.stringify({ start: this.startDate, end: this.endDate })
          );
        } else {
          window.localStorage.removeItem(this.storageKey);
        }
      } catch (e) {
        /* storage disabled / over quota — persistence is best-effort */
      }
    },

    setupUrlSync(startParam, endParam) {
      const urlSync = this.$el.dataset.urlSync === "true";

      const pushToFilters = () => {
        // Mutate the existing store keys rather than replacing the whole store
        // object. Replacing it re-triggers every other filter component's
        // reactive URL-sync effect, which re-dispatches stale `url-updated`
        // events that would clobber the dates we just set (single-path rule).
        const store = window.Alpine && Alpine.store ? Alpine.store("filters") : null;
        if (store) {
          store[startParam] = this.startDate || "";
          store[endParam] = this.endDate || "";
        }
        // Persist after each change so the global filter survives navigation.
        this.writeStored();
        // Writing the store keys above is the single reactive path: data
        // components subscribe to `$store.filters` via Alpine effects and
        // re-render on change. No custom broadcast event needed.
      };

      // Watch for changes and sync to filters store (and optionally URL)
      this.$watch("startDate", () => {
        pushToFilters();
        if (urlSync) syncDateRangeToUrl(startParam, endParam, this.startDate, this.endDate);
      });

      this.$watch("endDate", () => {
        pushToFilters();
        if (urlSync) syncDateRangeToUrl(startParam, endParam, this.startDate, this.endDate);
      });

      if (!urlSync) return;

      // Handle popstate events
      window.addEventListener("popstate", () => {
        const urlParams = parseUrlParams();
        if (urlParams[startParam] !== this.startDate) {
          this.startDate = urlParams[startParam] || "";
        }
        if (urlParams[endParam] !== this.endDate) {
          this.endDate = urlParams[endParam] || "";
        }
        this.updateActivePreset();
      });

      // NOTE: no `dashdown:url-updated` listener here. Only this component ever
      // writes the date params, so reacting to that event is a pure feedback
      // loop — stale events from other filters would overwrite a freshly-picked
      // preset. Browser back/forward is handled by popstate above.
    },

    setPreset(presetName) {
      // The empty "All time" option clears the range entirely (the watchers
      // push the empty dates to the store/URL like any other change).
      if (!presetName) {
        this.startDate = "";
        this.endDate = "";
        this.activePreset = "";
        return;
      }
      const preset = this.presets[presetName];
      if (!preset) return;

      // "Custom" carries no fixed range: just reveal the inputs and keep
      // whatever dates are already set so the user can adjust them.
      if (preset.kind === "custom") {
        this.activePreset = "custom";
        return;
      }

      const today = new Date();
      today.setHours(0, 0, 0, 0);

      const range = presetRange(preset, today);
      if (range) {
        // Format in LOCAL time. toISOString() converts to UTC, which rolls the
        // day back/forward in non-UTC timezones — that desynced these dates from
        // updateActivePreset()'s local-time comparison, so the active preset
        // never matched and the day was off by one.
        this.startDate = formatLocalDate(range.start);
        this.endDate = formatLocalDate(range.end);
        this.activePreset = presetName;
      }
    },

    updateFromInputs() {
      this.activePreset = "custom";
    },

    updateActivePreset() {
      if (!this.startDate && !this.endDate) {
        this.activePreset = "";
        return;
      }
      // A half-set range (deep link with only one param) is "custom" so the
      // date inputs stay visible rather than the select claiming "All time".
      if (!this.startDate || !this.endDate) {
        this.activePreset = "custom";
        return;
      }

      const today = new Date();
      today.setHours(0, 0, 0, 0);

      const start = new Date(this.startDate + "T00:00:00");
      const end = new Date(this.endDate + "T00:00:00");

      for (const [presetName, preset] of Object.entries(this.presets)) {
        const range = presetRange(preset, today);
        if (
          range &&
          start.getTime() === range.start.getTime() &&
          end.getTime() === range.end.getTime()
        ) {
          this.activePreset = presetName;
          return;
        }
      }

      this.activePreset = "custom";
    }
  };
};
