// Dashdown shared geo helpers
// One module for everything the SVG map components (ChoroplethTime,
// ChoroplethFacets, BivariateMap, BubbleMap, DotDensityMap) share: the
// equirectangular projection, GeoJSON→SVG path building (with antimeridian
// breaks), geometry math (area/centroid/point-in-polygon/sampling), color
// ramps + the 3×3 bivariate palettes, a seeded PRNG, and small legend/tooltip
// DOM builders. `_`-prefixed = shared library, not an async component itself.
//
// Everything is self-drawn SVG — no mapping library, no CDN — so the maps are
// CSP-safe, offline-safe, and render identically in `dashdown build` exports.

"use strict";

import { readBrandingConfig } from "../core.js";

// --- projection --------------------------------------------------------------

// Equirectangular plate carrée into a fixed 2:1 viewBox. Every component draws
// into these coordinates and lets SVG scale to the card.
export const MAP_W = 960;
export const MAP_H = 480;

/** Project [lon, lat] (degrees) to viewBox [x, y]. */
export function project(lon, lat) {
  return [((lon + 180) / 360) * MAP_W, ((90 - lat) / 180) * MAP_H];
}

// --- GeoJSON geometry --------------------------------------------------------

/** A geometry's polygons as a uniform array: [ [outerRing, hole…], … ].
 * Degenerate polygons (no outer ring — the bundled geometry has one in
 * China's MultiPolygon) are dropped so every consumer can trust rings[0]. */
export function polygonsOf(geometry) {
  if (!geometry) return [];
  let polys = [];
  if (geometry.type === "Polygon") polys = [geometry.coordinates];
  else if (geometry.type === "MultiPolygon") polys = geometry.coordinates;
  return polys.filter((rings) => rings && rings.length && rings[0].length);
}

/** Signed shoelace area of a lon/lat ring (projection is linear in lon/lat,
 * so relative areas — all we need — are preserved). */
export function ringArea(ring) {
  let area = 0;
  for (let i = 0, n = ring.length; i < n; i++) {
    const [x1, y1] = ring[i];
    const [x2, y2] = ring[(i + 1) % n];
    area += x1 * y2 - x2 * y1;
  }
  return area / 2;
}

/** Ring centroid (standard polygon centroid; falls back to the vertex mean for
 * degenerate rings). */
function ringCentroid(ring) {
  let area = 0;
  let cx = 0;
  let cy = 0;
  for (let i = 0, n = ring.length; i < n; i++) {
    const [x1, y1] = ring[i];
    const [x2, y2] = ring[(i + 1) % n];
    const f = x1 * y2 - x2 * y1;
    area += f;
    cx += (x1 + x2) * f;
    cy += (y1 + y2) * f;
  }
  if (Math.abs(area) < 1e-9) {
    let sx = 0;
    let sy = 0;
    ring.forEach(([x, y]) => {
      sx += x;
      sy += y;
    });
    return [sx / ring.length, sy / ring.length];
  }
  return [cx / (3 * area), cy / (3 * area)];
}

/**
 * A feature's anchor point as [lon, lat]: the centroid of its *largest*
 * polygon's outer ring. Largest-only keeps a symbol on the mainland — the
 * contiguous US rather than a US-including-Alaska average in the Pacific.
 */
export function centroid(geometry) {
  const polys = polygonsOf(geometry);
  if (!polys.length) return null;
  let best = polys[0];
  let bestArea = -1;
  polys.forEach((rings) => {
    const a = Math.abs(ringArea(rings[0]));
    if (a > bestArea) {
      bestArea = a;
      best = rings;
    }
  });
  return ringCentroid(best[0]);
}

/** Ray-cast test: is [lon, lat] inside the ring? */
function pointInRing(lon, lat, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    if (
      yi > lat !== yj > lat &&
      lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi
    ) {
      inside = !inside;
    }
  }
  return inside;
}

