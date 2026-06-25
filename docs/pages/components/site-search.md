---
title: SiteSearch
sidebar_label: SiteSearch
sidebar_position: 13
icon: "\U0001F50E"
---

# SiteSearch

Full-text search across **every page** of the project, ranked client-side. Unlike
the [Search](/components/search) *filter*, it searches a static index of all pages
(not a query), so it survives static builds. Dashdown already puts one in the app
header and the mobile menu — add `<SiteSearch>` in a page for an extra in-context
box.

```markdown
<SiteSearch placeholder="Search the docs…" max_results="8" />
```

<SiteSearch placeholder="Search the docs…" />

| Attribute     | Default                 | Purpose                  |
| ------------- | ----------------------- | ------------------------ |
| `placeholder` | `Search documentation…` | Input placeholder.       |
| `label`       | `Search`                | Accessible label.        |
| `max_results` | `8`                     | How many results to show.|

For the full design — index, ranking, static vs live — see the
[Full-text search](/search) page.
