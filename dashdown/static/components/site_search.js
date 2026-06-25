// Dashdown Site Search
//
// Full-text search across every page. The component placeholder ships empty; this
// module fetches the search index once (shared across every box on the page) and
// ranks pages/sections entirely in the browser — there is no server-side search.
//
// Index source:
//   - live server : GET /_dashdown/api/search-index
//   - static build: _dashdown/search-index.json (root-relative, resolved by <base>)

"use strict";

import { readBuildConfig, esc } from "../core.js";

// One in-flight fetch shared by every <SiteSearch> on the page.
let _indexPromise = null;

function indexUrl() {
  const build = readBuildConfig();
  // In a static export the data API doesn't exist; the build wrote the index next
  // to the data snapshots. Root-relative so the page's <base> resolves it under
  // any sub-path host.
  if (build && build.static) return "_dashdown/search-index.json";
  return "/_dashdown/api/search-index";
}

function loadIndex() {
  if (_indexPromise) return _indexPromise;
  _indexPromise = fetch(indexUrl())
    .then((r) => (r.ok ? r.json() : []))
    .catch((e) => {
      console.error("dashdown: failed to load search index", e);
      return [];
    })
    .then((entries) => (Array.isArray(entries) ? entries : []));
  return _indexPromise;
}

// Turn an app URL ("/components/charts") into an href that works on both the live
// server (absolute) and a static export (root-relative, resolved by <base>).
function hrefFor(url) {
  const build = readBuildConfig();
  if (build && build.static) {
    if (url === "/") return ".";
    return url.replace(/^\//, "");
  }
  return url;
}

function tokenize(q) {
  return q
    .toLowerCase()
    .split(/[^a-z0-9]+/i)
    .filter((t) => t.length > 0);
}

// Score one entry against the query terms. Every term must appear *somewhere*
// (title, a heading, or the body) for the page to match (AND semantics). Title
// hits weigh heaviest, then headings, then body occurrences. Returns null when a
// term is missing.
function scoreEntry(entry, terms) {
  const title = (entry.title || "").toLowerCase();
  const text = (entry.text || "").toLowerCase();
  const headings = entry.headings || [];
  let score = 0;
  let bestHeading = null;

  for (const term of terms) {
    let termScore = 0;
    if (title.includes(term)) termScore += title.startsWith(term) ? 14 : 10;
    for (const h of headings) {
      if ((h.text || "").toLowerCase().includes(term)) {
        termScore += 6;
        if (!bestHeading) bestHeading = h;
      }
    }
    // Count body occurrences (capped) so a denser page ranks above a passing
    // mention, without letting one huge page dominate.
    let idx = text.indexOf(term);
    let hits = 0;
    while (idx !== -1 && hits < 5) {
      hits += 1;
      idx = text.indexOf(term, idx + term.length);
    }
    termScore += hits;
    if (termScore === 0) return null; // term absent everywhere → not a match
    score += termScore;
  }
  return { score, bestHeading };
}

// Build a snippet around the first matched term, with the term highlighted.
function snippetFor(entry, terms) {
  const text = entry.text || "";
  const lower = text.toLowerCase();
  let pos = -1;
  for (const term of terms) {
    const i = lower.indexOf(term);
    if (i !== -1 && (pos === -1 || i < pos)) pos = i;
  }
  if (pos === -1) return esc(text.slice(0, 120));
  const start = Math.max(0, pos - 50);
  const end = Math.min(text.length, pos + 90);
  let snip = (start > 0 ? "… " : "") + text.slice(start, end) + (end < text.length ? " …" : "");
  // Escape first, then wrap each term in <mark> on the escaped string.
  let html = esc(snip);
  for (const term of terms) {
    html = html.replace(new RegExp("(" + escapeRe(term) + ")", "gi"), "<mark>$1</mark>");
  }
  return html;
}

function escapeRe(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function rank(entries, query, max) {
  const terms = tokenize(query);
  if (!terms.length) return [];
  const scored = [];
  for (const entry of entries) {
    const r = scoreEntry(entry, terms);
    if (r) scored.push({ entry, ...r, terms });
  }
  scored.sort((a, b) => b.score - a.score);
  return scored.slice(0, max);
}

function renderResults(panel, results) {
  if (!results.length) {
    panel.innerHTML = '<div class="dashdown-site-search-empty">No matches</div>';
    panel.hidden = false;
    return [];
  }
  const items = results.map((r, i) => {
    const e = r.entry;
    const anchor = r.bestHeading ? "#" + r.bestHeading.id : "";
    const href = hrefFor(e.url) + anchor;
    const sub = r.bestHeading ? esc(r.bestHeading.text) : esc(e.url);
    return (
      `<a class="dashdown-site-search-result" href="${esc(href)}" role="option" ` +
      `data-idx="${i}" ${i === 0 ? 'aria-selected="true"' : ""}>` +
      `<span class="dashdown-site-search-title">${esc(e.title)}</span>` +
      `<span class="dashdown-site-search-crumb">${sub}</span>` +
      `<span class="dashdown-site-search-snippet">${snippetFor(e, r.terms)}</span>` +
      `</a>`
    );
  });
  panel.innerHTML = items.join("");
  panel.hidden = false;
  return Array.from(panel.querySelectorAll(".dashdown-site-search-result"));
}

export function initSiteSearch(el) {
  const input = el.querySelector(".dashdown-site-search-input");
  const panel = el.querySelector(".dashdown-site-search-results");
  if (!input || !panel) return;

  let config = {};
  try {
    config = JSON.parse(el.dataset.config || "{}");
  } catch (e) {
    /* keep defaults */
  }
  const maxResults = config.max_results || 8;

  let entries = null;
  let options = [];
  let active = -1;
  let debounceTimer = null;

  function close() {
    panel.hidden = true;
    input.setAttribute("aria-expanded", "false");
    active = -1;
  }

  function setActive(next) {
    if (!options.length) return;
    if (active >= 0 && options[active]) options[active].removeAttribute("aria-selected");
    active = (next + options.length) % options.length;
    const opt = options[active];
    opt.setAttribute("aria-selected", "true");
    opt.scrollIntoView({ block: "nearest" });
  }

  async function run() {
    const q = input.value.trim();
    if (!q) {
      close();
      return;
    }
    if (entries === null) entries = await loadIndex();
    // Bail if the input changed while we were loading the index.
    if (input.value.trim() !== q) return;
    const results = rank(entries, q, maxResults);
    options = renderResults(panel, results);
    input.setAttribute("aria-expanded", "true");
    active = options.length ? 0 : -1;
  }

  input.addEventListener("input", () => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(run, 120);
  });

  input.addEventListener("keydown", (ev) => {
    if (ev.key === "ArrowDown") {
      ev.preventDefault();
      setActive(active + 1);
    } else if (ev.key === "ArrowUp") {
      ev.preventDefault();
      setActive(active - 1);
    } else if (ev.key === "Enter") {
      if (active >= 0 && options[active]) {
        ev.preventDefault();
        window.location.href = options[active].getAttribute("href");
      }
    } else if (ev.key === "Escape") {
      close();
      input.blur();
    }
  });

  // Click-away closes the panel.
  document.addEventListener("click", (ev) => {
    if (!el.contains(ev.target)) close();
  });
  input.addEventListener("focus", () => {
    if (input.value.trim()) run();
  });

  // "/" focuses the first *visible* search box (skip when already typing in a
  // field). Visibility matters because the header box is display:none on mobile
  // and the menu box is display:none on desktop — the shortcut should land on
  // whichever one the user can actually see.
  document.addEventListener("keydown", (ev) => {
    if (ev.key !== "/" || ev.metaKey || ev.ctrlKey || ev.altKey) return;
    const t = ev.target;
    const tag = t && t.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || (t && t.isContentEditable)) return;
    const inputs = Array.from(document.querySelectorAll(".dashdown-site-search-input"));
    const firstVisible = inputs.find((i) => i.offsetParent !== null);
    if (firstVisible === input) {
      ev.preventDefault();
      input.focus();
    }
  });
}

export function initAllSiteSearches() {
  document.querySelectorAll('[data-async-component="site-search"]').forEach((el) => {
    initSiteSearch(el);
  });
}
