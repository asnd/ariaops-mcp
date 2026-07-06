# AriaOps MCP Server — Requirements Specification

## 1. Overview

A Model Context Protocol (MCP) server that exposes VMware Aria Operations (on-prem) REST API capabilities as MCP tools. This enables AI assistants (Claude, etc.) to query infrastructure health, alerts, metrics, capacity, and reports through a standardized interface, with write operations available only when explicitly enabled.

**Base API:** `https://<vrops-host>/suite-api/api/`
**API Version target:** 8.x (8.12–8.18+, compatible with Aria Operations on-prem)

---

## 2. Constraints

| Constraint          | Value                                    |
|---------------------|------------------------------------------|
| Deployment          | On-prem only (no SaaS/Cloud support)    |
| Access mode         | Read-only by default; write operations opt-in via `ARIAOPS_ENABLE_WRITE_OPERATIONS=true` |
| Language            | Python 3.11+                             |
| MCP SDK             | `mcp` (official Python SDK)              |
| Transport           | `stdio` (testing/local) + `streamable HTTP` (production) |
| Container runtime   | Podman (no Docker)                       |
| TLS                 | Accept self-signed certs (configurable)  |

---

## 3. Authentication & Token Lifecycle

Aria Operations on-prem uses a token-based auth flow:

```
POST /suite-api/api/auth/token/acquire
Body: { "username": "...", "password": "...", "authSource": "..." }
Response: { "token": "...", "validity": 1234567890, "expiresAt": "..." }
```

### Requirements

| ID     | Requirement |
|--------|-------------|
| AUTH-1 | Accept credentials via environment variables: `ARIAOPS_HOST`, `ARIAOPS_USERNAME`, `ARIAOPS_PASSWORD`, `ARIAOPS_AUTH_SOURCE` (default: `local`) |
| AUTH-2 | Acquire token on first API call (lazy init) |
| AUTH-3 | Cache token and track expiry timestamp |
| AUTH-4 | Auto-refresh token when expired or within 5-minute expiry window |
| AUTH-5 | Release token on graceful shutdown via `POST /api/auth/token/release` |
| AUTH-6 | Pass token as HTTP header: `Authorization: vRealizeOpsToken {token}` |
| AUTH-7 | Support `ARIAOPS_VERIFY_SSL` env var (default: `true`); when `false`, skip TLS verification |
| AUTH-8 | For HTTP transport, optionally require OAuth 2.x bearer tokens and validate JWT issuer, audience, expiry, signature, and scopes |
| AUTH-9 | Support Keycloak as the reference OAuth IdP via realm issuer URLs, RS256 JWKS verification, and protected-resource metadata |

---

## 4. MCP Tools — Functional Scope

Read-only tools are always available. Mutating tools are exposed only when `ARIAOPS_ENABLE_WRITE_OPERATIONS=true`. Organized by domain.

### 4.1 Resources

| Tool Name                    | API Endpoint                                              | Description |
|------------------------------|----------------------------------------------------------|-------------|
| `list_resources`             | `GET /api/resources`                                      | List/search resources with pagination. Params: `resourceKind`, `adapterKind`, `name`, `page`, `pageSize` |
| `get_resource`               | `GET /api/resources/{id}`                                 | Get resource details by ID |
| `query_resources`            | `POST /api/resources/query`                               | Advanced resource query with filters |
| `get_resource_properties`    | `GET /api/resources/{id}/properties`                      | Get properties of a resource |
| `get_resource_relationships` | `GET /api/resources/{id}/relationships`                   | Get parent/child relationships |
| `list_resource_kinds`        | `GET /api/adapterkinds/{adapterKindKey}/resourcekinds`    | List resource kinds for an adapter kind |
| `list_adapter_kinds`         | `GET /api/adapterkinds`                                   | List all adapter kinds |
| `list_resource_groups`       | `GET /api/resources/groups`                               | List custom/dynamic groups |
| `get_resource_group_members` | `GET /api/resources/groups/{groupId}/members`             | List members of a resource group |

### 4.2 Alerts

