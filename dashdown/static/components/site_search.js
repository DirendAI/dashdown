// Dashdown Site Search
//
// Full-text search across every page. The component placeholder ships empty; this
// module fetches the search index once (shared across every box on the page) and
// ranks pages/sections entirely in the browser — there is no server-side search.
// The index is prefetched on focus so it's warm before the first keystroke, and
// matched terms are highlighted in the title, crumb, and snippet.
//
// When ask is merged into the box an "Ask the data" row joins the results: it
// leads the dropdown when the query reads as a question (so the visible, aria-
// selected default — the first row — asks) and trails the hits otherwise; an
// imperative-shaped input ("add …") on a compose-capable box leads with an
// "✎ Add to this page" row instead. Focusing an empty ask box opens a small
// recents + "try asking" dropdown (three of each, no extra chrome).
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

// ---- Empty-focus dropdown: recents + "try asking" suggestions ------------

// One in-flight fetch of ask suggestions, shared by every ask-enabled box on the
// page (module-level, like loadIndex). Suggestions never change within a page
// load, so this is fetched at most once. Failures degrade to an empty list.
let _suggestionsPromise = null;

// The leading word of every loaded suggestion ("revenue" from "revenue by
// region"), used by the ask-default heuristic. Empty until suggestions land —
// the heuristic works without it and only widens once the fetch resolves.
const _suggestionFirstWords = new Set();

// localStorage key holding the operator's recent questions (JSON array of
// strings, newest-first). Written by ask_box.js on every successful answer.
const _RECENT_KEY = "dashdown-recent-asks";

// Platform detection for the ⌘/Ctrl modifier hints (Mac → ⌘, else Ctrl). Drives
// the ask row's resting kbd label and the per-box ⌘K chip swap.
const _IS_MAC = /Mac|iP(hone|ad|od)/.test(navigator.platform || "");
const _ASK_KBD_REST = _IS_MAC ? "⌘↵" : "Ctrl ↵";

function firstWord(s) {
  const m = String(s || "")
    .toLowerCase()
    .match(/[a-z0-9]+/);
  return m ? m[0] : "";
}

function loadSuggestions() {
  if (_suggestionsPromise) return _suggestionsPromise;
  const build = readBuildConfig();
  // Static exports have no data API (and no ask surface) — never fetch there.
  if (build && build.static) {
    _suggestionsPromise = Promise.resolve([]);
    return _suggestionsPromise;
  }
  _suggestionsPromise = fetch("/_dashdown/api/ask/suggestions")
    .then((r) => (r.ok ? r.json() : { suggestions: [] }))
    .catch(() => ({ suggestions: [] }))
    .then((d) => (d && Array.isArray(d.suggestions) ? d.suggestions : []))
    .then((list) => {
      for (const s of list) {
        // The dev-only compose starter ("add …") must not widen the
        // question-shape heuristic — "add" is the compose verb, not an ask verb.
        if (isComposeShaped(s)) continue;
        const w = firstWord(s);
        if (w) _suggestionFirstWords.add(w);
      }
      return list;
    });
  return _suggestionsPromise;
}

// Read the operator's recent questions from localStorage (newest-first). Any
// storage error / malformed value degrades to an empty list (private mode).
function readRecents() {
  try {
    const raw = window.localStorage.getItem(_RECENT_KEY);
    const arr = raw ? JSON.parse(raw) : [];
    return Array.isArray(arr) ? arr.filter((x) => typeof x === "string") : [];
  } catch (e) {
    return [];
  }
}

// A conservative "does this read like a question (ask), not a keyword search?"
// test. True when: the query ends in "?", OR its leading word is an
// interrogative / aggregate, OR (once suggestions have loaded) it mentions a
// known metric's leading word. Word-boundaried so "sum" doesn't match
// "consumer". Empty/until-loaded suggestions simply narrow the last clause.
const _INTERROGATIVES = new Set([
  "who", "what", "when", "where", "which", "why", "how",
  "show", "list", "top", "count", "sum", "total", "average", "avg", "compare",
]);

