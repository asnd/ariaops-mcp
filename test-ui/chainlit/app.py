#!/usr/bin/env python3
"""AriaOps MCP — Chainlit auth test harness.

A minimal Chainlit frontend that demonstrates how end-user authentication maps
onto the AriaOps MCP server's two HTTP auth modes:

* **Password login**  → ``Authorization: Basic <user:pass>`` → MCP **LDAP** mode.
  The MCP server performs the LDAP/AD bind and derives role/country/instance
  claims from group membership.
* **OAuth login**     → ``Authorization: Bearer <access_token>`` → MCP **OAuth**
  mode. The same IdP token the user logged in with is forwarded to the MCP
  server, which validates it and reads the role claims.

In both cases the credential is captured at login, stashed on the Chainlit user,
and forwarded verbatim to the MCP server on every tool call. The MCP server is
the single source of authorization truth (``principal.resolve_principal``); this
frontend never decides what a user may access — it only proves the wiring works.

Run:
    chainlit run app.py -w
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any

import chainlit as cl
import httpx
from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

load_dotenv()

# ─── configuration ─────────────────────────────────────────────────────────────
MCP_URL = os.environ.get("ARIAOPS_MCP_URL", "http://localhost:8080/")
MCP_VERIFY_TLS = os.environ.get("ARIAOPS_MCP_VERIFY_TLS", "true").lower() != "false"

# Optional LLM gateway (OpenAI-compatible). When unset, the app still works as a
# pure auth tester: type a tool name to call it directly.
LLM_BASE_URL = os.environ.get("LITELLM_BASE_URL", "").rstrip("/")
LLM_TOKEN = os.environ.get("LITELLM_TOKEN", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "")
LLM_VERIFY_TLS = os.environ.get("LITELLM_VERIFY_TLS", "true").lower() != "false"

MAX_TOOL_ROUNDS = 25
MAX_TOOL_RESULT_CHARS = 6000

SYSTEM_PROMPT = (
    "You are an assistant for VMware Aria Operations. Use the available tools to "
    "answer questions about resources, alerts, metrics, capacity and reports. "
    "The user's role and accessible instances are enforced server-side — if a "
    "tool returns an access-denied error, explain it rather than retrying."
)


def _llm_enabled() -> bool:
    return bool(LLM_BASE_URL and LLM_TOKEN and LLM_MODEL)


# ─── MCP plumbing ──────────────────────────────────────────────────────────────
def _basic_header(username: str, password: str) -> str:
    raw = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {raw}"


def _result_text(result: Any) -> str:
    """Flatten an MCP CallToolResult's content blocks into a single string."""
    parts: list[str] = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(text)
    return "\n".join(parts)


async def _mcp_connect(auth_header: str):
    """Open an authenticated MCP session. Server is stateless, so one per op."""
    headers = {"Authorization": auth_header}
    # streamablehttp_client manages its own httpx client; TLS verification is its
    # default (verify=True). For local self-signed testing set ARIAOPS_MCP_URL to
    # http:// or front the server with a trusted cert.
    return streamablehttp_client(MCP_URL, headers=headers)


async def mcp_describe(auth_header: str) -> dict[str, Any]:
    """Verify the credential and return {role, instances, tools}.

    Raises on auth/connection failure so callers can deny login.
    """
    async with await _mcp_connect(auth_header) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            tools = listed.tools
            instances: dict[str, Any] = {}
            try:
                res = await session.call_tool("list_instances", {})
                instances = json.loads(_result_text(res) or "{}")
            except Exception:
                # list_instances may be unavailable; auth still proven by initialize.
                instances = {}
            return {"tools": tools, "instances": instances}


async def mcp_call(auth_header: str, name: str, arguments: dict[str, Any]) -> str:
    async with await _mcp_connect(auth_header) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool(name, arguments)
            return _result_text(res) or "(no content)"


# ─── auth callbacks ────────────────────────────────────────────────────────────
@cl.password_auth_callback
async def password_auth(username: str, password: str) -> cl.User | None:
    """Username/password → LDAP Basic auth, verified by the MCP server.

    We do NOT bind to LDAP here — the MCP server owns that. We simply forward the
    credentials as Basic auth and let the server's LDAP backend accept or reject
    them. A successful ``initialize`` means the bind + group mapping succeeded.
    """
    auth_header = _basic_header(username, password)
    try:
        info = await mcp_describe(auth_header)
    except Exception:
        # Wrong credentials, unmapped group, or server unreachable → deny.
        return None

    instances = info.get("instances", {})
    return cl.User(
        identifier=username,
        metadata={
            "provider": "ldap",
            "mcp_auth": auth_header,
            "role": instances.get("role", "unknown"),
            "instances": instances.get("instances", []),
        },
    )


@cl.oauth_callback
async def oauth_callback(
    provider_id: str,
    token: str,
    raw_user_data: dict[str, str],
    default_user: cl.User,
) -> cl.User | None:
    """OAuth login → forward the IdP access token to the MCP server as Bearer.

    ``token`` is the access token Chainlit obtained from the IdP (e.g. Keycloak).
    For this to authorize against the MCP server, BOTH must point at the same
    issuer and the token's audience/scopes must satisfy the MCP server's config
    (see CHAINLIT_AUTH.md → "Aligning the two sides").
    """
    auth_header = f"Bearer {token}"
    role = "unknown"
    try:
        info = await mcp_describe(auth_header)
        role = info.get("instances", {}).get("role", "unknown")
    except Exception:
        # The IdP login succeeded but the MCP server rejected the token. Allow the
        # session so the user sees a clear in-chat error rather than a blank deny.
        role = "token-not-accepted-by-mcp"

    default_user.metadata = {
        **(default_user.metadata or {}),
        "provider": provider_id,
        "mcp_auth": auth_header,
        "role": role,
    }
    return default_user


