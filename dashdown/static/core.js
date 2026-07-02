// Dashdown Core Utilities
// Shared functions for API client, caching, data transformation

"use strict";

// Cache for loaded query data
// Key: queryName + JSON.stringify(params)
export const queryCache = {};

// Track queries currently being loaded
export const loadingQueries = new Set();

// Track components waiting for data: queryCacheKey -> [resolve functions]
export const pendingComponents = new Map();

// Global datasets (for legacy support)
export let allDatasets = {};

/**
 * Trailing-edge debounce: returns a wrapper that delays calling `fn` until `ms`
 * have elapsed since the LAST call, coalescing a burst (keystrokes, slider drag
 * ticks) into one invocation. `ms <= 0` calls through synchronously (no timer).
 * The returned wrapper exposes `.cancel()` to drop a pending call. Shared by the
 * filter controls that write the store from JS (slider/range/daterange); Search
 * uses Alpine's own `x-model.debounce` instead.
 * @param {Function} fn
 * @param {number} ms
 * @returns {Function & {cancel: () => void}}
 */
export function debounce(fn, ms) {
  let timer = null;
  const wrapped = function (...args) {
    if (!ms || ms <= 0) {
      fn.apply(this, args);
      return;
    }
    clearTimeout(timer);
    timer = setTimeout(() => {
      timer = null;
      fn.apply(this, args);
    }, ms);
  };
  wrapped.cancel = () => {
    clearTimeout(timer);
    timer = null;
  };
  return wrapped;
}

/**
 * Parse URL query parameters
 * @returns {Object} - Key-value pairs of query parameters
 */
export function parseUrlParams() {
  const params = new URLSearchParams(window.location.search);
  const out = {};
  for (const [k, v] of params.entries()) {
    // `_`-prefixed keys are internal (_connector, _refresh, _embed, _theme) and
    // must not seed filter state or surface as stray filter chips.
    if (k.startsWith("_")) continue;
    out[k] = v;
  }
  return out;
}

// The embed token from ?_embed=<token>, present when this page is rendered
// inside an iframe (the standalone embed.js loader, or a hand-written one).
// Read raw from the URL (like _connector) and threaded onto data/ask/WS
// requests so an embedded, authed page can still fetch its own data. A bare
// `_embed=1` (token-free open embed) yields null — no token to send.
let _embedToken;
export function readEmbedToken() {
  if (_embedToken !== undefined) return _embedToken;
  const v = new URLSearchParams(window.location.search).get("_embed");
  _embedToken = v && v !== "1" ? v : null;
  return _embedToken;
}

// Route params for a dynamic `[slug]` page, emitted by the server as a
// #dashdown-route-params JSON script (e.g. `/teams/Brazil` -> {team: "Brazil"}).
// They are merged into every data/ask/WS request at LOWEST precedence (an
// explicit filter or URL query param of the same name still wins), so the
// request URL is UNIQUE PER RECORD. Without this, two slugs of one page template
// produce byte-identical, cacheable data URLs (`/api/data/q?_connector=main`) and
// the browser serves the first record's response for the second — and the
// server-side result cache key collides too. `undefined` = not yet read.
let _routeParams;
export function readRouteParams() {
  if (_routeParams !== undefined) return _routeParams;
  const el =
    typeof document !== "undefined" &&
    document.getElementById("dashdown-route-params");
  if (!el) {
    _routeParams = {};
    return _routeParams;
  }
  try {
    _routeParams = JSON.parse(el.textContent || "{}") || {};
  } catch (e) {
    console.error("dashdown: failed to parse route params", e);
    _routeParams = {};
  }
  return _routeParams;
}

const _DEFAULT_CLIENT_TTL_MS = 60_000; // 60 seconds

// Static-build config, injected by `dashdown build` as a #dashdown-build JSON
// script. When present (`static: true`), there is no live data API — query data
// is fetched from pre-rendered JSON files instead. `undefined` means "not yet
// read"; `null` means "read, not a static build" (i.e. the dev/live server).
let _buildConfig;

/**
 * Read the static-build config (memoized). Returns null on a live server.
 * @returns {{static: boolean, dataBase: string}|null}
 */
