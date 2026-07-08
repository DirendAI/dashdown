// Dashdown ChoroplethTime Component
// Animated world choropleth: one metric shaded per country (ISO numeric join),
// stepped across years by a play/scrub control, with an optional metric toggle.
// All frames come from the ONE query result — the controls are the component's
// own DOM, never Dashdown filters — so the animation is fully interactive in
// static exports too.

"use strict";

import { fetchQueryData, recordsOf, queryUsesFilters } from "../core.js";
import { showLoading, hideLoading } from "../loading.js";
import { mountFilterBadge } from "./filter_badge.js";
import {
  colorAt,
  createMapSvg,
  createTooltip,
  enableMapZoom,
  escapeHtml,
  featurePath,
  fmtValue,
  gradientLegend,
  loadGeometry,
  makeScale,
  mapShell,
  metricToggle,
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
 * Initialize a ChoroplethTime component
 * @param {HTMLElement} el - Element with data-async-component="choropleth-time"
 */
export function initChoroplethTime(el) {
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
  // A re-render (filter change) replaces the whole card body — stop any
  // animation still driving the old DOM.
  if (el._dashdownMapTimer) {
    clearInterval(el._dashdownMapTimer);
    el._dashdownMapTimer = null;
  }
  const shell = mapShell(el, config);
  if (!records.length) {
    showMapEmpty(shell.region, config.empty_message);
    return;
  }

  const metrics = config.metrics || [];
  const yearCol = config.year;
  const years = [
    ...new Set(
      records.filter((r) => r[yearCol] != null).map((r) => String(r[yearCol]))
    ),
  ].sort((a, b) => Number(a) - Number(b));
  if (!years.length) {
    showMapEmpty(shell.region, config.empty_message);
    return;
  }

  // year -> country id -> row
  const frames = new Map();
  records.forEach((r) => {
    const id = normalizeId(r[config.id]);
    if (id === null || r[yearCol] == null) return;
    const y = String(r[yearCol]);
    if (!frames.has(y)) frames.set(y, new Map());
    frames.get(y).set(id, r);
  });

  const ramp = resolveScheme(config);
  // One scale per metric across EVERY frame, so colors stay comparable while
  // the years play instead of re-normalizing per frame.
  const scales = metrics.map((m) =>
    makeScale(config.scale, records.map((r) => r[m.column]))
  );

  const state = { metric: 0, year: years.length - 1 };

  const svg = createMapSvg(world.frame);
  shell.region.appendChild(svg);
  enableMapZoom(svg, shell.region, world.frame);
  const tooltip = createTooltip(shell.region);

  const nodes = world.features.map((feature) => {
    const path = svgEl("path", {
      d: featurePath(feature.geometry),
      class: "dashdown-map-country",
      "vector-effect": "non-scaling-stroke",
    });
    svg.appendChild(path);
    path.addEventListener("mousemove", (e) => {
      const metric = metrics[state.metric];
      const rows = frames.get(years[state.year]);
      const row = rows && feature._dashdownId !== null ? rows.get(feature._dashdownId) : null;
      const v = row ? Number(row[metric.column]) : NaN;
      const name = (feature.properties && feature.properties.name) || feature._dashdownId || "";
      tooltip.show(
        `<strong>${escapeHtml(name)}</strong><br>` +
          `${escapeHtml(metric.label)} (${escapeHtml(years[state.year])}): ` +
          `${isFinite(v) ? fmtValue(v, metric.unit) : "–"}`,
        e
      );
    });
    path.addEventListener("mouseleave", tooltip.hide);
    return { path, feature };
  });

  // Legend overlays the map; the timeline is the one row-like control that
  // stays a footer (a scrub slider over the map would fight pan/zoom).
  const legendHost = document.createElement("div");
  legendHost.className = "dashdown-map-overlay-legend";
  shell.region.appendChild(legendHost);

  const timeline = document.createElement("div");
  timeline.className = "dashdown-map-timeline";
  const playBtn = document.createElement("button");
  playBtn.type = "button";
  playBtn.className = "dashdown-map-play";
  const slider = document.createElement("input");
  slider.type = "range";
  slider.className = "dashdown-map-scrub";
  slider.min = "0";
  slider.max = String(years.length - 1);
  slider.step = "1";
  const yearLabel = document.createElement("span");
  yearLabel.className = "dashdown-map-year";
  timeline.append(playBtn, slider, yearLabel);
  if (years.length > 1) shell.footer.appendChild(timeline);

  function updateLegend() {
    const metric = metrics[state.metric];
    const scale = scales[state.metric];
    legendHost.textContent = "";
    legendHost.appendChild(
      gradientLegend(
        ramp,
        fmtValue(scale.min, metric.unit),
        fmtValue(scale.max, metric.unit)
      )
    );
  }

  function update() {
    const metric = metrics[state.metric];
    const scale = scales[state.metric];
    const rows = frames.get(years[state.year]) || new Map();
    nodes.forEach(({ path, feature }) => {
      const row = feature._dashdownId !== null ? rows.get(feature._dashdownId) : null;
      const v = row ? Number(row[metric.column]) : NaN;
      if (isFinite(v)) {
        path.style.fill = colorAt(ramp, scale.t(v));
        path.classList.remove("is-nodata");
      } else {
        path.style.fill = "";
        path.classList.add("is-nodata");
      }
    });
    yearLabel.textContent = years[state.year];
    slider.value = String(state.year);
  }

  function stop() {
    if (el._dashdownMapTimer) {
      clearInterval(el._dashdownMapTimer);
      el._dashdownMapTimer = null;
    }
    playBtn.textContent = "▶";
    playBtn.setAttribute("aria-label", "Play");
  }

  function play() {
    playBtn.textContent = "❚❚";
    playBtn.setAttribute("aria-label", "Pause");
    if (state.year >= years.length - 1) state.year = 0; // replay from the start
    update();
    el._dashdownMapTimer = setInterval(() => {
      if (state.year >= years.length - 1) {
        stop();
        return;
      }
      state.year++;
      update();
    }, config.interval || 700);
  }

  playBtn.addEventListener("click", () => {
    if (el._dashdownMapTimer) stop();
    else play();
  });
  slider.addEventListener("input", () => {
    stop();
    state.year = Number(slider.value);
    update();
  });

  const toggle = metricToggle(metrics, (i) => {
    state.metric = i;
    update();
    updateLegend();
  });
  if (toggle) shell.controls.appendChild(toggle);

  stop();
  updateLegend();
  update();
}

// Fullscreen: the modal re-draws this map type via the shared registry.
registerMapRenderer("choropleth-time", draw);

/**
 * Initialize all ChoroplethTime components on the page
 */
export function initAllChoroplethTimes() {
  document
    .querySelectorAll('[data-async-component="choropleth-time"]')
    .forEach((el) => initChoroplethTime(el));
}