/** Is [lon, lat] inside the polygon (outer ring minus holes)? */
export function pointInPolygon(lon, lat, rings) {
  if (!pointInRing(lon, lat, rings[0])) return false;
  for (let i = 1; i < rings.length; i++) {
    if (pointInRing(lon, lat, rings[i])) return false;
  }
  return true;
}

function ringBBox(ring) {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  ring.forEach(([x, y]) => {
    if (x < minX) minX = x;
    if (y < minY) minY = y;
    if (x > maxX) maxX = x;
    if (y > maxY) maxY = y;
  });
  return [minX, minY, maxX, maxY];
}

/**
 * `count` deterministic sample points inside a geometry, as [lon, lat] pairs.
 * Polygons are picked proportionally to their area, then rejection-sampled in
 * their bounding box. `rng` is a seeded PRNG (see `mulberry32`), so with the
 * same seed the dots land identically on every load and in static exports.
 */
export function samplePoints(geometry, count, rng) {
  const polys = polygonsOf(geometry);
  if (!polys.length || count <= 0) return [];
  const areas = polys.map((rings) => Math.abs(ringArea(rings[0])));
  const total = areas.reduce((s, a) => s + a, 0);
  if (total <= 0) return [];
  const bboxes = polys.map((rings) => ringBBox(rings[0]));

  const points = [];
  for (let d = 0; d < count; d++) {
    // Pick a polygon by area share.
    let pick = rng() * total;
    let pi = 0;
    while (pi < areas.length - 1 && pick > areas[pi]) {
      pick -= areas[pi];
      pi++;
    }
    const rings = polys[pi];
    const [minX, minY, maxX, maxY] = bboxes[pi];
    let placed = false;
    for (let tries = 0; tries < 40; tries++) {
      const lon = minX + rng() * (maxX - minX);
      const lat = minY + rng() * (maxY - minY);
      if (pointInPolygon(lon, lat, rings)) {
        points.push([lon, lat]);
        placed = true;
        break;
      }
    }
    // Slivers can defeat rejection sampling; anchor the leftover dot at the
    // polygon's centroid rather than dropping quantity from the map.
    if (!placed) points.push(ringCentroid(rings[0]));
  }
  return points;
}

// --- GeoJSON → SVG path ------------------------------------------------------

// Path strings are pure functions of the (immutable, shared) geometry, so they
// are memoized per geometry object across components and re-renders.
const pathCache = new WeakMap();

/**
 * SVG path data for a geometry under the shared projection. A segment jumping
 * more than 180° of longitude crosses the antimeridian (Fiji, Chukotka): the
 * path breaks into a new subpath there instead of smearing a line across the
 * whole map. SVG's even-odd fill closes each subpath, so fills stay correct.
 */
export function featurePath(geometry) {
  const cached = pathCache.get(geometry);
  if (cached !== undefined) return cached;
  const parts = [];
  polygonsOf(geometry).forEach((rings) => {
    rings.forEach((ring) => {
      let d = "";
      let open = false;
      let prevLon = null;
      ring.forEach(([lon, lat]) => {
        const [x, y] = project(lon, lat);
        const jump = prevLon !== null && Math.abs(lon - prevLon) > 180;
        if (!open || jump) {
          if (open) parts.push(d);
          d = `M${x.toFixed(2)} ${y.toFixed(2)}`;
          open = true;
        } else {
          d += `L${x.toFixed(2)} ${y.toFixed(2)}`;
        }
        prevLon = lon;
      });
      if (open) parts.push(d + "Z");
    });
  });
  const path = parts.join("");
  pathCache.set(geometry, path);
  return path;
}

// --- world geometry loader ---------------------------------------------------

// Same bundled file MapChart registers with ECharts — one geometry, two
// consumers. Enriched with `iso` (ISO 3166-1 numeric) by
// tooling/enrich-world-iso.py so these components can join on the code
// analytics datasets actually carry.
const WORLD_URL = new URL("../vendor/world.json", import.meta.url).href;
const geoCache = {};