export function readBuildConfig() {
  if (_buildConfig !== undefined) return _buildConfig;
  const el = document.getElementById("dashdown-build");
  if (!el) {
    _buildConfig = null;
    return null;
  }
  try {
    _buildConfig = JSON.parse(el.textContent || "null");
  } catch (e) {
    console.error("dashdown: failed to parse build config", e);
    _buildConfig = null;
  }
  return _buildConfig;
}

// Branding config (`branding:` in dashdown.yaml), injected as a
// #dashdown-branding JSON script. Same memoization contract as _buildConfig.
let _brandingConfig;

/**
 * Read the branding config (memoized). Returns null when the project has no
 * `branding:` block.
 * @returns {{palette: string[]}|null}
 */
export function readBrandingConfig() {
  if (_brandingConfig !== undefined) return _brandingConfig;
  const el = document.getElementById("dashdown-branding");
  if (!el) {
    _brandingConfig = null;
    return null;
  }
  try {
    _brandingConfig = JSON.parse(el.textContent || "null");
  } catch (e) {
    console.error("dashdown: failed to parse branding config", e);
    _brandingConfig = null;
  }
  return _brandingConfig;
}

let _formatConfig;

/**
 * Read the project-wide formatting defaults (memoized) from the `#dashdown-format`
 * script tag (the `format:` block in dashdown.yaml). Returns `{}` when unset.
 * @returns {{locale?: string, currency?: string}}
 */
export function readFormatConfig() {
  if (_formatConfig !== undefined) return _formatConfig;
  const el = typeof document !== "undefined" && document.getElementById("dashdown-format");
  if (!el) {
    _formatConfig = {};
    return _formatConfig;
  }
  try {
    _formatConfig = JSON.parse(el.textContent || "{}") || {};
  } catch (e) {
    console.error("dashdown: failed to parse format config", e);
    _formatConfig = {};
  }
  return _formatConfig;
}

/**
 * Merge a component's own format config over the project-wide defaults, yielding
 * the opts passed to `formatValue`. A component's `locale=`/`currency=` win; the
 * `format:` block fills the gaps. Crucially this only supplies *values* (which
 * symbol, which separators) — it never decides *whether* a value is formatted,
 * so a project `currency` default can't turn a plain count into money.
 * @param {{format?: string, currency?: string, decimals?: number, locale?: string, date_format?: string}} cfg
 * @returns {{currency: string, decimals: any, locale: string|undefined, dateFormat: string|undefined}}
 */
export function resolveFormatOpts(cfg = {}) {
  const proj = readFormatConfig();
  return {
    currency: cfg.currency ?? proj.currency ?? "$",
    decimals: cfg.decimals,
    locale: cfg.locale ?? proj.locale,
    dateFormat: cfg.date_format ?? proj.date_format,
  };
}

/**
 * Fetch data for a query from the API
 * @param {string} queryName - Name of the query to fetch
 * @param {Object} params - URL parameters
 * @param {Object} filters - Filter parameters (take precedence over params)
 * @returns {Promise<Object>} - Query result with columns and rows
 */
