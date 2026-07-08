// Dashdown DotDensityMap Component
// One dot per fixed quantity of a metric, scattered inside each country's
// borders by a SEEDED PRNG — the same data draws the identical dot pattern on
// every load and in static exports (determinism is part of the contract).
// Pure SVG over the bundled ISO-keyed geometry.

"use strict";

import { fetchQueryData, recordsOf, queryUsesFilters } from "../core.js";
import { showLoading, hideLoading } from "../loading.js";
import { mountFilterBadge } from "./filter_badge.js";
import {
  createMapSvg,
  createTooltip,
  enableMapZoom,
  escapeHtml,
  featurePath,
  fmtValue,
  hashSeed,
  loadGeometry,
  MAP_W,
  mapShell,
  metricToggle,
  mulberry32,
  normalizeId,
  project,
  queryDefs,
  registerMapRenderer,
  resolveScheme,
  samplePoints,
  showMapEmpty,
  showMapError,
  sliceYear,
  subscribeFilters,
  svgEl,
} from "./_geo.js";

/**
 * Initialize a DotDensityMap component
 * @param {HTMLElement} el - Element with data-async-component="dot-density-map"
 */
export function initDotDensityMap(el) {
  const config = JSON.parse(el.dataset.config);
  const queryName = config.query_name;

  function render(filters = {}) {
    if (!queryUsesFilters(queryName, filters, queryDefs())) return;
    showLoading(el);
    Promise.all([loadGeometry(config), fetchQueryData(queryName, {}, filters)])
      .then(([world, data]) => {
        hideLoading(el);
        draw(el, world, recordsOf(data), config);
      })
      .catch((err) => {
        hideLoading(el);
        showMapError(el, err);
      });
  }

  subscribeFilters(render);
  mountFilterBadge(el, queryName);
}

/** Round a raw per-dot quantity up to a "nice" 1/2/5×10ⁿ value. */
function niceQuantity(x) {
  if (!isFinite(x) || x <= 0) return 1;
  const pow = Math.pow(10, Math.floor(Math.log10(x)));
  for (const f of [1, 2, 5, 10]) {
    if (f * pow >= x) return f * pow;
  }
  return 10 * pow;
}

function draw(el, world, records, config) {
  const shell = mapShell(el, config);
  if (!records.length) {
    showMapEmpty(shell.region, config.empty_message);
    return;
  }

  const { rows, year } = sliceYear(records, config);
  const byId = new Map();
  rows.forEach((r) => {
    const id = normalizeId(r[config.id]);
    if (id !== null) byId.set(id, r);
  });
  if (!byId.size) {
    showMapEmpty(shell.region, config.empty_message);
    return;
  }

  const metrics = config.metrics || [];
  // dot_radius means "size on the card" — card-relative like BubbleMap's
  // max_radius, so an auto-fit custom frame scales the dots with it.
  const dotRadius = (config.dot_radius || 1.2) * (world.frame.w / MAP_W);
  const maxDots = config.max_dots || 20000;
  const dotColor = resolveScheme(config)[3];

  const svg = createMapSvg(world.frame);
  shell.region.appendChild(svg);
  enableMapZoom(svg, shell.region, world.frame);
  const tooltip = createTooltip(shell.region);

  const state = { metric: 0 };

  world.features.forEach((feature) => {
    const path = svgEl("path", {
      d: featurePath(feature.geometry),
      class: "dashdown-map-country is-basemap",
      "vector-effect": "non-scaling-stroke",
    });
    path.addEventListener("mousemove", (e) => {
      const metric = metrics[state.metric];
      const row = feature._dashdownId !== null ? byId.get(feature._dashdownId) : null;
      const v = row ? Number(row[metric.column]) : NaN;
      const name =
        (feature.properties && feature.properties.name) || feature._dashdownId || "";
      const suffix = year ? ` (${escapeHtml(year)})` : "";
      tooltip.show(
        `<strong>${escapeHtml(name)}</strong>${suffix}<br>` +
          `${escapeHtml(metric.label)}: ${isFinite(v) ? fmtValue(v, metric.unit) : "–"}`,
        e
      );
    });
    path.addEventListener("mouseleave", tooltip.hide);
    svg.appendChild(path);
  });

  const dotLayer = svgEl("g", { class: "dashdown-map-dots" });
  // The dot layer must not swallow hovers meant for the countries underneath.
  dotLayer.setAttribute("pointer-events", "none");
  svg.appendChild(dotLayer);

  const legendHost = document.createElement("div");
  legendHost.className = "dashdown-map-overlay-legend";
  shell.region.appendChild(legendHost);

  // Dots per metric are deterministic, so cache the built layer per metric and
  // just swap on toggle.
  const layerCache = new Map();

  function buildLayer(metricIndex) {
    const metric = metrics[metricIndex];
    const entries = [];
    let total = 0;
    byId.forEach((row, id) => {
      const feature = world.byId[id];
      if (!feature) return;
      const v = Number(row[metric.column]);
      if (!isFinite(v) || v <= 0) return;
      entries.push({ feature, v, id });
      total += v;
    });

    let perDot = metric.per_dot;
    if (!perDot || perDot <= 0) perDot = niceQuantity(total / maxDots);
    // An explicit per_dot that would exceed the dot budget is scaled up to a
    // nice value that fits — never silently dropped dots.
    if (total / perDot > maxDots) perDot = niceQuantity(total / maxDots);

    const group = svgEl("g", {});
    entries.forEach(({ feature, v, id }) => {
      const count = Math.round(v / perDot);
      if (count <= 0) return;
      // Seeded per country + metric: dot placement is stable across loads,
      // filters, exports — and independent of data row order.
      const rng = mulberry32(hashSeed(`${id}|${metric.column}`));
      samplePoints(feature.geometry, count, rng).forEach(([lon, lat]) => {
        const [x, y] = project(lon, lat);
        const dot = svgEl("circle", {
          cx: x.toFixed(1),
          cy: y.toFixed(1),
          r: dotRadius,
          class: "dashdown-map-dot",
        });
        dot.style.fill = dotColor;
        group.appendChild(dot);
      });
    });
    return { group, perDot };
  }

  function update(metricIndex) {
    state.metric = metricIndex;
    if (!layerCache.has(metricIndex)) {
      layerCache.set(metricIndex, buildLayer(metricIndex));
    }
    const { group, perDot } = layerCache.get(metricIndex);
    dotLayer.textContent = "";
    dotLayer.appendChild(group);

    const metric = metrics[metricIndex];
    legendHost.textContent = "";
    const legend = document.createElement("div");
    legend.className = "dashdown-map-legend";
    const swatch = svgEl("svg", {
      width: 10,
      height: 10,
      viewBox: "0 0 10 10",
      class: "dashdown-map-legend-swatch",
    });
    const dot = svgEl("circle", { cx: 5, cy: 5, r: 3, class: "dashdown-map-dot" });
    dot.style.fill = dotColor;
    swatch.appendChild(dot);
    const label = document.createElement("span");
    label.textContent = `1 dot = ${fmtValue(perDot, metric.unit)}`;
    legend.append(swatch, label);
    legendHost.appendChild(legend);
  }

  const toggle = metricToggle(metrics, update);
  if (toggle) shell.controls.appendChild(toggle);
  update(0);
}

// Fullscreen: the modal re-draws this map type via the shared registry.
registerMapRenderer("dot-density-map", draw);

/**
 * Initialize all DotDensityMap components on the page
 */
export function initAllDotDensityMaps() {
  document
    .querySelectorAll('[data-async-component="dot-density-map"]')
    .forEach((el) => initDotDensityMap(el));
}
