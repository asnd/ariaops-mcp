"""Minimal MCP client demo without an LLM.

This script launches the local ariaops-mcp server over stdio, performs MCP
initialization, discovers tools/resources, and runs a tool call to list
vCenters from inventory.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from getpass import getpass
from typing import Any, Protocol, TextIO

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

REQUIRED_ENV_VARS = ("ARIAOPS_HOST", "ARIAOPS_USERNAME", "ARIAOPS_PASSWORD")
VCENTER_QUERY = {"adapterKind": "VMWARE", "resourceKind": "VirtualCenter", "page": 0, "pageSize": 200}


class MCPSession(Protocol):
    async def initialize(self) -> Any: ...

    async def list_tools(self, cursor: str | None = None, *, params: Any | None = None) -> Any: ...

    async def list_resources(self, cursor: str | None = None, *, params: Any | None = None) -> Any: ...

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None, read_timeout_seconds: Any | None = None, **kwargs: Any
    ) -> Any: ...


class MCPTextContent(Protocol):
    type: str
    text: str


class MCPCallResult(Protocol):
    content: Sequence[MCPTextContent]


def resolve_runtime_env(
    env: Mapping[str, str] | None = None,
    *,
    input_fn: Callable[[str], str] = input,
    secret_input_fn: Callable[[str], str] = getpass,
    stdin_isatty: bool | None = None,
) -> dict[str, str]:
    values = dict(os.environ if env is None else env)
    missing = [key for key in REQUIRED_ENV_VARS if not values.get(key, "").strip()]
    if missing:
        interactive = sys.stdin.isatty() if stdin_isatty is None else stdin_isatty
        if not interactive:
            missing_str = ", ".join(missing)
            raise RuntimeError(
                f"Missing required environment variable(s): {missing_str}. "
                "Set them in your environment or run this script in an interactive terminal."
            )

        for key in missing:
            prompt = f"Enter {key}: "
            entered = secret_input_fn(prompt) if key == "ARIAOPS_PASSWORD" else input_fn(prompt)
            entered = entered.strip()
            if not entered:
                raise RuntimeError(f"{key} is required and cannot be empty.")
            values[key] = entered

    values.setdefault("ARIAOPS_TRANSPORT", "stdio")
    return values


def _text_payload(call_result: MCPCallResult | Any) -> str:
    for item in getattr(call_result, "content", []):
        if getattr(item, "type", "") == "text":
            return str(getattr(item, "text", ""))
    return ""


def _parse_json(text: str) -> Any:
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


@asynccontextmanager
async def _default_session_factory(env: dict[str, str]):
    server_params = StdioServerParameters(command=sys.executable, args=["-m", "ariaops_mcp"], env=env)
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            yield session


async def run_demo(
    env: Mapping[str, str] | None = None,
    *,
    session_factory: Callable[[dict[str, str]], Any] = _default_session_factory,
    output: TextIO = sys.stdout,
) -> dict[str, Any]:
    runtime_env = resolve_runtime_env(env)
    async with session_factory(runtime_env) as session:
        await session.initialize()
        tools_result = await session.list_tools()
        resources_result = await session.list_resources()
        vcenter_result = await session.call_tool("list_resources", VCENTER_QUERY)

    tools = [tool.name for tool in getattr(tools_result, "tools", [])]
    resources = [str(resource.uri) for resource in getattr(resources_result, "resources", [])]
    vcenters = _parse_json(_text_payload(vcenter_result))

    print("MCP initialized successfully.", file=output)
    print(f"Discovered {len(tools)} tools.", file=output)
    print(f"Discovered {len(resources)} resources.", file=output)
    print("vCenter inventory query completed via list_resources.", file=output)
    print(json.dumps(vcenters, indent=2), file=output)

    return {
        "tools": tools,
        "resources": resources,
        "vcenters": vcenters,
    }


def main() -> None:
    asyncio.run(run_demo())


if __name__ == "__main__":
    main()
