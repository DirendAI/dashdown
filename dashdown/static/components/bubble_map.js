// Dashdown BubbleMap Component
// Proportional-symbol map: a circle on each country's centroid with area ∝
// value, over a muted basemap, with an optional metric toggle. Pure SVG over
// the bundled ISO-keyed geometry — static-export safe.

"use strict";

import { fetchQueryData, recordsOf, queryUsesFilters } from "../core.js";
import { showLoading, hideLoading } from "../loading.js";
import { mountFilterBadge } from "./filter_badge.js";
import {
  centroid,
  createMapSvg,
  createTooltip,
  escapeHtml,
  featurePath,
  fmtValue,
  loadGeometry,
  mapShell,
  metricToggle,
  normalizeId,
  project,
  queryDefs,
  resolveScheme,
  showMapEmpty,
  showMapError,
  sliceYear,
  subscribeFilters,
  svgEl,
} from "./_geo.js";

/**
 * Initialize a BubbleMap component
 * @param {HTMLElement} el - Element with data-async-component="bubble-map"
 */
export function initBubbleMap(el) {
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
  const maxRadius = config.max_radius || 40;
  // Bubbles read as the series color, not a ramp — take the scheme's base stop.
  const bubbleColor = resolveScheme(config)[3];

  const svg = createMapSvg();
  shell.region.appendChild(svg);
  const tooltip = createTooltip(shell.region);

  // Muted basemap under the symbols.
  world.features.forEach((feature) => {
    svg.appendChild(
      svgEl("path", {
        d: featurePath(feature.geometry),
        class: "dashdown-map-country is-basemap",
      })
    );
  });
  const bubbleLayer = svgEl("g", { class: "dashdown-map-bubbles" });
  svg.appendChild(bubbleLayer);

  const legendHost = document.createElement("div");
  shell.footer.appendChild(legendHost);

  function update(metricIndex) {
    const metric = metrics[metricIndex];
    bubbleLayer.textContent = "";

    // Anchor + value per country present in both the data and the geometry.
    const entries = [];
    byId.forEach((row, id) => {
      const feature = world.byId[id];
      if (!feature) return;
      const v = Number(row[metric.column]);
      if (!isFinite(v) || v <= 0) return;
      const anchor = centroid(feature.geometry);
      if (!anchor) return;
      entries.push({ feature, v, anchor });
    });
    if (!entries.length) return;

    const max = Math.max(...entries.map((e) => e.v));
    const radius = (v) => maxRadius * Math.sqrt(v / max);
    // Big circles first so small ones stay hoverable on top.
    entries.sort((a, b) => b.v - a.v);
    entries.forEach(({ feature, v, anchor }) => {
      const [cx, cy] = project(anchor[0], anchor[1]);
      const circle = svgEl("circle", {
        cx: cx.toFixed(1),
        cy: cy.toFixed(1),
        r: Math.max(1, radius(v)).toFixed(2),
        class: "dashdown-map-bubble",
      });
      circle.style.fill = bubbleColor;
      circle.addEventListener("mousemove", (e) => {
        const name =
          (feature.properties && feature.properties.name) || feature._dashdownId || "";
        const suffix = year ? ` (${escapeHtml(year)})` : "";
        tooltip.show(
          `<strong>${escapeHtml(name)}</strong>${suffix}<br>` +
            `${escapeHtml(metric.label)}: ${fmtValue(v, metric.unit)}`,
          e
        );
      });
      circle.addEventListener("mouseleave", tooltip.hide);
      bubbleLayer.appendChild(circle);
    });

    legendHost.textContent = "";
    legendHost.appendChild(sizeLegend(max, radius, metric, bubbleColor));
  }

  const toggle = metricToggle(metrics, update);
  if (toggle) shell.controls.appendChild(toggle);
  update(0);
}

/** Two reference circles (max and quarter-of-max) with their values. */
function sizeLegend(max, radius, metric, color) {
  const wrap = document.createElement("div");
  wrap.className = "dashdown-map-legend";
  [max, max / 4].forEach((v) => {
    const r = Math.max(2, radius(v));
    const size = Math.ceil(r * 2 + 2);
    const svg = svgEl("svg", {
      width: Math.min(size, 34),
      height: Math.min(size, 34),
      viewBox: `0 0 ${size} ${size}`,
      class: "dashdown-map-legend-swatch",
    });
    const circle = svgEl("circle", {
      cx: size / 2,
      cy: size / 2,
      r,
      class: "dashdown-map-bubble",
    });
    circle.style.fill = color;
    svg.appendChild(circle);
    const label = document.createElement("span");
    label.textContent = fmtValue(v, metric.unit);
    wrap.append(svg, label);
  });
  return wrap;
}

/**
 * Initialize all BubbleMap components on the page
 */
export function initAllBubbleMaps() {
  document
    .querySelectorAll('[data-async-component="bubble-map"]')
    .forEach((el) => initBubbleMap(el));
}
