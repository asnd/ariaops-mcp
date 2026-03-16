"""Tests for MCP interaction demo script."""

from __future__ import annotations

import io
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from ariaops_mcp.demo_mcp_interaction import VCENTER_QUERY, resolve_runtime_env, run_demo


def test_resolve_runtime_env_prompts_for_missing_values():
    prompts: list[str] = []

    def fake_input(prompt: str) -> str:
        prompts.append(prompt)
        responses = {"Enter ARIAOPS_HOST: ": "vrops.example.local", "Enter ARIAOPS_USERNAME: ": "admin"}
        if prompt not in responses:
            raise AssertionError(f"Unexpected prompt: {prompt}")
        return responses[prompt]

    def fake_secret_input(prompt: str) -> str:
        prompts.append(prompt)
        return "super-secret"

    env = resolve_runtime_env(
        {"ARIAOPS_AUTH_SOURCE": "local"},
        input_fn=fake_input,
        secret_input_fn=fake_secret_input,
        stdin_isatty=True,
    )

    assert env["ARIAOPS_HOST"] == "vrops.example.local"
    assert env["ARIAOPS_USERNAME"] == "admin"
    assert env["ARIAOPS_PASSWORD"] == "super-secret"
    assert env["ARIAOPS_TRANSPORT"] == "stdio"
    assert prompts == ["Enter ARIAOPS_HOST: ", "Enter ARIAOPS_USERNAME: ", "Enter ARIAOPS_PASSWORD: "]


def test_resolve_runtime_env_non_interactive_missing_values_raises():
    with pytest.raises(
        RuntimeError,
        match="Missing required environment variable\\(s\\): ARIAOPS_HOST, ARIAOPS_USERNAME",
    ):
        resolve_runtime_env({"ARIAOPS_PASSWORD": "x"}, stdin_isatty=False)


@pytest.mark.asyncio
async def test_run_demo_initializes_and_calls_vcenter_inventory():
    call_log: list[tuple[str, dict]] = []

    class FakeSession:
        async def initialize(self):
            return SimpleNamespace()

        async def list_tools(self, cursor=None, *, params=None):
            return SimpleNamespace(
                tools=[
                    SimpleNamespace(name="list_resources"),
                    SimpleNamespace(name="get_version"),
                ]
            )

        async def list_resources(self, cursor=None, *, params=None):
            return SimpleNamespace(resources=[SimpleNamespace(uri="ariaops://version")])

        async def call_tool(self, name, arguments=None, read_timeout_seconds=None, **kwargs):
            call_log.append((name, arguments or {}))
            payload = {"resourceList": [{"name": "vcsa-01"}, {"name": "vcsa-02"}]}
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text=json.dumps(payload))]
            )

    @asynccontextmanager
    async def fake_factory(env):
        assert env["ARIAOPS_HOST"] == "vrops.example.local"
        assert env["ARIAOPS_USERNAME"] == "admin"
        assert env["ARIAOPS_PASSWORD"] == "secret"
        yield FakeSession()

    output = io.StringIO()
    result = await run_demo(
        {"ARIAOPS_HOST": "vrops.example.local", "ARIAOPS_USERNAME": "admin", "ARIAOPS_PASSWORD": "secret"},
        session_factory=fake_factory,
        output=output,
    )

    assert call_log == [("list_resources", VCENTER_QUERY)]
    assert result["tools"] == ["list_resources", "get_version"]
    assert result["resources"] == ["ariaops://version"]
    assert result["vcenters"]["resourceList"] == [{"name": "vcsa-01"}, {"name": "vcsa-02"}]
    assert "MCP initialized successfully." in output.getvalue()
