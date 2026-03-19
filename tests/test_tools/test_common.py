"""Tests for shared response-shaping utilities in _common.py."""


from ariaops_mcp.tools._common import (
    MAX_LIST_ITEMS,
    SUMMARY_FIELDS,
    apply_response_shaping,
    filter_fields,
    summarize_items,
    truncate_list_response,
)

# ---------------------------------------------------------------------------
# Strategy 1 – Enhanced pagination with navigation hints
# ---------------------------------------------------------------------------


class TestTruncateListResponse:
    def test_no_truncation_when_under_limit(self):
        data = {
            "pageInfo": {"totalCount": 3, "page": 0, "pageSize": 50},
            "resourceList": [{"id": "1"}, {"id": "2"}, {"id": "3"}],
        }
        result = truncate_list_response(data, "resourceList", page=0, page_size=50)
        assert len(result["resourceList"]) == 3
        assert "_truncated" not in result

    def test_truncation_when_over_limit(self):
        items = [{"id": str(i)} for i in range(MAX_LIST_ITEMS + 10)]
        data = {
            "pageInfo": {"totalCount": 200, "page": 0, "pageSize": 100},
            "resourceList": items,
        }
        result = truncate_list_response(data, "resourceList", page=0, page_size=100)
        assert len(result["resourceList"]) == MAX_LIST_ITEMS
        assert result["_truncated"] is True
        assert result["_truncatedAt"] == MAX_LIST_ITEMS

    def test_next_page_hint_when_more_data(self):
        data = {
            "pageInfo": {"totalCount": 200, "page": 0, "pageSize": 50},
            "resourceList": [{"id": str(i)} for i in range(50)],
        }
        result = truncate_list_response(data, "resourceList", page=0, page_size=50)
        assert result["_totalCount"] == 200
        assert result["_nextPage"] == 1
        assert "page=1" in result["_hint"]

    def test_no_next_page_when_last_page(self):
        data = {
            "pageInfo": {"totalCount": 10, "page": 0, "pageSize": 50},
            "resourceList": [{"id": str(i)} for i in range(10)],
        }
        result = truncate_list_response(data, "resourceList", page=0, page_size=50)
        assert result["_totalCount"] == 10
        assert "_nextPage" not in result
        assert "_hint" not in result

    def test_next_page_hint_on_second_page(self):
        data = {
            "pageInfo": {"totalCount": 150, "page": 1, "pageSize": 50},
            "resourceList": [{"id": str(i)} for i in range(50)],
        }
        result = truncate_list_response(data, "resourceList", page=1, page_size=50)
        assert result["_nextPage"] == 2
        assert "page=2" in result["_hint"]

    def test_no_page_info_skips_hints(self):
        data = {"resourceList": [{"id": "1"}]}
        result = truncate_list_response(data, "resourceList", page=0, page_size=50)
        assert "_totalCount" not in result
        assert "_nextPage" not in result

    def test_default_page_params(self):
        data = {
            "pageInfo": {"totalCount": 100, "page": 0, "pageSize": 50},
            "resourceList": [{"id": str(i)} for i in range(50)],
        }
        result = truncate_list_response(data, "resourceList")
        assert result["_totalCount"] == 100
        assert result["_nextPage"] == 1


# ---------------------------------------------------------------------------
# Strategy 2 – Field filtering
# ---------------------------------------------------------------------------


