# Available MCP Tools

This server exposes MCP tools for VMware Aria Operations.  Read-only tools are always active.
Write/mutating tools are exposed only when `ARIAOPS_ENABLE_WRITE_OPERATIONS=true`.

## Resources (9 read-only tools)

- `list_resources` - List/search VMs, hosts, clusters, datastores, and other resources.
- `get_resource` - Get details of a single resource by ID.
- `query_resources` - Run an advanced resource query with multiple filters.
- `get_resource_properties` - Get configuration properties for a resource.
- `get_resource_relationships` - Get parent/child relationships for a resource.
- `list_adapter_kinds` - List all registered adapter kinds.
- `list_resource_kinds` - List resource kinds for a given adapter kind.
- `list_resource_groups` - List custom and dynamic resource groups.
- `get_resource_group_members` - List members of a resource group.

## Alerts (7 tools)

- `list_alerts` - List active alerts with optional status, criticality, or resource filters.
- `get_alert` - Get details of a single alert by ID.
- `query_alerts` - Run an advanced alert query with multiple filters.
- `get_alert_notes` - Get notes and comments for an alert.
- `list_alert_definitions` - List alert definition templates.
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
- `get_capacity_overview` - Get a capacity overview across resources of a given kind.
- `list_policies` - List capacity and alerting policies.

## Reports (6 tools)

- `list_report_definitions` - List available report templates.
- `get_report_definition` - Get details of a report definition by ID.
- `list_reports` - List generated reports.
- `get_report` - Get metadata for a generated report.
- `download_report` - Download a generated report as base64 content.
- `list_report_schedules` - List schedules for a report definition.

## Discovery (5 read-only tools)

- `get_version` - Get current Aria Operations version and deployment info.
- `list_collectors` - List registered data collectors.
- `list_symptoms` - List symptom definitions.
- `list_recommendations` - List recommendations.
- `list_supermetrics` - List super metrics.

---

## Write Tools (17 tools — requires `ARIAOPS_ENABLE_WRITE_OPERATIONS=true`)

### Alert Write Operations (4 tools)

- `modify_alerts` - Bulk cancel, suspend, or acknowledge one or more alerts by ID.
- `add_alert_note` - Add a note/comment to an alert.
- `delete_alert_note` - Delete a specific note from an alert.
- `delete_canceled_alerts` - Delete canceled alerts matching given criteria.

### Resource Maintenance (2 tools)

- `mark_resources_maintained` - Put resources into maintenance mode (suppresses alerts).
- `unmark_resources_maintained` - Take resources out of maintenance mode.

### Maintenance Schedules (3 tools)

- `create_maintenance_schedule` - Create a maintenance schedule for one or more resources.
- `update_maintenance_schedule` - Update an existing maintenance schedule.
- `delete_maintenance_schedule` - Delete one or more maintenance schedules by ID.

### Report Write Operations (5 tools)

- `generate_report` - Generate (create) a report from a report definition for a given resource.
- `delete_report` - Delete a generated report by ID.
- `create_report_schedule` - Create a schedule to automatically generate a report.
- `update_report_schedule` - Update an existing report schedule.
- `delete_report_schedule` - Delete a report schedule.

### Resource Lifecycle (3 tools)

- `create_resource` - Create a new resource associated with a given adapter kind or adapter instance.
- `update_resource` - Update an existing resource's metadata.
- `delete_resources` - Delete one or more resources by ID (irreversible).

---

## Total

- **37 read-only tools** (always active)
- **17 write tools** (active when `ARIAOPS_ENABLE_WRITE_OPERATIONS=true`)
- **54 tools total**