| Tool Name               | API Endpoint                                | Description |
|--------------------------|---------------------------------------------|-------------|
| `list_alerts`            | `GET /api/alerts`                           | List active alerts. Params: `status`, `criticality`, `resourceId`, `page`, `pageSize` |
| `get_alert`              | `GET /api/alerts/{id}`                      | Get alert details |
| `query_alerts`           | `POST /api/alerts/query`                    | Advanced alert query with filters |
| `get_alert_notes`        | `GET /api/alerts/{id}/notes`                | Get notes/comments on an alert |
| `list_alert_definitions` | `GET /api/alertdefinitions`                 | List alert definitions |
| `get_alert_definition`   | `GET /api/alertdefinitions/{id}`            | Get alert definition details |
| `get_contributing_symptoms` | `GET /api/alerts/contributingsymptoms`   | Get symptoms contributing to alerts |

### 4.3 Metrics / Stats

| Tool Name                | API Endpoint                                     | Description |
|--------------------------|--------------------------------------------------|-------------|
| `get_resource_stats`     | `GET /api/resources/{id}/stats`                  | Get stats/metrics for a resource. Params: `statKey`, `begin`, `end`, `rollUpType`, `intervalType` |
| `get_latest_stats`       | `GET /api/resources/{id}/stats/latest`           | Get latest stat values for a resource |
| `query_stats`            | `POST /api/resources/stats/query`                | Bulk stats query across multiple resources |
| `query_latest_stats`     | `POST /api/resources/stats/latest/query`         | Bulk latest stats query |
| `get_stat_keys`          | `GET /api/resources/{id}/statkeys`               | List available stat keys for a resource |
| `get_top_n_stats`        | `GET /api/resources/{id}/stats/topn`             | Get Top-N stats for a resource |
| `list_properties_latest` | `POST /api/resources/properties/latest/query`    | Bulk latest properties query |

### 4.4 Capacity (via Stats + Policies)

Aria Operations doesn't have a dedicated `/api/capacity` endpoint — capacity data is exposed through specific stat keys and resource properties.

| Tool Name                 | API Endpoint / Logic                                        | Description |
|---------------------------|-------------------------------------------------------------|-------------|
| `get_capacity_remaining`  | `GET /api/resources/{id}/stats` with capacity stat keys     | Get remaining capacity (CPU, memory, storage) for a cluster/host. Uses stat keys like `capacity|remainingCapacity_*` |
| `get_capacity_overview`   | Composite: query resources + stats for clusters             | Summarize capacity across clusters — total, used, remaining |
| `list_policies`           | `GET /api/policies`                                         | List policies (which define capacity thresholds) |

**Known capacity-related stat keys:**
- `cpu|capacity_contentionPct`
- `mem|host_usable`, `mem|host_demand`
- `capacity|remainingCapacity`, `capacity|timeRemaining`
- `capacity|badge|capacityRemaining`
- `diskspace|capacity`, `diskspace|used`

### 4.5 Reports

| Tool Name                 | API Endpoint                                        | Description |
|---------------------------|-----------------------------------------------------|-------------|
| `list_report_definitions` | `GET /api/reportdefinitions`                        | List available report templates |
| `get_report_definition`   | `GET /api/reportdefinitions/{id}`                   | Get report definition details |
| `list_reports`            | `GET /api/reports`                                  | List generated reports |
| `get_report`              | `GET /api/reports/{id}`                             | Get report metadata |
| `download_report`         | `GET /api/reports/{id}/download`                    | Download a generated report (PDF/CSV) |
| `list_report_schedules`   | `GET /api/reportdefinitions/{id}/schedules`         | List schedules for a report definition |

### 4.6 Supporting / Discovery

| Tool Name              | API Endpoint                    | Description |
|------------------------|---------------------------------|-------------|
| `get_version`          | `GET /api/versions/current`     | Get Aria Operations version info |
| `list_collectors`      | `GET /api/collectors`           | List data collectors |
| `list_symptoms`        | `GET /api/symptomdefinitions`   | List symptom definitions |
| `list_recommendations` | `GET /api/recommendations`      | List recommendations |
| `list_supermetrics`    | `GET /api/supermetrics`         | List super metrics |

### 4.7 Write Operations (opt-in)

These tools are disabled by default and must only be registered when `ARIAOPS_ENABLE_WRITE_OPERATIONS=true`.

