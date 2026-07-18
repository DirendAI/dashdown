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

// Render the dropdown for one query. `results` are ranked search hits (may be
// empty); when `askOn` we append a final selectable "Ask the data" row and, if
// search is on but matched nothing, a "no pages match" hint above it. Every
// selectable node carries role="option" so keyboard nav treats hits and the ask
// row uniformly. Returns the option elements in DOM order.
function renderResults(panel, results, query, opts) {
  const askOn = !!(opts && opts.askOn);
  const searchOn = !(opts && opts.searchOn === false);
  const parts = [];

  if (results.length) {
    results.forEach((r, i) => {
      const e = r.entry;
      const anchor = r.bestHeading ? "#" + r.bestHeading.id : "";
      const href = hrefFor(e.url) + anchor;
      const sub = r.bestHeading ? esc(r.bestHeading.text) : esc(e.url);
      parts.push(
        `<a class="dashdown-site-search-result" href="${esc(href)}" role="option" ` +
          `data-idx="${i}">` +
          `<span class="dashdown-site-search-title">${esc(e.title)}</span>` +
          `<span class="dashdown-site-search-crumb">${sub}</span>` +
          `<span class="dashdown-site-search-snippet">${snippetFor(e, r.terms)}</span>` +
          `</a>`
      );
    });
  } else if (searchOn) {
    // Zero search hits. With ask available, nudge toward asking; otherwise the
    // plain "no matches" (unchanged search-only behavior).
    parts.push(
      '<div class="dashdown-site-search-empty">' +
        (askOn ? "No pages match — ask the data instead" : "No matches") +
        "</div>"
    );
  }

  if (askOn) {
    parts.push(
      '<div class="dashdown-site-search-result dashdown-site-search-ask-row" role="option" ' +
        `data-idx="${results.length}">` +
        '<span class="dashdown-site-search-ask-icon" aria-hidden="true">✦</span>' +
        '<span class="dashdown-site-search-ask-label">Ask the data: ' +
        `<span class="dashdown-site-search-ask-q">“${esc(query)}”</span></span>` +
        "</div>"
    );
  }

  panel.innerHTML = parts.join("");
  panel.hidden = false;
  return Array.from(panel.querySelectorAll('[role="option"]'));
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
  // `search` defaults on (a bare `{max_results}` config is a plain search box);
  // `ask` merges the runtime ask surface into this box (an "Ask the data" row +
  // an answer panel attached by ask_box.js).
  const askOn = !!config.ask;
  const searchOn = config.search !== false;

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

  // Selecting the ask row: hand the question to ask_box.js via a DOM event (no
  // import — the modules stay decoupled) and close the results panel.
  function selectAsk() {
    const question = input.value.trim();
    if (!question) return;
    close();
    el.dispatchEvent(
      new CustomEvent("dashdown:ask", { detail: { question }, bubbles: true })
    );
  }

  function isAskRow(opt) {
    return opt && opt.classList.contains("dashdown-site-search-ask-row");
  }

  async function run() {
    const q = input.value.trim();
    if (!q) {
      close();
      return;
    }
    let results = [];
    if (searchOn) {
      if (entries === null) entries = await loadIndex();
      // Bail if the input changed while we were loading the index.
      if (input.value.trim() !== q) return;
      results = rank(entries, q, maxResults);
    }
    options = renderResults(panel, results, q, { askOn, searchOn });
    // Wire the ask row's click (search results are <a>, so they navigate on
    // their own; the ask row is a <div> and needs an explicit handler).
    const askRow = panel.querySelector(".dashdown-site-search-ask-row");
    if (askRow) askRow.addEventListener("click", (e) => { e.preventDefault(); selectAsk(); });
    input.setAttribute("aria-expanded", "true");
    // Default selection: the first search hit when search matched, else the ask
    // row (which is options[0] whenever there are no hits) — so a plain Enter
    // prefers a page when one matched and asks otherwise.
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
      const opt = active >= 0 ? options[active] : null;
      if (isAskRow(opt)) {
        ev.preventDefault();
        selectAsk();
      } else if (opt && opt.tagName === "A") {
        ev.preventDefault();
        window.location.href = opt.getAttribute("href");
      } else if (askOn && !searchOn && input.value.trim()) {
        // Ask-only mode: Enter always asks, even before a row is rendered.
        ev.preventDefault();
        selectAsk();
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
  // field, and in ask-only boxes where the "/" hint isn't shown). Visibility
  // matters because the header box is display:none on mobile and the menu box is
  // display:none on desktop — the shortcut should land on whichever one the user
  // can actually see.
  if (searchOn) {
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

  // Ctrl/Cmd+K focuses the omnibox from anywhere (search owns "/"; this box owns
  // ⌘K when ask is on — the one owner, so no double-preventDefault). Same
  // first-visible arbitration as "/". Swap the hint chip for the Mac glyph.
  if (askOn) {
    const askHint = el.querySelector(".dashdown-site-search-hint-ask");
    const isMac = /Mac|iP(hone|ad|od)/.test(navigator.platform || "");
    if (askHint && isMac) askHint.textContent = "⌘K";
    document.addEventListener("keydown", (ev) => {
      if (ev.key.toLowerCase() !== "k" || !(ev.metaKey || ev.ctrlKey) || ev.altKey) return;
      const inputs = Array.from(document.querySelectorAll(".dashdown-site-search-input"));
      const firstVisible = inputs.find((i) => i.offsetParent !== null);
      if (firstVisible === input) {
        ev.preventDefault();
        input.focus();
        input.select();
      }
    });
  }
}

export function initAllSiteSearches() {
  document.querySelectorAll('[data-async-component="site-search"]').forEach((el) => {
    initSiteSearch(el);
  });
}
