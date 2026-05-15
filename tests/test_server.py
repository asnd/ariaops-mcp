"""Tests for MCP server creation and behavior."""

import json

import httpx
import pytest
import respx
from mcp.types import (
    CallToolRequest,
    GetPromptRequest,
    ListPromptsRequest,
    ListResourcesRequest,
    ListToolsRequest,
    ReadResourceRequest,
)

from ariaops_mcp.server import create_server
from tests.conftest import TOKEN_RESPONSE

BASE = "https://vrops.test.local/suite-api/api"


@pytest.mark.asyncio
async def test_list_tools_readonly(mock_env):
    server = create_server()
    result = await server.request_handlers[ListToolsRequest](
        ListToolsRequest(method="tools/list", params=None)
    )
    tools = result.root.tools
    tool_names = {t.name for t in tools}
    assert "list_resources" in tool_names
    assert "list_alerts" in tool_names
    assert "get_resource_stats" in tool_names
    assert "list_report_definitions" in tool_names
    assert "modify_alerts" not in tool_names
    assert "delete_resources" not in tool_names


@pytest.mark.asyncio
async def test_list_resources(mock_env):
    server = create_server()
    result = await server.request_handlers[ListResourcesRequest](
        ListResourcesRequest(method="resources/list", params=None)
    )
    resources = result.root.resources
    uris = {str(r.uri) for r in resources}
    assert "ariaops://version" in uris
    assert "ariaops://adapter-kinds" in uris


@pytest.mark.asyncio
async def test_read_resource_version(mock_env):
    version_data = {"releaseName": "8.18.0", "buildNumber": "12345678"}
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(
            return_value=httpx.Response(200, json=TOKEN_RESPONSE)
        )
        respx.get(f"{BASE}/versions/current").mock(
            return_value=httpx.Response(200, json=version_data)
        )

        server = create_server()
        result = await server.request_handlers[ReadResourceRequest](
            ReadResourceRequest(method="resources/read", params={"uri": "ariaops://version"})
        )

        contents = result.root.contents
        assert len(contents) == 1
        data = json.loads(contents[0].text)
        assert data["releaseName"] == "8.18.0"


@pytest.mark.asyncio
async def test_read_resource_adapter_kinds(mock_env):
    adapter_data = {"adapterKindList": [{"key": "VMWARE", "name": "VMware Adapter"}]}
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(
            return_value=httpx.Response(200, json=TOKEN_RESPONSE)
        )
        respx.get(f"{BASE}/adapterkinds").mock(
            return_value=httpx.Response(200, json=adapter_data)
        )

        server = create_server()
        result = await server.request_handlers[ReadResourceRequest](
            ReadResourceRequest(
                method="resources/read", params={"uri": "ariaops://adapter-kinds"}
            )
        )

        contents = result.root.contents
        assert len(contents) == 1
        data = json.loads(contents[0].text)
        assert data["adapterKindList"][0]["key"] == "VMWARE"


@pytest.mark.asyncio
async def test_read_resource_error(mock_env):
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(
            return_value=httpx.Response(200, json=TOKEN_RESPONSE)
        )
        respx.get(f"{BASE}/versions/current").mock(
            return_value=httpx.Response(503, json={"message": "down"})
        )

        server = create_server()
        result = await server.request_handlers[ReadResourceRequest](
            ReadResourceRequest(method="resources/read", params={"uri": "ariaops://version"})
        )

        contents = result.root.contents
        assert len(contents) == 1
        data = json.loads(contents[0].text)
        assert "error" in data


@pytest.mark.asyncio
async def test_read_resource_unknown_uri(mock_env):
    server = create_server()
    with pytest.raises(ValueError, match="Unknown resource URI"):
        await server.request_handlers[ReadResourceRequest](
            ReadResourceRequest(method="resources/read", params={"uri": "ariaops://unknown"})
        )


@pytest.mark.asyncio
async def test_call_tool_unknown(mock_env):
    server = create_server()
    result = await server.request_handlers[CallToolRequest](
        CallToolRequest(
            method="tools/call", params={"name": "nonexistent_tool", "arguments": {}}
        )
    )
    assert result.root.isError is True
    assert "Unknown tool" in result.root.content[0].text


@pytest.mark.asyncio
async def test_call_tool_get_version(mock_env):
    version_data = {"releaseName": "8.18.0", "buildNumber": "12345678"}
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(
            return_value=httpx.Response(200, json=TOKEN_RESPONSE)
        )
        respx.get(f"{BASE}/versions/current").mock(
            return_value=httpx.Response(200, json=version_data)
        )

        server = create_server()
        result = await server.request_handlers[CallToolRequest](
            CallToolRequest(
                method="tools/call", params={"name": "get_version", "arguments": {}}
            )
        )

        content = result.root.content
        assert len(content) == 1
        data = json.loads(content[0].text)
        assert data["releaseName"] == "8.18.0"


# ── Skill-related integration tests (#18) ──────────────────────────────────

SKILL_DIR_VAR = "ARIAOPS_SKILLS_DIR"


