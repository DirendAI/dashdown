---
title: BigQuery
sidebar_label: BigQuery
sidebar_position: 13
---

# BigQuery connector

Google BigQuery, wrapped in its native client's PEP 249 (DB-API) adapter so it
shares the same base as the other SQL connectors.

```yaml
# sources.yaml
warehouse:
  type: bigquery
  project: my-gcp-project
  location: EU                       # optional
  credentials_file: service-account.json   # path relative to the project
```

| Key                | Purpose                                          |
| ------------------ | ------------------------------------------------ |
| `project`          | GCP project id.                                  |
| `location`         | Dataset location (optional).                     |
| `credentials_file` | Service-account JSON (or `credentials_path`).    |

If `credentials_file` is omitted, Application Default Credentials are used.
**Install:** `uv add 'dashdown-md[bigquery]'`.