export async function fetchQueryData(queryName, params = {}, filters = {}) {
  // Merge route params (lowest precedence) + params + filters (highest). The
  // route params make a dynamic [slug] page's request URL unique per record.
  const allParams = { ...readRouteParams(), ...params, ...filters };

  // Create cache key that includes filter params
  const cacheKey = queryName + JSON.stringify(allParams);

  // Return cached data if still within TTL
  const cached = queryCache[cacheKey];
  if (cached && Date.now() < cached.expiry) {
    return cached.data;
  }

  // If already loading this exact query, return pending promise
  if (loadingQueries.has(cacheKey)) {
    return new Promise((resolve) => {
      if (!pendingComponents.has(cacheKey)) {
        pendingComponents.set(cacheKey, []);
      }
      pendingComponents.get(cacheKey).push(resolve);
    });
  }

  loadingQueries.add(cacheKey);

  // Get connector name and per-query TTL from query definitions
  const queryDef = readQueryDefs()[queryName];
  const connectorName = queryDef ? queryDef.connector : "main";
  const ttlMs = queryDef?.cache_ttl != null ? queryDef.cache_ttl * 1000 : _DEFAULT_CLIENT_TTL_MS;

  // In a static build there is no data API: every query was pre-rendered to a
  // JSON snapshot. Interactive filters are ignored (the snapshot is fixed), but
  // the build bakes a `data_url` onto each query def pointing at THIS page's
  // snapshot — per record on a dynamic [slug] page, so two records don't share
  // (and overwrite) one file. Plain pages fall back to the connector/name path
  // (also what an older build without `data_url` emits).
  const build = readBuildConfig();
  let requestUrl;
  if (build && build.static) {
    requestUrl =
      (queryDef && queryDef.data_url) ||
      `${build.dataBase}/${encodeURIComponent(connectorName)}/` +
        `${encodeURIComponent(queryName)}.json`;
  } else {
    // Build query string from params, including connector
    const allRequestParams = { ...allParams, _connector: connectorName };
    // Carry the embed token so an authed dashboard's data API accepts the
    // request from inside the iframe (no Basic/api_key header is possible there).
    const embedToken = readEmbedToken();
    if (embedToken) allRequestParams._embed = embedToken;
    const paramString = Object.keys(allRequestParams).length > 0
      ? "?" + new URLSearchParams(allRequestParams).toString()
      : "";
    requestUrl = `/_dashdown/api/data/${queryName}${paramString}`;
  }

  try {
    const response = await fetch(requestUrl);
    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`HTTP ${response.status}: ${errorText}`);
    }
    const data = await response.json();

    // Cache the result with TTL
    queryCache[cacheKey] = { data, expiry: Date.now() + ttlMs };
    loadingQueries.delete(cacheKey);

    // Resolve any pending components waiting for this data
    const resolvers = pendingComponents.get(cacheKey) || [];
    resolvers.forEach((r) => r(data));
    pendingComponents.delete(cacheKey);

    // Notify listeners (e.g. the page-header "Updated" stamp) that fresh data
    // landed. Best-effort: a missing CustomEvent must not fail the fetch.
    try {
      document.dispatchEvent(
        new CustomEvent("dashdown:data-loaded", { detail: { queryName } })
      );
    } catch (e) {
      /* CustomEvent unsupported — non-critical */
    }

    return data;
  } catch (error) {
    loadingQueries.delete(cacheKey);

    // Resolve pending with error
    const resolvers = pendingComponents.get(cacheKey) || [];
    resolvers.forEach((r) => r({ error: error.message }));
    pendingComponents.delete(cacheKey);

    console.error(`Failed to load query "${queryName}":`, error);
    throw error;
  }
}

/**
 * Fetch a page of distinct, server-side-searched column values for a <Combobox>.
 * Hits the options endpoint, which runs a DISTINCT … ILIKE … LIMIT against the
 * warehouse — so a high-cardinality column never ships whole to the browser.
 * Resolves the connector + embed token the same way fetchQueryData does, and
 * threads active filters so options can cascade off other controls.
 * @param {string} queryName
 * @param {string} column
 * @param {string} search - substring to match (server escapes it)
 * @param {Object} opts - { limit, filters }
 * @returns {Promise<Array<string>>}
 */
