---
title: DAX / Fabric
sidebar_label: DAX / Fabric
sidebar_position: 14
---

# DAX connector (Microsoft Fabric / Power BI)

Queries a Microsoft Fabric / Power BI dataset via the REST API with MSAL
authentication. Unlike the SQL connectors, queries are written in **DAX**, not
SQL — and `${param}` substitution still applies (a DAX string literal `"…"` gets
its quotes escaped automatically).

```yaml
# sources.yaml
fabric:
  type: dax
  tenant_id: ${FABRIC_TENANT_ID}
  client_id: ${FABRIC_CLIENT_ID}
  client_secret: ${FABRIC_CLIENT_SECRET}
  workspace_id: ${FABRIC_WORKSPACE_ID}
  dataset_id: ${FABRIC_DATASET_ID}
```

| Key             | Purpose                                  |
| --------------- | ---------------------------------------- |
| `tenant_id`     | Azure AD tenant.                         |
| `client_id` / `client_secret` | Service-principal credentials. |
| `workspace_id`  | Fabric/Power BI workspace.               |
| `dataset_id`    | The dataset to query.                    |

## Writing a DAX query

DAX queries start with `EVALUATE` and return a table. The most useful building
block is `SUMMARIZECOLUMNS`, which groups by one or more columns and projects
your measures alongside them — the table-shaped result a chart needs. Column
names come back **shortened**: `'Date'[Month]` arrives as just `Month`, so that's
the name you reference in `x=`/`y=`.

Define it once in the query library (`queries/revenue_by_month.dax`):

```dax
EVALUATE
SUMMARIZECOLUMNS(
    'Date'[Month],
    "Revenue", [Total Revenue]
)
ORDER BY 'Date'[Month]
```

Then reference it by name on a page and feed it to a chart:

```markdown
<LineChart data={revenue_by_month} x="Month" y="Revenue" />
```

Or inline on a single page, choosing the connector explicitly:

````markdown
:::query name=revenue_by_region connector=fabric
EVALUATE
SUMMARIZECOLUMNS(
    'Store'[Region],
    "Revenue", [Total Revenue]
)
:::

<BarChart data={revenue_by_region} x="Region" y="Revenue" />
````

### Parameters and filtering

`${param}` substitution works the same as the SQL connectors, but filter values
in DAX are **string literals wrapped in double quotes** (`"…"`) — and Dashdown
escapes the value's quotes (`"` → `""`) automatically for that context. A
`<Dropdown>` feeding `${region}` looks like:

```dax
EVALUATE
SUMMARIZECOLUMNS(
    'Date'[Month],
    FILTER(VALUES('Store'[Region]), 'Store'[Region] = "${region}"),
    "Revenue", [Total Revenue]
)
ORDER BY 'Date'[Month]
```

Library queries for this connector live in `queries/*.dax`. **Install:**
`uv add 'dashdown-md[dax]'`.
