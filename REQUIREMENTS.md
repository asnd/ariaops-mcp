# AriaOps MCP Server — Requirements Specification

## 1. Overview

A **read-only** Model Context Protocol (MCP) server that exposes VMware Aria Operations (on-prem) REST API capabilities as MCP tools. This enables AI assistants (Claude, etc.) to query infrastructure health, alerts, metrics, capacity, and reports through a standardized interface.

**Base API:** `https://<vrops-host>/suite-api/api/`
**API Version target:** 8.x (8.12–8.18+, compatible with Aria Operations on-prem)

---

## 2. Constraints

| Constraint          | Value                                    |
|---------------------|------------------------------------------|
| Deployment          | On-prem only (no SaaS/Cloud support)    |
| Access mode         | Read-only by default; optional gated writes |
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

---

## 4. MCP Tools — Functional Scope

Tools are read-only by default. Optional write tools are feature-flagged via `ARIAOPS_ENABLE_WRITE_OPERATIONS=true`.

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
| `ARIAOPS_LOG_LEVEL`   | No       | `INFO`    | Logging level                      |
| `ARIAOPS_ENABLE_WRITE_OPERATIONS` | No | `false` | Enables write tools (alert note + status actions) |

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
| NFR-9  | Pagination: paginated list/query tools support `page`/`pageSize` params (`page` default `0`, `pageSize` default `50`, max `200`; `get_capacity_overview` default `pageSize=20`) and return total count when available |
| NFR-10 | Large responses are truncated to avoid exceeding MCP message limits (default 50 items), with truncation metadata and pagination hints in the response |

---

## 11. Out of Scope (v1)

- Additional write operations beyond the feature-flagged alert note/status tools (for example group creation, policy mutation)
- SaaS / VMware Cloud deployment
- SSO / SAML authentication
- WebSocket transport
- Caching layer (beyond token caching)
- Multi-tenancy
- Custom dashboards API
- Super metric creation