export async function fetchQueryOptions(queryName, column, search = "", opts = {}) {
  const { limit, filters = {} } = opts;
  const queryDef = readQueryDefs()[queryName];
  const connectorName = queryDef ? queryDef.connector : "main";

  const params = {
    ...readRouteParams(),
    ...filters,
    _connector: connectorName,
    _column: column,
  };
  if (search) params._search = search;
  if (limit) params._limit = String(limit);
  const embedToken = readEmbedToken();
  if (embedToken) params._embed = embedToken;

  const url =
    `/_dashdown/api/options/${queryName}?` +
    new URLSearchParams(params).toString();
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${await response.text()}`);
  }
  const data = await response.json();
  return Array.isArray(data.options) ? data.options : [];
}

/**
 * Convert dataset to array of record objects
 * @param {Object} ds - Dataset with columns and rows
 * @returns {Array<Object>} - Array of record objects
 */
export function recordsOf(ds) {
  if (!ds || !ds.columns) return [];
  return (ds.rows || []).map((r) => {
    const o = {};
    for (let i = 0; i < ds.columns.length; i++) o[ds.columns[i]] = r[i];
    return o;
  });
}

/**
 * Whether a data component should re-fetch when filter state changes.
 *
 * Query SQL is never shipped to the client — it stays server-side — so the
 * client can't know which params a given query substitutes. We conservatively
 * re-fetch on any filter change; the server substitutes only the params each
 * query's SQL actually references, so the result is still correct.
 * @returns {boolean}
 */
export function queryUsesFilters() {
  return true;
}

/* ------------------------------------------------------------------ *
 * Real-time streaming: live WebSocket subscriptions.                  *
 * Additive to fetchQueryData — components still paint from their first *
 * fetch, then a live socket replaces the data when it changes.        *
 * ------------------------------------------------------------------ */

/**
 * Non-empty, non-internal filter values, used by the live path to decide when a
 * filter change should reopen the socket. Query SQL is never shipped to the
 * client, so every active filter is treated as relevant; the server substitutes
 * only the params each query actually uses.
 * @param {Object} filters
 * @returns {Object} - filter name -> string value
 */
export function relevantFilters(filters) {
  const out = {};
  for (const k of Object.keys(filters || {})) {
    if (k.startsWith("_")) continue;
    const v = filters[k];
    if (v == null || String(v) === "") continue;
    out[k] = String(v);
  }
  return out;
}

function queryDefsForLive() {
  return (
    (window.Alpine && Alpine.store && Alpine.store("queryDefs")) || readQueryDefs()
  );
}

/**
 * True if a query should stream over a WebSocket on this page — i.e. it's
 * marked `live` AND we're not in a static export (which has no server). Live
 * components use this to SKIP the one-shot data-API fetch and let the socket
 * deliver the first payload (it's sent immediately on connect): a flaky source
 * then can't surface a hard HTTP error on load, since the live path retries and
 * is self-healing. In a static build this returns false, so the snapshot fetch
 * runs as usual.
 * @param {string} queryName
 * @returns {boolean}
 */
export function isLiveQuery(queryName) {
  const def = queryDefsForLive()[queryName];
  if (!def || !def.live) return false;
  const build = readBuildConfig();
  return !(build && build.static);
}

/**
 * Open a WebSocket to the live-data endpoint for a query and invoke `onData`
 * with each pushed `{columns, rows}` payload. Returns an unsubscribe function
 * that closes the socket and stops reconnecting.
 *
 * Reconnects with capped exponential backoff if the socket drops — EXCEPT when
 * the server rejects with close code 1008 (not a live query / unauthorized),
 * since there's no point hammering a deliberate refusal.
 *
 * @param {string} queryName
 * @param {Object} params
 * @param {Object} filters - already reduced to relevant values by the caller
 * @param {(payload: Object) => void} onData
 * @returns {() => void} unsubscribe
 */
export function subscribeQueryData(queryName, params = {}, filters = {}, onData) {
  // Route params (lowest precedence) so a live query on a dynamic [slug] page
  // streams that record's data — matches the fetchQueryData merge above.
  const allParams = { ...readRouteParams(), ...params, ...filters };
  const def = queryDefsForLive()[queryName];
  const connectorName = def ? def.connector : "main";
  const reqParams = { ...allParams, _connector: connectorName };
  const embedToken = readEmbedToken();
  if (embedToken) reqParams._embed = embedToken;
  const qs = new URLSearchParams(reqParams).toString();
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const url =
    `${proto}//${window.location.host}/_dashdown/ws/data/` +
    `${encodeURIComponent(queryName)}` +
    (qs ? `?${qs}` : "");

  let socket = null;
  let closed = false;
  let attempt = 0;
  let retryTimer = null;

  function connect() {
    if (closed) return;
    try {
      socket = new WebSocket(url);
    } catch (e) {
      console.error(`dashdown: failed to open live socket for "${queryName}"`, e);
      return;
    }
    socket.onmessage = (event) => {
      attempt = 0; // healthy connection — reset backoff
      let data;
      try {
        data = JSON.parse(event.data);
      } catch (e) {
        console.error(`dashdown: bad live payload for "${queryName}"`, e);
        return;
      }
      onData(data);
    };
    socket.onclose = (event) => {
      if (closed) return;
      if (event && event.code === 1008) {
        closed = true; // server refused — do not reconnect
        return;
      }
      attempt += 1;
      const delay = Math.min(30000, 1000 * 2 ** Math.min(attempt, 5));
      retryTimer = setTimeout(connect, delay);
    };
    socket.onerror = () => {
      // Let onclose drive reconnection; just ensure the socket is closing.
      try {
        socket.close();
      } catch (e) {
        /* already closing */
      }
    };
  }

  connect();

  return function unsubscribe() {
    closed = true;
    clearTimeout(retryTimer);
    if (socket) {
      try {
        socket.close();
      } catch (e) {
        /* already closed */
      }
    }
  };
}

