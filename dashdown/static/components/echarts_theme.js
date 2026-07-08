// Dashdown ECharts Theme Sync
// Synchronizes ECharts theme with DaisyUI theme (light/dark)

"use strict";

// Default categorical palettes for projects with no `branding.palette`. The
// dark variants are lighter/less-saturated (Tailwind 400 vs 500) so series stay
// legible on the dark canvas. The first slot is the brand color: it's a
// placeholder here — currentDefaultPalette() swaps in the live DaisyUI `--p`
// token so single-series charts follow the project's primary color.
const LIGHT_PALETTE = [
  "#6d28d9", "#14b8a6", "#f59e0b", "#f43f5e",
  "#3b82f6", "#d946ef", "#84cc16", "#f97316",
];
const DARK_PALETTE = [
  "#a78bfa", "#2dd4bf", "#fbbf24", "#fb7185",
  "#60a5fa", "#e879f9", "#a3e635", "#fb923c",
];

/**
 * ECharts dark theme configuration
 */
const ECHARTS_DARK_THEME = {
  color: DARK_PALETTE,
  // Transparent canvas: the card's own bg-base-100 shows through, so chart
  // backgrounds can never drift from the DaisyUI surface color.
  backgroundColor: "transparent",
  textStyle: {
    color: "#e2e8f0",
  },
  title: {
    textStyle: {
      color: "#f1f5f9",
    },
    subtextStyle: {
      color: "#94a3b8",
    },
  },
  legend: {
    textStyle: {
      color: "#cbd5e1",
    },
  },
  tooltip: {
    backgroundColor: "#1e293b",
    textStyle: {
      color: "#f1f5f9",
    },
    borderColor: "#334155",
    borderWidth: 1,
    // Rounded card + soft shadow + breathing room, mirroring the dark theme's
    // --dashdown-shadow-hover token so tooltips read as part of the surface set.
    borderRadius: 10,
    padding: [8, 12],
    extraCssText:
      "box-shadow: 0 10px 28px -6px rgba(0,0,0,0.55), 0 4px 10px -4px rgba(0,0,0,0.4);",
  },
  grid: {
    borderColor: "#334155",
  },
  // Axis tones from the dark mockup: slate-700 lines, slate-400 labels,
  // slate-800 gridlines.
  categoryAxis: {
    axisLine: {
      lineStyle: {
        color: "#334155",
      },
    },
    axisLabel: {
      textStyle: {
        color: "#94a3b8",
      },
    },
    splitLine: {
      lineStyle: {
        color: ["#1e293b"],
      },
    },
  },
  valueAxis: {
    axisLine: {
      lineStyle: {
        color: "#334155",
      },
    },
    axisLabel: {
      textStyle: {
        color: "#94a3b8",
      },
    },
    splitLine: {
      lineStyle: {
        color: ["#1e293b"],
      },
    },
  },
  line: {
    itemStyle: {
      borderWidth: 1,
    },
    lineStyle: {
      width: 2.5,
    },
    symbolSize: 6,
  },
  bar: {
    itemStyle: {
      borderRadius: [6, 6, 0, 0],
    },
  },
  pie: {
    itemStyle: {
      borderWidth: 1,
      borderColor: "#0f172a",
    },
  },
};

/**
 * ECharts light theme configuration (default)
 */
const ECHARTS_LIGHT_THEME = {
  color: LIGHT_PALETTE,
  backgroundColor: "transparent",
  textStyle: {
    color: "#1e293b",
  },
  title: {
    textStyle: {
      color: "#0f172a",
    },
    subtextStyle: {
      color: "#64748b",
    },
  },
  legend: {
    textStyle: {
      color: "#475569",
    },
  },
  tooltip: {
    backgroundColor: "#ffffff",
    textStyle: {
      color: "#0f172a",
    },
    borderColor: "#e2e8f0",
    borderWidth: 1,
    // Rounded card + soft slate-tinted shadow + breathing room, mirroring the
    // light theme's --dashdown-shadow-hover token.
    borderRadius: 10,
    padding: [8, 12],
    extraCssText:
      "box-shadow: 0 8px 24px -6px rgba(15,23,42,0.16), 0 3px 8px -4px rgba(15,23,42,0.10);",
  },
  grid: {
    borderColor: "#e2e8f0",
  },
  // Axis tones from the light mockup: slate-300 lines, slate-500 labels,
  // slate-100 gridlines.
  categoryAxis: {
    axisLine: {
      lineStyle: {
        color: "#cbd5e1",
      },
    },
    axisLabel: {
      textStyle: {
        color: "#64748b",
      },
    },
    splitLine: {
      lineStyle: {
        color: ["#f1f5f9"],
      },
    },
  },
  valueAxis: {
    axisLine: {
      lineStyle: {
        color: "#cbd5e1",
      },
    },
    axisLabel: {
      textStyle: {
        color: "#64748b",
      },
    },
    splitLine: {
      lineStyle: {
        color: ["#f1f5f9"],
      },
    },
  },
  line: {
    itemStyle: {
      borderWidth: 1,
    },
    lineStyle: {
      width: 2.5,
    },
    symbolSize: 6,
  },
  bar: {
    itemStyle: {
      borderRadius: [6, 6, 0, 0],
    },
  },
};

