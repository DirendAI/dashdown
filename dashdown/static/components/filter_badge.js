// Per-widget "filtered by" indicator.
//
// Mounts a small funnel marker on a data widget when ≥1 active filter affects
// it. The marker stays hidden until the widget is hovered/focused (the reveal is
// pure CSS — `[data-async-component]:hover`, see dashdown.css), then its tooltip
// lists each affecting filter and its current value. Purely a UX affordance — it
// reads the same filter store the data path already uses and changes nothing
// about fetching.
//
// Which filters affect a widget comes from the query's *param names*, surfaced
// into `query_defs` server-side (`render/pipeline.py::_query_def_item`): the SQL
// never ships, only the `${param}` names it references. A query whose params
// can't be known statically (a Python `queries/*.py`, whose params are an opaque
// runtime dict) advertises `params_unknown` instead, and we show a muted
// "may be filtered" variant whenever any filter is active.

import { readQueryDefs, relevantFilters } from "../core.js";

const FUNNEL_ICON =
  '<svg viewBox="0 0 20 20" fill="currentColor" width="13" height="13" aria-hidden="true">' +
  '<path d="M2.628 1.601C5.028 1.206 7.49 1 10 1s4.973.206 7.372.601a.75.75 0 0 1 .628.74v2.288a2.25 2.25 0 0 1-.659 1.59l-4.682 4.683a2.25 2.25 0 0 0-.659 1.59v3.037c0 .684-.31 1.33-.844 1.757l-1.937 1.55A.75.75 0 0 1 7.5 18.25v-5.757a2.25 2.25 0 0 0-.659-1.591L2.16 6.22A2.25 2.25 0 0 1 1.5 4.629V2.34a.75.75 0 0 1 .628-.74Z"/>' +
  "</svg>";

/** Same source the live path uses: the Alpine store wins, else the inline JSON. */
function queryDefs() {
  return (
    (window.Alpine && Alpine.store && Alpine.store("queryDefs")) || readQueryDefs()
  );
}

function esc(s) {
  return String(s).replace(
    /[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c],
  );
}

/**
 * Mount a reactive "filtered by" badge on a data widget. Idempotent per element
 * (guards against a component initializing twice). Self-gates on Alpine being
 * ready, so callers don't have to.
 *
 * @param {HTMLElement} el - The widget root (`[data-async-component]`).
 * @param {string} queryName - The query this widget reads.
 * @param {Object} [opts]
 * @param {string} [opts.inlineInto] - Selector (searched within `el`) for a
 *   toolbar to host the badge inline; if absent at update time, the badge is
 *   absolutely positioned in the widget's corner instead. Used by `<Table>`,
 *   whose top-right corner already holds the search/export controls.
 */
export function mountFilterBadge(el, queryName, opts = {}) {
  if (!el || !queryName || el._filterBadgeMounted) return;
  // No filters can apply in a static export (controls are stripped) or a print
  // render — skip the marker entirely there.
  const html = document.documentElement;
  if (html.classList.contains("dashdown-print")) return;
  el._filterBadgeMounted = true;

  const start = () => {
    const badge = document.createElement("div");
    badge.className = "dashdown-filter-badge";
    badge.hidden = true;
    badge.tabIndex = 0;
    badge.innerHTML = `${FUNNEL_ICON}<div class="dashdown-filter-badge-tip" role="tooltip"></div>`;
    const tip = badge.querySelector(".dashdown-filter-badge-tip");

    Alpine.effect(() => {
      const relevant = relevantFilters(Alpine.store("filters") || {});
      const def = queryDefs()[queryName] || {};
      // `params` known → only the filters this query actually references.
      // params unknown (Python query / unscanned) → conservatively, every
      // active filter *might* apply.
      const known = Array.isArray(def.params);
      const active = known
        ? def.params.filter((p) => p in relevant)
        : def.params_unknown
          ? Object.keys(relevant)
          : []; // no info at all → don't guess
      // A query with a known, empty param set never reacts to filters.
      update(badge, tip, active, relevant, known, el, opts);
    });
  };

  if (window.Alpine) start();
  else document.addEventListener("alpine:init", start, { once: true });
}

function update(badge, tip, active, relevant, known, el, opts) {
  if (!active.length) {
    badge.hidden = true;
    if (badge.parentNode) badge.parentNode.removeChild(badge);
    return;
  }

  const heading = known ? "Filtered by" : "May be filtered by";
  badge.classList.toggle("dashdown-filter-badge--maybe", !known);
  badge.setAttribute(
    "aria-label",
    `${heading}: ${active.map((n) => `${n} = ${relevant[n]}`).join(", ")}`,
  );
  tip.innerHTML =
    `<div class="dashdown-filter-badge-tip-head">${esc(heading)}</div>` +
    active
      .map(
        (n) =>
          `<div class="dashdown-filter-badge-row">` +
          `<span class="dashdown-filter-badge-name">${esc(n)}</span>` +
          `<span class="dashdown-filter-badge-val">${esc(relevant[n])}</span>` +
          `</div>`,
      )
      .join("");

  badge.hidden = false;
  place(badge, el, opts);
}

/**
 * Put the badge in the right spot: inline inside a toolbar when the widget asked
 * for it and the toolbar exists (so it sits alongside the table's search/export
 * controls), otherwise absolutely in the widget's top-right corner.
 *
 * A `<Table>`'s toolbar is built client-side *after* its data loads, so when an
 * inline host is requested but not present yet we drop the badge in the corner
 * and watch for the toolbar to appear, then migrate it in once.
 */
function place(badge, el, opts) {
  if (opts.inlineInto) {
    const host = el.querySelector(opts.inlineInto);
    if (host) {
      inlineInto(badge, host);
      return;
    }
    if (!el._filterBadgeObserver) {
      el._filterBadgeObserver = new MutationObserver(() => {
        const h = el.querySelector(opts.inlineInto);
        if (h) {
          if (!badge.hidden) inlineInto(badge, h);
          el._filterBadgeObserver.disconnect();
          el._filterBadgeObserver = null;
        }
      });
      el._filterBadgeObserver.observe(el, { childList: true, subtree: true });
    }
  }
  corner(badge, el);
}

function inlineInto(badge, host) {
  badge.classList.add("dashdown-filter-badge--inline");
  if (badge.parentNode !== host) host.insertBefore(badge, host.firstChild);
}

function corner(badge, el) {
  badge.classList.remove("dashdown-filter-badge--inline");
  // Absolute positioning needs a positioned ancestor.
  if (getComputedStyle(el).position === "static") el.style.position = "relative";
  if (badge.parentNode !== el) el.appendChild(badge);
}
