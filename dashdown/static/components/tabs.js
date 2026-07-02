// Dashdown Tabs Component
// Pure layout (not a filter): shows one server-rendered `.dashdown-tab-panel`
// at a time behind a client-built tab bar. This module builds the bar from the
// panels' `data-tab-title` markers (direct children only, so nested <Tabs>
// keep their own bar), handles activation + WAI-ARIA keyboard support, syncs a
// named Tabs to the URL, and re-measures ECharts on every switch (a chart
// initialized inside a display:none panel is 0-sized until revealed).

"use strict";

import { parseUrlParams } from "../core.js";
import { resizeAllCharts } from "./chart.js";

/** A URL-safe slug for a tab title ("By region" → "by-region"). */
function slugify(title) {
  const s = String(title || "")
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return s || "tab";
}

/**
 * Initialize one tabs container.
 * @param {HTMLElement} el - element with data-async-component="tabs"
 */
export function initTabs(el) {
  if (el.classList.contains("dashdown-tabs-ready")) return;

  let config = {};
  try {
    config = JSON.parse(el.dataset.config || "{}");
  } catch (e) {
    console.error("Failed to parse tabs config:", e);
    return;
  }
  const { name = "", default: defaultTitle = "", url_sync: urlSync = true } = config;

  const nav = el.querySelector(":scope > .dashdown-tabs-nav");
  const panelsBox = el.querySelector(":scope > .dashdown-tabs-panels");
  if (!nav || !panelsBox) return;
  const panels = Array.from(panelsBox.children).filter((c) =>
    c.classList.contains("dashdown-tab-panel")
  );
  if (!panels.length) return;

  // Slugs identify tabs in the URL; a duplicate title gets a numeric suffix so
  // every tab stays addressable.
  const seen = new Map();
  const slugs = panels.map((p) => {
    let s = slugify(p.dataset.tabTitle);
    const n = (seen.get(s) || 0) + 1;
    seen.set(s, n);
    return n > 1 ? `${s}-${n}` : s;
  });

  const buttons = panels.map((panel, i) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "dashdown-tab";
    btn.setAttribute("role", "tab");
    btn.id = `${el.id}-tab-${i}`;
    if (!panel.id) panel.id = `${el.id}-panel-${i}`;
    panel.setAttribute("role", "tabpanel");
    panel.setAttribute("aria-labelledby", btn.id);
    btn.setAttribute("aria-controls", panel.id);
    btn.textContent = panel.dataset.tabTitle || `Tab ${i + 1}`;
    btn.addEventListener("click", () => activate(i, true));
    btn.addEventListener("keydown", (e) => onKeydown(e, i));
    nav.appendChild(btn);
    return btn;
  });

  let defaultIndex = 0;
  if (defaultTitle) {
    const di = panels.findIndex((p) => (p.dataset.tabTitle || "") === defaultTitle);
    if (di >= 0) defaultIndex = di;
  }

  const synced = Boolean(name) && urlSync !== false && urlSync !== "false";
  let active = -1;

  /** The index the URL asks for, else the default. */
  function resolveIndex() {
    if (synced) {
      const v = parseUrlParams()[name];
      if (v != null) {
        const i = slugs.indexOf(String(v));
        if (i >= 0) return i;
      }
    }
    return defaultIndex;
  }

  /** Mirror the active tab to the URL; the default tab keeps the URL clean. */
  function syncUrl(i) {
    if (!synced) return;
    const params = new URLSearchParams(window.location.search);
    if (i === defaultIndex) {
      params.delete(name);
    } else {
      params.set(name, slugs[i]);
    }
    const qs = params.toString();
    const newUrl = window.location.pathname + (qs ? "?" + qs : "");
    if (newUrl !== window.location.pathname + window.location.search) {
      window.history.replaceState({}, "", newUrl);
    }
  }

  function activate(i, fromUser) {
    if (i !== active) {
      active = i;
      panels.forEach((p, j) => p.classList.toggle("dashdown-tab-active", j === i));
      buttons.forEach((b, j) => {
        b.classList.toggle("dashdown-tab-selected", j === i);
        b.setAttribute("aria-selected", j === i ? "true" : "false");
        b.tabIndex = j === i ? 0 : -1;
      });
      // A chart initialized while its panel was hidden measured 0×0 — nudge
      // every live ECharts instance to re-measure now that it's visible.
      resizeAllCharts();
    }
    if (fromUser) syncUrl(i);
  }

  /** WAI-ARIA tabs keyboard pattern: arrows move + activate, Home/End jump. */
  function onKeydown(e, i) {
    let next = null;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") next = (i + 1) % panels.length;
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp")
      next = (i - 1 + panels.length) % panels.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = panels.length - 1;
    if (next == null) return;
    e.preventDefault();
    activate(next, true);
    buttons[next].focus();
  }

  // Back/forward navigation: URL → active tab.
  if (synced) {
    window.addEventListener("popstate", () => activate(resolveIndex(), false));
  }

  // `dashdown-tabs-ready` flips the CSS from "show first panel" (the pre-JS /
  // no-JS fallback) to "show .dashdown-tab-active only".
  el.classList.add("dashdown-tabs-ready");
  activate(resolveIndex(), false);
}

/**
 * Initialize all tabs containers on the page.
 */
export function initAllTabs() {
  document
    .querySelectorAll('[data-async-component="tabs"]')
    .forEach((el) => initTabs(el));
}