class TestFilterFields:
    def test_returns_only_requested_fields(self):
        items = [
            {"id": "1", "name": "foo", "status": "ACTIVE", "extra": "data"},
            {"id": "2", "name": "bar", "status": "CANCELLED", "extra": "more"},
        ]
        result = filter_fields(items, ["id", "name"])
        assert result == [{"id": "1", "name": "foo"}, {"id": "2", "name": "bar"}]

    def test_empty_fields_returns_unchanged(self):
        items = [{"id": "1", "name": "foo"}]
        result = filter_fields(items, [])
        assert result == items

    def test_missing_fields_silently_omitted(self):
        items = [{"id": "1", "name": "foo"}]
        result = filter_fields(items, ["id", "nonexistent"])
        assert result == [{"id": "1"}]

    def test_empty_list_returns_empty(self):
        assert filter_fields([], ["id"]) == []

    def test_non_dict_items_skipped(self):
        items = [{"id": "1"}, "not-a-dict", {"id": "2"}]
        result = filter_fields(items, ["id"])  # type: ignore[arg-type]
        assert result == [{"id": "1"}, {"id": "2"}]


# ---------------------------------------------------------------------------
# Strategy 3 – Summary mode
# ---------------------------------------------------------------------------


class TestSummarizeItems:
    def test_summarize_resources(self):
        items = [
            {
                "identifier": "vm-001",
                "resourceKey": {"name": "TestVM"},
                "resourceStatusStates": [],
                "badges": {},
                "links": [],
            }
        ]
        result = summarize_items(items, "resourceList")
        assert result == [{"identifier": "vm-001", "resourceKey": {"name": "TestVM"}}]

    def test_summarize_alerts(self):
        items = [
            {
                "id": "a1",
                "alertDefinitionName": "CPU",
                "status": "ACTIVE",
                "criticality": "CRITICAL",
                "resourceId": "r1",
                "startTimeUTC": 1234,
                "updateTimeUTC": 5678,
                "extra": "data",
            }
        ]
        result = summarize_items(items, "alerts")
        assert result == [
            {
                "id": "a1",
                "alertDefinitionName": "CPU",
                "status": "ACTIVE",
                "criticality": "CRITICAL",
                "resourceId": "r1",
            }
        ]

    def test_unknown_list_key_returns_unchanged(self):
        items = [{"id": "1", "extra": "data"}]
        result = summarize_items(items, "unknownKey")
        assert result == items

    def test_all_summary_fields_defined(self):
        expected_keys = {
            "resourceList", "alerts", "alertDefinitions", "groups",
            "resourceGroups", "reportDefinitions", "reports",
            "symptomDefinitions", "recommendations",
        }
        assert set(SUMMARY_FIELDS.keys()) == expected_keys


# ---------------------------------------------------------------------------
# apply_response_shaping – combined
# ---------------------------------------------------------------------------


class TestApplyResponseShaping:
    def test_fields_takes_precedence_over_summary(self):
        data = {
            "resourceList": [
                {"identifier": "vm-001", "resourceKey": {"name": "VM"}, "extra": "data"}
            ]
        }
        result = apply_response_shaping(
            data, "resourceList", fields=["identifier"], summary_only=True,
        )
        assert result["resourceList"] == [{"identifier": "vm-001"}]

    def test_summary_only_mode(self):
        data = {
            "alerts": [
                {
                    "id": "a1",
                    "alertDefinitionName": "CPU",
                    "status": "ACTIVE",
                    "criticality": "CRITICAL",
                    "resourceId": "r1",
                    "extra": "verbose",
                }
            ]
        }
        result = apply_response_shaping(data, "alerts", summary_only=True)
        assert "extra" not in result["alerts"][0]
        assert result["alerts"][0]["id"] == "a1"

    def test_no_shaping_by_default(self):
        data = {"resourceList": [{"identifier": "vm-001", "extra": "data"}]}
        result = apply_response_shaping(data, "resourceList")
        assert result["resourceList"] == [{"identifier": "vm-001", "extra": "data"}]

    def test_missing_list_key_returns_unchanged(self):
        data = {"other": "data"}
        result = apply_response_shaping(data, "resourceList", fields=["id"])
        assert result == {"other": "data"}

    def test_non_list_value_returns_unchanged(self):
        data = {"resourceList": "not-a-list"}
        result = apply_response_shaping(data, "resourceList", fields=["id"])
        assert result == {"resourceList": "not-a-list"}
