// Dashdown BivariateMap Component
// Two-variable choropleth: each country's x and y metrics are classed into
// terciles and colored from a 3×3 bivariate palette, with the classic square
// legend. Pure SVG over the bundled ISO-keyed geometry — static-export safe.

"use strict";

import { fetchQueryData, recordsOf, queryUsesFilters } from "../core.js";
import { showLoading, hideLoading } from "../loading.js";
import { mountFilterBadge } from "./filter_badge.js";
import {
  BIVARIATE_SCHEMES,
  createMapSvg,
  createTooltip,
  enableMapZoom,
  escapeHtml,
  featurePath,
  fmtValue,
  loadGeometry,
  mapShell,
  normalizeId,
  queryDefs,
  registerMapRenderer,
  showMapEmpty,
  showMapError,
  sliceYear,
  subscribeFilters,
  svgEl,
  tercileClass,
  terciles,
} from "./_geo.js";

/**
 * Initialize a BivariateMap component
 * @param {HTMLElement} el - Element with data-async-component="bivariate-map"
 */
export function initBivariateMap(el) {
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

  const xs = [];
  const ys = [];
  byId.forEach((r) => {
    const x = Number(r[config.x]);
    const y = Number(r[config.y]);
    if (isFinite(x) && isFinite(y)) {
      xs.push(x);
      ys.push(y);
    }
  });
  if (!xs.length) {
    showMapEmpty(shell.region, config.empty_message);
    return;
  }

  const xBreaks = terciles(xs);
  const yBreaks = terciles(ys);
  const palette =
    BIVARIATE_SCHEMES[config.scheme] || BIVARIATE_SCHEMES["blue-purple"];

  const svg = createMapSvg(world.frame);
  shell.region.appendChild(svg);
  enableMapZoom(svg, shell.region, world.frame);
  const tooltip = createTooltip(shell.region);

  world.features.forEach((feature) => {
    const path = svgEl("path", {
      d: featurePath(feature.geometry),
      class: "dashdown-map-country",
      "vector-effect": "non-scaling-stroke",
    });
    const row = feature._dashdownId !== null ? byId.get(feature._dashdownId) : null;
    const x = row ? Number(row[config.x]) : NaN;
    const y = row ? Number(row[config.y]) : NaN;
    if (isFinite(x) && isFinite(y)) {
      const cls = tercileClass(y, yBreaks) * 3 + tercileClass(x, xBreaks);
      path.style.fill = palette[cls];
    } else {
      path.classList.add("is-nodata");
    }
    path.addEventListener("mousemove", (e) => {
      const name =
        (feature.properties && feature.properties.name) || feature._dashdownId || "";
      const suffix = year ? ` (${escapeHtml(year)})` : "";
      tooltip.show(
        `<strong>${escapeHtml(name)}</strong>${suffix}<br>` +
          `${escapeHtml(config.xlabel)}: ${isFinite(x) ? fmtValue(x, config.xunit) : "–"}<br>` +
          `${escapeHtml(config.ylabel)}: ${isFinite(y) ? fmtValue(y, config.yunit) : "–"}`,
        e
      );
    });
    path.addEventListener("mouseleave", tooltip.hide);
    svg.appendChild(path);
  });

  // Overlaid on the map's bottom-left (empty ocean in the world view) instead
  // of a footer row, so the matrix costs the map no height.
  shell.region.appendChild(bivariateLegend(palette, config));
}

/** The 3×3 square legend: high-y rows on top, axis labels along each edge. */
function bivariateLegend(palette, config) {
  const wrap = document.createElement("div");
  wrap.className = "dashdown-map-bilegend";

  const yLabel = document.createElement("span");
  yLabel.className = "dashdown-map-bilegend-y";
  yLabel.textContent = `${config.ylabel} ↑`;

  const grid = document.createElement("div");
  grid.className = "dashdown-map-bilegend-grid";
  for (let row = 2; row >= 0; row--) {
    for (let col = 0; col < 3; col++) {
      const cell = document.createElement("i");
      cell.style.background = palette[row * 3 + col];
      grid.appendChild(cell);
    }
  }

  const xLabel = document.createElement("span");
  xLabel.className = "dashdown-map-bilegend-x";
  xLabel.textContent = `${config.xlabel} →`;

  const cols = document.createElement("div");
  cols.className = "dashdown-map-bilegend-cols";
  cols.append(grid, xLabel);
  wrap.append(yLabel, cols);
  return wrap;
}

// Fullscreen: the modal re-draws this map type via the shared registry.
registerMapRenderer("bivariate-map", draw);

/**
 * Initialize all BivariateMap components on the page
 */
export function initAllBivariateMaps() {
  document
    .querySelectorAll('[data-async-component="bivariate-map"]')
    .forEach((el) => initBivariateMap(el));
}
