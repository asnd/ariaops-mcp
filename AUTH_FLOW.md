# Authentication and Authorization Flow

ariaops-mcp supports three authentication modes selected by `ARIAOPS_HTTP_AUTH_MODE`.
All three converge on the same **principal resolution** step, which enforces
role-based instance access before any Aria Operations API call is made.

---

## Middleware stack (HTTP transport)

```
Incoming HTTP request
        │
        ▼
┌──────────────────────────────────┐
│  AuthenticationMiddleware        │  Starlette — calls the configured backend
│  (Starlette)                     │  and writes scope["user"] / scope["auth"]
└──────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────┐
│  AuthContextMiddleware           │  MCP SDK — copies the token into an
│  (mcp)                           │  async-context-var for get_access_token()
└──────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────┐
│  RequireAuthMiddleware           │  OAuth mode: checks user + required scopes
│  — or —                          │  LDAP mode: checks user only (no scopes)
│  BasicRequireAuthMiddleware      │  → 401 / 403 if not satisfied
└──────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────┐
│  StreamableHTTPASGIApp (MCP)     │  Dispatches JSON-RPC tool calls
└──────────────────────────────────┘
        │
        ▼
    call_tool()  →  principal resolution  →  tool handler
```

---

## OAuth mode (`ARIAOPS_HTTP_AUTH_MODE=oauth`)

**Backend:** `BearerAuthBackend` + `JWTTokenVerifier`

```
Client
  │  Authorization: Bearer <JWT>
  ▼
BearerAuthBackend.authenticate()
  │
  ├─► JWTTokenVerifier.verify_token(token)
  │       │
  │       ├─ Resolve signing key
  │       │     JWKS URL configured?
  │       │       yes → PyJWKClient.get_signing_key_from_jwt()   [off-thread]
  │       │       no  → static ARIAOPS_HTTP_OAUTH_JWT_KEY
  │       │
  │       ├─ jwt.decode()  — signature + expiry checked by library
  │       │
  │       ├─ Validate iss  (must match ARIAOPS_HTTP_OAUTH_ISSUER_URL)
  │       ├─ Validate aud  (must contain ARIAOPS_HTTP_OAUTH_AUDIENCE)
  │       └─ Require client identity (client_id / azp / appid / sub)
  │
  │  on failure → None → AuthenticationMiddleware leaves request unauthenticated
  │
  └─► Returns ClaimsAccessToken {
            token:     raw JWT string
            client_id: azp / client_id claim
            scopes:    split "scope" or "scp" claim
            expires_at: exp
            subject:   sub
            claims:    full decoded payload   ← carries ariaops_role etc.
        }

AuthContextMiddleware stores token in context-var

RequireAuthMiddleware
  ├─ scope["user"] is AuthenticatedUser?  no → 401
  └─ all required_scopes in scope["auth"].scopes?  no → 403

→ call_tool() (see §Principal resolution below)
```

**Key claim names** (configurable via settings):

| Env var | Default claim | Description |
|---|---|---|
| `ARIAOPS_ROLE_CLAIM` | `ariaops_role` | `"ops"` or `"country"` |
| `ARIAOPS_COUNTRY_CLAIM` | `ariaops_country` | ISO country code (country role only) |
| `ARIAOPS_INSTANCE_CLAIM` | `ariaops_instance` | Explicit instance id override |

---

## LDAP mode (`ARIAOPS_HTTP_AUTH_MODE=ldap`)

**Backend:** `BasicLDAPAuthBackend` + `LDAPAuthenticator`

```
Client
  │  Authorization: Basic base64(username:password)
  ▼
BasicLDAPAuthBackend.authenticate()
  │
  ├─ Decode base64 → username, password
  │
  └─► LDAPAuthenticator.authenticate(username, password)
          │
          ├─ Cache check
          │     key = HMAC-SHA256(per_process_key, "username:password")
          │     hit?  → return cached claims immediately
          │     miss? → proceed to bind
          │
          ├─► _sync_bind_and_get_groups()   [off-thread via asyncio.to_thread]
          │       │
          │       ├─ bind_dn = user_dn_template.replace("{username}", username)
          │       │
          │       ├─ ldap3.Connection(server, user=bind_dn, password=password,
          │       │                   auto_bind=True)
          │       │     LDAPException → return None  (wrong credentials)
          │       │
          │       ├─ Build search filter (FILTER INJECTION SAFE):
          │       │     safe_user = escape_filter_chars(username)
          │       │     safe_dn   = escape_filter_chars(bind_dn)
          │       │     (|(userPrincipalName={safe_user})
          │       │       (sAMAccountName={safe_user})
          │       │       (uid={safe_user})
          │       │       (distinguishedName={safe_dn}))
          │       │
          │       ├─ conn.search(base, filter, attributes=["memberOf"])
          │       └─ return [str(g) for g in entry.memberOf.values]
          │
          ├─► _claims_for_groups(groups)
          │       │
          │       ├─ no group_role_map?  → {role_claim: default_role}
          │       │
          │       └─► map_groups_to_claims()
          │               ├─ match groups against map (full DN or CN)
          │               ├─ ops descriptor found? → {role: "ops"}  (wins)
          │               └─ else first country match:
          │                    {role: "country", country?: "SE", instance?: "se"}
          │
          ├─ claims is None?  → log + return None  (authenticated but unmapped)
          │
          ├─ _set_cache(key, claims)   ← only on success
          │     sweeps expired entries if len ≥ 1000
          │
          └─ return claims

  claims is None? → None → AuthenticationMiddleware: unauthenticated

  → ClaimsAccessToken {
        token:     "ldap"   (sentinel — not a real JWT)
        client_id: username
        scopes:    []       ← always empty; scope-gate is bypassed in LDAP mode
        expires_at: now + cache_ttl
        claims:    {ariaops_role: ..., ariaops_country?: ..., ariaops_instance?: ...}
    }
  → AuthCredentials([])
  → AuthenticatedUser(access_token)

AuthContextMiddleware stores token in context-var

BasicRequireAuthMiddleware
  ├─ scope["user"] is AuthenticatedUser?  no → 401  WWW-Authenticate: Basic realm="ariaops-mcp"
  └─ (no scope check — scopes always empty in LDAP mode)

→ call_tool() (see §Principal resolution below)
```

