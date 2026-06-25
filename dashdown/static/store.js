// Dashdown Alpine Stores
// Centralized state management using Alpine.store()

"use strict";

import { readQueryDefs, parseUrlParams } from "./core.js";

/**
 * Filters store - manages filter state across all components
 * Used by dropdowns, charts, and tables for reactivity
 */
export function initFiltersStore() {
  // Only initialize if store doesn't exist yet
  if (!window.Alpine || Alpine.store("filters")) {
    return;
  }
  
  // Parse URL params on initialization
  const urlParams = parseUrlParams();
  
  // Initialize filters from URL parameters
  Alpine.store("filters", { ...urlParams });
}

/**
 * Query definitions store - caches query metadata from the page
 */
export function initQueryDefsStore() {
  // Only initialize if store doesn't exist yet
  if (!window.Alpine || Alpine.store("queryDefs")) {
    return;
  }
  Alpine.store("queryDefs", readQueryDefs());
}

/**
 * Dropdown metadata store - tracks which dropdowns exist and their config
 */
export function initDropdownMetaStore() {
  // Only initialize if store doesn't exist yet
  if (!window.Alpine || Alpine.store("dropdownMeta")) {
    return;
  }
  Alpine.store("dropdownMeta", {});
}

/**
 * Filter-controls store - the set of filter keys that have a visible control
 * (dropdown/search/date range) on the page. Used to suppress redundant chips:
 * a filter whose value is already shown in its own control doesn't need one.
 */
export function initFilterControlsStore() {
  if (!window.Alpine || Alpine.store("filterControls")) return;
  Alpine.store("filterControls", {});
}