@pytest.mark.asyncio
async def test_list_skills_when_none_configured(mock_env):
    """When ARIAOPS_SKILLS_DIR is not set, skill tools should not appear."""
    server = create_server()
    result = await server.request_handlers[ListToolsRequest](
        ListToolsRequest(method="tools/list", params=None)
    )
    tool_names = {t.name for t in result.root.tools}
    assert "list_skills" not in tool_names
    assert "execute_skill" not in tool_names


@pytest.mark.asyncio
async def test_list_skills_with_directory(mock_env, monkeypatch, tmp_path):
    """When ARIAOPS_SKILLS_DIR is set, skill tools should appear."""
    skill_content = "---\nname: test-skill\ndescription: A test skill\n---\n\nBody"
    (tmp_path / "skill.md").write_text(skill_content)
    monkeypatch.setenv(SKILL_DIR_VAR, str(tmp_path))

    server = create_server()
    result = await server.request_handlers[ListToolsRequest](
        ListToolsRequest(method="tools/list", params=None)
    )
    tool_names = {t.name for t in result.root.tools}
    assert "list_skills" in tool_names
    assert "execute_skill" in tool_names
    assert "reload_skills" in tool_names


@pytest.mark.asyncio
async def test_call_list_skills(mock_env, monkeypatch, tmp_path):
    """Calling the list_skills tool should return skill metadata."""
    skill_content = "---\nname: test-skill\ndescription: A test skill\n---\n\nBody"
    (tmp_path / "skill.md").write_text(skill_content)
    monkeypatch.setenv(SKILL_DIR_VAR, str(tmp_path))

    server = create_server()
    result = await server.request_handlers[CallToolRequest](
        CallToolRequest(
            method="tools/call", params={"name": "list_skills", "arguments": {}}
        )
    )
    data = json.loads(result.root.content[0].text)
    assert len(data) == 1
    assert data[0]["name"] == "test-skill"
    assert data[0]["description"] == "A test skill"


@pytest.mark.asyncio
async def test_call_execute_skill_not_found(mock_env, monkeypatch, tmp_path):
    """Calling execute_skill with a nonexistent name should error."""
    monkeypatch.setenv(SKILL_DIR_VAR, str(tmp_path))

    server = create_server()
    result = await server.request_handlers[CallToolRequest](
        CallToolRequest(
            method="tools/call",
            params={"name": "execute_skill", "arguments": {"name": "nonexistent"}},
        )
    )
    data = json.loads(result.root.content[0].text)
    assert "Skill not found" in data["error"]


@pytest.mark.asyncio
async def test_call_execute_skill_no_orchestration(mock_env, monkeypatch, tmp_path):
    """Calling execute_skill on a non-orchestration skill should error."""
    skill_content = "---\nname: info-only\ndescription: Info only\n---\n\n# Info"
    (tmp_path / "info.md").write_text(skill_content)
    monkeypatch.setenv(SKILL_DIR_VAR, str(tmp_path))

    server = create_server()
    result = await server.request_handlers[CallToolRequest](
        CallToolRequest(
            method="tools/call",
            params={"name": "execute_skill", "arguments": {"name": "info-only"}},
        )
    )
    data = json.loads(result.root.content[0].text)
    assert "does not support orchestration" in data["error"]


@pytest.mark.asyncio
async def test_call_reload_skills(mock_env, monkeypatch, tmp_path):
    """Calling reload_skills should succeed when directory is set."""
    skill_content = "---\nname: one\ndescription: One\n---\n\nBody"
    (tmp_path / "one.md").write_text(skill_content)
    monkeypatch.setenv(SKILL_DIR_VAR, str(tmp_path))

    server = create_server()
    result = await server.request_handlers[CallToolRequest](
        CallToolRequest(
            method="tools/call", params={"name": "reload_skills", "arguments": {}}
        )
    )
    data = json.loads(result.root.content[0].text)
    assert data["status"] == "ok"
    assert data["skills_loaded"] == 1


@pytest.mark.asyncio
async def test_list_prompts(mock_env, monkeypatch, tmp_path):
    """List prompts should return skills as MCP prompts."""
    skill_content = (
        "---\nname: greet\ndescription: Greeting skill\n"
        "arguments:\n  - name: name\n    description: Person name\n    required: true\n"
        "---\n\nHello {{name}}!"
    )
    (tmp_path / "greet.md").write_text(skill_content)
    monkeypatch.setenv(SKILL_DIR_VAR, str(tmp_path))

    server = create_server()
    result = await server.request_handlers[ListPromptsRequest](
        ListPromptsRequest(method="prompts/list", params=None)
    )
    prompts = result.root.prompts
    assert len(prompts) == 1
    assert prompts[0].name == "greet"
    assert len(prompts[0].arguments) == 1
    assert prompts[0].arguments[0].name == "name"


