/**
 * Vendors Dashdown's frontend dependencies into `dashdown/static/vendor/` and
 * builds the self-hosted Tailwind/DaisyUI stylesheet. Run by `npm run build`.
 *
 * This is *our* release-time step: the outputs are committed and shipped in the
 * wheel, so `pip install dashdown-md` users never need Node. Re-run after bumping
 * any devDependency version in package.json.
 *
 * Outputs:
 *   vendor/tailwind.css      — Tailwind base+components+utilities + all daisyUI themes
 *   vendor/echarts.min.js    — ECharts 5
 *   vendor/alpine.min.js     — Alpine.js 3
 *   vendor/mermaid.min.js    — Mermaid 11 (lazy-loaded for ```mermaid diagrams)
 *   vendor/world.json        — ECharts world GeoJSON (for <MapChart/>)
 *   vendor/fonts/inter.woff2 — Inter variable font (latin)
 */
import { execSync } from "node:child_process";
import {
  copyFileSync,
  existsSync,
  mkdirSync,
  readdirSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const HERE = dirname(fileURLToPath(import.meta.url));
const VENDOR = resolve(HERE, "..", "dashdown", "static", "vendor");
const FONTS = join(VENDOR, "fonts");

// Pinned echarts@4 GeoJSON — echarts 5 ships no maps, and this is the source
// the app referenced from the CDN before vendoring.
const WORLD_JSON_URL =
  "https://cdn.jsdelivr.net/npm/echarts@4/map/json/world.json";

function log(msg) {
  console.log(`[build-assets] ${msg}`);
}

/** Absolute path to a file inside an installed package. */
function pkgFile(...segments) {
  // Resolve the package's own directory via its package.json, then join.
  const [pkg, ...rest] = segments;
  const pkgJson = require.resolve(`${pkg}/package.json`);
  return join(dirname(pkgJson), ...rest);
}

/** Find the Inter latin variable woff2 inside @fontsource-variable/inter. */
function findInterWoff2() {
  const filesDir = pkgFile("@fontsource-variable/inter", "files");
  const candidates = readdirSync(filesDir).filter(
    (f) => f.endsWith(".woff2") && f.includes("latin") && f.includes("wght-normal")
  );
  if (candidates.length === 0) {
    throw new Error(`No latin variable woff2 found in ${filesDir}`);
  }
  // Prefer the plain latin subset over latin-ext if both are present.
  candidates.sort((a, b) => a.length - b.length);
  return join(filesDir, candidates[0]);
}

function copy(src, dest) {
  copyFileSync(src, dest);
  const kb = (statSync(dest).size / 1024).toFixed(0);
  log(`${dest.replace(VENDOR, "vendor")}  (${kb} KB)`);
}

async function fetchWorldJson(dest) {
  log(`fetch ${WORLD_JSON_URL}`);
  const resp = await fetch(WORLD_JSON_URL);
  if (!resp.ok) {
    throw new Error(`world.json fetch failed: HTTP ${resp.status}`);
  }
  const text = await resp.text();
  JSON.parse(text); // validate
  writeFileSync(dest, text);
  const kb = (statSync(dest).size / 1024).toFixed(0);
  log(`vendor/world.json  (${kb} KB)`);
}

async function main() {
  mkdirSync(FONTS, { recursive: true });

  copy(pkgFile("echarts", "dist", "echarts.min.js"), join(VENDOR, "echarts.min.js"));
  copy(pkgFile("alpinejs", "dist", "cdn.min.js"), join(VENDOR, "alpine.min.js"));
  // Self-contained IIFE build that assigns globalThis.mermaid — loadable via a
  // plain <script> tag (no bundler / no ESM chunk-splitting). ~3MB, so the
  // client lazy-loads it only on pages that actually contain a mermaid block.
  copy(pkgFile("mermaid", "dist", "mermaid.min.js"), join(VENDOR, "mermaid.min.js"));
  copy(findInterWoff2(), join(FONTS, "inter.woff2"));
  await fetchWorldJson(join(VENDOR, "world.json"));

  log("building tailwind.css (this scans framework content)…");
  execSync(
    "npx tailwindcss -c tailwind.config.js -i input.css -o ../dashdown/static/vendor/tailwind.css --minify",
    { cwd: HERE, stdio: "inherit" }
  );
  const css = join(VENDOR, "tailwind.css");
  if (!existsSync(css)) throw new Error("tailwind.css was not produced");
  const kb = (statSync(css).size / 1024).toFixed(0);
  log(`vendor/tailwind.css  (${kb} KB)`);
  log("done.");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
