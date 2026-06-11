#!/usr/bin/env python3
"""OAuth2 end-to-end test: Keycloak IdP + ariaops-mcp in Podman.

Simulates real users (password grant against Keycloak) and verifies that the
MCP server's OAuth verifier and role-based instance access behave correctly:

  1. no token            -> 401
  2. garbage token       -> 401
  3. alice (role=ops)    -> sees both instances, may target any of them
  4. bob (role=country, country=SE) -> sees only 'se'; targeting 'de' is denied

Run via run.sh (which starts the containers), or directly once the stack is up:
    python e2e_oauth.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

KEYCLOAK_URL = "http://localhost:8081"
REALM = "ariaops"
CLIENT_ID = "ariaops-client"
MCP_URL = "http://localhost:8090/"

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  PASS  {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL  {name}  {detail}")


def _no_proxy_client(headers: dict[str, str] | None = None, **kwargs: Any) -> httpx.AsyncClient:
    """httpx client that ignores the host's proxy env (corporate proxy would
    otherwise intercept localhost traffic)."""
    kwargs.pop("timeout", None)
    return httpx.AsyncClient(trust_env=False, timeout=30, headers=headers, **kwargs)


async def get_token(username: str, password: str) -> str:
    async with _no_proxy_client() as client:
        response = await client.post(
            f"{KEYCLOAK_URL}/realms/{REALM}/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": CLIENT_ID,
                "username": username,
                "password": password,
            },
        )
        response.raise_for_status()
        return response.json()["access_token"]


async def call_tool(token: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}"}
    async with streamablehttp_client(
        MCP_URL, headers=headers, httpx_client_factory=_no_proxy_client
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(name, arguments)
            text = "".join(getattr(block, "text", "") for block in result.content)
            return json.loads(text)


async def raw_post_status(token: str | None) -> int:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    async with _no_proxy_client() as client:
        response = await client.post(
            MCP_URL,
            headers=headers,
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        )
        return response.status_code


async def main() -> int:
    print("== Negative cases ==")
    check("no token -> 401", await raw_post_status(None) == 401)
    check("garbage token -> 401", await raw_post_status("not-a-jwt") == 401)

    print("== alice (ops role) ==")
    alice_token = await get_token("alice", "alicepw")
    info = await call_tool(alice_token, "list_instances", {})
    check("alice role is ops", info.get("role") == "ops", json.dumps(info))
    ids = {inst["id"] for inst in info.get("instances", [])}
    check("alice sees se and de", ids == {"se", "de"}, str(ids))

    print("== bob (country role, SE) ==")
    bob_token = await get_token("bob", "bobpw")
    info = await call_tool(bob_token, "list_instances", {})
    check("bob role is country", info.get("role") == "country", json.dumps(info))
    ids = {inst["id"] for inst in info.get("instances", [])}
    check("bob sees only se", ids == {"se"}, str(ids))
    check("bob default instance is se", info.get("default_instance") == "se")

    print("== instance enforcement ==")
    # Principal check runs before any Aria Ops call, so the denial is immediate
    # and no real vROps backend is needed.
    denied = await call_tool(bob_token, "list_alerts", {"instance": "de"})
    check(
        "bob targeting de is denied",
        denied.get("error") == "Access denied",
        json.dumps(denied),
    )

    print()
    print(f"{len(PASSED)} passed, {len(FAILED)} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