/**
 * Register both ECharts themes so they can be passed by name to echarts.init().
 */
export function registerEChartsThemes() {
  if (typeof echarts !== "undefined") {
    echarts.registerTheme("dashdown-dark", ECHARTS_DARK_THEME);
    echarts.registerTheme("dashdown-light", ECHARTS_LIGHT_THEME);
  }
}

/**
 * The registered ECharts theme name matching the current DaisyUI theme.
 * Pass this to `echarts.init(el, currentEChartsTheme())` so chart text/axes/
 * legends are readable in both light and dark mode.
 * @returns {"dashdown-dark" | "dashdown-light"}
 */
export function currentEChartsTheme() {
  const theme = document.documentElement.getAttribute("data-theme") || "light";
  return theme === "dark" ? "dashdown-dark" : "dashdown-light";
}

/**
 * Resolve the live DaisyUI primary token (`--p`, an oklch triple) to a concrete
 * rgb()/rgba() string. We paint one pixel and read it back so the result is
 * always sRGB — browsers may serialize getComputedStyle/canvas fillStyle as
 * oklch(), which zrender (ECharts' renderer) can't manipulate for emphasis or
 * gradients.
 * @returns {string | null} null if the token can't be resolved.
 */
export function resolvePrimaryColor() {
  return resolveCssColor("oklch(var(--p) / 1)");
}

/** Resolve any CSS color expression (vars, oklch, …) to an sRGB string zrender
 * can use — same probe-and-paint trick as resolvePrimaryColor. */
export function resolveCssColor(cssColor) {
  try {
    const probe = document.createElement("span");
    probe.style.color = cssColor;
    probe.style.display = "none";
    document.body.appendChild(probe);
    const computed = getComputedStyle(probe).color;
    probe.remove();
    if (!computed) return null;
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 1;
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = computed;
    ctx.fillRect(0, 0, 1, 1);
    const [r, g, b, a] = ctx.getImageData(0, 0, 1, 1).data;
    return a === 255
      ? `rgb(${r}, ${g}, ${b})`
      : `rgba(${r}, ${g}, ${b}, ${(a / 255).toFixed(3)})`;
  } catch (err) {
    return null;
  }
}

/**
 * The translucent card wash behind overlaid map chrome. The SVG geo maps get
 * it from CSS (`oklch(var(--b1) / 0.72)`); MapChart draws its title/legend on
 * canvas, so it needs the same wash as a resolved rgba.
 * @returns {string | null}
 */
export function currentSurfaceWash() {
  const c = resolveCssColor("oklch(var(--b1) / 1)");
  const m = c && c.match(/(\d+),\s*(\d+),\s*(\d+)/);
  return m ? `rgba(${m[1]}, ${m[2]}, ${m[3]}, 0.72)` : null;
}

/**
 * The default categorical palette for the active theme, with the first series
 * color following the live DaisyUI `--p` primary token (so a single-series
 * chart picks up the project's brand color). Used by chart.js when a project
 * sets no `branding.palette` and a chart sets no `color=` attr.
 * @returns {string[]}
 */
export function currentDefaultPalette() {
  const base = currentEChartsTheme() === "dashdown-dark" ? DARK_PALETTE : LIGHT_PALETTE;
  const primary = resolvePrimaryColor();
  return primary ? [primary, ...base.slice(1)] : [...base];
}

/**
 * Title/subtitle text colors for the active theme, matching the registered
 * ECharts themes' `title.textStyle`/`subtextStyle`. Used for canvas text we
 * draw ourselves (e.g. a donut's center total via `graphic`), which doesn't
 * inherit the theme the way `title`/`legend` components do.
 * @returns {{ heading: string, muted: string }}
 */
export function currentTextColors() {
  return currentEChartsTheme() === "dashdown-dark"
    ? { heading: "#f1f5f9", muted: "#94a3b8" }
    : { heading: "#0f172a", muted: "#64748b" };
}

// Listeners notified (with the new theme name) whenever data-theme changes.
const themeChangeListeners = [];

/**
 * Subscribe to DaisyUI theme changes. ECharts applies a theme only at init
 * time, so live charts must dispose + re-init on toggle — that is what
 * chart.js registers here. Returns an unsubscribe function.
 * @param {(theme: string) => void} callback
 */
export function onThemeChange(callback) {
  themeChangeListeners.push(callback);
  return () => {
    const i = themeChangeListeners.indexOf(callback);
    if (i !== -1) themeChangeListeners.splice(i, 1);
  };
}

/**
 * Watch <html data-theme> and notify subscribers. Single observer for the
 * whole app — components subscribe via onThemeChange() rather than adding
 * their own observers.
 */
export function watchThemeChanges() {
  let last = currentEChartsTheme();
  const observer = new MutationObserver(() => {
    const next = currentEChartsTheme();
    if (next === last) return; // ignore unrelated attribute mutations
    last = next;
    themeChangeListeners.forEach((cb) => {
      try {
        cb(next);
      } catch (err) {
        console.error("Theme change listener failed:", err);
      }
    });
  });
  observer.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["data-theme"],
  });
}

// Register themes and start watching when this module loads.
if (typeof echarts !== "undefined") {
  registerEChartsThemes();
  watchThemeChanges();
}
