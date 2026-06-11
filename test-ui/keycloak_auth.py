#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.parse
import urllib.request
from urllib.error import HTTPError

BASE_URL = os.getenv("KEYCLOAK_BASE_URL", "")
REALM = os.getenv("KEYCLOAK_REALM", "master")
USERNAME = os.getenv("KEYCLOAK_USERNAME")
PASSWORD = os.getenv("KEYCLOAK_PASSWORD")
CLIENT_ID = os.getenv("KEYCLOAK_CLIENT_ID", "admin-cli")
VERIFY_SSL = os.getenv("KEYCLOAK_VERIFY_SSL", "true").lower() in {"1", "true", "yes"}
MCP_REALM = os.getenv("KEYCLOAK_MCP_REALM", REALM)
MCP_RESOURCE_SERVER_URL = os.getenv("KEYCLOAK_MCP_RESOURCE_SERVER_URL")
MCP_AUDIENCE = os.getenv("KEYCLOAK_MCP_AUDIENCE")
MCP_REQUIRED_SCOPES = os.getenv("KEYCLOAK_MCP_REQUIRED_SCOPES", "mcp:read")
WRITE_ENV_FILE = os.getenv("KEYCLOAK_WRITE_ENV_FILE", ".env")


def _build_opener() -> urllib.request.OpenerDirector:
    if VERIFY_SSL:
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))

    import ssl

    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=context),
    )


def get_token() -> str:
    if not BASE_URL:
        raise SystemExit("Set KEYCLOAK_BASE_URL in the environment (e.g. https://idp.example.com/auth).")
    if not USERNAME or not PASSWORD:
        raise SystemExit("Set KEYCLOAK_USERNAME and KEYCLOAK_PASSWORD in the environment.")

    token_url = f"{BASE_URL.rstrip('/')}/realms/{REALM}/protocol/openid-connect/token"
    payload = urllib.parse.urlencode(
        {
            "client_id": CLIENT_ID,
            "username": USERNAME,
            "password": PASSWORD,
            "grant_type": "password",
        }
    ).encode()
    request = urllib.request.Request(token_url, data=payload, method="POST")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        with _build_opener().open(request) as response:
            body = json.load(response)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        raise SystemExit(f"Authentication failed with HTTP {exc.code}: {detail}") from exc

    return body["access_token"]


def decode_token_claims(token: str) -> dict[str, object]:
    parts = token.split(".")
    if len(parts) != 3:
        raise SystemExit("Access token is not a JWT.")

    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    decoded = base64.urlsafe_b64decode(payload.encode("ascii"))
    claims = json.loads(decoded)
    if not isinstance(claims, dict):
        raise SystemExit("Decoded JWT payload is not an object.")
    return claims


def _extract_roles(claims: dict[str, object]) -> tuple[list[str], dict[str, list[str]]]:
    realm_access = claims.get("realm_access")
    realm_roles: list[str] = []
    if isinstance(realm_access, dict):
        raw_realm_roles = realm_access.get("roles", [])
        if isinstance(raw_realm_roles, list):
            realm_roles = [str(role) for role in raw_realm_roles]

    resource_access = claims.get("resource_access")
    client_roles: dict[str, list[str]] = {}
    if isinstance(resource_access, dict):
        for client, access in resource_access.items():
            if not isinstance(access, dict):
                continue
            raw_roles = access.get("roles", [])
            if isinstance(raw_roles, list) and raw_roles:
                client_roles[str(client)] = [str(role) for role in raw_roles]

    return realm_roles, client_roles


def describe_token(token: str) -> None:
    claims = decode_token_claims(token)
    realm_roles, client_roles = _extract_roles(claims)
    scopes = str(claims.get("scope", "")).strip()
    username = claims.get("preferred_username") or claims.get("email") or claims.get("sub")
    print(f"Token user: {username}", file=sys.stderr)
    print(f"Token issuer: {claims.get('iss')}", file=sys.stderr)
    print(f"Token client: {claims.get('azp') or claims.get('client_id')}", file=sys.stderr)
    print(f"Token scopes: {scopes or '(none)'}", file=sys.stderr)
    print(
        f"Realm roles: {', '.join(realm_roles) if realm_roles else '(none)'}",
        file=sys.stderr,
    )
    if client_roles:
        for client, roles in sorted(client_roles.items()):
            print(f"Client roles for {client}: {', '.join(roles)}", file=sys.stderr)
    else:
        print("Client roles: (none)", file=sys.stderr)


