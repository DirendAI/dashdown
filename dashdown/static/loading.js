// Dashdown Loading States
// Handles skeleton screens and loading indicators for components

"use strict";

/**
 * Show a component's loading state. Unified across charts, tables and pivots so
 * none of them collapses the card (which caused layout shift).
 *
 * Two cases:
 *  - First load: the card-body holds a sized skeleton (server-rendered, or
 *    rebuilt by the component on init). That shimmer *is* the loading state —
 *    leave it untouched so the placeholder keeps the final card's footprint.
 *  - Re-fetch (e.g. a filter change): the component already shows content. Float
 *    a spinner over the card-body without disturbing it, so the card keeps its
 *    height while the new data loads.
 * @param {HTMLElement} el - Component element
 */
export function showLoading(el) {
  const existing = el.querySelector(".dashdown-loading-overlay");
  if (existing) existing.remove();

  // A skeleton is present only before the first real render — leave it be.
  if (el.querySelector(".skeleton")) return;

  // Overlay host: the card-body when the component has one (charts, tables,
  // pivots), else the component root itself (counters — their root *is* the
  // card). An empty synthetic card-body has zero height, so an overlay parked
  // in one is invisible.
  const host = el.querySelector(".card-body") || el;
  host.style.position = "relative";

  const overlay = document.createElement("div");
  overlay.className = "dashdown-loading-overlay";
  overlay.innerHTML =
    '<span class="loading loading-spinner loading-lg text-primary"></span>';
  host.appendChild(overlay);
}

/**
 * Clear a component's loading state: drop any re-fetch overlay and the first-
 * load skeleton placeholders. Each component calls this immediately before its
 * update() in the same tick, so the skeleton → content swap is atomic (no
 * flash, no intermediate empty card).
 * @param {HTMLElement} el - Component element
 */
export function hideLoading(el) {
  const overlay = el.querySelector(".dashdown-loading-overlay");
  if (overlay) overlay.remove();

  el.querySelectorAll(
    ".skeleton, .dashdown-chart-skeleton, .dashdown-table-skeleton, .dashdown-pivot-skeleton"
  ).forEach((s) => s.remove());
}