// Leading words that read as a page-composition instruction ("add a KPI row…").
// Only OFFERS the "✎ Add to this page" row — selection stays explicit, exactly
// like the question heuristic only promotes the ask row. Compose is available
// only when the box config carries ask_keep (the dev-server authoring surface).
const _IMPERATIVES = new Set(["add", "insert", "put", "pin", "place"]);
function isComposeShaped(q) {
  const lead = firstWord(q);
  return !!lead && _IMPERATIVES.has(lead);
}
function isQuestionShaped(q) {
  const s = (q || "").trim().toLowerCase();
  if (!s) return false;
  if (s.endsWith("?")) return true;
  const lead = firstWord(s);
  if (lead && _INTERROGATIVES.has(lead)) return true;
  for (const w of _suggestionFirstWords) {
    if (w && new RegExp("\\b" + escapeRe(w) + "\\b").test(s)) return true;
  }
  return false;
}

// One suggestion/recent row: same row chrome as a search hit, with a leading
// glyph and the question stashed on `data-q` for keyboard + click selection.
function suggestRowMarkup(panel, idx, q, glyph) {
  return (
    `<div class="dashdown-site-search-result dashdown-ask-suggest-row" ` +
    `role="option" id="${esc(panel.id)}-opt-${idx}" data-idx="${idx}" ` +
    `data-q="${esc(q)}">` +
    `<span class="dashdown-ask-suggest-icon" aria-hidden="true">${esc(glyph)}</span>` +
    `<span class="dashdown-ask-suggest-label">${esc(q)}</span>` +
    "</div>"
  );
}

