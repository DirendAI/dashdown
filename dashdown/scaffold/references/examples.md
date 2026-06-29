<!-- AUTO-GENERATED from docs/pages/ by tooling/gen-agent-docs.py — do not edit. -->
<!-- Topic: examples. Regenerate with: python tooling/gen-agent-docs.py -->

<!-- source: docs/pages/examples.md -->

# Examples

Real, end-to-end dashboards built with Dashdown. Each one is a small public repo
you can clone and run locally — and each is also deployed live (a `dashdown build`
static export hosted on GitHub Pages), so you can click through it before you
install anything.

```bash
git clone https://github.com/DirendAI/dashdown-csv-demo
dashdown serve dashdown-csv-demo   # → http://localhost:8000
```

## CSV demo

The quickest way to see the basics: a dashboard built straight over plain CSV
files with the [`csv` connector](/connectors/csv) — queries, a few charts, and
filters, nothing else to set up.

**[▶ Live demo](https://direndai.github.io/dashdown-csv-demo/)** ·
**[Source](https://github.com/DirendAI/dashdown-csv-demo)**

## Excel demo

The same ideas backed by an Excel workbook via the
[`excel` connector](/connectors/excel) — sheets become tables you query with SQL,
rendered as charts and tables.

**[▶ Live demo](https://direndai.github.io/dashdown-excel-demo/)** ·
**[Source](https://github.com/DirendAI/dashdown-excel-demo)**

## World Cup demo

A richer, multi-page dashboard exploring World Cup data — more pages, more chart
types, and cross-page navigation, showing how a fuller project comes together.

**[▶ Live demo](https://direndai.github.io/dashdown-world-cup-demo/)** ·
**[Source](https://github.com/DirendAI/dashdown-world-cup-demo)**

---

These docs are themselves a Dashdown project too — the most complete worked
example of all. Read the source under
[`docs/`](https://github.com/DirendAI/dashdown/tree/main/docs), or run it with
`dashdown serve docs`.
