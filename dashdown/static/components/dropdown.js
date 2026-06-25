// Dashdown Dropdown Component
// Self-contained dropdown filter with async options loading and URL sync

"use strict";

import { fetchQueryData, recordsOf, esc, parseUrlParams } from "../core.js";

/**
 * Update URL with new query parameter
 * @param {string} name - Parameter name
 * @param {string} value - Parameter value (null/undefined to remove)
 */
export function syncDropdownToUrl(name, value) {
  const params = new URLSearchParams(window.location.search);
  if (value && value !== "") {
    params.set(name, value);
  } else {
    params.delete(name);
  }
  const newUrl = `${window.location.pathname}?${params.toString()}`;
  window.history.pushState({}, "", newUrl);
  
  // Trigger a custom event so other components can react to URL changes
  window.dispatchEvent(new CustomEvent("dashdown:url-updated", {
    detail: { params: parseUrlParams() }
  }));
}

/**
 * Initialize a dropdown component
 * @param {HTMLElement} el - Dropdown element with data-async-component="dropdown"
 */
export function initDropdown(el) {
  // Wait for Alpine to be available before proceeding
  waitForAlpine(() => {
    let config;
    try {
      config = JSON.parse(el.dataset.config);
    } catch (e) {
      console.error("Failed to parse dropdown config:", e);
      return;
    }

    const {
      name,
      query_name,
      column,
      label,
      include_all = true,
      url_sync = true,
      multi = false,
      options: explicitOptions = [],
    } = config;

    // Store reference to config on element
    el._dropdownConfig = config;

    // Single-select binds a `<select>` straight to the filters store via
    // x-model. Multi-select is a button + checkmark popover instead (the store
    // still holds a comma-separated STRING so URL sync, chips, and `IN (...)`
    // substitution all see one value); its controller owns the two-way bridge.
    let select = null;
    if (multi) {
      const ms = createMultiSelect(el, name);
      el._multiSelect = ms;
      if (query_name && column) {
        loadMultiOptionsAsync(ms, query_name, column);
      } else {
        ms.setOptions(explicitOptions);
      }
    } else {
      select = el.querySelector("select");
      if (!select) {
        console.error("Dropdown element has no select child:", el);
        return;
      }
      select.setAttribute("x-model", `$store.filters['${name}']`);
      // Load options if we have a query and column. The x-model binding above
      // is already set, so no ensureAlpineBinding needed.
      if (query_name && column) {
        loadOptionsAsync(select, query_name, column, include_all, name);
      }
      // else: explicit options mode — already populated server-side.
    }

    // Store metadata for legacy filter compatibility
    if (Alpine && Alpine.store && Alpine.store("dropdownMeta")) {
      Alpine.store("dropdownMeta")[name] = { name, column, label };
    }
    // Mark as a visible control so its value isn't repeated as a chip.
    if (Alpine && Alpine.store && Alpine.store("filterControls")) {
      Alpine.store("filterControls")[name] = true;
    }

    // Register in component store if available
    if (Alpine && Alpine.store && Alpine.store("components")) {
      Alpine.store("components").registerDropdown({ el, config, select, name });
    }

    // Set up URL sync if enabled
    if (url_sync) {
      setupUrlSync(name);
    }

    // Set up Alpine effect for URL sync - single reactive path
    // This watches the Alpine store and syncs to URL when it changes
    if (url_sync && window.Alpine && Alpine.store) {
      Alpine.effect(() => {
        const filters = Alpine.store("filters");
        if (filters && filters[name] !== undefined) {
          const currentUrlParams = parseUrlParams();
          // Treat "absent from URL" and "empty value" as the same (no filter):
          // otherwise a dropdown cleared to "" (absent from the URL) reads as a
          // perpetual mismatch and re-syncs on every effect run, spamming
          // pushState and stale url-updated events. Only sync on a real change.
          if ((currentUrlParams[name] || "") !== (filters[name] || "")) {
            syncDropdownToUrl(name, filters[name]);
          }
        }
      });
    }

    // No manual change listener: `x-model` already writes the selection into
    // `$store.filters[name]`, and data components subscribe to that store via
    // Alpine effects. (Single reactive path — REVIEW §4.)
  });
}

/**
 * Set up URL synchronization for a dropdown. The control (single `<select>` via
 * x-model, or the multi-select widget via its Alpine effect) is bound to
 * `$store.filters[name]`, so this only seeds the store from the URL and reacts
 * to back/forward navigation — it never touches the control directly.
 * @param {string} name - Filter name
 */
