"""Tests for the opt-in Ansible inventory export tool."""

from __future__ import annotations

import pytest
import yaml

from ariaops_mcp.client import reset_client_override, set_client_override
from ariaops_mcp.config import clear_settings_cache
from ariaops_mcp.tools.ansible_inventory import tool_handlers


class _MockAriaOpsClient:
    def __init__(self) -> None:
        self.properties_requests: list[str] = []
        self.resources_by_kind = {
            "ClusterComputeResource": [
                {
                    "identifier": "cluster-1",
                    "resourceKey": {
                        "name": "Prod Cluster",
                        "adapterKindKey": "VMWARE",
                        "resourceKindKey": "ClusterComputeResource",
                        "resourceIdentifiers": [
                            {"identifierType": {"name": "primary_ip_address"}, "value": "10.0.0.10"},
                            {"identifierType": {"name": "moid"}, "value": "domain-c101"},
                        ],
                    },
                    "resourceStatusStates": ["DATA_RECEIVING"],
                    "resourceHealth": "GREEN",
                }
            ],
            "NsxtEdgeNode": [
                {
                    "identifier": "edge-1",
                    "resourceKey": {
                        "name": "edge-a",
                        "adapterKindKey": "NSXT",
                        "resourceKindKey": "NsxtEdgeNode",
                        "resourceIdentifiers": [
                            {"identifierType": {"name": "node_uuid"}, "value": "edge-uuid-1"}
                        ],
                    },
                    "resourceStatusStates": ["DATA_RECEIVING"],
                    "resourceHealth": "YELLOW",
                }
            ],
            "NsxtManagerNode": [
                {
                    "identifier": "manager-1",
                    "resourceKey": {
                        "name": "mgr-01",
                        "adapterKindKey": "NSXT",
                        "resourceKindKey": "NsxtManagerNode",
                        "resourceIdentifiers": [
                            {"identifierType": {"name": "node_uuid"}, "value": "manager-uuid-1"}
                        ],
                    },
                    "resourceStatusStates": ["DATA_RECEIVING"],
                    "resourceHealth": "GREEN",
                }
            ],
        }

    async def post(
        self,
        path: str,
        body: dict[str, list[str]],
        *,
        idempotent: bool = False,
        **params: int,
    ) -> dict[str, object]:
        assert path == "/resources/query"
        assert idempotent is True
        assert params["page"] == 0
        resource_kind = body["resourceKind"][0]
        resources = self.resources_by_kind[resource_kind]
        return {
            "pageInfo": {"totalCount": len(resources), "page": 0, "pageSize": params["pageSize"]},
            "resourceList": resources,
        }

    async def get(self, path: str, **params: object) -> dict[str, object]:
        assert not params
        self.properties_requests.append(path)
        if path == "/resources/edge-1/properties":
            return {"property": [{"name": "primary_ip_address", "value": "10.0.1.20"}]}
        if path == "/resources/manager-1/properties":
            return {"property": [{"name": "ip_address", "value": "10.0.2.30"}]}
        raise AssertionError(f"Unexpected GET path: {path}")

    async def close(self) -> None:
        return None


@pytest.fixture
def handlers(mock_env, monkeypatch):
    monkeypatch.setenv("ARIAOPS_ENABLE_WRITE_OPERATIONS", "true")
    clear_settings_cache()
    return tool_handlers()


@pytest.mark.asyncio
async def test_export_ansible_inventory_yaml_structure(handlers, tmp_path):
    client = _MockAriaOpsClient()
    token = set_client_override(client)

    try:
        output_path = tmp_path / "inventory" / "ariaops.yml"
        rendered = await handlers["export_ansible_inventory"]({"outputPath": str(output_path)})
    finally:
        reset_client_override(token)

    inventory = yaml.safe_load(rendered)
    children = inventory["all"]["children"]
    assert set(children) == {"clusters", "nsx_edges", "nsx_managers"}

    cluster_host = children["clusters"]["hosts"]["Prod_Cluster"]
    assert cluster_host["ansible_host"] == "10.0.0.10"
    assert cluster_host["ariaops_identifier"] == "cluster-1"
    assert cluster_host["ariaops_identity"]["moid"] == "domain-c101"
    assert cluster_host["ariaops_summary"]["resourceHealth"] == "GREEN"

    edge_host = children["nsx_edges"]["hosts"]["edge-a"]
    assert edge_host["ansible_host"] == "10.0.1.20"
    assert edge_host["ariaops_resource_kind"] == "NsxtEdgeNode"

    manager_host = children["nsx_managers"]["hosts"]["mgr-01"]
    assert manager_host["ansible_host"] == "10.0.2.30"
    assert manager_host["ariaops_identity"]["node_uuid"] == "manager-uuid-1"

    assert client.properties_requests == [
        "/resources/edge-1/properties",
        "/resources/manager-1/properties",
    ]
    assert output_path.read_text(encoding="utf-8") == rendered


def test_export_ansible_inventory_registry_opt_in(mock_env, monkeypatch):
    import ariaops_mcp.server as server_mod

    monkeypatch.delenv("ARIAOPS_ENABLE_WRITE_OPERATIONS", raising=False)
    clear_settings_cache()
    server_mod._tool_defs = None
    server_mod._tool_handlers = None
    defs, _ = server_mod._get_tool_registry()
    assert "export_ansible_inventory" not in {tool.name for tool in defs}

    monkeypatch.setenv("ARIAOPS_ENABLE_WRITE_OPERATIONS", "true")
    clear_settings_cache()
    server_mod._tool_defs = None
    server_mod._tool_handlers = None
    defs, _ = server_mod._get_tool_registry()
    assert "export_ansible_inventory" in {tool.name for tool in defs}
