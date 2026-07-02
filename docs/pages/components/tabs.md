---
title: Tabs
sidebar_label: Tabs
sidebar_position: 7
icon: "\U0001F5C2"
---

# Tabs

Section a page into switchable panels — "Overview · By region · Raw data" views
that share the same page, filters, and queries. Wrap each section in a
`<Tab title="…">` inside a `<Tabs>` container; one panel shows at a time behind
a tab bar.

```markdown
<Tabs name="view">
  <Tab title="Trend">
    <LineChart data={downloads_by_month} x="month" y="downloads" title="Downloads over time" />
  </Tab>
  <Tab title="By channel">
    <BarChart data={channel_totals} x="channel" y="downloads" title="Downloads by channel" />
  </Tab>
</Tabs>
```

<Tabs name="view">
  <Tab title="Trend">
    <LineChart data={downloads_by_month} x="month" y="downloads" title="Downloads over time" />
  </Tab>
  <Tab title="By channel">
    <BarChart data={channel_totals} x="channel" y="downloads" title="Downloads by channel" />
  </Tab>
</Tabs>

Tabs are pure **layout** — switching one shows different authored content but
never changes any query. To let the *reader's choice* filter the data instead,
reach for a [ButtonGroup](/components/button-group): it looks similar but writes
a filter value your SQL reads as `${name}`.

## Attributes

### `<Tabs>`

| Attribute  | Purpose                                                                                          |
| ---------- | ------------------------------------------------------------------------------------------------ |
| `name`     | Sync the active tab to the URL as `?name=<title-slug>` — deep-linkable, back/forward-aware. Omit for no URL sync. |
| `default`  | Title of the tab active on first load (a URL param wins). Defaults to the first tab.             |
| `url_sync` | Set `false` to keep a named Tabs out of the URL. Default `true`.                                  |
| `label`    | Accessible label for the tab bar (default `"Tabs"`).                                              |
| `col-span` / `span` | Columns to span when the Tabs sits inside a [Grid](/components/grid).                    |

### `<Tab>`

| Attribute | Purpose                              |
| --------- | ------------------------------------ |
| `title`   | Required. The tab's label in the bar. |

## Behavior notes

- A panel can hold anything a page can — markdown, charts, tables, nested
  `<Tabs>` — and every panel's queries load normally whether or not it's visible.
- In a **PDF/print export** the tab bar is hidden and every panel is printed
  stacked, each introduced by its title, so hidden content still makes it into
  the document. Static builds work unchanged.
- The tab bar is keyboard-accessible (arrow keys, Home/End) and follows the
  WAI-ARIA tabs pattern.
