// Dashdown Filter Bar Component
// Relocates filter controls that opt INTO the top filter bar into the inline
// filter row / off-canvas drawer (both emitted below the page header by
// pipeline.py's _filter_bar_slot_html).
//
// Filter controls render **inline where authored** by default; only a control
// marked `data-filter-bar="true"` (the `bar` / `filter_bar=true` attribute) is
// relocated here. Pages with no such control have no bar slot at all, so this
// initializer no-ops.
//
// Routing rule (#21) for the opted-in controls: up to INLINE_MAX render inline
// as compact pills; from the next one on, controls go to the right drawer and a
// "Filters" button with an active-count badge appears at the end of the row.
// On narrow viewports, or with `filters: drawer` frontmatter
// (data-filter-mode="drawer"), every control goes to the drawer. Active-
// filter chips stay inline in every mode. Controls write to $store.filters
// through the same single reactive path wherever they're mounted (inline or bar).

"use strict";

import { initFilterDrawerStore } from "../store.js";

/**
 * Filter component selectors — the control types eligible for the top bar.
 * Only those also marked `data-filter-bar="true"` are actually relocated
 * (see initFilterBar); the rest stay inline where authored.
 */
const FILTER_SELECTORS = [
  '[data-async-component="dropdown"]',
  '[data-dashdown-dropdown]',
  '[data-async-component="daterange"]',
  '[data-async-component="rangeslider"]',
  '[data-async-component="slider"]',
  '[data-async-component="search"]',
  '[data-async-component="toggle"]',
  '[data-async-component="buttongroup"]',
  '[data-async-component="combobox"]',
];

/** Max controls kept inline before overflow routes to the drawer. */
const INLINE_MAX = 3;

/** Below this width every control collapses into the drawer. */
const NARROW_MEDIA_QUERY = "(max-width: 639px)";

/** Search is routed to the right-aligned slot; everything else to the controls slot. */
function isSearchFilter(el) {
  return el.matches('[data-async-component="search"]');
}

/**
 * The filter keys a control writes to — used for the drawer badge's active
 * count. A DateRange owns its start/end param pair (one control, two keys).
 */
function filterKeysFor(el) {
  if (el.matches('[data-async-component="daterange"]')) {
    return [el.dataset.startParam, el.dataset.endParam].filter(Boolean);
  }
  // A RangeSlider owns its min/max param pair (one control, two keys).
  if (el.matches('[data-async-component="rangeslider"]')) {
    return [el.dataset.minParam, el.dataset.maxParam].filter(Boolean);
  }
  if (el.dataset.filterName) return [el.dataset.filterName];
  if (el.dataset.name) return [el.dataset.name];
  // Explicit-options dropdown: name lives in the data-dashdown-dropdown JSON.
  const meta = el.getAttribute("data-dashdown-dropdown");
  if (meta) {
    try {
      const name = JSON.parse(meta).name;
      if (name) return [name];
    } catch (e) {
      /* fall through */
    }
  }
  return [];
}

/**
 * Initialize the filter bar: gather the page's filter components and route
 * them between the inline row and the drawer, re-routing on viewport changes.
 */
export function initFilterBar() {
  waitForAlpine(() => {
    // Slots: inline controls (left), search (right-aligned), drawer body.
    const filterBarContainer = document.getElementById("dashdown-filter-bar-container");
    const searchSlot = document.getElementById("dashdown-filter-bar-search");
    const drawerBody = document.getElementById("dashdown-filter-drawer-body");
    const drawerBtn = document.getElementById("dashdown-filter-drawer-btn");

    if (!filterBarContainer) {
      console.debug("Filter bar container not found, skipping filter bar initialization");
      return;
    }

    const filterBar = filterBarContainer.closest(".dashdown-filter-bar");
    const forcedDrawer = filterBar && filterBar.dataset.filterMode === "drawer";

    // The bar-routed filter components, in authored DOM order (captured once —
    // routing moves them between slots, which would scramble a re-query). Only
    // controls that opted in (`data-filter-bar="true"`) are relocated; every
    // other filter stays inline where authored (the default).
    const filters = Array.from(
      document.querySelectorAll(FILTER_SELECTORS.join(", "))
    ).filter((el) => el.getAttribute("data-filter-bar") === "true");

    if (filters.length === 0) {
      // No control opted into the bar — hide the (chrome-only) slot. The Python
      // side normally omits the slot entirely in this case; this is a defensive
      // fallback (e.g. a bar control stripped by another path).
      if (filterBar) {
        filterBar.style.display = "none";
      }
      return;
    }

    // The store registers during alpine:init like this callback does, and
    // listener order isn't guaranteed — idempotent init makes it safe.
    initFilterDrawerStore();
    const drawerStore =
      window.Alpine && Alpine.store ? Alpine.store("filterDrawer") : null;

    const narrowQuery = window.matchMedia(NARROW_MEDIA_QUERY);

    function route() {
      const allToDrawer = forcedDrawer || narrowQuery.matches;
      const inlineCount = allToDrawer
        ? 0
        : filters.length <= INLINE_MAX
          ? filters.length
          : INLINE_MAX;

      const drawerEls = [];
      filters.forEach((el, i) => {
        const target =
          drawerBody && i >= inlineCount
            ? drawerBody
            : isSearchFilter(el) && searchSlot
              ? searchSlot
              : filterBarContainer;
        if (target === drawerBody) drawerEls.push(el);
        // Appending in authored order keeps each slot's internal order stable.
        target.appendChild(el);
      });

      // Visibility is reactive (the button hides while none of its filters
      // are active), so hand it to the Alpine x-show binding: drop the
      // server-rendered `hidden` (pre-Alpine no-flash) and feed the store.
      if (drawerBtn) drawerBtn.hidden = false;
      if (drawerStore) {
        drawerStore.controlKeys = drawerEls.map(filterKeysFor);
        drawerStore.hasControls = drawerEls.length > 0;
        // Pinned = the drawer holds the page's only filter UI, so the
        // trigger must stay visible even with nothing active.
        drawerStore.pinned = allToDrawer;
        // Don't leave an open drawer floating with nothing in it (e.g. a
        // resize back to a wide viewport routed everything inline again).
        if (drawerEls.length === 0 && drawerStore.open) drawerStore.close();
      }
    }

    route();
    // Re-route when the viewport crosses the narrow breakpoint.
    if (narrowQuery.addEventListener) {
      narrowQuery.addEventListener("change", route);
    } else if (narrowQuery.addListener) {
      narrowQuery.addListener(route); // older Safari
    }
  });
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
 * Initialize filter bar on DOM ready
 */
export function initFilterBarOnReady() {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initFilterBar);
  } else {
    initFilterBar();
  }
}

// Initialize filter bar when this module loads
initFilterBarOnReady();
