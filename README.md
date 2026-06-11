# AriaOps MCP Server

A [Model Context Protocol (MCP)](https://modelcontextprotocol.io) server for VMware Aria Operations (on-prem). Exposes Aria Ops REST API capabilities as MCP tools so AI assistants can query infrastructure health, alerts, metrics, capacity, and reports — and optionally perform write operations when explicitly enabled.

## Features

- **Resources** — list, search, query VMs/hosts/clusters and their properties
- **Alerts** — active alerts, definitions, notes, contributing symptoms
- **Metrics** — stats over time, latest values, bulk queries, Top-N
- **Capacity** — remaining capacity per cluster, time-to-full, policy thresholds
- **Reports** — list templates, generated reports, download (PDF/CSV)
- **Discovery** — version info, collectors, symptom definitions, recommendations
- **Write operations** *(opt-in)* — modify alerts, maintenance mode, schedules, report generation, resource lifecycle

Supports both `stdio` (local/testing) and `streamable HTTP` (production) transports.

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

```bash
cp .env.example .env
# Edit .env with your Aria Ops credentials
```

```env
ARIAOPS_HOST=vrops.example.com
ARIAOPS_USERNAME=admin
ARIAOPS_PASSWORD=secret
ARIAOPS_AUTH_SOURCE=local     # or your LDAP source name
ARIAOPS_VERIFY_SSL=true
```

### Run (stdio — for MCP clients)

```bash
python -m ariaops_mcp
```

### Run (HTTP — production)

```bash
ARIAOPS_TRANSPORT=http ARIAOPS_PORT=8080 python -m ariaops_mcp
```

To require OAuth 2.x bearer tokens on the HTTP transport, enable the flag and provide the resource-server settings:

```bash
ARIAOPS_TRANSPORT=http \
ARIAOPS_HTTP_OAUTH_ENABLED=true \
ARIAOPS_HTTP_OAUTH_ISSUER_URL=https://issuer.example.com \
ARIAOPS_HTTP_OAUTH_RESOURCE_SERVER_URL=https://mcp.example.com \
ARIAOPS_HTTP_OAUTH_JWT_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" \
ARIAOPS_HTTP_OAUTH_REQUIRED_SCOPES=mcp:read \
python -m ariaops_mcp
```

#### OAuth knobs

| Variable | Required | Default | Notes |
|----------|----------|---------|-------|
| `ARIAOPS_HTTP_OAUTH_ENABLED` | no | `false` | Turn enforcement on. Requires `ARIAOPS_TRANSPORT=http`. |
| `ARIAOPS_HTTP_OAUTH_PROVIDER` | no | `generic` | Set to `keycloak` to derive the realm JWKS URL and default accepted algorithms to `RS256` when no key material is supplied. |
| `ARIAOPS_HTTP_OAUTH_ISSUER_URL` | yes | — | Expected `iss` claim and the authorization server published via the discovery doc. |
| `ARIAOPS_HTTP_OAUTH_RESOURCE_SERVER_URL` | yes | — | This server's resource identifier — used as the default `aud` and exposed via `/.well-known/oauth-protected-resource`. |
| `ARIAOPS_HTTP_OAUTH_JWT_KEY` | one of | — | HS256/384/512 shared secret (≥32 bytes) **or** PEM public key for RS*/ES*/PS*. |
| `ARIAOPS_HTTP_OAUTH_JWKS_URL` | one of | — | JWKS endpoint URL (e.g. Keycloak's `/realms/<realm>/protocol/openid-connect/certs`). Mutually exclusive with `JWT_KEY`; required for IdPs that rotate keys. Optional when `ARIAOPS_HTTP_OAUTH_PROVIDER=keycloak`. |
| `ARIAOPS_HTTP_OAUTH_JWT_ALGORITHMS` | no | `HS256` | Comma-separated list. The verifier rejects every algorithm not on this list (so `alg: none` and algorithm confusion are blocked). |
| `ARIAOPS_HTTP_OAUTH_AUDIENCE` | no | resource URL | Override if your IdP issues a different `aud`. |
| `ARIAOPS_HTTP_OAUTH_REQUIRED_SCOPES` | no | `[]` | Tokens must carry every listed scope or get a 403 `insufficient_scope`. |
| `ARIAOPS_HTTP_OAUTH_LEEWAY_SECONDS` | no | `30` | Clock-skew tolerance for `exp` / `nbf` / `iat`. |
| `ARIAOPS_HTTP_OAUTH_JWKS_CACHE_TTL` | no | `300` | How long (s) to cache JWKS keys before refetching. |

**Discovery:** With OAuth enabled, the server publishes
`GET /.well-known/oauth-protected-resource` per RFC 9728 so MCP clients
auto-discover the authorization server and required scopes.

**Health:** `GET /health` is intentionally unauthenticated so probes and load
balancers keep working with OAuth turned on.

#### Keycloak setup

Keycloak is a supported IdP. Use JWKS — Keycloak rotates RS256 keys.

1. **Create a client** in your realm (`Clients → Create client`):
   - *Client type:* OpenID Connect
   - *Client authentication:* on (confidential) for the MCP client
   - *Valid redirect URIs:* whatever your MCP client uses
2. **Add an Audience mapper** (`Client scopes → <client>-dedicated → Add mapper → Audience`):
   - *Included Client Audience:* the client ID you want as `aud` (e.g. `mcp-client`).
   - This is required because Keycloak does **not** put the resource URL in `aud` by default.
3. **Add scope(s)** (`Client scopes → Create`) and assign to the client (`Default` or `Optional`).
4. **Configure ariaops-mcp:**

   ```bash
   ARIAOPS_TRANSPORT=http \
   ARIAOPS_HTTP_OAUTH_ENABLED=true \
   ARIAOPS_HTTP_OAUTH_PROVIDER=keycloak \
   ARIAOPS_HTTP_OAUTH_ISSUER_URL=https://kc.example.com/realms/myrealm \
   ARIAOPS_HTTP_OAUTH_RESOURCE_SERVER_URL=https://mcp.example.com \
   ARIAOPS_HTTP_OAUTH_AUDIENCE=mcp-client \
   ARIAOPS_HTTP_OAUTH_REQUIRED_SCOPES=mcp:read \
   python -m ariaops_mcp
   ```

5. **Test from the CLI** with a client-credentials grant:

   ```bash
   TOKEN=$(curl -s -X POST \
     "https://kc.example.com/realms/myrealm/protocol/openid-connect/token" \
     -d grant_type=client_credentials \
     -d client_id=mcp-client \
     -d client_secret=$KC_SECRET \
     -d scope="mcp:read" | jq -r .access_token)

   curl -H "Authorization: Bearer $TOKEN" https://mcp.example.com/
   ```

Notes:
- `iss` in Keycloak tokens is exactly `https://<host>/realms/<realm>` (no trailing slash). The verifier strips trailing slashes on both sides, so either form works in config.
- `ARIAOPS_HTTP_OAUTH_PROVIDER=keycloak` derives `ARIAOPS_HTTP_OAUTH_JWKS_URL` as `<issuer>/protocol/openid-connect/certs` and defaults `ARIAOPS_HTTP_OAUTH_JWT_ALGORITHMS` to `RS256` unless you explicitly set algorithms or `ARIAOPS_HTTP_OAUTH_JWT_KEY`.
- Roles (`realm_access.roles`, `resource_access.<client>.roles`) are not enforced — only OAuth `scope`. Map roles to scopes in Keycloak if you need RBAC.
- The verifier wraps `PyJWKClient` in `asyncio.to_thread`, so a JWKS cache miss does not block the event loop.

#### LDAP / Active Directory authentication

As an alternative to OAuth, the HTTP transport can authenticate MCP clients with
**HTTP Basic** credentials verified against LDAPS. The server binds directly with
the user's credentials (no service account), reads their `memberOf` groups, and
maps them to the **same** `role`/`country`/`instance` claims the OAuth path uses —
so [role-based access](#role-based-access) works identically.

```bash
ARIAOPS_TRANSPORT=http \
ARIAOPS_HTTP_AUTH_MODE=ldap \
ARIAOPS_LDAP_SERVER_URI="ldaps://dc1.corp.example.com:636" \
ARIAOPS_LDAP_USER_DN_TEMPLATE="{username}@corp.example.com" \
ARIAOPS_LDAP_USER_SEARCH_BASE="dc=corp,dc=example,dc=com" \
ARIAOPS_LDAP_GROUP_ROLE_MAP='{"vrops-ops":{"role":"ops"},"vrops-se":{"role":"country","country":"SE"}}' \
python -m ariaops_mcp
```

| Variable | Required | Default | Notes |
|---|---|---|---|
| `ARIAOPS_HTTP_AUTH_MODE` | yes | `none` | Set to `ldap`. Mutually exclusive with `ARIAOPS_HTTP_OAUTH_ENABLED`. |
| `ARIAOPS_LDAP_SERVER_URI` | yes | — | `ldaps://` URI. Plain `ldap://` only allowed with `ARIAOPS_LDAP_VERIFY_TLS=false`. |
| `ARIAOPS_LDAP_USER_DN_TEMPLATE` | yes | — | Bind DN with `{username}`. AD UPN `{username}@corp.example.com` or `uid={username},ou=people,dc=corp,dc=com`. |
| `ARIAOPS_LDAP_USER_SEARCH_BASE` | yes | — | Base DN for the `memberOf` lookup. |
| `ARIAOPS_LDAP_GROUP_ROLE_MAP` | no | `{}` | JSON: AD group CN/DN → `{"role":"ops"}` or `{"role":"country","country":"SE"}`/`{"role":"country","instance":"de"}`. An `ops` group wins over `country`. When empty, every authenticated user gets `ARIAOPS_DEFAULT_ROLE`. A bound user matching no mapped group is denied. |
| `ARIAOPS_LDAP_CA_CERT_FILE` | no | system trust | PEM bundle for LDAPS verification. |
| `ARIAOPS_LDAP_VERIFY_TLS` | no | `true` | Disable only for lab/testing. |
| `ARIAOPS_LDAP_CACHE_TTL` | no | `300` | Seconds to cache a successful bind's claims. |
| `ARIAOPS_LDAP_BIND_TIMEOUT` | no | `10` | LDAP connect timeout (seconds). |

`/health` stays unauthenticated, and instance authorization is enforced by the
same `principal` layer as OAuth — LDAP only decides the caller's role/instance.

Security notes:

- Group lookup reads `memberOf`, which contains **direct** group memberships
  only — a user who is in a mapped group via a nested group will not receive
  its role. Map the groups users belong to directly (resolving nested
  membership via the AD matching-rule-in-chain OID `1.2.840.113556.1.4.1941`
  is a possible future extension).
- Failed binds are never cached, so every bad-password request reaches the
  directory. Put a rate limit on the MCP endpoint at your reverse proxy to
  protect against password spraying and AD account-lockout abuse.
- `ARIAOPS_HTTP_OAUTH_REQUIRED_SCOPES` is ignored in LDAP mode (Basic-auth
  requests carry no OAuth scopes); a warning is logged if it is set.

See [`AUTH_FLOW.md`](AUTH_FLOW.md) for a detailed message-flow walkthrough of both OAuth and LDAP modes, including middleware stack, token verification steps, cache behaviour, and principal resolution.

### Run with Podman

```bash
podman build --format docker -t ariaops-mcp .
podman run --env-file .env -p 8080:8080 ariaops-mcp
```

## Multiple Aria Operations instances

A single MCP server can front several Aria Operations instances. Define them
with `ARIAOPS_INSTANCES` (a JSON array); when set, the legacy single-host
variables (`ARIAOPS_HOST`/`ARIAOPS_USERNAME`/`ARIAOPS_PASSWORD`) are optional.

```env
ARIAOPS_INSTANCES=[
  {"id":"us","host":"us.vrops.example.com","username":"svc","password":"secret","country":"US"},
  {"id":"de","host":"de.vrops.example.com","username":"svc","password":"secret","country":"DE"}
]
```

Each entry needs a unique `id`. Optional per-instance fields: `auth_source`
(default `local`), `verify_ssl` (default `true`), and `country` (used to pin
country-role users).

### Role-based access

Access is scoped by the caller's **role**:

| Role | Access |
|------|--------|
| `ops` | All configured instances. The target instance is chosen per call via the tool's `instance` argument (required only when more than one instance exists). |
| `country` | Exactly one instance — the one matching the user's country (or explicit instance) claim. Requests for any other instance are rejected. |

On the **HTTP transport** the role/country/instance are read from the validated
JWT claims (claim names are configurable). On **stdio** (local) they fall back to
the `ARIAOPS_DEFAULT_*` settings.

Every instance-bound tool accepts an optional `instance` argument, and the
`list_instances` tool returns the instances the current caller may use. Backward
compatibility is preserved: with only the legacy single-host variables set, a
single `default` instance is synthesized and existing behavior is unchanged.

| Variable | Default | Description |
|---|---|---|
| `ARIAOPS_INSTANCES` | — | JSON array of instance objects (`id`, `host`, `username`, `password`, optional `auth_source`/`verify_ssl`/`country`) |
| `ARIAOPS_ROLE_CLAIM` | `ariaops_role` | JWT claim holding the caller's role |
| `ARIAOPS_COUNTRY_CLAIM` | `ariaops_country` | JWT claim holding the country code for country-role users |
| `ARIAOPS_INSTANCE_CLAIM` | `ariaops_instance` | JWT claim holding an explicit instance id for country-role users |
| `ARIAOPS_OPS_ROLE` | `ops` | Role value that grants access to all instances |
| `ARIAOPS_COUNTRY_ROLE` | `country` | Role value that pins a user to a single instance |
| `ARIAOPS_DEFAULT_ROLE` | `ops` | Role assumed when no JWT role claim is present (e.g. stdio) |
| `ARIAOPS_DEFAULT_COUNTRY` | — | Country assumed for a country-role caller without a claim |
| `ARIAOPS_DEFAULT_INSTANCE` | — | Default instance id for `ops` callers and the synthesized single instance |

## MCP Architecture

```text
+---------------------------+     MCP (stdio / streamable HTTP)     +---------------------------+
| MCP Client / AI Assistant | <-----------------------------------> | ariaops-mcp server        |
| Claude Desktop, IDE, etc. |                                       |                           |
+-------------+-------------+                                       |  read tools: always on    |
              |                                                     |  write tools: opt-in      |
              |                                                     +-------------+-------------+
              |                                                                   |
              |                                                                   | HTTPS REST
              |                                                                   v
              |                                                     +---------------------------+
              +---------------------------------------------------> | VMware Aria Operations   |
                                                                    | /suite-api/api/...       |
                                                                    +---------------------------+
```

```text
+-----------------------+      chat / tool plans      +-----------------------+
| Browser               | <-------------------------> | test-ui/app.py        |
| Gradio test UI        |                             | Gradio + tool bridge  |
+-----------+-----------+                             +-----+-----------+-----+
            |                                               |           |
            |                                               |           |
            |                                    LLM Gateway |           | in-process MCP tools
            v                                               v           v
  +-------------------+                           +----------------+  +----------------------+
  | Human operator    |                           | OpenAI-compat  |  | ariaops_mcp modules  |
  +-------------------+                           | model gateway  |  | + AriaOpsClient      |
                                                  +----------------+  +----------+-----------+
                                                                                  |
                                                                                  v
                                                                       +----------------------+
                                                                       | VMware Aria Ops      |
                                                                       +----------------------+
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
| Capacity   | `get_capacity_remaining`, `get_capacity_overview`, `list_policies`, `get_capacity_forecast`, `get_trend_analysis` |
| Reports    | `list_report_definitions`, `get_report_definition`, `list_reports`, `get_report`, `download_report`, `list_report_schedules` |
| Discovery  | `get_version`, `list_collectors`, `list_symptoms`, `list_recommendations`, `list_supermetrics` |
| Instances  | `list_instances` (always available; lists the Aria Ops instances accessible to the caller) |

### Write Tools (requires `ARIAOPS_ENABLE_WRITE_OPERATIONS=true`)

| Domain               | Tools |
|----------------------|-------|
| Alert operations     | `modify_alerts`, `add_alert_note`, `delete_alert_note`, `delete_canceled_alerts` |
| Resource maintenance | `mark_resources_maintained`, `unmark_resources_maintained` |
| Maint. schedules     | `create_maintenance_schedule`, `update_maintenance_schedule`, `delete_maintenance_schedule` |
| Report operations    | `generate_report`, `delete_report`, `create_report_schedule`, `update_report_schedule`, `delete_report_schedule` |
| Resource lifecycle   | `create_resource`, `update_resource`, `delete_resources` |

Full tool catalog: [`TOOLS.md`](TOOLS.md)

Full spec: [`REQUIREMENTS.md`](REQUIREMENTS.md)

## Test UI App

The repo includes a Gradio-based test UI in [`test-ui/`](test-ui) for exercising the same tool handlers in-process while routing chat through an LLM gateway.

Create a local env file first so no tenant IDs, client IDs, or tokens need to be hardcoded in the repo:

```bash
cp .env.example .env
```

Set `LITELLM_BASE_URL` for the gateway. If you want to use the **Get Token via Azure SSO** button, also set:

```env
AZURE_TENANT_ID=your-azure-tenant-id
AZURE_CLIENT_ID=your-azure-client-id
```

```bash
python -m pip install -r test-ui/requirements.txt
python test-ui/app.py --port 7860
```

To run its automated tests:

```bash
cd test-ui
pytest tests
```

### Chainlit auth test harness

`test-ui/chainlit/` is a minimal [Chainlit](https://docs.chainlit.io) frontend that tests both auth modes end-to-end. It proves the LDAP (Basic) and OAuth (Bearer) wiring without coupling the demo UI to Gradio.

```bash
cp test-ui/chainlit/.env.example test-ui/chainlit/.env
# edit .env: set ARIAOPS_MCP_URL + optional LITELLM_* vars
pip install chainlit httpx python-dotenv
chainlit run test-ui/chainlit/app.py -w
```

See [`CHAINLIT_AUTH.md`](CHAINLIT_AUTH.md) for full configuration details.

### OAuth2 end-to-end scenario

`test-e2e/oauth/` contains a self-contained Podman scenario that spins up a real Keycloak IdP with two simulated users (`alice`=ops, `bob`=country/SE) and verifies token validation, role-based instance access, and instance enforcement over the streamable HTTP transport.

```bash
./test-e2e/oauth/run.sh        # build image, start stack, run 8 tests, leave running
./test-e2e/oauth/run.sh down   # tear down
```

See [`test-e2e/oauth/README.md`](test-e2e/oauth/README.md) for topology, user table, and proxy notes.

## Development

```bash
pip install -e ".[dev]"
pytest                  # run tests
ruff check src/ tests/  # lint
pyright                 # type-check
cd test-ui && pytest tests
```

## Configuration Reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `ARIAOPS_HOST` | Yes | — | Aria Ops hostname (no scheme) |
| `ARIAOPS_USERNAME` | Yes | — | API username |
| `ARIAOPS_PASSWORD` | Yes | — | API password |
| `ARIAOPS_AUTH_SOURCE` | No | `local` | Auth source (local / LDAP name) |
| `ARIAOPS_INSTANCES` | No | — | JSON array of Aria Ops instances for multi-instance mode (see [Multiple Aria Operations instances](#multiple-aria-operations-instances)) |
| `ARIAOPS_ROLE_CLAIM` | No | `ariaops_role` | JWT claim holding the caller's role (`ops` / `country`) |
| `ARIAOPS_COUNTRY_CLAIM` | No | `ariaops_country` | JWT claim holding a country-role user's country code |
| `ARIAOPS_INSTANCE_CLAIM` | No | `ariaops_instance` | JWT claim holding an explicit instance id for country-role users |
| `ARIAOPS_OPS_ROLE` | No | `ops` | Role value granting access to all instances |
| `ARIAOPS_COUNTRY_ROLE` | No | `country` | Role value pinning a user to a single instance |
| `ARIAOPS_DEFAULT_ROLE` | No | `ops` | Role assumed when no JWT role claim is present |
| `ARIAOPS_DEFAULT_COUNTRY` | No | — | Country assumed for a country-role caller without a claim |
| `ARIAOPS_DEFAULT_INSTANCE` | No | — | Default instance id for `ops` callers / synthesized single instance |
| `ARIAOPS_VERIFY_SSL` | No | `true` | TLS certificate verification |
| `ARIAOPS_TRANSPORT` | No | `stdio` | `stdio` or `http` |
| `ARIAOPS_PORT` | No | `8080` | HTTP listen port |
| `ARIAOPS_HTTP_OAUTH_ENABLED` | No | `false` | Require OAuth 2.x bearer tokens on the HTTP MCP transport |
| `ARIAOPS_HTTP_OAUTH_PROVIDER` | No | `generic` | Use `keycloak` for Keycloak realm JWKS/RS256 defaults |
| `ARIAOPS_HTTP_OAUTH_ISSUER_URL` | No | — | OAuth 2.x issuer URL advertised to MCP clients when HTTP auth is enabled |
| `ARIAOPS_HTTP_OAUTH_RESOURCE_SERVER_URL` | No | — | Public MCP HTTP endpoint URL used for OAuth protected-resource metadata |
| `ARIAOPS_HTTP_OAUTH_JWT_KEY` | One of | — | HS* shared secret (≥32 bytes) or PEM public key for RS*/ES*/PS* |
| `ARIAOPS_HTTP_OAUTH_JWKS_URL` | One of | — | JWKS endpoint (e.g. Keycloak certs URL); mutually exclusive with `JWT_KEY` |
| `ARIAOPS_HTTP_OAUTH_JWT_ALGORITHMS` | No | `HS256` | Comma-separated or JSON-array list of accepted JWT algorithms |
| `ARIAOPS_HTTP_OAUTH_AUDIENCE` | No | `ARIAOPS_HTTP_OAUTH_RESOURCE_SERVER_URL` | Expected JWT audience for HTTP bearer tokens |
| `ARIAOPS_HTTP_OAUTH_REQUIRED_SCOPES` | No | — | Comma-separated or JSON-array list of scopes required for HTTP MCP access |
| `ARIAOPS_HTTP_OAUTH_LEEWAY_SECONDS` | No | `30` | Clock-skew tolerance for `exp`/`nbf`/`iat` |
| `ARIAOPS_HTTP_OAUTH_JWKS_CACHE_TTL` | No | `300` | JWKS cache lifetime in seconds |
| `ARIAOPS_LOG_LEVEL` | No | `INFO` | Log level |
| `ARIAOPS_ENABLE_WRITE_OPERATIONS` | No | `false` | Enable mutating tools (alert management, maintenance, reports, resource lifecycle) |
| `LITELLM_BASE_URL` | No | — | Test UI LLM gateway base URL |
| `LITELLM_TOKEN` | No | — | Test UI JWT for the gateway |
| `AZURE_TENANT_ID` | No | — | Test UI Azure tenant ID for browser SSO |
| `AZURE_CLIENT_ID` | No | — | Test UI Azure client ID for browser SSO |

## License

MIT
