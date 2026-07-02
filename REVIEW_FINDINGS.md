# Code Review Findings — `feat/ldap-capacity`

Review of the branch diff vs `main` (~4,400 lines): LDAP/AD authentication mapped
to role claims, the `Principal` authorization model, multi-instance client
routing, capacity pagination/chunking, and the Keycloak/Chainlit test harnesses.

**Scope:** correctness, security (auth-focused), and quality/conventions.
**Status: all findings fixed on this branch.** Every fix carries a regression
test. Validation after fixes: `ruff check` clean, `pyright` 0 errors,
`pytest` 383 passed.

| # | Severity | Area | Finding | Status |
|---|---|---|---|---|
| F1 | Medium / security | LDAP group mapping | CN fallback defeated full-DN group keys | Fixed |
| F2 | Medium / security | Config | Fail-open default: no group map ⇒ everyone gets `ops` | Fixed |
| F3 | Medium / robustness | Capacity tool | `get_capacity_overview` response unbounded | Fixed |
| F4 | Low / defense-in-depth | LDAP auth | Empty-password bind guarded only at HTTP layer | Fixed |
| F5 | Low / documentation | LDAP auth | Password-change cache window & no rate limiting undocumented | Fixed |
| F6 | Low / robustness | Health endpoint | Sequential instance probes could exceed probe timeout | Fixed |
| F7 | Low / security | Server meta-tools | `reload_skills` bypassed principal checks | Fixed |
| F8 | Nit | Capacity / principal | Constant naming; list-valued role claim repr in error | Fixed |

---

## F1 — CN fallback defeated full-DN group keys (Medium, security)

**Where:** `src/ariaops_mcp/ldap_auth.py`, `map_groups_to_claims()`

**Problem:** Every configured group-map key — including full DNs — was also
indexed by its CN. An admin who deliberately configured
`"CN=vrops-ops,OU=Privileged,DC=corp"` to disambiguate two same-named groups
was silently matched by membership in **any** group named `vrops-ops` anywhere
in the tree (e.g. a delegated OU where users can create their own groups).
That granted the `ops` role and access to every configured instance.

**Fix:** Bare-CN keys (no `=` in the key) still match any group with that CN;
full-DN keys now match only that exact DN, case-insensitively
(`ldap_auth.py:80-91`). Behavior is documented in the function docstring and
`AUTH_FLOW.md` → *Group mapping rules*.

**Tests:** `tests/test_ldap_auth.py::test_map_full_dn_key_does_not_match_same_cn_in_other_ou`,
`::test_map_full_dn_key_matches_exact_dn_case_insensitive`

---

## F2 — Fail-open default role in LDAP mode (Medium, security posture)

**Where:** `src/ariaops_mcp/config.py`, `validate_http_ldap()`;
`src/ariaops_mcp/ldap_auth.py` default-role handling

**Problem:** With `ARIAOPS_HTTP_AUTH_MODE=ldap` and no
`ARIAOPS_LDAP_GROUP_ROLE_MAP`, every user who could bind to the directory was
granted `ARIAOPS_DEFAULT_ROLE` — which itself defaults to `ops` (all
instances). In a corporate AD that is effectively "any employee gets full
access", enabled silently by omission.

**Fix:** Config now fails closed: LDAP mode with an empty group map requires
`ARIAOPS_DEFAULT_ROLE` to be set explicitly (`config.py:404-417`). Setting it
to `ops` is still allowed — but it is now a deliberate, visible choice.
Documented in `AUTH_FLOW.md`.

**Tests:** `tests/test_ldap_auth.py::test_config_ldap_requires_default_role_when_group_map_empty`,
`::test_config_ldap_allows_empty_group_map_with_explicit_default_role`

---

## F3 — Unbounded `get_capacity_overview` response (Medium, robustness)

**Where:** `src/ariaops_mcp/tools/capacity.py`, `get_capacity_overview()`

**Problem:** The stats *request* was chunked (good), but all chunk results
were aggregated into a single JSON reply — a large deployment (thousands of
resources × 7 stat keys) went straight into the LLM context with no cap,
unlike every other list-returning tool.

**Fix:** The merged `values` list is passed through the existing
`truncate_list_response()` helper (`capacity.py:205`), capping the payload at
`MAX_LIST_ITEMS` (50) and marking it with `_truncated: true` / `_truncatedAt`.
`resourceCount` still reports the true total.

**Tests:** `tests/test_tools/test_capacity.py::test_get_capacity_overview_chunks_stats_query`
(updated to assert the cap and truncation markers)

---

## F4 — Empty-password bind guarded only at the HTTP layer (Low, defense-in-depth)

**Where:** `src/ariaops_mcp/ldap_auth.py`, `LDAPAuthenticator.authenticate()`