| Tool Name                     | API Endpoint / Logic                                 | Description |
|------------------------------|------------------------------------------------------|-------------|
| `modify_alerts`              | `POST /api/alerts`                                   | Bulk cancel, suspend, or acknowledge alerts |
| `add_alert_note`             | `POST /api/alerts/{id}/notes`                        | Add a note to an alert |
| `delete_alert_note`          | `DELETE /api/alerts/{id}/notes/{noteId}`             | Delete a note from an alert |
| `delete_canceled_alerts`     | `DELETE /api/alerts`                                 | Delete canceled alerts matching filters |
| `mark_resources_maintained`  | `POST /api/resources/maintained`                     | Put resources into maintenance mode |
| `unmark_resources_maintained`| `DELETE /api/resources/maintained`                   | Remove resources from maintenance mode |
| `create_maintenance_schedule`| `POST /api/maintenanceschedules`                     | Create a maintenance schedule |
| `update_maintenance_schedule`| `PUT /api/maintenanceschedules/{id}`                 | Update a maintenance schedule |
| `delete_maintenance_schedule`| `DELETE /api/maintenanceschedules`                   | Delete one or more maintenance schedules |
| `generate_report`            | `POST /api/reports`                                  | Generate a report from a report definition |
| `delete_report`              | `DELETE /api/reports/{id}`                           | Delete a generated report |
| `create_report_schedule`     | `POST /api/reportdefinitions/{id}/schedules`         | Create a report schedule |
| `update_report_schedule`     | `PUT /api/reportdefinitions/{id}/schedules/{sid}`    | Update a report schedule |
| `delete_report_schedule`     | `DELETE /api/reportdefinitions/{id}/schedules/{sid}` | Delete a report schedule |
| `create_resource`            | `POST /api/resources`                                | Create a new resource |
| `update_resource`            | `PUT /api/resources/{id}`                            | Update resource metadata |
| `delete_resources`           | `DELETE /api/resources`                              | Delete one or more resources |
| `export_ansible_inventory`   | Composite: `POST /api/resources/query` (+ optional file write) | Export YAML inventory for vSphere clusters, NSX-T edge nodes, and NSX-T managers |

---

## 5. MCP Resources (Context)

Expose key reference data as MCP resources (read-only context the LLM can pull):

| Resource URI                     | Description |
|----------------------------------|-------------|
| `ariaops://version`              | Current Aria Ops version and deployment info |
| `ariaops://adapter-kinds`        | List of adapter kinds (VMWARE, etc.) |
| `ariaops://resource-kinds/{ak}`  | Resource kinds for a given adapter kind |
| `ariaops://alert-definitions`    | Summary of all alert definitions |

---

## 6. Architecture

```
┌──────────────────────────────────────────────┐
│  MCP Client (AI code assistant / App)        │
│                                              │
│  stdio (local)  ──or──  HTTP (production)    │
└──────────┬───────────────────────┬───────────┘
           │                       │
           ▼                       ▼
┌──────────────────────────────────────────────┐
│  ariaops-mcp-server                          │
│                                              │
│  ┌─────────────┐  ┌──────────────────────┐   │
│  │ Transport    │  │ Tool Registry        │   │
│  │ stdio / HTTP │  │ (resources, alerts,  │   │
│  └──────┬──────┘  │  metrics, capacity,  │   │
│         │         │  reports, discovery)  │   │
│         │         └──────────┬───────────┘   │
│         │                    │               │
│  ┌──────▼────────────────────▼───────────┐   │
│  │ AriaOpsClient                         │   │
│  │  - Token lifecycle (acquire/refresh)  │   │
│  │  - HTTP session (httpx.AsyncClient)   │   │
│  │  - Retry & rate limiting              │   │
│  │  - Response normalization             │   │
│  └──────────────────┬───────────────────┘   │
│                     │                        │
└─────────────────────┼────────────────────────┘
                      │ HTTPS
                      ▼
        ┌──────────────────────────┐
        │  Aria Operations 8.x    │
        │  /suite-api/api/...     │
        └──────────────────────────┘
```

---

## 7. Project Structure

```
ariaops_mcp/
├── pyproject.toml              # Project metadata, dependencies
├── Containerfile               # Podman container definition
├── .env.example                # Environment variable template
├── src/
│   └── ariaops_mcp/
│       ├── __init__.py
│       ├── __main__.py         # Entry point (stdio or HTTP)
│       ├── server.py           # MCP server setup, tool registration
│       ├── client.py           # AriaOpsClient (HTTP + auth)
│       ├── tools/
│       │   ├── __init__.py
│       │   ├── resources.py    # Resource tools
│       │   ├── alerts.py       # Alert tools
│       │   ├── metrics.py      # Metrics/stats tools
│       │   ├── capacity.py     # Capacity tools (composite)
│       │   ├── reports.py      # Report tools
│       │   ├── ansible_inventory.py # Opt-in Ansible inventory export
│       │   └── discovery.py    # Version, collectors, symptoms, etc.
│       ├── models.py           # Pydantic models for params/responses
│       └── config.py           # Settings from env vars
├── tests/
│   ├── conftest.py
│   ├── test_client.py
│   ├── test_tools/
│   │   ├── test_resources.py
│   │   ├── test_alerts.py
│   │   ├── test_metrics.py
│   │   └── ...
│   └── fixtures/               # Sample API response JSON
└── CLAUDE.md                   # AI code assistant project instructions
```

