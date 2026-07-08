// Dashdown ChoroplethFacets Component
// Small-multiple choropleths: one mini world map per year on a shared color
// scale, so the panels compare honestly. Pure SVG over the bundled geometry —
// static-export safe like every geo map.

"use strict";

import { fetchQueryData, recordsOf, queryUsesFilters } from "../core.js";
import { showLoading, hideLoading } from "../loading.js";
import { mountFilterBadge } from "./filter_badge.js";
import {
  colorAt,
  createMapSvg,
  createTooltip,
  escapeHtml,
  featurePath,
  fmtValue,
  gradientLegend,
  loadGeometry,
  makeScale,
  mapShell,
  normalizeId,
  queryDefs,
  registerMapRenderer,
  resolveScheme,
  showMapEmpty,
  showMapError,
  subscribeFilters,
  svgEl,
} from "./_geo.js";

/**
 * Initialize a ChoroplethFacets component
 * @param {HTMLElement} el - Element with data-async-component="choropleth-facets"
 */
export function initChoroplethFacets(el) {
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
  // Flow header/footer, not overlays: the region is a dense facet grid with
  // no spare corners for floating chrome.
  const shell = mapShell(el, config, { chrome: "header" });
  if (!records.length) {
    showMapEmpty(shell.region, config.empty_message);
    return;
  }

  const yearCol = config.year;
  const valueCol = config.value;
  const allYears = [
    ...new Set(
      records.filter((r) => r[yearCol] != null).map((r) => String(r[yearCol]))
    ),
  ].sort((a, b) => Number(a) - Number(b));
  const years =
    config.years && config.years.length
      ? config.years.map(String).filter((y) => allYears.includes(y))
      : allYears;
  if (!years.length) {
    showMapEmpty(shell.region, config.empty_message);
    return;
  }

  // year -> country id -> value
  const frames = new Map();
  years.forEach((y) => frames.set(y, new Map()));
  const included = [];
  records.forEach((r) => {
    const y = r[yearCol] == null ? null : String(r[yearCol]);
    if (y === null || !frames.has(y)) return;
    const id = normalizeId(r[config.id]);
    if (id === null) return;
    frames.get(y).set(id, r[valueCol]);
    included.push(r[valueCol]);
  });

  const ramp = resolveScheme(config);
  // ONE scale across every shown facet — the whole point of small multiples.
  const scale = makeScale(config.scale, included);

  shell.region.classList.add("dashdown-map-facets");
  const columns = Math.max(1, Math.min(config.columns || 3, years.length));
  shell.region.style.gridTemplateColumns = `repeat(${columns}, minmax(0, 1fr))`;
  const tooltip = createTooltip(shell.region);

  years.forEach((year) => {
    const cell = document.createElement("div");
    cell.className = "dashdown-map-facet";
    const svg = createMapSvg(world.frame);
    const values = frames.get(year);
    world.features.forEach((feature) => {
      const path = svgEl("path", {
        d: featurePath(feature.geometry),
        class: "dashdown-map-country",
      });
      const v =
        feature._dashdownId !== null ? Number(values.get(feature._dashdownId)) : NaN;
      if (isFinite(v)) {
        path.style.fill = colorAt(ramp, scale.t(v));
      } else {
        path.classList.add("is-nodata");
      }
      path.addEventListener("mousemove", (e) => {
        const name =
          (feature.properties && feature.properties.name) || feature._dashdownId || "";
        tooltip.show(
          `<strong>${escapeHtml(name)}</strong><br>` +
            `${escapeHtml(config.label)} (${escapeHtml(year)}): ` +
            `${isFinite(v) ? fmtValue(v, config.unit) : "–"}`,
          e
        );
      });
      path.addEventListener("mouseleave", tooltip.hide);
      svg.appendChild(path);
    });
    const caption = document.createElement("div");
    caption.className = "dashdown-map-facet-caption";
    caption.textContent = year;
    cell.append(svg, caption);
    shell.region.appendChild(cell);
  });

  const legend = gradientLegend(
    ramp,
    fmtValue(scale.min, config.unit),
    fmtValue(scale.max, config.unit)
  );
  if (config.label) {
    const label = document.createElement("span");
    label.className = "dashdown-map-legend-label";
    label.textContent = config.label;
    legend.prepend(label);
  }
  shell.footer.appendChild(legend);
}

// Fullscreen: the modal re-draws the facet grid via the shared registry. The
// panels deliberately stay un-zoomable small multiples — fullscreen IS the
// "see them bigger" affordance here.
registerMapRenderer("choropleth-facets", draw);

/**
 * Initialize all ChoroplethFacets components on the page
 */
export function initAllChoroplethFacets() {
  document
    .querySelectorAll('[data-async-component="choropleth-facets"]')
    .forEach((el) => initChoroplethFacets(el));
}