/**
 * Bind a live subscription to an element for a query — but only if the query is
 * `live` and we're not in a static export. Manages a single socket per element:
 * a filter change that the query references re-subscribes; an unrelated change
 * is a no-op (the relevant-filter set is unchanged). Call this from inside the
 * component's existing filter `Alpine.effect` so re-subscription is automatic.
 *
 * @param {HTMLElement} el
 * @param {string} queryName
 * @param {Object} filters - the full filters store
 * @param {(payload: Object) => void} onData
 */
export function bindLiveQuery(el, queryName, filters, onData) {
  const defs = queryDefsForLive();
  const def = defs[queryName];
  if (!def || !def.live) return; // not a live query
  const build = readBuildConfig();
  if (build && build.static) return; // no server to stream from

  const relevant = relevantFilters(filters);
  const key = JSON.stringify(relevant);
  if (el._liveUnsub && el._liveKey === key) return; // nothing relevant changed

  if (el._liveUnsub) el._liveUnsub();
  el._liveKey = key;
  el._liveUnsub = subscribeQueryData(queryName, {}, relevant, onData);
}

/**
 * Read query definitions from the page
 * @returns {Object} - Query definitions
 */
export function readQueryDefs() {
  const el = document.getElementById("dashdown-query-defs");
  if (!el) return {};
  try {
    return JSON.parse(el.textContent || "{}");
  } catch (e) {
    console.error("dashdown: failed to parse query defs", e);
    return {};
  }
}

/**
 * Read datasets from the page (for legacy support)
 * @returns {Object} - Datasets
 */
export function readDatasets() {
  const el = document.getElementById("dashdown-data");
  if (!el) return {};
  try {
    return JSON.parse(el.textContent || "{}");
  } catch (e) {
    console.error("dashdown: failed to parse datasets", e);
    return {};
  }
}

/**
 * Apply filters to records client-side (for legacy non-async mode)
 * @param {Array<Object>} records - Array of record objects
 * @param {Object} filters - Filter object
 * @param {Object} dropdownMeta - Metadata about dropdown filters
 * @returns {Array<Object>} - Filtered records
 */
export function applyFilters(records, filters, dropdownMeta) {
  if (!filters) return records;
  const active = Object.entries(filters).filter(([, v]) => v !== "" && v != null);
  if (active.length === 0) return records;

  return records.filter((rec) =>
    active.every(([name, val]) => {
      const meta = dropdownMeta[name];
      if (meta && meta.column) {
        // Dropdown: exact match on the mapped column
        return String(rec[meta.column]) === String(val);
      }
      // Search / unknown: case-insensitive substring match on column = filter name
      if (!(name in rec)) return true;
      return String(rec[name]).toLowerCase().includes(String(val).toLowerCase());
    })
  );
}

/**
 * Escape HTML special characters
 * @param {string} str - String to escape
 * @returns {string} - Escaped string
 */
