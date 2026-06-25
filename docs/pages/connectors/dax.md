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

Library queries for this connector live in `queries/*.dax`. **Install:**
`uv add 'dashdown-md[dax]'`.
