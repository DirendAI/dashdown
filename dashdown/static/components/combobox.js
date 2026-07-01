// Dashdown Combobox Component
// A searchable single- or multi-select for high-cardinality columns. The text
// input is a SEARCH box (debounced → /api/options, server-side DISTINCT…ILIKE…
// LIMIT); the FILTER value in the store is only set when an option is chosen.
//   - single: store holds the one picked value (input shows it).
//   - multi:  store holds a comma-joined value for an IN (...) clause; picks show
//             as removable chips, the input stays a pure search box.
// The store stays the single source of truth (URL sync + data-component re-fetch
// ride off it), the same split toggle.js/dropdown.js use.

"use strict";

import { parseUrlParams, fetchQueryOptions } from "../core.js";

// Fallback if a config somehow omits `debounce` (older cached page markup); the
// component normally reads the resolved value (project `filters.debounce` or the
// per-control `debounce=`) from its config.
const DEFAULT_DEBOUNCE_MS = 300;

/** Split a comma-joined store value into a trimmed, non-empty array. */
function splitValues(v) {
  return String(v || "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

/**
 * Initialize a combobox component.
 * @param {HTMLElement} el - element with data-async-component="combobox"
 */
export function initCombobox(el) {
  waitForAlpine(() => {
    let config;
    try {
      config = JSON.parse(el.dataset.config);
    } catch (e) {
      console.error("Failed to parse combobox config:", e);
      return;
    }

    const {
      name,
      query_name: queryName,
      column,
      multi = false,
      limit = 50,
      min_chars: minChars = 0,
      debounce: debounceMs = DEFAULT_DEBOUNCE_MS,
      url_sync: urlSync = true,
    } = config;

    const input = el.querySelector(".dashdown-combobox-input");
    const panel = el.querySelector(".dashdown-combobox-panel");
    const clearBtn = el.querySelector(".dashdown-combobox-clear");
    const chipsBox = el.querySelector(".dashdown-combobox-chips");
    if (!input || !panel) return;

    // Mark this filter as having a visible control (chip suppression).
    if (Alpine.store("filterControls")) {
      Alpine.store("filterControls")[name] = true;
    }

    const store = () => Alpine.store("filters");
    const rawValue = () => {
      const s = store();
      return s && s[name] != null ? String(s[name]) : "";
    };
    // The committed value in the store. Single → the picked value; multi → the
    // comma-joined set. Writing it is the single reactive path (data components
    // re-fetch off the store; URL mirrors it).
    const commit = (value) => {
      const s = store();
      if (s) s[name] = value;
      reflect();
      if (urlSync) syncComboboxToUrl(name, value);
    };

    const selected = () => splitValues(rawValue());
    const isSelected = (v) => selected().includes(String(v));

    // Reflect the committed value into the chrome: chips (multi), the clear
    // button's visibility, and (single) the input text.
    const reflect = () => {
      if (multi) {
        renderChips();
        if (clearBtn) clearBtn.hidden = selected().length === 0;
      } else if (clearBtn) {
        clearBtn.hidden = rawValue() === "";
      }
    };

    const renderChips = () => {
      if (!chipsBox) return;
      const vals = selected();
      chipsBox.innerHTML = vals
        .map(
          (v) =>
            `<span class="dashdown-combobox-chip">${escapeHtml(v)}` +
            `<button type="button" class="dashdown-combobox-chip-x" ` +
            `data-value="${escapeHtml(v)}" aria-label="Remove ${escapeHtml(
              v
            )}">&times;</button></span>`
        )
        .join("");
    };

    // First-load seed: URL param wins, mirrored into the store.
    const urlParams = parseUrlParams();
    if (urlParams[name] != null && rawValue() === "") {
      const s = store();
      if (s) s[name] = urlParams[name];
    }
    if (!multi) input.value = rawValue();
    reflect();

    let activeIndex = -1;
    let options = [];
    let debounceTimer = null;
    let seq = 0; // guards against out-of-order async responses

    const close = () => {
      panel.hidden = true;
      input.setAttribute("aria-expanded", "false");
      activeIndex = -1;
    };

    const renderOptions = (opts) => {
      options = opts;
      if (!opts.length) {
        panel.innerHTML =
          '<div class="dashdown-combobox-empty">No matches</div>';
        panel.hidden = false;
        input.setAttribute("aria-expanded", "true");
        return;
      }
      panel.innerHTML = opts
        .map((o, i) => {
          const on = isSelected(o);
          const cls =
            "dashdown-combobox-option" +
            (on ? " is-selected" : "") +
            (multi ? " is-multi" : "");
          const check = multi
            ? '<span class="dashdown-combobox-check">' +
              (on ? "✓" : "") +
              "</span>"
            : "";
          return (
            `<div class="${cls}" role="option" aria-selected="${on}" ` +
            `data-index="${i}">${check}${escapeHtml(String(o))}</div>`
          );
        })
        .join("");
      panel.hidden = false;
      input.setAttribute("aria-expanded", "true");
    };

    const fetchAndShow = async (term) => {
      const mySeq = ++seq;
      // Exclude this combobox's own filter so picking a value doesn't shrink its
      // own future option list; keep every other active filter (cascading).
      const s = store() || {};
      const filters = {};
      for (const k of Object.keys(s)) {
        if (k !== name && s[k] != null && String(s[k]) !== "") filters[k] = s[k];
      }
      try {
        const opts = await fetchQueryOptions(queryName, column, term, {
          limit,
          filters,
        });
        if (mySeq !== seq) return; // a newer keystroke already superseded this
        renderOptions(opts);
      } catch (e) {
        if (mySeq !== seq) return;
        panel.innerHTML =
          '<div class="dashdown-combobox-empty">Could not load options</div>';
        panel.hidden = false;
      }
    };

    const scheduleFetch = (term) => {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => fetchAndShow(term), debounceMs);
    };

    const setActive = (i) => {
      const nodes = panel.querySelectorAll(".dashdown-combobox-option");
      nodes.forEach((n) => n.classList.remove("is-active"));
      if (i >= 0 && i < nodes.length) {
        nodes[i].classList.add("is-active");
        nodes[i].scrollIntoView({ block: "nearest" });
      }
      activeIndex = i;
    };

    const choose = (value) => {
      const v = String(value);
      if (multi) {
        // Toggle membership; keep the panel open for more picks and re-render
        // so the checkmark updates in place.
        const set = selected();
        const next = set.includes(v)
          ? set.filter((x) => x !== v)
          : set.concat(v);
        commit(next.join(","));
        if (input.value !== "") {
          // A pick consumes the search text (token-field UX): clear it, drop
          // any pending debounced fetch for the stale term, and show the
          // unfiltered list again for the next pick.
          clearTimeout(debounceTimer);
          input.value = "";
          if (minChars === 0) fetchAndShow("");
          else close();
        } else {
          renderOptions(options); // refresh checkmarks without a refetch
        }
        input.focus();
      } else {
        commit(v);
        input.value = v;
        close();
      }
    };

    // Typing searches (it is NOT the filter value until an option is chosen).
    input.addEventListener("input", () => {
      const term = input.value;
      if (term.length < minChars) {
        close();
        return;
      }
      scheduleFetch(term);
    });

    // Focus opens the panel with the first page of values (or current search).
    input.addEventListener("focus", () => {
      if (input.value.length >= minChars) fetchAndShow(input.value);
    });

    input.addEventListener("keydown", (e) => {
      if (panel.hidden && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
        fetchAndShow(input.value);
        return;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActive(Math.min(activeIndex + 1, options.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setActive(Math.max(activeIndex - 1, 0));
      } else if (e.key === "Enter") {
        if (activeIndex >= 0 && options[activeIndex] != null) {
          e.preventDefault();
          choose(options[activeIndex]);
        }
      } else if (e.key === "Backspace" && multi && input.value === "") {
        // Empty-input backspace removes the last chip (familiar token-field UX).
        const set = selected();
        if (set.length) commit(set.slice(0, -1).join(","));
      } else if (e.key === "Escape") {
        close();
      }
    });

    // Click an option (mousedown so it fires before the input's blur).
    panel.addEventListener("mousedown", (e) => {
      const opt = e.target.closest(".dashdown-combobox-option");
      if (!opt) return;
      e.preventDefault();
      const i = Number(opt.dataset.index);
      if (options[i] != null) choose(options[i]);
    });

    // Remove a chip via its × (mousedown to beat the input blur).
    if (chipsBox) {
      chipsBox.addEventListener("mousedown", (e) => {
        const x = e.target.closest(".dashdown-combobox-chip-x");
        if (!x) return;
        e.preventDefault();
        const v = x.dataset.value;
        commit(selected().filter((s) => s !== v).join(","));
        renderOptions(options); // keep panel checkmarks in sync if open
      });
    }

    // Blur: single → revert the input to the committed value (a half-typed
    // search must not masquerade as a selection); multi → clear the search box
    // (the chips already show the selection). Delay so an option/chip mousedown
    // wins.
    input.addEventListener("blur", () => {
      setTimeout(() => {
        input.value = multi ? "" : rawValue();
        close();
      }, 120);
    });

    if (clearBtn) {
      clearBtn.addEventListener("click", () => {
        commit(""); // single → clear value; multi → clear all chips
        input.value = "";
        input.focus();
      });
    }

    if (urlSync) {
      window.addEventListener("popstate", () => {
        const p = parseUrlParams();
        const s = store();
        if (s) s[name] = p[name] != null ? p[name] : "";
        if (!multi) input.value = rawValue();
        reflect();
      });
    }
  });
}

/**
 * Mirror the selected value to the URL (empty removes the param).
 * @param {string} name
 * @param {string} value
 */
export function syncComboboxToUrl(name, value) {
  const params = new URLSearchParams(window.location.search);
  if (value !== "") params.set(name, value);
  else params.delete(name);
  const qs = params.toString();
  const newUrl = window.location.pathname + (qs ? "?" + qs : "");
  if (newUrl !== window.location.pathname + window.location.search) {
    window.history.replaceState({}, "", newUrl);
  }
}

/** Minimal HTML escape for option/chip text. */
function escapeHtml(s) {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
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
 * Initialize all combobox components on the page.
 */
export function initAllComboboxes() {
  document
    .querySelectorAll('[data-async-component="combobox"]')
    .forEach((el) => initCombobox(el));
}
