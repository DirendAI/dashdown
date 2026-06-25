/**
 * Tailwind config for Dashdown's pre-built, self-hosted stylesheet.
 *
 * Replaces the in-browser `cdn.tailwindcss.com` compiler and the DaisyUI CDN
 * CSS with a single static file (`dashdown/static/vendor/tailwind.css`) built
 * at *our* release time. The JIT only emits classes it can see, so:
 *   - `content` scans every framework file that emits class names (templates,
 *     Python component render() f-strings, and the static JS).
 *   - `safelist` covers classes composed at runtime / commonly authored by
 *     users that the scan can't prove are present.
 *   - daisyUI ships *all* its themes (parity with the old `full.css`), so
 *     `theme: <name>` in a user's dashdown.yaml keeps working.
 *
 * Exotic per-project utility classes beyond this set are handled by the
 * documented `static/custom.css` escape hatch, not by widening the safelist.
 */
import daisyui from "daisyui";

/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "../dashdown/templates/**/*.html",
    "../dashdown/components/**/*.py",
    "../dashdown/render/**/*.py",
    "../dashdown/server.py",
    "../dashdown/static/**/*.js",
  ],
  safelist: [
    // Semantic-color utilities are frequently composed from data at runtime
    // (counter color map, delta badge, alert states) and authored by users.
    {
      pattern:
        /^(text|bg|border|badge|btn|alert)-(primary|secondary|accent|neutral|info|success|warning|error|ghost|outline)$/,
    },
    // ...with the low-opacity tints the framework uses for soft fills/text.
    {
      pattern:
        /^(text|bg|border)-(primary|secondary|accent|neutral|info|success|warning|error|base-content)\/(10|20|40|50|60|70|80)$/,
    },
    // base-* surface tokens used across cards/skeletons.
    { pattern: /^(bg|text|border)-base-(100|200|300|content)$/ },
  ],
  theme: { extend: {} },
  plugins: [daisyui],
  daisyui: {
    // All built-in themes (matches the old daisyui dist/full.css), so any
    // `theme: <name>` configured in a project still resolves.
    themes: true,
    darkTheme: "dark",
    logs: false,
  },
};
