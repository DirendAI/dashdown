// Page header "Updated <time>" stamp.
//
// When a page's frontmatter sets `updated: true`, the server renders an empty
// `[data-dashdown-updated]` slot. This module fills it with a human-readable
// timestamp as soon as the page's data lands (the `dashdown:data-loaded` event
// fired by core.js's fetchQueryData), so the stamp reflects query-fetch time.
//
// In a static export there is no live fetch, so we prefer the build snapshot
// time (`builtAt` in the #dashdown-build config) over the viewing time.

"use strict";

import { readBuildConfig } from "../core.js";

/** Format a Date like "Jun 11, 2026 · 9:41 AM" (locale-aware). */
function formatUpdated(date) {
  const d = date.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
  const t = date.toLocaleTimeString(undefined, {
    hour: "numeric",
    minute: "2-digit",
  });
  return `${d} · ${t}`;
}

let _filled = false;

function fillUpdated() {
  if (_filled) return;
  _filled = true;

  const el = document.querySelector("[data-dashdown-updated]");
  if (!el) return;

  // Static snapshot: show when the data was built, not when it was viewed.
  const build = readBuildConfig();
  let date = new Date();
  if (build && build.static && build.builtAt) {
    const parsed = new Date(build.builtAt);
    if (!isNaN(parsed.getTime())) date = parsed;
  }

  const timeEl = el.querySelector(".dashdown-updated-time");
  if (timeEl) timeEl.textContent = formatUpdated(date);
  el.hidden = false;
}

/**
 * Wire up the auto "Updated" stamp, if present. Stamps on first data load, with
 * a short timeout fallback so a page with no queries still gets a timestamp.
 */
export function initPageHeader() {
  if (!document.querySelector("[data-dashdown-updated]")) return;
  document.addEventListener("dashdown:data-loaded", fillUpdated, { once: true });
  setTimeout(fillUpdated, 1500);
}

/**
 * Localize the static-build "Generated <time>" footer, if present. build.py
 * emits the footer with a UTC fallback baked into the markup; here we reformat
 * the `<time datetime>` to the viewer's locale, matching the "Updated" stamp's
 * style. No-op when the footer is absent (the dev server never emits it) or the
 * timestamp can't be parsed — the server-rendered text stays as the fallback.
 */
export function initBuildStamp() {
  document.querySelectorAll("time[data-dashdown-build-time]").forEach((el) => {
    const iso = el.getAttribute("datetime");
    if (!iso) return;
    const date = new Date(iso);
    if (isNaN(date.getTime())) return;
    el.textContent = formatUpdated(date);
  });
}