export function esc(str) {
  if (str == null) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/**
 * Safe JSON stringify that handles circular references
 * @param {any} obj - Object to stringify
 * @returns {string} - JSON string
 */
export function safeJson(obj) {
  const seen = new Set();
  return JSON.stringify(obj, (key, value) => {
    if (typeof value === "object" && value !== null) {
      if (seen.has(value)) {
        return "[Circular]";
      }
      seen.add(value);
    }
    return value;
  });
}

/** Fraction-digit options: pin to `decimals` when the author set one, else the
 * format's natural default range. */
function fracDigits(decimals, defMin, defMax) {
  if (decimals != null && decimals !== "") {
    const d = Number(decimals);
    if (isFinite(d)) return { minimumFractionDigits: d, maximumFractionDigits: d };
  }
  return { minimumFractionDigits: defMin, maximumFractionDigits: defMax };
}

/** `Number#toLocaleString` that degrades to the default locale if `loc` is a
 * malformed BCP-47 tag, so a typo'd `locale=` attr can't throw inside a chart
 * axis formatter (which would break the whole render). */
function localeNumber(n, loc, opts) {
  try {
    return n.toLocaleString(loc || undefined, opts);
  } catch {
    return n.toLocaleString(undefined, opts);
  }
}

const _DATE_STYLES = new Set(["short", "medium", "long", "full"]);

/**
 * Apply a `date_format`: either one of the locale-aware Intl style keywords
 * (short | medium | long | full) or a moment.js-style token pattern
 * ("DD.MM.YYYY", "MMM D, YYYY h:mm A"). We implement the moment token subset
 * directly rather than bundling moment.js (this project is no-npm / no-CDN).
 */
function formatDateCustom(d, df, loc, withTime) {
  if (_DATE_STYLES.has(df)) {
    const o = withTime ? { dateStyle: df, timeStyle: "short" } : { dateStyle: df };
    try {
      return d.toLocaleString(loc || undefined, o);
    } catch {
      return d.toLocaleString(undefined, o);
    }
  }
  return formatMomentPattern(d, df, loc);
}

/**
 * Render a Date with a moment.js-compatible token string. Supported tokens:
 * YYYY YY · MMMM MMM MM M · DD D · dddd ddd · HH H · hh h · mm m · ss s · A a.
 * `[literal]` text is emitted verbatim; localized names (MMM/dddd/…) follow `loc`.
 */
function formatMomentPattern(d, pattern, loc) {
  const pad = (n) => String(n).padStart(2, "0");
  const name = (opt) => {
    try {
      return d.toLocaleDateString(loc || undefined, opt);
    } catch {
      return d.toLocaleDateString(undefined, opt);
    }
  };
  const h12 = d.getHours() % 12 || 12;
  const map = {
    YYYY: d.getFullYear(),
    YY: pad(d.getFullYear() % 100),
    MMMM: name({ month: "long" }),
    MMM: name({ month: "short" }),
    MM: pad(d.getMonth() + 1),
    M: d.getMonth() + 1,
    DD: pad(d.getDate()),
    D: d.getDate(),
    dddd: name({ weekday: "long" }),
    ddd: name({ weekday: "short" }),
    HH: pad(d.getHours()),
    H: d.getHours(),
    hh: pad(h12),
    h: h12,
    mm: pad(d.getMinutes()),
    m: d.getMinutes(),
    ss: pad(d.getSeconds()),
    s: d.getSeconds(),
    A: d.getHours() < 12 ? "AM" : "PM",
    a: d.getHours() < 12 ? "am" : "pm",
  };
  // `[literal]` passes through; otherwise longest-token-first wins.
  return pattern.replace(
    /\[([^\]]*)\]|YYYY|YY|MMMM|MMM|MM|M|DD|D|dddd|ddd|HH|H|hh|h|mm|m|ss|s|A|a/g,
    (tok, lit) => (lit !== undefined ? lit : String(map[tok])),
  );
}

