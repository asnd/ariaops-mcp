# AriaOps MCP Server

A **read-only** [Model Context Protocol (MCP)](https://modelcontextprotocol.io) server for VMware Aria Operations (on-prem). Exposes Aria Ops REST API capabilities as MCP tools so AI assistants can query infrastructure health, alerts, metrics, capacity, and reports.

## Features

- **Resources** — list, search, query VMs/hosts/clusters and their properties
- **Alerts** — active alerts, definitions, notes, contributing symptoms
- **Metrics** — stats over time, latest values, bulk queries, Top-N
- **Capacity** — remaining capacity per cluster, time-to-full, policy thresholds
- **Reports** — list templates, generated reports, download (PDF/CSV)
- **Discovery** — version info, collectors, symptom definitions, recommendations

Supports both `stdio` (local/testing) and `streamable HTTP` (production) transports.
This makes it usable from cloud-hosted and on-prem model runtimes, including locally running MCP-capable setups (for example Ollama- or LM Studio-based workflows).

## MCP Architecture

```
MCP Client (Claude Desktop, IDE agent, or app)
  └── MCP transport: stdio (local) or streamable HTTP (production)
      └── ariaops_mcp server
          ├── Transport layer (__main__.py)
          ├── MCP server + registry (server.py)
          ├── Tool handlers (tools/*.py)
          └── AriaOpsClient (client.py)
               └── VMware Aria Operations REST API
```

### Components and roles

| Component | Role |
|---|---|
| MCP client | Sends tool calls and reads MCP resources/context. |
| Transport layer (`src/ariaops_mcp/__main__.py`) | Starts the server in `stdio` mode for local MCP clients or HTTP mode for remote/production use. |
| MCP server (`src/ariaops_mcp/server.py`) | Registers all tools/resources and routes incoming MCP calls to the matching handler. |
| Tool modules (`src/ariaops_mcp/tools/*.py`) | Implement domain-specific operations (resources, alerts, metrics, capacity, reports, discovery). |
| API client (`src/ariaops_mcp/client.py`) | Authenticates with Aria Operations, manages token lifecycle, performs API requests, and retries transient failures. |
| Aria Operations API | Upstream system of record for infrastructure inventory, health, alerts, metrics, and reports. |

## Requirements

- Python 3.11+
- Access to an on-prem VMware Aria Operations 8.x instance
- [Podman](https://podman.io/) (optional, for containerized deployment)

## Quick Start

### Install

```bash
git clone https://github.com/asnd/ariaops-mcp.git
cd ariaops-mcp
pip install -e .
```

For development (includes linting and test tools):

```bash
pip install -e ".[dev]"
```

### Configure

Create a `settings.ini` file in the working directory (the demo script and server load this automatically):

```env
ARIAOPS_HOST=vrops.example.com
ARIAOPS_USERNAME=admin
ARIAOPS_PASSWORD=secret
ARIAOPS_AUTH_SOURCE=local     # or your LDAP source name
ARIAOPS_VERIFY_SSL=false
ARIAOPS_TRANSPORT=stdio
ARIAOPS_PORT=443
ARIAOPS_LOG_LEVEL=DEBUG
```

### Run (stdio — for MCP clients)

```bash
python -m ariaops_mcp
```

### MCP interaction demo (no LLM)

Run the built-in demo script to initialize MCP, discover tools/resources, and query vCenter inventory:

```bash
python -m ariaops_mcp.demo_mcp_interaction
```

### Run (HTTP — production)

```bash
ARIAOPS_TRANSPORT=http ARIAOPS_PORT=8080 python -m ariaops_mcp
```

### Run with Podman

```bash
podman build --format docker -t ariaops-mcp .
podman run -v "$(pwd)/settings.ini:/app/settings.ini:ro" -p 8080:8080 ariaops-mcp
```

## MCP Client Integration

Add to your MCP config (e.g. `~/.claude/mcp_settings.json` for AI code assistants):

```json
{
  "mcpServers": {
    "ariaops": {
      "command": "python",
      "args": ["-m", "ariaops_mcp"],
      "env": {
        "ARIAOPS_HOST": "vrops.example.com",
        "ARIAOPS_USERNAME": "admin",
        "ARIAOPS_PASSWORD": "secret"
      }
    }
  }
}
```

## Available Tools

| Domain     | Tools |
|------------|-------|
| Resources  | `list_resources`, `get_resource`, `query_resources`, `get_resource_properties`, `get_resource_relationships`, `list_resource_kinds`, `list_adapter_kinds`, `list_resource_groups`, `get_resource_group_members` |
| Alerts     | `list_alerts`, `get_alert`, `query_alerts`, `get_alert_notes`, `list_alert_definitions`, `get_alert_definition`, `get_contributing_symptoms` |
| Metrics    | `get_resource_stats`, `get_latest_stats`, `query_stats`, `query_latest_stats`, `get_stat_keys`, `get_top_n_stats`, `list_properties_latest` |
| Capacity   | `get_capacity_remaining`, `get_capacity_overview`, `list_policies` |
| Reports    | `list_report_definitions`, `get_report_definition`, `list_reports`, `get_report`, `download_report`, `list_report_schedules` |
| Discovery  | `get_version`, `list_collectors`, `list_symptoms`, `list_recommendations`, `list_supermetrics` |

Full tool catalog: [`TOOLS.md`](TOOLS.md)

Full spec: [`REQUIREMENTS.md`](REQUIREMENTS.md)

## Development

```bash
pip install -e ".[dev]"
pytest                  # run tests
ruff check src/ tests/  # lint
```

## Configuration Reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `ARIAOPS_HOST` | Yes | — | Aria Ops hostname (no scheme) |
| `ARIAOPS_USERNAME` | Yes | — | API username |
| `ARIAOPS_PASSWORD` | Yes | — | API password |
| `ARIAOPS_AUTH_SOURCE` | No | `local` | Auth source (local / LDAP name) |
| `ARIAOPS_VERIFY_SSL` | No | `false` | TLS certificate verification |
| `ARIAOPS_TRANSPORT` | No | `stdio` | `stdio` or `http` |
| `ARIAOPS_PORT` | No | `443` | HTTP listen port |
| `ARIAOPS_LOG_LEVEL` | No | `DEBUG` | Log level |

## License

MIT
