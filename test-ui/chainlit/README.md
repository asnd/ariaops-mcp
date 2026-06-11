# Chainlit auth test harness

A minimal [Chainlit](https://docs.chainlit.io) frontend that proves end-user
login flows through to the AriaOps MCP server's role-based authorization.

| Chainlit login | Header sent to MCP | MCP auth mode |
|---|---|---|
| Password (`@cl.password_auth_callback`) | `Authorization: Basic …` | `ARIAOPS_HTTP_AUTH_MODE=ldap` |
| OAuth (`@cl.oauth_callback`) | `Authorization: Bearer …` | OAuth (Keycloak/OIDC) |

The full design write-up — sequence diagrams, config alignment, security notes —
lives in [`../../CHAINLIT_AUTH.md`](../../CHAINLIT_AUTH.md).

## Quickstart

```bash
cd test-ui/chainlit
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
chainlit create-secret          # paste the value into CHAINLIT_AUTH_SECRET in .env
```

### 1. Start the MCP server in the mode you want to test

**LDAP (password login):**
```bash
ARIAOPS_TRANSPORT=http \
ARIAOPS_HTTP_AUTH_MODE=ldap \
ARIAOPS_LDAP_SERVER_URI=ldaps://dc1.corp.example.com:636 \
ARIAOPS_LDAP_USER_DN_TEMPLATE='{username}@corp.example.com' \
ARIAOPS_LDAP_USER_SEARCH_BASE='dc=corp,dc=example,dc=com' \
ARIAOPS_INSTANCES='[{"id":"us","host":"…","username":"…","password":"…","country":"US"}]' \
python -m ariaops_mcp
```

**OAuth (OAuth login):**
```bash
ARIAOPS_TRANSPORT=http \
ARIAOPS_HTTP_OAUTH_ENABLED=true \
ARIAOPS_HTTP_OAUTH_PROVIDER=keycloak \
ARIAOPS_HTTP_OAUTH_ISSUER_URL=https://kc.example.com/realms/myrealm \
ARIAOPS_HTTP_OAUTH_RESOURCE_SERVER_URL=https://mcp.example.com \
ARIAOPS_HTTP_OAUTH_AUDIENCE=chainlit-ui \
python -m ariaops_mcp
```

### 2. Run the Chainlit app

```bash
chainlit run app.py -w
```

Open http://localhost:8000. Log in; the welcome message shows your
**server-resolved role** and **accessible instances**.

## Chat modes

- **With an LLM gateway** (`LITELLM_BASE_URL` + `LITELLM_TOKEN` + `LLM_MODEL`):
  natural-language chat; the agent calls MCP tools on your behalf.
- **Without one** (auth-only testing): call a tool directly by typing its name
  and JSON args, e.g.

  ```
  list_instances {}
  get_capacity_overview {"instance":"us"}
  ```

## What this proves

Every tool call opens an MCP session carrying *your* credential. If you log in as
an `ops` user you can pass any `instance`; as a `country` user the server pins you
to one instance and rejects others with `AccessDenied`. The UI enforces nothing —
it only forwards the header, so a green result is real server-side authorization.
