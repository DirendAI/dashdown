<!-- AUTO-GENERATED from docs/pages/ by tooling/gen-agent-docs.py — do not edit. -->
<!-- Topic: theming. Regenerate with: python tooling/gen-agent-docs.py -->

<!-- source: docs/pages/theming.md -->

# Theming & styling

Dashdown ships a polished light/dark theme out of the box, but every project can
override the look — colours, radii, spacing, chrome, typography — with **one
file: `assets/custom.css`**. There is no theme config block and no build step;
drop the file in and it's picked up automatically.

## How it works — the cascade

The page links its stylesheets in this order, so each one can override the one
before it:

1. **`vendor/tailwind.css`** — Tailwind base/utilities + all DaisyUI themes.
2. **`dashdown.css`** — the framework's own styling and design tokens.
3. **`components/<Name>/<file>.css`** — any [colocated component CSS](/extending#data-driven-components).
4. **`assets/custom.css`** — *your* project, linked **last → highest priority**.

Because `custom.css` loads last, its rules win by plain stylesheet order — you
rarely need `!important`. It applies identically on the dev server and in a
`dashdown build` static export.

:::note
**Theme (light/dark) is viewer-controlled, not configured.** The page follows
the visitor's OS preference, the header toggle overrides it (saved per browser).
`custom.css` restyles
*both* themes — scope rules to `[data-theme="light"]` / `[data-theme="dark"]`
when you want them to differ.
:::

## Four things you can reach for

### 1. Design tokens — retune spacing & radii at once

The framework's spacing/radius values are `--dashdown-*` custom properties.
Re-declare them on `:root` to change them everywhere in one place:

```css
:root {
  --dashdown-radius-card: 0.5rem;      /* cards & card-like boxes (default 0.75rem) */
  --dashdown-radius-control: 0.375rem; /* inputs, nav links, small boxes (default 0.5rem) */
  --dashdown-grid-gap: 1.25rem;        /* Grid / KPI-row cell gap (default 1rem) */
  --dashdown-space-section: 2rem;      /* vertical rhythm between blocks (default 1.5rem) */
}
```

The card/control radius tokens are also grafted onto DaisyUI's own
`--rounded-box` / `--rounded-btn`, so DaisyUI surfaces (`card`, `btn`, `select`)
follow the same values.

### 2. Brand colours — re-pin the palette

The shipped light/dark themes pin DaisyUI's colour variables. Re-pin them —
scoped per theme so *other* DaisyUI themes keep their own look — to recolour
accents, surfaces, borders and text. Values are **oklch** (DaisyUI's format):

```css
[data-theme="light"] {
  --p:  59.6% 0.145 163;  /* primary accent — emerald-600 here */
  --pc: 100% 0 0;         /* text drawn on the accent */
  --b1: 100% 0 0;         /* raised surfaces: header / sidebar / cards */
  --b2: 98% 0.003 247;    /* page background, one step down */
  --b3: 93% 0.013 255;    /* hairline borders */
  --bc: 21% 0.04 265;     /* body text */
}
[data-theme="dark"] {
  --p: 72.3% 0.135 163;   /* lighter accent for the dark background */
}
```

| Variable | Role |
| -------- | ---- |
| `--p` / `--pc` | Primary accent and the text drawn on it |
| `--b1` / `--b2` / `--b3` | Raised surfaces / page background / hairline borders |
| `--bc` / `--bc2` / `--bc3` | Body text / secondary / faint text |

:::tip
**Chart series colours** aren't CSS — set them with `branding.palette` in
[`dashdown.yaml`](/configuration#branding). Keep the two in sync so charts match
your reskinned surfaces.
:::

### 3. Chrome & layout — restyle the app shell

The header, sidebar, page header and widget cards all use plain `.dashdown-*`
classes you can target directly:

```css
.dashdown-header { /* the sticky top bar */ }
.dashdown-sidebar { /* the left nav column */ }
.dashdown-brand-title { letter-spacing: -0.02em; font-weight: 800; }
.dashdown-sidenav-link[aria-current="page"] {
  box-shadow: inset 3px 0 0 0 oklch(var(--p));   /* accent the active nav item */
}
.dashdown-chart, .dashdown-table { /* widget cards */ }
.dashdown-page-header { /* the per-page title block */ }
```

The nav column width is its own token: `:root { --sidebar-width: 300px; }`.

:::note
Hand-written CSS in `custom.css` is plain CSS — it is **not** run through
Tailwind's JIT. So Tailwind utility classes you use in your own page markup
beyond what the framework already ships won't be in the vendored bundle; write
the rule out in `custom.css` instead of relying on an unscanned utility class.
:::

### 4. Prose — markdown typography

Rendered markdown lives under `.dashdown-prose`. Tune headings, links, tables and
spacing there without touching the framework:

```css
.dashdown-prose h2 {
  border-bottom: 1px solid oklch(var(--b3));
  padding-bottom: 0.25rem;
}
.dashdown-prose a { text-underline-offset: 2px; }
```

## A worked example

Drop this complete `assets/custom.css` into a project to exercise all four layers
at once — an emerald reskin with squarer cards, a gradient header bar and an
accented active nav link. Delete any block to drop that override; delete the whole
file to get the stock look. The dev server reloads as you edit it.

```css
/* assets/custom.css — auto-linked LAST, so rules win by order (no !important). */

/* 1) Design tokens — retune spacing/radius for the whole dashboard at once. */
:root {
  --dashdown-radius-card: 0.5rem;     /* default 0.75rem — squarer cards */
  --dashdown-radius-control: 0.375rem;/* default 0.5rem  — squarer inputs/nav */
  --dashdown-grid-gap: 1.25rem;       /* default 1rem    — roomier Grid gap */
  --dashdown-space-section: 2rem;     /* default 1.5rem  — more air between blocks */
}

/* 2) Brand colours — re-pin DaisyUI's oklch vars, scoped to [data-theme] so
      OTHER themes keep their own look. Here: emerald instead of stock indigo. */
[data-theme="light"] {
  --p: 59.6% 0.145 163;     /* emerald-600 accent */
  --pc: 100% 0 0;           /* text on the accent */
}
[data-theme="dark"] {
  --p: 72.3% 0.135 163;     /* emerald-400 accent (lighter for dark bg) */
}

/* 3) Chrome & layout — app chrome uses plain `.dashdown-*` classes. */
.dashdown-header {
  background: linear-gradient(90deg, oklch(var(--b1)) 0%, oklch(var(--b2)) 100%);
  backdrop-filter: saturate(1.1);
}
.dashdown-brand-title {
  letter-spacing: -0.02em;
  font-weight: 800;
}
.dashdown-sidenav-link[aria-current="page"] {
  box-shadow: inset 3px 0 0 0 oklch(var(--p));   /* hairline accent on active link */
}

/* 4) Prose — Markdown body content is under `.dashdown-prose`. */
.dashdown-prose h2 {
  border-bottom: 1px solid oklch(var(--b3));
  padding-bottom: 0.25rem;
}
.dashdown-prose a { text-underline-offset: 2px; }
```

## Shared assets vs. component CSS

- **`assets/custom.css`** — project-wide overrides (this page). Also use
  `assets/` for shared images/logos/fonts, served at `/assets/…`.
- **`components/<Name>/<Name>.css`** — styling that belongs to *one* custom
  component, colocated with its `.py`/`.js` and auto-injected. See
  [Extending](/extending#data-driven-components).