/**
 * Format a single scalar for display, per a `format=` directive. The single
 * source for number/currency/percent/date rendering — Table, Value, Counter and
 * chart axes all route through here so a `$63712.895` becomes `$63,712.90`.
 *
 * @param {any} v - Raw value.
 * @param {string} [fmt] - currency | number | compact | percent | date |
 *   datetime. Empty or unknown → the raw string (still honoring `decimals` for
 *   numbers).
 * @param {{currency?: string, decimals?: number, locale?: string, dateFormat?: string}} [opts]
 *   - currency: for `currency` format. A bare symbol ("$", "€") is prepended;
 *     an ISO 4217 code ("EUR", "USD", "GBP") uses full locale-aware currency
 *     formatting (correct symbol placement *and* separators). Default "$".
 *   - decimals: pin the fraction-digit count (overrides the format's default).
 *   - locale: a BCP-47 tag ("de-DE", "fr-FR") for grouping/decimal separators;
 *     omitted → the viewer's browser locale.
 *   - dateFormat: for `date`/`datetime`, a moment.js-style token pattern
 *     ("DD.MM.YYYY") or an Intl style keyword (short|medium|long|full);
 *     omitted → a locale-aware medium date.
 * @returns {string}
 */
export function formatValue(v, fmt, opts = {}) {
  if (v == null || v === "") return "";
  const { currency = "$", decimals, locale, dateFormat } = opts;
  const n = Number(v);
  switch (fmt) {
    case "currency": {
      if (!isFinite(n)) return String(v);
      // An ISO 4217 code (EUR, USD, GBP) → full locale-aware currency format:
      // the symbol lands where the locale puts it (e.g. de-DE → "1.157.252,33 €")
      // and grouping/decimal separators follow the locale too.
      if (/^[A-Z]{3}$/.test(currency)) {
        const o = { style: "currency", currency };
        if (decimals != null && decimals !== "") {
          o.minimumFractionDigits = Number(decimals);
          o.maximumFractionDigits = Number(decimals);
        }
        return localeNumber(n, locale, o);
      }
      // A bare symbol is prepended; grouping follows `locale` (or the browser).
      return currency + localeNumber(n, locale, fracDigits(decimals, 0, 2));
    }
    case "number":
      if (!isFinite(n)) return String(v);
      return localeNumber(n, locale, fracDigits(decimals, 0, 3));
    case "compact": {
      // Abbreviated magnitude (3,338,316,067 → "3.34B") for KPI headlines.
      // Three significant digits by default; an explicit `decimals=` pins the
      // fraction-digit count instead ("3.3B"). Locale-aware — de-DE → "3,34 Mrd.".
      if (!isFinite(n)) return String(v);
      const o = { notation: "compact" };
      if (decimals != null && decimals !== "") {
        o.minimumFractionDigits = Number(decimals);
        o.maximumFractionDigits = Number(decimals);
      } else {
        o.maximumSignificantDigits = 3;
      }
      return localeNumber(n, locale, o);
    }
    case "percent":
      if (!isFinite(n)) return String(v);
      return localeNumber(n, locale, fracDigits(decimals, 0, 1)) + "%";
    case "date":
    case "datetime": {
      // Parse a bare ISO date as local midnight so the day doesn't shift west.
      const iso = typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v);
      const d = new Date(iso ? v + "T00:00:00" : v);
      if (isNaN(d.getTime())) return String(v);
      // An explicit `date_format` (moment-style pattern or Intl style keyword)
      // overrides the default locale-aware medium date.
      if (dateFormat) return formatDateCustom(d, dateFormat, locale, fmt === "datetime");
      const dopts = { year: "numeric", month: "short", day: "numeric" };
      const loc = locale || undefined;
      try {
        if (fmt === "datetime")
          return d.toLocaleString(loc, { ...dopts, hour: "numeric", minute: "2-digit" });
        return d.toLocaleDateString(loc, dopts);
      } catch {
        return fmt === "datetime"
          ? d.toLocaleString(undefined, { ...dopts, hour: "numeric", minute: "2-digit" })
          : d.toLocaleDateString(undefined, dopts);
      }
    }
    default:
      // No (recognized) format: still apply `decimals` to a bare number, so
      // `decimals=2` alone rounds a value without choosing a format.
      if (decimals != null && decimals !== "" && isFinite(n)) {
        return localeNumber(n, locale, fracDigits(decimals, 0, 0));
      }
      return String(v);
  }
}
