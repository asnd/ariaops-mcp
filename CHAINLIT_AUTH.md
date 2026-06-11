# Chainlit ↔ AriaOps MCP — Authentication

How a [Chainlit](https://docs.chainlit.io) chat frontend authenticates end users
and forwards their identity to the AriaOps MCP server so that the server's
existing **role-based authorization** (`ops` / `country`) applies unchanged.

A runnable reference implementation lives in
[`test-ui/chainlit/`](test-ui/chainlit/).

---

## The core idea

The MCP server already knows how to authenticate and authorize a request on its
HTTP transport. It accepts **two** credential shapes, and both end at the same
place — `principal.resolve_principal(claims)`, which decides the caller's role
and which Aria Operations instances they may touch:

| MCP auth mode (`ARIAOPS_HTTP_AUTH_MODE`) | Header the server expects | How claims are derived |
|---|---|---|
| `oauth` | `Authorization: Bearer <JWT>` | `JWTTokenVerifier` validates the JWT and reads `role`/`country`/`instance` claims |
| `ldap`  | `Authorization: Basic <user:pass>` | server binds to LDAPS, reads `memberOf`, maps groups → the *same* claims |

**Chainlit's only job is to obtain a credential at login and forward the matching
header on every MCP call.** It must not make its own authorization decisions —
the server is the single source of truth. This keeps one authorization model for
the API, the CLI, and the chat UI.

```
┌────────────┐   login (password│oauth)   ┌──────────────┐   Authorization: Basic│Bearer   ┌──────────────┐
│   Browser  │ ─────────────────────────▶ │   Chainlit   │ ─────────────────────────────▶  │  MCP server  │
│   (user)   │                            │  (frontend)  │                                  │ (authorizes) │
└────────────┘                            └──────────────┘                                  └──────┬───────┘
                                                                                                   │ role/instance
                                                                                            resolve_principal()
                                                                                                   ▼
                                                                                         Aria Operations API
```

---

## Mode A — Password login → MCP LDAP

Chainlit's [password auth](https://docs.chainlit.io/authentication/password) shows
a username/password form and calls `@cl.password_auth_callback`. We **do not bind
to LDAP in the frontend** — that would duplicate logic and split trust. Instead we
forward the credentials to the MCP server as Basic auth and let *its* LDAP backend
accept or reject them.

```python
@cl.password_auth_callback
async def password_auth(username: str, password: str) -> cl.User | None:
    auth_header = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
    try:
        info = await mcp_describe(auth_header)   # initialize + list_instances
    except Exception:
        return None                              # bad creds / unmapped group / down
    return cl.User(identifier=username, metadata={"mcp_auth": auth_header, ...})
```

A successful MCP `initialize` means the server's LDAP bind **and** the group→role
mapping both succeeded (a bound-but-unmapped user is denied server-side). The
`Basic` header is stashed on the Chainlit user and replayed on every tool call.

### Sequence

```
User → Chainlit:  username + password
Chainlit → MCP:   initialize + list_instances   (Authorization: Basic …)
MCP → LDAPS:      bind as user, read memberOf
MCP:              map_groups_to_claims → {role: ops|country, …}
MCP → Chainlit:   200 + accessible instances     (or 401 → login denied)
Chainlit:         store Basic header on the session
…
User → Chainlit:  "show capacity for US"
Chainlit → MCP:   call_tool(get_capacity_overview, {instance: us})  (Authorization: Basic …)
MCP:              resolve_principal → enforce → query Aria Ops
```

### Server side

```bash
ARIAOPS_TRANSPORT=http
ARIAOPS_HTTP_AUTH_MODE=ldap
ARIAOPS_LDAP_SERVER_URI=ldaps://dc1.corp.example.com:636
ARIAOPS_LDAP_USER_DN_TEMPLATE={username}@corp.example.com
ARIAOPS_LDAP_USER_SEARCH_BASE=dc=corp,dc=example,dc=com
# Optional: map AD groups → roles (omit → every authenticated user gets ARIAOPS_DEFAULT_ROLE)
ARIAOPS_LDAP_GROUP_ROLE_MAP={"vrops-ops":{"role":"ops"},"vrops-se":{"role":"country","country":"SE"}}
```

---

## Mode B — OAuth login → MCP OAuth

Chainlit's [OAuth](https://docs.chainlit.io/authentication/oauth) handles the
full authorization-code flow with your IdP and calls `@cl.oauth_callback` with the
issued **access token**. We forward *that same token* to the MCP server as a
Bearer credential — there is no second login.

```python
@cl.oauth_callback
async def oauth_callback(provider_id, token, raw_user_data, default_user):
    auth_header = f"Bearer {token}"
    # forward the IdP access token unchanged; the MCP server validates it
    default_user.metadata = {**default_user.metadata, "mcp_auth": auth_header}
    return default_user
```

### Sequence

```
User → Chainlit:    "Login with Keycloak"
Chainlit ⇄ Keycloak: OAuth authorization-code flow → access token
Keycloak → Chainlit: @cl.oauth_callback(token=<access_token>, …)
Chainlit → MCP:      initialize  (Authorization: Bearer <access_token>)
MCP:                 JWTTokenVerifier → verify sig/iss/aud/exp → read role claims
MCP → Chainlit:      200 (or 401 if token rejected)
…  every tool call forwards the same Bearer token
```

### The critical part — aligning the two sides

The token Chainlit obtains must be one the MCP server will accept. Both must agree
on issuer, audience, and signing keys:

| Concern | Chainlit (frontend) | MCP server | Must match |
|---|---|---|---|
| Issuer / realm | `OAUTH_KEYCLOAK_BASE_URL` + `OAUTH_KEYCLOAK_REALM` | `ARIAOPS_HTTP_OAUTH_ISSUER_URL` | **Yes** — same realm URL |
| Audience | the registered client (`OAUTH_KEYCLOAK_CLIENT_ID`) | `ARIAOPS_HTTP_OAUTH_AUDIENCE` | **Yes** — token `aud` must satisfy the server |
| Signing keys | n/a (IdP signs) | `…_JWKS_URL` (or `…_JWT_KEY`) | server must fetch the realm's JWKS |
| Required scopes | requested at login | `ARIAOPS_HTTP_OAUTH_REQUIRED_SCOPES` | token must carry them |
| Role claims | mapped in the IdP (e.g. a Keycloak *mapper*) | `ARIAOPS_ROLE_CLAIM` / `…_COUNTRY_CLAIM` / `…_INSTANCE_CLAIM` | claim names must match |

> **Keycloak audience gotcha:** Keycloak only puts your client id in the `aud`
> claim if you add an *Audience* mapper to the client. Without it, set
> `ARIAOPS_HTTP_OAUTH_AUDIENCE` to whatever the token actually carries (often the
> `azp`/client id), or the server will reject every token.

### Server side

```bash
ARIAOPS_TRANSPORT=http
ARIAOPS_HTTP_OAUTH_ENABLED=true
ARIAOPS_HTTP_OAUTH_PROVIDER=keycloak
ARIAOPS_HTTP_OAUTH_ISSUER_URL=https://kc.example.com/realms/myrealm
ARIAOPS_HTTP_OAUTH_RESOURCE_SERVER_URL=https://mcp.example.com
ARIAOPS_HTTP_OAUTH_AUDIENCE=chainlit-ui
# role claims (defaults shown) — must equal the claim names your IdP mapper emits
ARIAOPS_ROLE_CLAIM=ariaops_role
ARIAOPS_COUNTRY_CLAIM=ariaops_country
ARIAOPS_INSTANCE_CLAIM=ariaops_instance
```

---

## How a role becomes an authorization decision

Regardless of login method, the server reduces the request to a `Principal`:

- **`ops`** → may target **any** configured instance; passes `instance` per call.
- **`country`** → pinned to the **one** instance matching their `country`/`instance`
  claim; any other `instance` argument raises `AccessDenied`.

For LDAP, the group→role map produces those claims; for OAuth, an IdP mapper does.
Either way `resolve_principal(claims)` is the single decision point — so the chat
UI inherits the exact authorization the API already enforces. The frontend never
needs to know the rules.

---

## Choosing a mode (and supporting both)

The MCP server runs **one** auth mode at a time (`oauth` and `ldap` are mutually
exclusive — the server's config validation rejects enabling both). Chainlit,
however, can present *both* a password form and OAuth buttons; just point each
callback at an MCP server configured for the matching mode. In practice:

- **Single mode:** run one MCP server; enable only the matching Chainlit callback.
- **Both modes:** run two MCP server processes (one `ldap`, one `oauth`) and route
  each Chainlit login to the right `ARIAOPS_MCP_URL`. Keep it simple — most
  deployments pick one.

---

## Security notes

- **Always use TLS** between Chainlit and the MCP server. Both Basic credentials
  and Bearer tokens are replayed on every call; plaintext HTTP leaks them.
- **The credential lives on the Chainlit session.** Chainlit persists the
  authenticated user in a cookie signed with `CHAINLIT_AUTH_SECRET`; treat that
  secret like a password and rotate it. For password/LDAP this means the user's
  Basic credential rides in the signed cookie — acceptable for a controlled test
  tool, but for production prefer the OAuth path (short-lived tokens) over
  long-lived Basic credentials.
- **OAuth tokens expire.** When the access token lapses the MCP server returns
  401; the user simply logs in again. Don't cache decisions past token lifetime.
- **No authorization in the frontend.** The UI must forward, not decide. Keeping
  the single server-side decision point avoids drift between what the UI shows and
  what the API allows.
- **`/health` stays open** on the MCP server in both modes; only the MCP endpoint
  (`/`) requires a credential.

---

## Try it

See [`test-ui/chainlit/README.md`](test-ui/chainlit/README.md) for the runnable
harness: start the MCP server in `ldap` or `oauth` mode, run
`chainlit run app.py -w`, log in, and watch the welcome message report your
**server-resolved role** and **accessible instances**.