# ─── chat lifecycle ────────────────────────────────────────────────────────────
@cl.on_chat_start
async def on_chat_start() -> None:
    user = cl.user_session.get("user")
    meta = (user.metadata if user else {}) or {}
    auth_header = meta.get("mcp_auth")

    if not auth_header:
        await cl.Message(content="⚠️ No MCP credential on this session. Re-login.").send()
        return

    cl.user_session.set("mcp_auth", auth_header)
    cl.user_session.set("history", [{"role": "system", "content": SYSTEM_PROMPT}])

    try:
        info = await mcp_describe(auth_header)
    except Exception as exc:
        await cl.Message(
            content=f"❌ Could not reach the MCP server at `{MCP_URL}`.\n\n```\n{exc}\n```"
        ).send()
        return

    tools = info["tools"]
    cl.user_session.set("mcp_tools", tools)
    cl.user_session.set(
        "openai_tools",
        [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": (t.description or "")[:1024],
                    "parameters": t.inputSchema or {"type": "object", "properties": {}},
                },
            }
            for t in tools
        ],
    )

    instances = info.get("instances", {})
    role = instances.get("role", meta.get("role", "unknown"))
    accessible = instances.get("instances", [])
    inst_lines = (
        "\n".join(f"- `{i.get('id')}` ({i.get('country', '—')})" for i in accessible)
        if accessible
        else "_(none reported)_"
    )

    mode = "LLM agent" if _llm_enabled() else "direct tool-call"
    await cl.Message(
        content=(
            f"✅ **Authenticated as `{meta.get('provider', '?')}` user "
            f"`{user.identifier}`**\n\n"
            f"- **Role (server-resolved):** `{role}`\n"
            f"- **Accessible instances:**\n{inst_lines}\n"
            f"- **MCP tools available:** {len(tools)}\n"
            f"- **Chat mode:** {mode}\n\n"
            + (
                "Ask a question about your environment.\n"
                if _llm_enabled()
                else "No LLM gateway configured — call a tool directly, e.g. "
                "`list_instances {}` or `get_capacity_overview {\"instance\":\"us\"}`.\n"
            )
        )
    ).send()


# ─── LLM gateway ───────────────────────────────────────────────────────────────
async def _llm_chat(messages: list[dict], tools: list[dict]) -> dict:
    payload: dict[str, Any] = {"model": LLM_MODEL, "messages": messages, "max_tokens": 2048}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    async with httpx.AsyncClient(verify=LLM_VERIFY_TLS, timeout=180) as client:
        try:
            r = await client.post(
                f"{LLM_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {LLM_TOKEN}"},
                json=payload,
            )
            r.raise_for_status()
            return r.json()
        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:300]}"}
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}


async def _agentic_turn(user_text: str) -> None:
    auth_header = cl.user_session.get("mcp_auth")
    history: list[dict] = cl.user_session.get("history")
    tools: list[dict] = cl.user_session.get("openai_tools", [])
    history.append({"role": "user", "content": user_text})

    for _ in range(MAX_TOOL_ROUNDS):
        body = await _llm_chat(history, tools)
        if "error" in body:
            await cl.Message(content=f"**LLM error:** {body['error']}").send()
            return

        message = body.get("choices", [{}])[0].get("message", {})
        history.append(message)
        tool_calls = message.get("tool_calls") or []

        if not tool_calls:
            await cl.Message(content=message.get("content") or "_(empty response)_").send()
            return

        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}

            async with cl.Step(name=name, type="tool") as step:
                step.input = args
                try:
                    result = await mcp_call(auth_header, name, args)
                except Exception as exc:  # noqa: BLE001
                    result = json.dumps({"error": str(exc)})
                step.output = result[:2000]

            history.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "name": name,
                    "content": result[:MAX_TOOL_RESULT_CHARS],
                }
            )

    await cl.Message(content="Reached the tool-call limit without a final answer.").send()


async def _direct_tool_turn(user_text: str) -> None:
    """Fallback when no LLM is configured: '<tool_name> {json-args}'."""
    auth_header = cl.user_session.get("mcp_auth")
    parts = user_text.strip().split(maxsplit=1)
    name = parts[0]
    try:
        args = json.loads(parts[1]) if len(parts) > 1 else {}
    except json.JSONDecodeError:
        await cl.Message(content="Args must be valid JSON, e.g. `list_alerts {\"instance\":\"us\"}`.").send()
        return

    async with cl.Step(name=name, type="tool") as step:
        step.input = args
        try:
            result = await mcp_call(auth_header, name, args)
        except Exception as exc:  # noqa: BLE001
            result = json.dumps({"error": str(exc)})
        step.output = result[:4000]

    await cl.Message(content=f"```json\n{result[:4000]}\n```").send()


@cl.on_message
async def on_message(msg: cl.Message) -> None:
    if not cl.user_session.get("mcp_auth"):
        await cl.Message(content="Not authenticated — please re-login.").send()
        return
    if _llm_enabled():
        await _agentic_turn(msg.content)
    else:
        await _direct_tool_turn(msg.content)