**Cache security:**
The cache key is `HMAC-SHA256(per_process_random_key, "username:password")`. The
key is generated at process start with `secrets.token_bytes(32)` and never
persisted, so a memory dump of the cache dict cannot be used for offline
dictionary attacks.

**Group mapping rules:**
- An `"ops"` descriptor always wins over any `"country"` descriptors.
- Only direct `memberOf` groups are checked; nested AD group membership
  (`LDAP_MATCHING_RULE_IN_CHAIN`) is not followed.
- Failed binds are never cached so that password changes take effect immediately.

---

## stdio mode (no HTTP auth)

When `ARIAOPS_TRANSPORT=stdio` the Starlette middleware stack is not present.
`_current_claims()` returns `None` because there is no HTTP context var.

`resolve_principal(claims=None)` falls back entirely to settings:

```
ARIAOPS_DEFAULT_ROLE       → role
ARIAOPS_DEFAULT_COUNTRY    → country  (country role only)
ARIAOPS_DEFAULT_INSTANCE   → explicit instance override
```

---

## Principal resolution (all modes)

Called at the start of every tool invocation from `call_tool()` in `server.py`.

```
_current_claims()
  │  OAuth:  get_access_token().claims  (full JWT payload)
  │  LDAP:   get_access_token().claims  (group-derived dict)
  │  stdio:  None
  ▼
resolve_principal(claims, settings)
  │
  ├─ Extract role  (settings.role_claim from claims, else settings.default_role)
  │
  ├─ role == "ops"
  │     instance_ids    = all configured instance ids
  │     default         = settings.default_instance or sole instance or None
  │
  └─ role == "country"
        explicit_instance claim present?
          yes → single allowed instance
          no  → _instance_for_country(country_claim) — must match exactly one
        instance_ids    = (target,)
        default         = target

  → Principal { role, instance_ids: tuple[str,...], default_instance_id }

call_tool():
  ├─ instance-agnostic tools (list_instances, list_skills, reload_skills):
  │     no instance enforcement; principal.can_access() used for filtering
  │
  └─ instance-bound tools:
        principal.resolve_instance(requested_instance)
          ├─ requested not in instance_ids → AccessDenied → {"error": "Access denied"}
          ├─ requested is None + default_instance_id set → use default
          ├─ requested is None + single instance → use it
          └─ requested is None + multiple → AccessDenied (must specify)

        set_current_instance(instance_id)   ← async context-var
        handler(args)                        ← get_client() picks up the pinned instance
        reset_current_instance(token)
```

---

## Error responses

| Condition | Mode | Code | Body |
|---|---|---|---|
| No `Authorization` header | both | 401 | `{"error":"unauthorized"}` + `WWW-Authenticate: Bearer` (OAuth) or `Basic` (LDAP) |
| Invalid / expired JWT | OAuth | 401 | `{"error":"unauthorized"}` |
| Bad Base64 or missing password | LDAP | 401 | `{"error":"unauthorized"}` |
| Wrong password / bind failure | LDAP | 401 | `{"error":"unauthorized"}` |
| Authenticated but no mapped group | LDAP | 401 | `{"error":"unauthorized"}` |
| Missing required scope | OAuth | 403 | `{"error":"insufficient_scope"}` |
| Instance not in principal's allowed set | both | 200 | `{"error":"Access denied","detail":"..."}` |
| Country claim maps to no instance | both | 200 | `{"error":"Access denied","detail":"..."}` |

> Instance-access denials are returned as MCP tool results (HTTP 200) because
> the MCP protocol encodes tool errors in the response body, not via HTTP status.