// Render the empty-focus dropdown: up to three Recent rows (↻) and up to three
// "Try asking" suggestions (✦). Section headers are non-option, aria-hidden
// labels. Returns the option elements in DOM order, or null when there's
// nothing to show (the caller then leaves the panel closed).
function renderEmptyResults(panel, data) {
  const recents = data.recents || [];
  const suggestions = data.suggestions || [];
  if (!recents.length && !suggestions.length) return null;

  const parts = [];
  let idx = 0;
  if (recents.length) {
    parts.push(
      '<div class="dashdown-ask-suggest-head" aria-hidden="true">Recent</div>'
    );
    for (const q of recents) {
      parts.push(suggestRowMarkup(panel, idx, q, "↻"));
      idx += 1;
    }
  }
  if (suggestions.length) {
    parts.push(
      '<div class="dashdown-ask-suggest-head" aria-hidden="true">Try asking</div>'
    );
    for (const q of suggestions) {
      // The dev-only compose starter ("add …") gets the ✎ glyph so the one
      // page-writing verb is discoverable without any extra chrome.
      parts.push(suggestRowMarkup(panel, idx, q, isComposeShaped(q) ? "✎" : "✦"));
      idx += 1;
    }
  }
  panel.innerHTML = parts.join("");
  panel.hidden = false;
  return Array.from(panel.querySelectorAll('[role="option"]'));
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

// Escape `text`, then wrap each search term in <mark> (case-insensitive). The
// shared highlighter for titles, crumbs, and snippets.
function highlightTerms(text, terms) {
  let html = esc(text || "");
  for (const term of terms) {
    html = html.replace(new RegExp("(" + escapeRe(term) + ")", "gi"), "<mark>$1</mark>");
  }
  return html;
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
  return highlightTerms(snip, terms);
}

function escapeRe(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// Structural words that must not gate the AND-match: a question-shaped query
// ("how is revenue by month?") would otherwise match nothing, since no page
// contains "how"/"is". When any content term remains, rank (and highlight) on
// the content terms only; an all-stopword query falls back to the full set.
const _STOPWORDS = new Set([
  "a", "an", "the", "is", "are", "was", "were", "be", "been",
  "do", "does", "did", "can", "could", "should", "would", "will",
  "how", "what", "when", "where", "which", "who", "why",
  "of", "in", "on", "at", "to", "for", "from", "with", "by",
  "and", "or", "per", "vs", "me", "my", "our", "your",
]);

function rank(entries, query, max) {
  const all = tokenize(query);
  if (!all.length) return [];
  const content = all.filter((t) => !_STOPWORDS.has(t));
  const terms = content.length ? content : all;
  const scored = [];
  for (const entry of entries) {
    const r = scoreEntry(entry, terms);
    if (r) scored.push({ entry, ...r, terms });
  }
  scored.sort((a, b) => b.score - a.score);
  return scored.slice(0, max);
}

// Render the dropdown for one query. `results` are ranked search hits (may be
// empty); when `askOn` a selectable "Ask the data" row joins them — leading the
// dropdown when `askFirst` (the query reads as a question, so the visible
// default is the first row), else trailing the hits (or, with zero hits, a "no
// pages match" hint above it). Matched terms are highlighted in title + crumb.
// Every selectable node carries role="option" so keyboard nav treats hits and
// the ask row uniformly. Returns the option elements in DOM order.
function renderResults(panel, results, query, opts) {
  const askOn = !!(opts && opts.askOn);
  const searchOn = !(opts && opts.searchOn === false);
  const askFirst = !!(opts && opts.askFirst);
  const composeOn = !!(opts && opts.composeOn);
  const parts = [];

  // The ask row's id needn't encode order (aria-activedescendant only needs it
  // unique), so it's fixed while hit ids stay -opt-<hit index>.
  const askRow = askOn
    ? '<div class="dashdown-site-search-result dashdown-site-search-ask-row" role="option" ' +
        `id="${esc(panel.id)}-opt-ask">` +
        '<span class="dashdown-site-search-ask-icon" aria-hidden="true">✦</span>' +
        '<span class="dashdown-site-search-ask-label">Ask your data: ' +
        `<span class="dashdown-site-search-ask-q">“${esc(query)}”</span></span>` +
        `<kbd class="dashdown-site-search-ask-kbd" aria-hidden="true">${esc(_ASK_KBD_REST)}</kbd>` +
        "</div>"
    : "";

  // Offered only for an imperative-shaped input on a compose-capable box
  // (ask ∧ ask_keep — the dev-server authoring surface): "add a KPI row…"
  // becomes new page content via the compose preview flow (ask_box.js). It
  // LEADS (and is the Enter default) only when nothing else matched — with
  // search hits present it trails them, so a plain Enter on a plausible search
  // ("add connector") never fires a billable compose call.
  const composeRow = composeOn
    ? '<div class="dashdown-site-search-result dashdown-site-search-ask-row ' +
      'dashdown-site-search-compose-row" role="option" ' +
        `id="${esc(panel.id)}-opt-compose">` +
        '<span class="dashdown-site-search-ask-icon" aria-hidden="true">✎</span>' +
        '<span class="dashdown-site-search-ask-label">Add to page: ' +
        `<span class="dashdown-site-search-ask-q">“${esc(query)}”</span></span>` +
        "</div>"
    : "";
  const composeFirst = composeOn && results.length === 0;

  if (composeFirst) parts.push(composeRow);
  if (askFirst) parts.push(askRow);

  if (results.length) {
    results.forEach((r, i) => {
      const e = r.entry;
      const anchor = r.bestHeading ? "#" + r.bestHeading.id : "";
      const href = hrefFor(e.url) + anchor;
      const crumb = r.bestHeading
        ? highlightTerms(r.bestHeading.text, r.terms)
        : highlightTerms(e.url, r.terms);
      parts.push(
        `<a class="dashdown-site-search-result" href="${esc(href)}" role="option" ` +
          `id="${esc(panel.id)}-opt-${i}" data-idx="${i}">` +
          `<span class="dashdown-site-search-title">${highlightTerms(e.title, r.terms)}</span>` +
          `<span class="dashdown-site-search-crumb">${crumb}</span>` +
          `<span class="dashdown-site-search-snippet">${snippetFor(e, r.terms)}</span>` +
          `</a>`
      );
    });
  } else if (searchOn) {
    // Zero search hits. With ask available, nudge toward asking; otherwise the
    // plain "no matches" (unchanged search-only behavior).
    parts.push(
      '<div class="dashdown-site-search-empty">' +
        (askOn ? "No pages match — ask your data instead" : "No matches") +
        "</div>"
    );
  }

  if (askOn && !askFirst) parts.push(askRow);
  if (composeOn && !composeFirst) parts.push(composeRow);

  panel.innerHTML = parts.join("");
  panel.hidden = false;
  return Array.from(panel.querySelectorAll('[role="option"]'));
}

// Module-level shortcut registry. Every initialized box registers its input +
// which shortcuts it honors; the document-level "/" and Ctrl/⌘+K listeners are
// wired ONCE (below) and arbitrate across the set, so the two rendered boxes
// (header + mobile menu) don't each install a duplicate pair.
const _shortcutBoxes = new Set();
let _shortcutsWired = false;
let _boxSeq = 0; // fallback ids for the header/mobile omnibox (no wrapper id)

// The first registered box whose input is currently visible — the header box is
// display:none on mobile and the menu box display:none on desktop, so a shortcut
// should land on whichever one the user can actually see. Registration runs in
// DOM order (initAllSiteSearches iterates the nodes in order), matching the prior
// per-box `document.querySelectorAll(...).find(visible)` arbitration.
function firstVisibleShortcutBox() {
  for (const entry of _shortcutBoxes) {
    if (entry.input.offsetParent !== null) return entry;
  }
  return null;
}

function wireShortcutsOnce() {
  if (_shortcutsWired) return;
  _shortcutsWired = true;

  // "/" focuses the first visible box that has search on (skip when already
  // typing in a field, and in ask-only boxes where the "/" hint isn't shown).
  document.addEventListener("keydown", (ev) => {
    if (ev.key !== "/" || ev.metaKey || ev.ctrlKey || ev.altKey) return;
    const t = ev.target;
    const tag = t && t.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || (t && t.isContentEditable)) return;
    const entry = firstVisibleShortcutBox();
    if (entry && entry.searchOn) {
      ev.preventDefault();
      entry.input.focus();
    }
  });

  // Ctrl/Cmd+K focuses the omnibox from anywhere (search owns "/"; the ask box
  // owns ⌘K when ask is on — the one owner, so no double-preventDefault).
  document.addEventListener("keydown", (ev) => {
    if (ev.key.toLowerCase() !== "k" || !(ev.metaKey || ev.ctrlKey) || ev.altKey) return;
    const entry = firstVisibleShortcutBox();
    if (entry && entry.askOn) {
      ev.preventDefault();
      entry.input.focus();
      entry.input.select();
    }
  });
}

export function initSiteSearch(el) {
  const input = el.querySelector(".dashdown-site-search-input");
  const panel = el.querySelector(".dashdown-site-search-results");
  if (!input || !panel) return;

  // Combobox contract: give the results listbox an id (derived from the wrapper
  // id, with a fallback for the header/mobile omnibox which has none) and point
  // the input's aria-controls at it. markActive/close then move the input's
  // aria-activedescendant across the option ids renderResults stamps.
  const panelId = `${el.id || `dashdown-site-search-${++_boxSeq}`}-results`;
  panel.id = panelId;
  input.setAttribute("aria-controls", panelId);

  let config = {};
  try {
    config = JSON.parse(el.dataset.config || "{}");
  } catch (e) {
    /* keep defaults */
  }
  const maxResults = config.max_results || 8;
  // `search` defaults on (a bare `{max_results}` config is a plain search box);
  // `ask` merges the runtime ask surface into this box (an "Ask the data" row +
  // an answer panel attached by ask_box.js); `ask_keep` additionally arms the
  // compose row ("Add to this page" — dev-server authoring only).
  const askOn = !!config.ask;
  const searchOn = config.search !== false;
  const keepOn = !!config.ask_keep;

  let entries = null;
  let options = [];
  let active = -1;
  let debounceTimer = null;

  function close() {
    panel.hidden = true;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
    active = -1;
  }

  // Move selection to `next` (wrapping), stamping aria-selected + the input's
  // aria-activedescendant so the active row is visible. `scroll` gates the
  // scrollIntoView so the default row can be marked on render without jumping.
  function markActive(next, scroll) {
    if (!options.length) return;
    if (active >= 0 && options[active]) options[active].removeAttribute("aria-selected");
    active = (next + options.length) % options.length;
    const opt = options[active];
    opt.setAttribute("aria-selected", "true");
    if (scroll) opt.scrollIntoView({ block: "nearest" });
    if (opt.id) input.setAttribute("aria-activedescendant", opt.id);
    else input.removeAttribute("aria-activedescendant");
    syncAskKbd();
  }

  function setActive(next) {
    markActive(next, true);
  }

  // Keep the ask row's kbd hint truthful as rows are traversed: plain "↵" when
  // the ask row is the active option (Enter asks now), else the platform resting
  // label. No-op when the panel has no ask row (e.g. the empty state).
  function syncAskKbd() {
    const kbd = panel.querySelector(".dashdown-site-search-ask-kbd");
    if (!kbd) return;
    const opt = active >= 0 ? options[active] : null;
    kbd.textContent = isAskRow(opt) ? "↵" : _ASK_KBD_REST;
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

  function isComposeRow(opt) {
    return opt && opt.classList.contains("dashdown-site-search-compose-row");
  }

  function isAskRow(opt) {
    return (
      opt &&
      opt.classList.contains("dashdown-site-search-ask-row") &&
      !isComposeRow(opt)
    );
  }

  // Selecting the compose row: hand the instruction to ask_box.js's compose
  // flow via a DOM event (same decoupling as the ask row).
  function selectCompose() {
    const instruction = input.value.trim();
    if (!instruction) return;
    close();
    el.dispatchEvent(
      new CustomEvent("dashdown:compose", {
        detail: { instruction },
        bubbles: true,
      })
    );
  }

  function isSuggestRow(opt) {
    return opt && opt.classList.contains("dashdown-ask-suggest-row");
  }

  // Selecting a recent / suggestion row: fill the input with the question and
  // hand it to ask_box.js. An imperative-shaped suggestion on a compose-capable
  // box (the server's dev-only "add …" starter) routes to compose, not ask.
  function selectSuggestion(q) {
    const question = (q || "").trim();
    if (!question) return;
    input.value = question;
    close();
    if (keepOn && isComposeShaped(question)) {
      el.dispatchEvent(
        new CustomEvent("dashdown:compose", {
          detail: { instruction: question },
          bubbles: true,
        })
      );
      return;
    }
    el.dispatchEvent(
      new CustomEvent("dashdown:ask", { detail: { question }, bubbles: true })
    );
  }

  // The empty-focus dropdown (ask boxes only): a small recents + "try asking"
  // list. Fetches suggestions once per page (module cache) then renders; a
  // value typed while that loads switches back to the normal flow.
  async function runEmpty() {
    if (!askOn) return; // search-only boxes show nothing on empty focus
    const recents = readRecents().slice(0, 3);
    const suggestions = (await loadSuggestions()).slice(0, 3);
    // Bail if the user began typing (or the value otherwise changed) while the
    // suggestions were loading — the input handler owns the non-empty flow.
    if (input.value.trim()) return;
    // Also bail if focus left the input while suggestions loaded (tabbed/clicked
    // away) — don't open a dropdown under an unfocused box.
    if (document.activeElement !== input) return;
    const opts = renderEmptyResults(panel, { recents, suggestions });
    if (!opts) {
      close();
      return;
    }
    options = opts;
    // Wire each row's click (they're <div>s, not links).
    panel.querySelectorAll(".dashdown-ask-suggest-row").forEach((row) => {
      row.addEventListener("click", (e) => {
        e.preventDefault();
        selectSuggestion(row.getAttribute("data-q"));
      });
    });
    input.setAttribute("aria-expanded", "true");
    active = -1; // no default row in the empty state
    // ask_box.js reopens its answer panel on focus, and open() hides the results
    // dropdown. If that happened, keep the results hidden (checked once, after
    // the render tick, per the answer-open interplay).
    if (el.classList.contains("dashdown-ask-answer-open")) {
      panel.hidden = true;
      input.setAttribute("aria-expanded", "false");
      active = -1;
    }
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
    // Promote the ask row to the top when the query reads like a question, so a
    // plain Enter asks; otherwise it trails the hits and the top page wins. An
    // imperative-shaped input on a compose-capable box leads with the compose
    // row instead ("add …" is an instruction, not a search).
    const composeOn = askOn && keepOn && isComposeShaped(q);
    const askFirst = askOn && searchOn && results.length > 0 && isQuestionShaped(q);
    options = renderResults(panel, results, q, { askOn, searchOn, askFirst, composeOn });
    // Wire the ask/compose rows' clicks (search results are <a>, so they
    // navigate on their own; these rows are <div>s and need explicit handlers).
    const askRow = panel.querySelector(
      ".dashdown-site-search-ask-row:not(.dashdown-site-search-compose-row)"
    );
    if (askRow) askRow.addEventListener("click", (e) => { e.preventDefault(); selectAsk(); });
    const composeRow = panel.querySelector(".dashdown-site-search-compose-row");
    if (composeRow) {
      composeRow.addEventListener("click", (e) => {
        e.preventDefault();
        selectCompose();
      });
    }
    input.setAttribute("aria-expanded", "true");
    // Default selection is always the first row — the ask row when askFirst
    // promoted it, else the top hit (or the lone ask row on zero hits). Mark it
    // without scrolling so the default is visibly highlighted on render.
    active = options.length ? 0 : -1;
    if (active >= 0) markActive(active, false);
  }

  input.addEventListener("input", () => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(run, 120);
  });

  input.addEventListener("keydown", (ev) => {
    // Cmd/Ctrl+Enter always asks immediately (regardless of the active row), so
    // long as ask is on and the input isn't empty.
    if (
      ev.key === "Enter" &&
      (ev.metaKey || ev.ctrlKey) &&
      askOn &&
      input.value.trim()
    ) {
      ev.preventDefault();
      selectAsk();
      return;
    }
    if (ev.key === "ArrowDown") {
      ev.preventDefault();
      setActive(active + 1);
    } else if (ev.key === "ArrowUp") {
      ev.preventDefault();
      setActive(active - 1);
    } else if (ev.key === "Enter") {
      const opt = active >= 0 ? options[active] : null;
      if (isComposeRow(opt)) {
        ev.preventDefault();
        selectCompose();
      } else if (isAskRow(opt)) {
        ev.preventDefault();
        selectAsk();
      } else if (isSuggestRow(opt)) {
        // Empty-state row (recent / suggestion): re-ask its question.
        ev.preventDefault();
        selectSuggestion(opt.getAttribute("data-q"));
      } else if (opt && opt.tagName === "A") {
        ev.preventDefault();
        window.location.href = opt.getAttribute("href");
      } else if (askOn && input.value.trim()) {
        // No active row (ask-only mode, a closed dropdown, or an open answer
        // panel): Enter asks the typed question — which is also the retry
        // affordance after an error (cache makes an exact re-ask cheap).
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
    // Warm the search index while the user types (dedups via the module promise).
    if (searchOn) loadIndex();
    if (input.value.trim()) run();
    else runEmpty();
  });
  // A click on an already-focused (so no `focus` event) empty box also opens the
  // empty-focus dropdown. Guarded so it doesn't fight ask_box.js's own click
  // reopen of the answer panel (that keeps the results hidden anyway).
  input.addEventListener("click", () => {
    if (!input.value.trim() && panel.hidden) runEmpty();
  });

  // Swap the ⌘K hint chip for the Mac glyph (per-box — each box owns its chip).
  if (askOn) {
    const askHint = el.querySelector(".dashdown-site-search-hint-ask");
    if (askHint && _IS_MAC) askHint.textContent = "⌘K";
  }

  // Register this box for the shared "/" + Ctrl/⌘K shortcuts, then wire the two
  // document-level listeners once (they arbitrate to the first visible box).
  _shortcutBoxes.add({ input, searchOn, askOn });
  wireShortcutsOnce();
}

export function initAllSiteSearches() {
  document.querySelectorAll('[data-async-component="site-search"]').forEach((el) => {
    initSiteSearch(el);
  });
}