def print_ariaops_env() -> None:
    issuer_url = f"{BASE_URL.rstrip('/')}/realms/{MCP_REALM}"
    jwks_url = f"{issuer_url}/protocol/openid-connect/certs"
    resource_server_url = MCP_RESOURCE_SERVER_URL or "<set-mcp-public-url>"
    audience = MCP_AUDIENCE or "ariaops-mcp"

    print("# ariaops-mcp HTTP OAuth settings for Keycloak 13.0.1", file=sys.stderr)
    print("ARIAOPS_TRANSPORT=http", file=sys.stderr)
    print("ARIAOPS_HTTP_OAUTH_ENABLED=true", file=sys.stderr)
    print("ARIAOPS_TRUST_ENV=false", file=sys.stderr)
    print(f"ARIAOPS_HTTP_OAUTH_ISSUER_URL={issuer_url}", file=sys.stderr)
    print(f"ARIAOPS_HTTP_OAUTH_RESOURCE_SERVER_URL={resource_server_url}", file=sys.stderr)
    print(f"ARIAOPS_HTTP_OAUTH_JWKS_URL={jwks_url}", file=sys.stderr)
    print("ARIAOPS_HTTP_OAUTH_JWT_ALGORITHMS=RS256", file=sys.stderr)
    print(f"ARIAOPS_HTTP_OAUTH_AUDIENCE={audience}", file=sys.stderr)
    print(f"ARIAOPS_HTTP_OAUTH_REQUIRED_SCOPES={MCP_REQUIRED_SCOPES}", file=sys.stderr)
    print("ARIAOPS_HTTP_OAUTH_LEEWAY_SECONDS=30", file=sys.stderr)
    print("ARIAOPS_HTTP_OAUTH_JWKS_CACHE_TTL=300", file=sys.stderr)

    if not MCP_RESOURCE_SERVER_URL or not MCP_AUDIENCE:
        print("Missing values for final ariaops-mcp config:", file=sys.stderr)
        if not MCP_RESOURCE_SERVER_URL:
            print("- KEYCLOAK_MCP_RESOURCE_SERVER_URL", file=sys.stderr)
        if not MCP_AUDIENCE:
            print("- KEYCLOAK_MCP_AUDIENCE (recommended: ariaops-mcp)", file=sys.stderr)


def build_ariaops_env_lines() -> list[str]:
    issuer_url = f"{BASE_URL.rstrip('/')}/realms/{MCP_REALM}"
    jwks_url = f"{issuer_url}/protocol/openid-connect/certs"
    resource_server_url = MCP_RESOURCE_SERVER_URL or "<set-mcp-public-url>"
    audience = MCP_AUDIENCE or "ariaops-mcp"

    return [
        "ARIAOPS_TRANSPORT=http",
        "ARIAOPS_TRUST_ENV=false",
        "ARIAOPS_HTTP_OAUTH_ENABLED=true",
        f"ARIAOPS_HTTP_OAUTH_ISSUER_URL={issuer_url}",
        f"ARIAOPS_HTTP_OAUTH_RESOURCE_SERVER_URL={resource_server_url}",
        f"ARIAOPS_HTTP_OAUTH_JWKS_URL={jwks_url}",
        "ARIAOPS_HTTP_OAUTH_JWT_ALGORITHMS=RS256",
        f"ARIAOPS_HTTP_OAUTH_AUDIENCE={audience}",
        f"ARIAOPS_HTTP_OAUTH_REQUIRED_SCOPES={MCP_REQUIRED_SCOPES}",
        "ARIAOPS_HTTP_OAUTH_LEEWAY_SECONDS=30",
        "ARIAOPS_HTTP_OAUTH_JWKS_CACHE_TTL=300",
    ]


def write_repo_env(*, force: bool = False) -> None:
    env_path = WRITE_ENV_FILE
    if os.path.exists(env_path) and not force:
        raise SystemExit(
            f"{env_path} already exists — refusing to overwrite. "
            "Pass --force or set KEYCLOAK_WRITE_ENV_FILE to another path."
        )
    lines = build_ariaops_env_lines()
    content = "\n".join(lines) + "\n"
    with open(env_path, "w", encoding="utf-8") as handle:
        handle.write(content)
    print(f"Wrote {env_path}", file=sys.stderr)


def list_realms(token: str) -> list[str]:
    realms_url = f"{BASE_URL.rstrip('/')}/admin/realms"
    request = urllib.request.Request(realms_url)
    request.add_header("Authorization", f"Bearer {token}")

    try:
        with _build_opener().open(request) as response:
            body = json.load(response)
    except HTTPError as exc:
        if exc.code == 403:
            raise SystemExit(
                "Authenticated, but this account is not allowed to list realms. "
                "Keycloak requires admin roles on the master realm for /admin/realms."
            ) from exc
        raise

    return [realm["realm"] for realm in body]


def main() -> int:
    token = get_token()
    print("Authenticated successfully.", file=sys.stderr)
    describe_token(token)
    print(token)

    if "--list-realms" in sys.argv:
        for realm in list_realms(token):
            print(realm, file=sys.stderr)

    if "--print-ariaops-env" in sys.argv:
        print_ariaops_env()

    if "--write-ariaops-env" in sys.argv:
        write_repo_env(force="--force" in sys.argv)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