function setupUrlSync(name) {
  // Initialize from URL on page load
  const urlParams = parseUrlParams();
  if (urlParams[name]) {
    waitForAlpine(() => {
      // Set the Alpine store directly - x-model will update the select
      if (Alpine && Alpine.store && Alpine.store("filters")) {
        Alpine.store("filters")[name] = urlParams[name];
      }
    });
  }

  // Listen for URL changes (back/forward navigation)
  window.addEventListener("popstate", () => {
    const currentParams = parseUrlParams();
    if (currentParams[name] !== undefined) {
      const currentValue = Alpine?.store("filters")?.[name];
      if (currentValue !== currentParams[name]) {
        // Update Alpine store - x-model will update the select
        if (Alpine && Alpine.store) {
          Alpine.store("filters")[name] = currentParams[name];
        }
      }
    } else if (Alpine?.store("filters")?.[name] !== "") {
      // URL param was removed, reset to default
      if (Alpine && Alpine.store) {
        Alpine.store("filters")[name] = "";
      }
    }
  });

  // NOTE: no `dashdown:url-updated` listener here. The filters store is the
  // single reactive path: this dropdown is bound to `$store.filters[name]` via
  // x-model, and every other control writes that store directly (Clear-all/
  // chip-clear in store.js, DateRange in daterange.js), so cross-component
  // changes already reach us. Adding a url-updated listener creates a feedback
  // loop: its `e.detail.params` snapshot omits keys a *sibling* control owns, so
  // the else-branch below would reset THIS dropdown to "" while the live URL
  // still holds its value. Back/forward navigation is handled by the popstate
  // listener above, which reads the authoritative live URL.
}

/** The filters store, or null if Alpine isn't ready. */
function filtersStore() {
  return window.Alpine && Alpine.store ? Alpine.store("filters") : null;
}

/**
 * Parse the comma-separated `filters[name]` value into a Set of selected values
 * (trimmed, empties dropped). This is the canonical form fed to `IN (...)`.
 * @param {string} name - filter name
 * @returns {Set<string>}
 */
function selectedSet(name) {
  const store = filtersStore();
  return new Set(
    String((store && store[name]) || "")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean)
  );
}

/**
 * Build the multi-select dropdown controller: a trigger button + a checkmark
 * popover panel. Selection lives in the string-valued filters store (so URL
 * sync, chips, and `IN (...)` substitution are identical to a single-select);
 * clicking a row toggles its value, comma-joining the store. An Alpine effect
 * mirrors the store back onto the panel (checkmarks) and the button summary, so
 * URL seeds, popstate, and Clear-all all reflect — the effect only reads the
 * store and writes the DOM, so there's no feedback loop.
 *
 * Returns `{ setOptions(list) }` so the caller can populate options either from
 * the config (explicit) or after an async fetch.
 * @param {HTMLElement} el - the `.dashdown-multiselect` element
 * @param {string} name - filter name
 */
function createMultiSelect(el, name) {
  const trigger = el.querySelector(".dashdown-multiselect-trigger");
  const panel = el.querySelector(".dashdown-multiselect-panel");
  const summary = el.querySelector(".dashdown-multiselect-summary");
  let options = [];

  const isOpen = () => !panel.hidden;
  function open() {
    panel.hidden = false;
    trigger.setAttribute("aria-expanded", "true");
    el.classList.add("is-open");
  }
  function close() {
    panel.hidden = true;
    trigger.setAttribute("aria-expanded", "false");
    el.classList.remove("is-open");
  }

  // Row markup. The check SVG is always present; CSS shows it only when the row
  // carries `is-selected`, so toggling a class is all the reflect step does.
  const checkSvg =
    '<svg class="dashdown-multiselect-check" viewBox="0 0 16 16" ' +
    'fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">' +
    '<path d="M3 8.5l3.5 3.5L13 5" stroke-linecap="round" ' +
    'stroke-linejoin="round"/></svg>';

  function renderRows() {
    panel.innerHTML = options
      .map(
        (o) =>
          `<div class="dashdown-multiselect-option" role="option" ` +
          `data-value="${esc(o)}" aria-selected="false" tabindex="-1">` +
          `<span class="dashdown-multiselect-option-label">${esc(o)}</span>` +
          checkSvg +
          `</div>`
      )
      .join("");
  }

  // Store -> DOM: reflect the selection onto checkmarks + the button summary.
  function reflect() {
    const sel = selectedSet(name);
    panel.querySelectorAll(".dashdown-multiselect-option").forEach((row) => {
      const on = sel.has(row.dataset.value);
      row.classList.toggle("is-selected", on);
      row.setAttribute("aria-selected", on ? "true" : "false");
    });
    const picked = [...sel];
    if (picked.length === 0) {
      summary.textContent = summary.dataset.placeholder || "All";
      summary.classList.add("is-placeholder");
    } else {
      summary.textContent = picked.join(", ");
      summary.classList.remove("is-placeholder");
    }
  }

  // DOM -> store: toggle one value, comma-joining (order follows the option
  // list so the summary reads predictably). reflect() runs via the effect.
  function toggleValue(value) {
    const sel = selectedSet(name);
    if (sel.has(value)) sel.delete(value);
    else sel.add(value);
    const ordered = options.filter((o) => sel.has(o));
    const store = filtersStore();
    if (store) store[name] = ordered.join(",");
  }

  trigger.addEventListener("click", (e) => {
    e.stopPropagation();
    isOpen() ? close() : open();
  });

  panel.addEventListener("click", (e) => {
    const row = e.target.closest(".dashdown-multiselect-option");
    if (!row || !panel.contains(row)) return;
    toggleValue(row.dataset.value); // keep the panel open for more picks
  });

  // Close on outside click / Escape.
  document.addEventListener("click", (e) => {
    if (isOpen() && !el.contains(e.target)) close();
  });
  el.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && isOpen()) {
      close();
      trigger.focus();
    }
  });

  if (window.Alpine && Alpine.effect) {
    Alpine.effect(() => {
      const store = Alpine.store("filters") || {};
      void store[name]; // track the store value as a dependency
      reflect();
    });
  }

  return {
    setOptions(list) {
      options = Array.isArray(list) ? list.slice() : [];
      renderRows();
      reflect();
    },
  };
}