@pytest.mark.asyncio
async def test_get_prompt(mock_env, monkeypatch, tmp_path):
    """Get prompt should render skill body with template substitution."""
    skill_content = "---\nname: greet\ndescription: Greeting\n---\n\nHello {{name}}!"
    (tmp_path / "greet.md").write_text(skill_content)
    monkeypatch.setenv(SKILL_DIR_VAR, str(tmp_path))

    server = create_server()
    result = await server.request_handlers[GetPromptRequest](
        GetPromptRequest(
            method="prompts/get",
            params={"name": "greet", "arguments": {"name": "Alice"}},
        )
    )
    messages = result.root.messages
    assert len(messages) == 1
    assert "Hello Alice!" in messages[0].content.text


@pytest.mark.asyncio
async def test_get_prompt_not_found(mock_env, monkeypatch, tmp_path):
    """Get prompt with unknown name should raise ValueError."""
    monkeypatch.setenv(SKILL_DIR_VAR, str(tmp_path))

    server = create_server()
    with pytest.raises(ValueError, match="Skill not found"):
        await server.request_handlers[GetPromptRequest](
            GetPromptRequest(
                method="prompts/get",
                params={"name": "nonexistent", "arguments": {}},
            )
        )


@pytest.mark.asyncio
async def test_read_resource_skill(mock_env, monkeypatch, tmp_path):
    """Reading ariaops://skills/{name} should return the skill body."""
    skill_content = "---\nname: my-skill\ndescription: My skill\n---\n\n# My Skill\n\nBody content here"
    (tmp_path / "skill.md").write_text(skill_content)
    monkeypatch.setenv(SKILL_DIR_VAR, str(tmp_path))

    server = create_server()
    result = await server.request_handlers[ReadResourceRequest](
        ReadResourceRequest(
            method="resources/read", params={"uri": "ariaops://skills/my-skill"}
        )
    )
    contents = result.root.contents
    assert len(contents) == 1
    assert "# My Skill" in contents[0].text


@pytest.mark.asyncio
async def test_read_resource_skill_not_found(mock_env, monkeypatch, tmp_path):
    """Reading a nonexistent skill resource should raise ValueError."""
    monkeypatch.setenv(SKILL_DIR_VAR, str(tmp_path))

    server = create_server()
    with pytest.raises(ValueError, match="Skill not found"):
        await server.request_handlers[ReadResourceRequest](
            ReadResourceRequest(
                method="resources/read", params={"uri": "ariaops://skills/nonexistent"}
            )
        )


@pytest.mark.asyncio
async def test_skill_tool_rejected_when_not_configured(mock_env):
    """Calling a skill tool when skills_dir is not set should be treated as unknown tool."""
    server = create_server()
    result = await server.request_handlers[CallToolRequest](
        CallToolRequest(
            method="tools/call", params={"name": "list_skills", "arguments": {}}
        )
    )
    assert result.root.isError is True
    assert "Unknown tool" in result.root.content[0].text


@pytest.mark.asyncio
async def test_list_resources_includes_skills(mock_env, monkeypatch, tmp_path):
    """list_resources should include skill resources when skills are configured."""
    skill_content = "---\nname: my-skill\ndescription: My skill\n---\n\nBody"
    (tmp_path / "skill.md").write_text(skill_content)
    monkeypatch.setenv(SKILL_DIR_VAR, str(tmp_path))

    server = create_server()
    result = await server.request_handlers[ListResourcesRequest](
        ListResourcesRequest(method="resources/list", params=None)
    )
    uris = {str(r.uri) for r in result.root.resources}
    assert "ariaops://skills/my-skill" in uris


@pytest.mark.asyncio
async def test_execute_skill_orchestration_success(mock_env, monkeypatch, tmp_path):
    """execute_skill should run orchestration steps through the server."""
    skill_content = (
        "---\n"
        "name: chain-alert\n"
        "description: Chain alert to resource\n"
        "tools:\n  - get_version\n"
        "orchestration: true\n"
        "steps:\n"
        "  - tool: get_version\n"
        "    args_template: {}\n"
        "    output_key: version\n"
        "---\n\nOrchestrate"
    )
    (tmp_path / "chain.md").write_text(skill_content)
    monkeypatch.setenv(SKILL_DIR_VAR, str(tmp_path))

    version_data = {"releaseName": "8.18.0", "buildNumber": "12345678"}
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(
            return_value=httpx.Response(200, json=TOKEN_RESPONSE)
        )
        respx.get(f"{BASE}/versions/current").mock(
            return_value=httpx.Response(200, json=version_data)
        )

        server = create_server()
        result = await server.request_handlers[CallToolRequest](
            CallToolRequest(
                method="tools/call",
                params={"name": "execute_skill", "arguments": {"name": "chain-alert"}},
            )
        )
        data = json.loads(result.root.content[0].text)
        assert data["status"] == "completed"
        assert data["skill"] == "chain-alert"
        assert len(data["steps"]) == 1
        assert data["steps"][0]["status"] == "success"


@pytest.mark.asyncio
async def test_execute_skill_rejects_non_dict_arguments(mock_env, monkeypatch, tmp_path):
    """execute_skill should reject non-dict 'arguments' parameter."""
    from ariaops_mcp.server import _handle_execute_skill

    result = await _handle_execute_skill(
        {"name": "test-skill", "arguments": "not-a-dict"}, "test-cid"
    )
    data = json.loads(result)
    assert "must be an object" in data["error"]