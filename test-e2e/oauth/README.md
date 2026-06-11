# OAuth2 e2e scenario (Keycloak + ariaops-mcp in Podman)

Spins up a real OAuth2 IdP (Keycloak) with simulated users, runs the
ariaops-mcp container in OAuth mode against it, and verifies token validation
and role-based instance access end-to-end over the streamable HTTP transport.

## Topology

```
host                          podman network "ariaops-e2e"
─────                         ────────────────────────────
e2e_oauth.py ── :8081 ──────► keycloak        (realm "ariaops")
             ── :8090 ──────► ariaops-mcp-e2e ── JWKS fetch ──► keycloak:8080
```

- Tokens are fetched from the host (`iss = http://localhost:8081/realms/ariaops`),
  so `ARIAOPS_HTTP_OAUTH_ISSUER_URL` uses the host-visible URL while
  `ARIAOPS_HTTP_OAUTH_JWKS_URL` uses the in-network `keycloak:8080` address.
- Two fake Aria Ops instances (`se`, `de`) are configured; no real vROps is
  contacted — the tests use `list_instances` (instance-agnostic) and the
  access-denied path (principal check happens before any backend call).

## Simulated users

| User | Password | AD-equivalent claims | Expected access |
|---|---|---|---|
| `alice` | `alicepw` | `ariaops_role=ops` | both instances (`se`, `de`) |
| `bob` | `bobpw` | `ariaops_role=country`, `ariaops_country=SE` | only `se`; `de` denied |

Claims are mapped from Keycloak user attributes into the access token by
protocol mappers in `realm-ariaops.json` (plus an `aud=ariaops-mcp` audience
mapper, which the MCP verifier requires).

## Run

```bash
./run.sh          # build image, start stack, run tests, leave stack running
./run.sh down     # tear down containers and network
```

Test cases (in `e2e_oauth.py`):

1. unauthenticated POST → 401
2. garbage bearer token → 401
3. alice → role `ops`, sees `se` + `de`
4. bob → role `country`, sees only `se`, default instance `se`
5. bob calling a tool with `instance="de"` → `Access denied`

## Notes

- Keycloak runs in `start-dev` mode with admin `admin/admin` — lab only.
- The script and env files neutralize corporate proxy variables (`--noproxy`,
  `trust_env=False`, empty `http_proxy` in `mcp.env`) so localhost and
  in-network traffic is direct.
- Requires the repo venv (`.venv`) for the test script (`mcp`, `httpx`).