/** snake_case / kebab-case / camelCase → "Title Case" for chip labels. */
function humanizeFilterKey(s) {
  return String(s)
    .replace(/[_-]+/g, " ")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Non-empty filter entries as `{key, value}`. */
function activeFilterEntries(filters) {
  const out = [];
  for (const k of Object.keys(filters || {})) {
    const v = filters[k];
    if (v != null && String(v).trim() !== "") out.push({ key: k, value: String(v) });
  }
  return out;
}

/**
 * Reset filter keys everywhere: clear the store (so data components re-fetch and
 * `x-model`-bound dropdowns reset), drop the URL params, then notify the other
 * control types via the same events they already listen to (popstate +
 * `dashdown:url-updated`) so search boxes and date ranges reset their local state.
 */
function resetFilterKeys(keys) {
  const arr = Array.isArray(keys) ? keys : [keys];
  const store = window.Alpine && Alpine.store ? Alpine.store("filters") : null;
  if (store) arr.forEach((k) => { store[k] = ""; });

  const params = new URLSearchParams(window.location.search);
  arr.forEach((k) => params.delete(k));
  const qs = params.toString();
  window.history.pushState({}, "", window.location.pathname + (qs ? "?" + qs : ""));

  window.dispatchEvent(new CustomEvent("dashdown:url-updated", { detail: { params: parseUrlParams() } }));
  window.dispatchEvent(new PopStateEvent("popstate"));
}

/**
 * Active-filter chips store - derives dismissible chips from the live `filters`
 * store so the filter bar can show what's applied and clear it. Reads the
 * reactive store inside its getters, so Alpine re-evaluates chips on any change.
 */
export function initFilterChipsStore() {
  if (!window.Alpine || Alpine.store("filterChips")) return;

  Alpine.store("filterChips", {
    /**
     * Chips for active filters that have NO visible control — a value already
     * shown in its own dropdown/search/date-range control isn't repeated here.
     * @returns {Array<{key:string, keys:string[], label:string, display:string}>}
     */
    list() {
      const filters = Alpine.store("filters") || {};
      const meta = Alpine.store("dropdownMeta") || {};
      const controls = Alpine.store("filterControls") || {};
      // A control routed into the drawer isn't visible inline, so its active
      // value still gets a chip — the always-visible cue that something is
      // filtering even when its control lives in the drawer (#21).
      const drawer = Alpine.store("filterDrawer");
      const drawerKeys = new Set(drawer ? drawer.controlKeys.flat() : []);
      const hasVisibleControl = (k) => controls[k] && !drawerKeys.has(k);
      const chips = [];
      const consumed = new Set();

      for (const { key, value } of activeFilterEntries(filters)) {
        if (consumed.has(key)) continue;

        // Collapse a DateRange `<name>_start` / `<name>_end` pair into one chip.
        let base = null;
        if (key.endsWith("_start")) base = key.slice(0, -6);
        else if (key.endsWith("_end")) base = key.slice(0, -4);
        if (base !== null && (filters[base + "_start"] || filters[base + "_end"])) {
          const sk = base + "_start";
          const ek = base + "_end";
          consumed.add(sk);
          consumed.add(ek);
          if (hasVisibleControl(sk) || hasVisibleControl(ek)) continue; // inline control → no chip
          const s = filters[sk];
          const e = filters[ek];
          const display = s && e ? `${s} → ${e}` : s ? `from ${s}` : `until ${e}`;
          chips.push({ key: sk, keys: [sk, ek], label: humanizeFilterKey(base), display });
          continue;
        }

        consumed.add(key);
        if (hasVisibleControl(key)) continue; // value already visible in its control
        const label = (meta[key] && meta[key].label) || humanizeFilterKey(key);
        chips.push({ key, keys: [key], label, display: value });
      }
      return chips;
    },

    /** Any chip to show (a control-less active filter). */
    any() {
      return this.list().length > 0;
    },

    /** Any active filter at all (controlled or not) — gates the "Clear all" link. */
    anyActive() {
      return activeFilterEntries(Alpine.store("filters") || {}).length > 0;
    },

    clear(keys) {
      resetFilterKeys(keys);
    },

    clearAll() {
      const filters = Alpine.store("filters") || {};
      resetFilterKeys(activeFilterEntries(filters).map((e) => e.key));
    },
  });
}

/**
 * Filter-drawer store - open/closed state of the off-canvas filter drawer
 * plus the filter keys of the controls routed into it (set by filter_bar.js).
 * `controlKeys` is an array of key-groups, one per control — a DateRange
 * contributes [start, end] but counts as a single filter in the badge.
 */
export function initFilterDrawerStore() {
  if (!window.Alpine || Alpine.store("filterDrawer")) return;

  Alpine.store("filterDrawer", {
    open: false,
    controlKeys: [],
    // Whether any control is currently routed into the drawer, and whether
    // the drawer is the only filter surface (forced mode / narrow viewport).
    // Both set by filter_bar.js on every route.
    hasControls: false,
    pinned: false,

    toggle() {
      this.open = !this.open;
    },

    close() {
      this.open = false;
    },

    /**
     * The trigger button shows while the drawer holds the page's only filter
     * UI (pinned); in auto-overflow mode it only appears once one of its
     * filters is active — inactive overflow filters stay out of the chrome.
     */
    buttonVisible() {
      return this.hasControls && (this.pinned || this.activeCount() > 0);
    },

    /** Number of drawer-routed controls with an active (non-empty) value. */
    activeCount() {
      const filters = Alpine.store("filters") || {};
      let n = 0;
      for (const keys of this.controlKeys) {
        const active = keys.some((k) => {
          const v = filters[k];
          return v != null && String(v).trim() !== "";
        });
        if (active) n += 1;
      }
      return n;
    },
  });
}

/**
 * Component registry - tracks initialized components
 */
export function initComponentRegistry() {
  // Only initialize if store doesn't exist yet
  if (!window.Alpine || Alpine.store("components")) {
    return;
  }

  Alpine.store("components", {
    charts: [],
    tables: [],
    dropdowns: [],

    registerChart(chart) {
      this.charts.push(chart);
    },

    registerTable(table) {
      this.tables.push(table);
    },

    registerDropdown(dropdown) {
      this.dropdowns.push(dropdown);
    },

    getChartsForQuery(queryName) {
      return this.charts.filter((c) => c.config.query_name === queryName);
    },

    getTablesForQuery(queryName) {
      return this.tables.filter((t) => t.config.query_name === queryName);
    },
  });
}

/**
 * Initialize all stores - registers during alpine:init so stores exist
 * before Alpine processes any directives.
 */
export function initStores(callback) {
  function setup() {
    if (Alpine.store("filters")) {
      if (callback) callback();
      return;
    }
    initFiltersStore();
    initQueryDefsStore();
    initDropdownMetaStore();
    initFilterControlsStore();
    initFilterChipsStore();
    initFilterDrawerStore();
    initComponentRegistry();
    if (callback) callback();
  }

  if (window.Alpine) {
    setup();
  } else {
    document.addEventListener("alpine:init", setup);
  }
}