**Problem:** Blank credentials were rejected in `BasicLDAPAuthBackend` and by
ldap3's `LDAPPasswordIsMandatoryError`, but `authenticate()` itself did not
guard. Any future non-HTTP caller risked the RFC 4513 §5.1.2
unauthenticated-bind pitfall, where some directories treat an empty-password
bind as success.

**Fix:** `authenticate()` returns `None` immediately for empty username or
password (`ldap_auth.py:334-337`), before any cache lookup or bind attempt.

**Tests:** `tests/test_ldap_auth.py::test_authenticator_rejects_empty_password_without_binding`,
`::test_authenticator_rejects_empty_username_without_binding`

---

## F5 — Undocumented credential-cache staleness window (Low, documentation)

**Where:** `src/ariaops_mcp/ldap_auth.py` docstring; `AUTH_FLOW.md`

**Problem:** The success cache keys on `HMAC(username:password)`, so after a
password change the **old** password keeps authenticating from cache for up to
`ldap_cache_ttl` seconds (default 300). The docstring only promised that the
new password works immediately — true, but half the story. Additionally, the
server does no bind rate limiting; brute-force protection relies entirely on
the directory's account lockout policy. Neither trade-off was written down.

**Fix:** Both limitations documented in the `LDAPAuthenticator` class
docstring and in `AUTH_FLOW.md` → *Known limitations*, including the
mitigation (lower `ARIAOPS_LDAP_CACHE_TTL`).

---

## F6 — Sequential `/health` instance probes (Low, robustness)

**Where:** `src/ariaops_mcp/__main__.py`, `_health_check()`

**Problem:** Instances were probed one at a time; with N degraded backends the
worst case was N × read-timeout — easily past a Kubernetes probe deadline,
turning one slow instance into a false-negative liveness signal for the whole
server.

**Fix:** Probes run concurrently via `asyncio.gather` with per-probe error
handling (`__main__.py:27-45`); worst case is now one timeout regardless of
instance count. The legacy single-instance payload shape is preserved.

**Tests:** `tests/test_http_auth.py::test_health_endpoint_probes_multiple_instances_concurrently`

---

## F7 — `reload_skills` bypassed principal checks (Low, security)

**Where:** `src/ariaops_mcp/server.py`, meta-tool dispatch in `call_tool()`

**Problem:** Instance-agnostic meta-tools (`list_skills`, `reload_skills`,
`list_instances`) skipped principal resolution entirely. `reload_skills`
mutates server state (re-reads skill definitions from disk) and was available
to any authenticated user — even one whose role would be denied on every
instance-bound tool.

**Fix:** Meta-tools now resolve the caller's principal; tools listed in
`_OPS_ONLY_TOOLS` (currently `reload_skills`) require the `ops` role
(`server.py:35-39`, `server.py:373-392`). `list_*` meta-tools remain open to
any authenticated principal.

**Tests:** `tests/test_server.py::test_call_reload_skills_denies_non_ops_role`
(also asserts `list_skills` stays accessible)

---

## F8 — Nits (batched)

1. **`PAGE_SIZE_MAX` reused as POST-body chunk size** —
   `src/ariaops_mcp/tools/capacity.py:22` now defines `STATS_QUERY_CHUNK_SIZE`
   (same value, distinct meaning: bounds a POST body, not a GET page).
2. **List-valued role claim rendered as Python repr** — an unmapped role claim
   like `["contractor"]` produced `Unknown role '['contractor']'` in the
   `AccessDenied` message. `src/ariaops_mcp/principal.py:75-84`
   (`_stringify_role`) joins sequence claims into a plain string.
   Test: `tests/test_instances.py::test_unmapped_list_role_claim_readable_error_message`

---

## Non-findings (checked, confirmed OK)

- **LDAP filter injection** — usernames and bind DNs are escaped with
  `escape_filter_chars` before being interpolated into the search filter.
- **TLS enforcement** — config validation rejects non-`ldaps://` URIs when
  `ARIAOPS_LDAP_VERIFY_TLS=true` (the default).
- **Credential cache design** — failed binds are never cached; cache keys are
  HMAC-SHA256 under a per-process random key, so a memory dump does not enable
  offline dictionary attacks.
- **Instance authorization** — enforced centrally in `server.call_tool` via
  `Principal.resolve_instance()`; the `instance` argument is popped before
  handler dispatch and the client is pinned via a contextvar, so tool handlers
  cannot bypass the check.
- **Retry idempotency** — read-only query POSTs are correctly marked
  `idempotent=True`; mutating requests are never retried on transport errors.
- **Circuit breaker** — half-open state admits a single probe request;
  deadline-budget checks prevent backoff sleeps from overrunning the request
  deadline.
- **Capacity pagination** — the page loop terminates correctly on both the
  `totalCount` bound and an empty page.

## Validation

```
ruff check .   → clean
pyright        → 0 errors, 0 warnings
pytest -q      → 383 passed (374 pre-existing + 9 new regression tests)
```

Run everything with: `uv run ruff check . && uv run pyright && uv run pytest -q`