/** Canonical join key: "004", 4 and "4" all mean Afghanistan. */
export function normalizeId(value) {
  if (value === null || value === undefined) return null;
  const n = parseInt(value, 10);
  if (isFinite(n)) return String(n);
  const s = String(value).trim();
  return s === "" ? null : s;
}

/**
 * Load the basemap for a component config: the bundled ISO-enriched world by
 * default (`map="world"`), or a custom `geojson=` URL. Resolves to
 * `{features, byId}` where `byId` joins on the config's `id_field` (default
 * `iso`; feature-level `id` is the fallback, matching common GeoJSON exports)
 * and each feature carries its normalized key as `_dashdownId`. Cached per
 * URL + id field.
 */
export function loadGeometry(config = {}) {
  let url = config.geojson;
  if (!url) {
    if (config.map && config.map !== "world") {
      return Promise.reject(
        new Error(`Unknown map "${config.map}" — pass a geojson="…" URL`)
      );
    }
    url = WORLD_URL;
  }
  const idField = config.id_field || "iso";
  const key = `${url}|${idField}`;
  if (!geoCache[key]) {
    geoCache[key] = fetch(url)
      .then((resp) => {
        if (!resp.ok) {
          throw new Error(`Failed to load map geometry (HTTP ${resp.status})`);
        }
        return resp.json();
      })
      .then((geojson) => {
        const features = (geojson.features || []).map((f) => {
          const raw = (f.properties && f.properties[idField]) ?? f.id;
          return { ...f, _dashdownId: normalizeId(raw) };
        });
        const byId = {};
        features.forEach((f) => {
          if (f._dashdownId !== null) byId[f._dashdownId] = f;
        });
        return { features, byId };
      })
      .catch((err) => {
        delete geoCache[key]; // allow a retry on the next render
        throw err;
      });
  }
  return geoCache[key];
}

// --- seeded PRNG ---------------------------------------------------------------

/** mulberry32 — tiny seeded PRNG. Deterministic dot placement is a feature:
 * the same data draws the same map on every load and in every export. */