/**
 * Async-load distinct column values for a multi-select and hand them to its
 * controller. Mirrors loadOptionsAsync but targets the popover (no `<option>`
 * tags, no "All" entry — empty selection already means all).
 * @param {{setOptions: Function}} ms - controller from createMultiSelect
 * @param {string} queryName
 * @param {string} column
 */
async function loadMultiOptionsAsync(ms, queryName, column) {
  try {
    const data = await fetchQueryData(queryName, {}, {});
    ms.setOptions(distinctColumnValues(recordsOf(data), column));
  } catch (error) {
    console.error(`Failed to load dropdown options for ${queryName}:`, error);
  }
}

/**
 * Distinct, sorted, stringified values of a column across records.
 * @param {Array<Object>} records
 * @param {string} column
 * @returns {string[]}
 */
function distinctColumnValues(records, column) {
  const options = [];
  if (records.length > 0 && column in records[0]) {
    const seen = new Set();
    records.forEach((r) => {
      const v = r[column];
      if (v !== null && v !== undefined) {
        const sv = String(v);
        if (!seen.has(sv)) {
          seen.add(sv);
          options.push(sv);
        }
      }
    });
    options.sort();
  }
  return options;
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
 * Load options from query data asynchronously into a single-select `<select>`.
 * (Multi-select uses loadMultiOptionsAsync + the popover controller instead.)
 * @param {HTMLSelectElement} select - Select element
 * @param {string} queryName - Query name
 * @param {string} column - Column to extract unique values from
 * @param {boolean} includeAll - Whether to include "All" option
 * @param {string} name - Filter name (for Alpine binding)
 */
async function loadOptionsAsync(select, queryName, column, includeAll, name) {
  try {
    // Show loading state
    select.innerHTML = '<option value="">Loading...</option>';

    const data = await fetchQueryData(queryName, {}, {});
    const options = distinctColumnValues(recordsOf(data), column);

    // Build options HTML
    let html = "";
    if (includeAll) {
      html += '<option value="">All</option>';
    }
    html += options
      .map((opt) => `<option value="${esc(opt)}">${esc(opt)}</option>`)
      .join("");

    select.innerHTML = html;

    // Restore the current selection from the filters store (seeded from the URL).
    // x-model applied the value during init — before the real options existed —
    // so rebuilding <option>s above reset the <select> to its first entry. Now
    // that the options are present, re-apply the value so the control matches the
    // active filter (and the chip) on a deep-linked / reloaded page.
    const store = window.Alpine && Alpine.store ? Alpine.store("filters") : null;
    const current = store ? store[name] : undefined;
    if (current && options.includes(current)) {
      select.value = current;
    }
    // Note: x-model binding is already set in initDropdown, no need for ensureAlpineBinding
  } catch (error) {
    console.error(`Failed to load dropdown options for ${queryName}:`, error);
    select.innerHTML = '<option value="">Error loading options</option>';
  }
}

/**
 * Initialize dropdowns from legacy data-dashdown-dropdown attribute
 * (for backward compatibility)
 */
export function initLegacyDropdowns() {
  document.querySelectorAll("[data-dashdown-dropdown]").forEach((el) => {
    let meta;
    try {
      meta = JSON.parse(el.dataset.dashdownDropdown);
    } catch (e) {
      return;
    }

    const { name, column, label, url_sync = true } = meta;
    const select = el.querySelector("select");

    if (!select) return;

    // Bind to filters store
    select.setAttribute("x-model", `$store.filters['${name}']`);

    // Store metadata
    if (window.Alpine && Alpine.store) {
      Alpine.store("dropdownMeta")[name] = { name, column, label };
      if (Alpine.store("filterControls")) Alpine.store("filterControls")[name] = true;
    }

    // Set up URL sync if enabled
    if (url_sync) {
      // Initialize from URL
      const urlParams = parseUrlParams();
      if (urlParams[name] && Alpine?.store("filters")) {
        Alpine.store("filters")[name] = urlParams[name];
      }
    }

    // No manual change listener: `x-model` already writes the selection into
    // `$store.filters[name]`, and data components subscribe to that store via
    // Alpine effects. (Single reactive path — REVIEW §4.)
  });
}

/**
 * Initialize all dropdowns on the page
 */
export function initAllDropdowns() {
  // Initialize async dropdowns first
  document.querySelectorAll('[data-async-component="dropdown"]').forEach((el) => {
    initDropdown(el);
  });

  // Initialize legacy dropdowns
  initLegacyDropdowns();
}