---

## 8. Dependencies

| Package        | Purpose                            |
|----------------|-------------------------------------|
| `mcp`          | Official MCP Python SDK             |
| `httpx`        | Async HTTP client                   |
| `pydantic`     | Settings + request/response models  |
| `uvicorn`      | ASGI server (for HTTP transport)    |
| `pytest`       | Testing                             |
| `pytest-asyncio` | Async test support               |
| `respx`        | Mock httpx for testing              |

---

## 9. Configuration

All via environment variables (12-factor):

| Variable              | Required | Default   | Description                        |
|-----------------------|----------|-----------|------------------------------------|
| `ARIAOPS_HOST`        | Yes      | —         | Aria Ops hostname (no protocol)    |
| `ARIAOPS_USERNAME`    | Yes      | —         | API username                       |
| `ARIAOPS_PASSWORD`    | Yes      | —         | API password                       |
| `ARIAOPS_AUTH_SOURCE`  | No       | `local`   | Auth source (local, LDAP name)    |
| `ARIAOPS_VERIFY_SSL`  | No       | `true`    | TLS verification                   |
| `ARIAOPS_PORT`        | No       | `8080`    | HTTP transport listen port         |
| `ARIAOPS_TRANSPORT`   | No       | `stdio`   | `stdio` or `http`                  |
| `ARIAOPS_HTTP_OAUTH_ENABLED` | No | `false` | Require OAuth 2.x bearer tokens on HTTP transport |
| `ARIAOPS_HTTP_OAUTH_PROVIDER` | No | `generic` | Use `keycloak` for Keycloak realm JWKS/RS256 defaults |
| `ARIAOPS_HTTP_OAUTH_ISSUER_URL` | No | — | OAuth issuer / Keycloak realm URL |
| `ARIAOPS_HTTP_OAUTH_RESOURCE_SERVER_URL` | No | — | Public MCP HTTP resource URL |
| `ARIAOPS_HTTP_OAUTH_JWT_KEY` | One of | — | Static JWT secret or PEM key |
| `ARIAOPS_HTTP_OAUTH_JWKS_URL` | One of | — | JWKS URL for IdP-managed signing keys |
| `ARIAOPS_HTTP_OAUTH_AUDIENCE` | No | resource URL | Expected JWT audience |
| `ARIAOPS_HTTP_OAUTH_REQUIRED_SCOPES` | No | — | Required OAuth scopes |
| `ARIAOPS_LOG_LEVEL`   | No       | `INFO`    | Logging level                      |
| `ARIAOPS_ENABLE_WRITE_OPERATIONS` | No | `false` | Enable write/mutating tools      |

---

## 10. Non-Functional Requirements

| ID     | Requirement |
|--------|-------------|
| NFR-1  | All API calls use `httpx.AsyncClient` with connection pooling |
| NFR-2  | Retry transient errors (429, 502, 503, 504) with exponential backoff, max 3 retries |
| NFR-3  | Request timeout: 30s connect, 60s read |
| NFR-4  | Log all API calls at DEBUG level (method, path, status, duration) |
| NFR-5  | Never log credentials or tokens at any log level |
| NFR-6  | Structured JSON logging when running in HTTP mode |
| NFR-7  | Containerfile uses multi-stage build, runs as non-root |
| NFR-8  | Health endpoint at `/health` in HTTP mode (returns Aria Ops connectivity status) |
| NFR-9  | Pagination: tools that return lists must support `page`/`pageSize` params and return total count |
| NFR-10 | Large responses truncated to avoid exceeding MCP message limits (configurable max, default 50 items) |

---

## 11. Out of Scope (v1)

- SaaS / VMware Cloud deployment
- SSO / SAML authentication
- WebSocket transport
- Caching layer (beyond token caching)
- Multi-tenancy
- Custom dashboards API
- Super metric creation
- Policy CRUD (create/update/delete policies, assign to objects)