export function mulberry32(seed) {
  let a = seed >>> 0;
  return function () {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** FNV-1a string hash → 32-bit seed (so "840|population" seeds stably). */
export function hashSeed(str) {
  let h = 0x811c9dc5;
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

// --- color ramps ---------------------------------------------------------------

function hexToRgb(hex) {
  let h = hex.replace("#", "");
  if (h.length === 3) h = h.replace(/./g, (c) => c + c);
  const n = parseInt(h, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function rgbToHex([r, g, b]) {
  const c = (v) => Math.round(Math.max(0, Math.min(255, v))).toString(16).padStart(2, "0");
  return `#${c(r)}${c(g)}${c(b)}`;
}

/** Linear mix of two hex colors (`t`=0 → a, `t`=1 → b). */
export function mixColors(a, b, t) {
  const ca = hexToRgb(a);
  const cb = hexToRgb(b);
  return rgbToHex([0, 1, 2].map((i) => ca[i] + (cb[i] - ca[i]) * t));
}

/** Color at `t` ∈ [0,1] along a ramp's stops (piecewise-linear). */
export function colorAt(stops, t) {
  const clamped = Math.max(0, Math.min(1, t));
  const pos = clamped * (stops.length - 1);
  const i = Math.min(Math.floor(pos), stops.length - 2);
  return mixColors(stops[i], stops[i + 1], pos - i);
}

/** Named sequential ramps (light → dark) for `scheme=`. */
export const SEQUENTIAL_SCHEMES = {
  blues: ["#eff6ff", "#bfdbfe", "#60a5fa", "#2563eb", "#1e3a8a"],
  greens: ["#f0fdf4", "#bbf7d0", "#4ade80", "#16a34a", "#14532d"],
  oranges: ["#fff7ed", "#fed7aa", "#fb923c", "#ea580c", "#7c2d12"],
  purples: ["#faf5ff", "#e9d5ff", "#c084fc", "#9333ea", "#581c87"],
  reds: ["#fef2f2", "#fecaca", "#f87171", "#dc2626", "#7f1d1d"],
  viridis: ["#440154", "#3b528b", "#21918c", "#5ec962", "#fde725"],
};

/** Derive a light→dark sequential ramp from one base color. */
export function rampFromColor(base) {
  return [
    mixColors("#ffffff", base, 0.1),
    mixColors("#ffffff", base, 0.35),
    mixColors("#ffffff", base, 0.65),
    base,
    mixColors(base, "#000000", 0.3),
  ];
}

/**
 * The effective sequential ramp for a map config: a named `scheme=` wins, then
 * a `color=` base color, then a ramp derived from the project's
 * `branding.palette`, else the built-in blues — the same precedence charts use
 * for their series palette.
 */
export function resolveScheme(config = {}) {
  if (config.scheme && SEQUENTIAL_SCHEMES[config.scheme]) {
    return SEQUENTIAL_SCHEMES[config.scheme];
  }
  if (config.color) {
    const first = String(config.color).split(",")[0].trim();
    if (first) return rampFromColor(first);
  }
  const branding = readBrandingConfig();
  if (branding && Array.isArray(branding.palette) && branding.palette.length) {
    return rampFromColor(branding.palette[0]);
  }
  return SEQUENTIAL_SCHEMES.blues;
}

/** 3×3 bivariate palettes (Joshua Stevens' classic sets). Index = row*3+col
 * with row = y class (low→high) and col = x class (low→high). */
export const BIVARIATE_SCHEMES = {
  "blue-purple": [
    "#e8e8e8", "#ace4e4", "#5ac8c8",
    "#dfb0d6", "#a5add3", "#5698b9",
    "#be64ac", "#8c62aa", "#3b4994",
  ],
  "green-blue": [
    "#e8e8e8", "#b5c0da", "#6c83b5",
    "#b8d6be", "#90b2b3", "#567994",
    "#73ae80", "#5a9178", "#2a5a5b",
  ],
  "red-blue": [
    "#e8e8e8", "#e4acac", "#c85a5a",
    "#b0d5df", "#ad9ea5", "#985356",
    "#64acbe", "#627f8c", "#574249",
  ],
};

/**
 * A value→[0,1] mapping for color ramps. `kind` comes from the `scale=` attr:
 * - "linear" (default): position between min and max.
 * - "log": position between log(min) and log(max) — for heavy-tailed metrics
 *   (population, GDP) where a linear ramp washes out everything but the max.
 * - "quantile": the value's rank share, so colors spread evenly regardless of
 *   distribution shape.
 * `values` is every value the scale must cover (e.g. all animation frames, so
 * the mapping stays fixed while the years play).
 */
export function makeScale(kind, values) {
  const nums = values.map(Number).filter((v) => isFinite(v));
  if (!nums.length) return { t: () => 0, min: 0, max: 0 };
  const min = Math.min(...nums);
  const max = Math.max(...nums);
  if (kind === "quantile") {
    const sorted = [...nums].sort((a, b) => a - b);
    return {
      min,
      max,
      t(v) {
        // Binary search: share of values ≤ v.
        let lo = 0;
        let hi = sorted.length;
        while (lo < hi) {
          const mid = (lo + hi) >> 1;
          if (sorted[mid] <= v) lo = mid + 1;
          else hi = mid;
        }
        return sorted.length > 1 ? lo / sorted.length : 1;
      },
    };
  }
  if (kind === "log") {
    const floor = Math.max(min, max > 0 ? max * 1e-6 : 1e-9);
    const lo = Math.log(floor);
    const hi = Math.log(Math.max(max, floor));
    const span = hi - lo || 1;
    return {
      min,
      max,
      t: (v) => Math.max(0, Math.min(1, (Math.log(Math.max(v, floor)) - lo) / span)),
    };
  }
  const span = max - min || 1;
  return { min, max, t: (v) => Math.max(0, Math.min(1, (v - min) / span)) };
}

/** Tercile break points of a numeric array: values ≤ b[0] are class 0, ≤ b[1]
 * class 1, else class 2. */
export function terciles(values) {
  const sorted = values.filter((v) => isFinite(v)).sort((a, b) => a - b);
  if (!sorted.length) return [0, 0];
  const q = (p) => sorted[Math.min(sorted.length - 1, Math.floor(p * sorted.length))];
  return [q(1 / 3), q(2 / 3)];
}

/** Class index 0/1/2 for a value against tercile breaks. */
export function tercileClass(v, breaks) {
  if (v <= breaks[0]) return 0;
  if (v <= breaks[1]) return 1;
  return 2;
}

// --- formatting ----------------------------------------------------------------

const compactFmt = new Intl.NumberFormat(undefined, {
  notation: "compact",
  maximumFractionDigits: 1,
});
const plainFmt = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });

/** Compact display number ("1.2M") with an optional unit suffix. */
export function fmtValue(v, unit) {
  if (v === null || v === undefined || !isFinite(Number(v))) return "–";
  const n = Number(v);
  const s = Math.abs(n) >= 10000 ? compactFmt.format(n) : plainFmt.format(n);
  if (!unit) return s;
  return unit === "%" ? `${s}%` : `${s} ${unit}`;
}

// --- shared DOM builders ---------------------------------------------------------

const SVG_NS = "http://www.w3.org/2000/svg";

/** An `<svg>` sized to the shared viewBox, scaling to its container. */
export function createMapSvg() {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${MAP_W} ${MAP_H}`);
  svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
  svg.classList.add("dashdown-map-svg");
  return svg;
}

export function svgEl(tag, attrs = {}) {
  const el = document.createElementNS(SVG_NS, tag);
  Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, v));
  return el;
}

/**
 * One shared tooltip per map region: `show(html, event)` positions it near the
 * pointer (clamped to the region), `hide()` removes it. The host gains
 * `position:relative` so the tooltip anchors to the map, not the page.
 */
export function createTooltip(host) {
  host.style.position = "relative";
  const tip = document.createElement("div");
  tip.className = "dashdown-map-tooltip";
  tip.hidden = true;
  host.appendChild(tip);
  return {
    show(html, event) {
      tip.innerHTML = html;
      tip.hidden = false;
      const rect = host.getBoundingClientRect();
      let x = event.clientX - rect.left + 12;
      let y = event.clientY - rect.top + 12;
      const w = tip.offsetWidth;
      const h = tip.offsetHeight;
      if (x + w > rect.width - 4) x = Math.max(4, x - w - 24);
      if (y + h > rect.height - 4) y = Math.max(4, y - h - 24);
      tip.style.left = `${x}px`;
      tip.style.top = `${y}px`;
    },
    hide() {
      tip.hidden = true;
    },
  };
}

/** A horizontal gradient legend for a sequential ramp, labeled min → max. */
export function gradientLegend(stops, minLabel, maxLabel) {
  const wrap = document.createElement("div");
  wrap.className = "dashdown-map-legend";
  const lo = document.createElement("span");
  lo.textContent = minLabel;
  const bar = document.createElement("span");
  bar.className = "dashdown-map-legend-bar";
  bar.style.background = `linear-gradient(to right, ${stops.join(", ")})`;
  const hi = document.createElement("span");
  hi.textContent = maxLabel;
  wrap.append(lo, bar, hi);
  return wrap;
}

/** Minimal HTML escape for text spliced into tooltip/error markup. */
export function escapeHtml(s) {
  return String(s ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

/**
 * Reset a map card's body into the shared shell every geo component draws
 * into: a header row (title + a slot for the component's own controls), the
 * map region, and a footer (legend / timeline). Rebuilt on every data render;
 * in-place updates (year scrub, metric toggle) mutate what's inside.
 */
export function mapShell(el, config) {
  let body = el.querySelector(".card-body");
  if (!body) {
    body = document.createElement("div");
    body.className = "card-body p-4 h-full";
    el.appendChild(body);
  }
  body.textContent = "";
  body.classList.add("dashdown-map-layout");

  const header = document.createElement("div");
  header.className = "dashdown-map-header";
  if (config.title) {
    const title = document.createElement("h3");
    title.className = "dashdown-map-card-title";
    title.textContent = config.title;
    header.appendChild(title);
  }
  const controls = document.createElement("div");
  controls.className = "dashdown-map-controls";
  header.appendChild(controls);

  const region = document.createElement("div");
  region.className = "dashdown-map-region";
  const footer = document.createElement("div");
  footer.className = "dashdown-map-footer";
  body.append(header, region, footer);
  return { body, header, controls, region, footer };
}

/** Replace a map card's body with the shared error card (chart.js parity). */
export function showMapError(el, error) {
  const body = el.querySelector(".card-body") || el;
  body.innerHTML = `<div class="alert alert-error">${escapeHtml(
    (error && error.message) || "Failed to load map data"
  )}</div>`;
}

/** Centered muted message for a zero-row result (chart empty-state parity). */
export function showMapEmpty(region, message) {
  const empty = document.createElement("div");
  empty.className = "dashdown-map-empty";
  empty.textContent = message || "No data available";
  region.appendChild(empty);
}

/** The page's query-def map (Alpine store, seeded by the pipeline). */
export function queryDefs() {
  return (window.Alpine && Alpine.store("queryDefs")) || {};
}

/**
 * The single reactive render path every data component uses: an Alpine effect
 * over the filters store that runs once immediately and again whenever any
 * filter changes (see counter.js — same subscription, no custom events).
 */
export function subscribeFilters(render) {
  const subscribe = () => {
    Alpine.effect(() => {
      const filters = { ...(Alpine.store("filters") || {}) };
      render(filters);
    });
  };
  if (window.Alpine) {
    subscribe();
  } else {
    document.addEventListener("alpine:init", subscribe);
  }
}

/**
 * Rows for the map's frame: with a `year` column and a `year_value` config the
 * matching year, with a `year` column and no `year_value` the latest year,
 * else every row. Returns `{rows, year}` (`year` null when not sliced).
 */
export function sliceYear(records, config) {
  const yearCol = config.year;
  if (!yearCol || !records.length || !(yearCol in records[0])) {
    return { rows: records, year: null };
  }
  let year = config.year_value != null ? String(config.year_value) : null;
  if (year === null) {
    let latest = null;
    records.forEach((r) => {
      const v = r[yearCol];
      if (v == null) return;
      if (latest === null || Number(v) > Number(latest)) latest = v;
    });
    if (latest === null) return { rows: records, year: null };
    year = String(latest);
  }
  return { rows: records.filter((r) => String(r[yearCol]) === year), year };
}

/**
 * A segmented metric toggle (the component's own DOM — deliberately NOT a
 * Dashdown filter control, so it works in static exports with every frame
 * already baked into the one query). No-op (returns null) for one metric.
 */
export function metricToggle(metrics, onChange) {
  if (!metrics || metrics.length < 2) return null;
  const wrap = document.createElement("div");
  wrap.className = "dashdown-map-toggle";
  wrap.setAttribute("role", "group");
  const buttons = metrics.map((m, i) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "dashdown-map-toggle-btn";
    btn.textContent = m.label || m.column;
    if (i === 0) btn.classList.add("is-active");
    btn.addEventListener("click", () => {
      buttons.forEach((b) => b.classList.toggle("is-active", b === btn));
      onChange(i);
    });
    wrap.appendChild(btn);
    return btn;
  });
  return wrap;
}
