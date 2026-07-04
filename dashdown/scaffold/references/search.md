<!-- AUTO-GENERATED from docs/pages/ by tooling/gen-agent-docs.py — do not edit. -->
<!-- Topic: search. Regenerate with: python tooling/gen-agent-docs.py -->

<!-- source: docs/pages/search.md -->

# Full-text search

`<SiteSearch />` is a built-in component that searches **every page** of the
project. Type in the box above (or press <kbd>/</kbd>) — results rank pages and
their sections, and clicking one deep-links to the matching heading.

## Using it

Drop the tag anywhere a page can render a component:

```markdown
<SiteSearch placeholder="Search the docs…" max_results="8" />
```

| Attribute     | Default               | Effect                          |
| ------------- | --------------------- | ------------------------------- |
| `placeholder` | `Search documentation…` | Input placeholder.            |
| `label`       | `Search`              | Accessible label.               |
| `max_results` | `8`                   | How many results to show.       |

You don't have to add it yourself for site-wide search: Dashdown puts a search box
in the **app header** (centered) and at the top of the **mobile menu** on every
page, so the same control follows you around. Use `<SiteSearch>` in a page when you
want an additional, in-context search box — like this live one:

<SiteSearch placeholder="Search the docs…" />

## Configuring the built-in box

The header / mobile-menu control is **on by default**. Tune or disable it with a
`search:` block in `dashdown.yaml`:

```yaml
search:
  enabled: true            # set false to remove the built-in header/menu box
  placeholder: "Search…"   # input placeholder
  max_results: 8           # results listed in the dropdown
```

Disabling it removes only the built-in chrome control — the `<SiteSearch>`
component and the search-index endpoint stay available, so you can still place
your own box on a page.

## How it works

1. At render time the framework builds a **search index** — one entry per page
   holding its title, section headings (with anchor ids), and plain body text.
   Inline-query SQL is already stripped, so query bodies never enter the index.
2. The index is served live at `GET /_dashdown/api/search-index`, and baked into
   static exports as `_dashdown/search-index.json`.
3. The browser fetches the index once and does **all ranking client-side** —
   there is no server-side search. Title hits weigh heaviest, then headings, then
   body occurrences; every search term must appear for a page to match.

Because the index is plain JSON and the ranking is in the browser, search works
identically on the live server and in a `dashdown build` static export.

:::note
Unlike the `<Search>` *filter* (which drives SQL substitution and is stripped
from static builds), `<SiteSearch>` searches a static snapshot of the docs and is
therefore **kept** in exports.
:::
