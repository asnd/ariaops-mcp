# Available MCP Tools

This server exposes the following read-only MCP tools for VMware Aria Operations.

## Resources (9 tools)

- `list_resources` - List/search VMs, hosts, clusters, datastores, and other resources (`page`, `pageSize`).
- `get_resource` - Get details of a single resource by ID.
- `query_resources` - Run an advanced resource query with multiple filters (`page`, `pageSize`).
- `get_resource_properties` - Get configuration properties for a resource.
- `get_resource_relationships` - Get parent/child relationships for a resource.
- `list_adapter_kinds` - List all registered adapter kinds.
- `list_resource_kinds` - List resource kinds for a given adapter kind.
- `list_resource_groups` - List custom and dynamic resource groups (`page`, `pageSize`).
- `get_resource_group_members` - List members of a resource group.

## Alerts (7 tools)

- `list_alerts` - List active alerts with optional status, criticality, or resource filters (`page`, `pageSize`).
- `get_alert` - Get details of a single alert by ID.
- `query_alerts` - Run an advanced alert query with multiple filters (`page`, `pageSize`).
- `get_alert_notes` - Get notes and comments for an alert.
- `list_alert_definitions` - List alert definition templates (`page`, `pageSize`).
- `get_alert_definition` - Get details of a single alert definition by ID.
- `get_contributing_symptoms` - Get symptom definitions contributing to active alerts.

## Metrics / Stats (7 tools)

- `get_resource_stats` - Get historical stats or metrics for a resource.
- `get_latest_stats` - Get the latest stat values for a resource.
- `query_stats` - Run a bulk stats query across multiple resources.
- `query_latest_stats` - Run a bulk latest-stats query across multiple resources.
- `get_stat_keys` - List available stat keys for a resource.
- `get_top_n_stats` - Get Top-N stat values for a resource.
- `list_properties_latest` - Get latest property values for multiple resources.

## Capacity (3 tools)

- `get_capacity_remaining` - Get remaining capacity stats for a resource.
- `get_capacity_overview` - Get a capacity overview across resources of a given kind (`page`, `pageSize`; default `pageSize=20`).
- `list_policies` - List capacity and alerting policies.

## Reports (6 tools)

- `list_report_definitions` - List available report templates (`page`, `pageSize`).
- `get_report_definition` - Get details of a report definition by ID.
- `list_reports` - List generated reports (`page`, `pageSize`).
- `get_report` - Get metadata for a generated report.
- `download_report` - Download a generated report as base64 content.
- `list_report_schedules` - List schedules for a report definition.

## Discovery (5 tools)

- `get_version` - Get current Aria Operations version and deployment info.
- `list_collectors` - List registered data collectors.
- `list_symptoms` - List symptom definitions (`page`, `pageSize`).
- `list_recommendations` - List recommendations.
- `list_supermetrics` - List super metrics.

## Total

- 37 read-only MCP tools

## Pagination & Truncation

- Paginated tools accept `page` (default `0`) and `pageSize` (default `50`, max `200`), except `get_capacity_overview` which defaults to `pageSize=20`.
- To keep MCP responses transport-safe, list responses are truncated to 50 items.
- Truncated responses include `_truncated` and `_truncatedAt`.
- When upstream `pageInfo.totalCount` is available, responses also include `_totalCount`, `_nextPage`, and `_hint` to guide fetching additional pages.
