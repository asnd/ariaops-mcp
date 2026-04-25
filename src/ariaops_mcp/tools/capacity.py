"""Capacity tools for Aria Operations (composite via stat keys)."""

import json
import logging
import statistics
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx
import mcp.types as types

from ariaops_mcp.client import get_client

logger = logging.getLogger(__name__)

# Capacity-related stat keys in Aria Operations
CAPACITY_STAT_KEYS = [
    "capacity|remainingCapacity",
    "capacity|timeRemaining",
    "capacity|badge|capacityRemaining",
    "cpu|capacity_contentionPct",
    "mem|host_usable",
    "diskspace|capacity",
    "diskspace|used",
]


def tool_definitions() -> list[types.Tool]:
    return [
        types.Tool(
            name="get_capacity_remaining",
            description=(
                "Get remaining capacity stats for a resource (cluster, host, or datastore). "
                "Returns CPU, memory, and storage capacity metrics."
            ),
            inputSchema={
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id": {"type": "string", "description": "Resource UUID (cluster, host, or datastore)"},
                },
            },
        ),
        types.Tool(
            name="get_capacity_overview",
            description=(
                "Get a capacity overview across all clusters in a datacenter or all resources "
                "of a given kind. Returns total, used, and remaining capacity."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "adapterKind": {"type": "string", "default": "VMWARE"},
                    "resourceKind": {
                        "type": "string",
                        "default": "ClusterComputeResource",
                        "description": "e.g. ClusterComputeResource, Datastore, HostSystem",
                    },
                    "pageSize": {"type": "integer", "default": 20},
                },
            },
        ),
        types.Tool(
            name="list_policies",
            description="List capacity and alerting policies defined in Aria Operations.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="get_capacity_forecast",
            description=(
                "Forecast when a resource will reach capacity thresholds based on historical trends. "
                "Analyzes capacity metrics over time to predict resource exhaustion."
            ),
            inputSchema={
                "type": "object",
                "required": ["id", "metric", "days_ahead"],
                "properties": {
                    "id": {"type": "string", "description": "Resource UUID (cluster, host, or datastore)"},
                    "metric": {
                        "type": "string",
                        "description": "Metric to forecast (e.g., 'capacity|remainingCapacity', 'mem|host_usable')",
                    },
                    "days_ahead": {
                        "type": "integer",
                        "description": "Number of days to forecast ahead",
                        "minimum": 1,
                        "maximum": 365,
                    },
                    "history_days": {
                        "type": "integer",
                        "description": "Number of days of historical data to analyze",
                        "default": 30,
                        "minimum": 7,
                        "maximum": 365,
                    },
                },
            },
        ),
        types.Tool(
            name="get_trend_analysis",
            description=(
                "Analyze usage patterns and trends for a resource over time. "
                "Provides statistical analysis including growth rate, seasonality, and volatility."
            ),
            inputSchema={
                "type": "object",
                "required": ["id", "metric"],
                "properties": {
                    "id": {"type": "string", "description": "Resource UUID"},
                    "metric": {
                        "type": "string",
                        "description": "Metric to analyze (e.g., 'mem|host_usable', 'diskspace|used')",
                    },
                    "period_days": {
                        "type": "integer",
                        "description": "Analysis period in days",
                        "default": 30,
                        "minimum": 7,
                        "maximum": 365,
                    },
                },
            },
        ),
    ]


def tool_handlers() -> dict[str, Callable[[dict[str, Any]], Any]]:
    async def get_capacity_remaining(args: dict) -> str:
        if not args.get("id"):
            return json.dumps({"error": "Missing required argument: id"})
        client = get_client()
        results: dict[str, Any] = {"resourceId": args["id"], "capacityStats": {}}
        rid = quote(args["id"], safe="")
        for stat_key in CAPACITY_STAT_KEYS:
            try:
                data = await client.get(
                    f"/resources/{rid}/stats/latest",
                    statKey=stat_key,
                )
                stat_list = data.get("values", [])
                if stat_list:
                    results["capacityStats"][stat_key] = stat_list[0].get("data", [])
            except httpx.HTTPError as exc:
                logger.warning(
                    "Failed to fetch capacity stat '%s' for resource '%s': %s",
                    stat_key,
                    args["id"],
                    exc,
                )
        return json.dumps(results, indent=2)

    async def get_capacity_overview(args: dict) -> str:
        try:
            client = get_client()
            adapter_kind = args.get("adapterKind", "VMWARE")
            resource_kind = args.get("resourceKind", "ClusterComputeResource")
            page_size = args.get("pageSize", 20)

            resources_data = await client.get(
                "/resources",
                adapterKind=adapter_kind,
                resourceKind=resource_kind,
                pageSize=page_size,
            )

            resource_ids = [r["identifier"] for r in resources_data.get("resourceList", [])]
            if not resource_ids:
                return json.dumps({"message": "No resources found", "resourceKind": resource_kind})

            body = {
                "resourceId": [{"resourceId": rid} for rid in resource_ids],
                "statKey": [{"key": k} for k in CAPACITY_STAT_KEYS],
            }
            stats_data = await client.post("/resources/stats/latest/query", body)

            return json.dumps(
                {
                    "resourceKind": resource_kind,
                    "resourceCount": len(resource_ids),
                    "capacityStats": stats_data,
                },
                indent=2,
            )
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": str(e), "status_code": e.response.status_code, "detail": e.response.text[:500]})
        except httpx.HTTPError as e:
            return json.dumps({"error": "Network error", "detail": str(e)})
        except Exception as e:
            return json.dumps({"error": "Unexpected error", "detail": str(e)})

    async def list_policies(args: dict) -> str:
        try:
            data = await get_client().get("/policies")
            return json.dumps(data, indent=2)
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": str(e), "status_code": e.response.status_code, "detail": e.response.text[:500]})
        except httpx.HTTPError as e:
            return json.dumps({"error": "Network error", "detail": str(e)})
        except Exception as e:
            return json.dumps({"error": "Unexpected error", "detail": str(e)})

    async def get_capacity_forecast(args: dict) -> str:
        if not args.get("id"):
            return json.dumps({"error": "Missing required argument: id"})
        if not args.get("metric"):
            return json.dumps({"error": "Missing required argument: metric"})
        if not args.get("days_ahead"):
            return json.dumps({"error": "Missing required argument: days_ahead"})
        
        resource_id = args["id"]
        metric = args["metric"]
        days_ahead = max(1, min(int(args.get("days_ahead", 30)), 365))
        history_days = max(7, min(int(args.get("history_days", 30)), 365))
        
        try:
            client = get_client()
            _ = quote(resource_id, safe="")
            
            # Get historical stats for the metric
            end_time = int(time.time() * 1000)
            start_time = end_time - (history_days * 24 * 60 * 60 * 1000)
            
            # Query historical data
            body = {
                "resourceId": [{"resourceId": resource_id}],
                "statKey": [{"key": metric}],
                "begin": start_time,
                "end": end_time,
            }
            
            stats_data = await client.post("/resources/stats/history/query", body)
            
            # Extract data points
            values = []
            # timestamps placeholder (unused)
            
            # Handle different response formats
            if isinstance(stats_data, dict):
                if "values" in stats_data:
                    values = stats_data["values"]
                elif "resourceList" in stats_data:
                    # Handle list format
                    resource_list = stats_data.get("resourceList", [])
                    if resource_list and len(resource_list) > 0:
                        resource_data = resource_list[0]
                        stat_list = resource_data.get("data", [])
                        if stat_list and len(stat_list) > 0:
                            values = stat_list[0].get("data", [])
                            
                            # Extract timestamps if available
                            if len(stat_list) > 1 and "timestamps" in stat_list[1]:
                                # timestamps placeholder (unused)
                                pass
            elif isinstance(stats_data, list) and len(stats_data) > 0:
                # Direct list format
                values = stats_data[0].get("data", []) if isinstance(stats_data[0], dict) else []
            
            # If we couldn't get proper historical data, return error
            if not values or len(values) < 2:
                return json.dumps({
                    "error": "Insufficient historical data for forecasting",
                    "resourceId": resource_id,
                    "metric": metric,
                    "dataPoints": len(values) if values else 0
                })
            
            # Simple linear forecasting (in production, use more sophisticated models)
            # Convert to numeric values, filtering out None/invalid
            numeric_values = []
            for i, val in enumerate(values):
                if val is not None and isinstance(val, (int, float)):
                    numeric_values.append(float(val))
                else:
                    # Try to use previous value or skip
                    if numeric_values:
                        numeric_values.append(numeric_values[-1])
            
            if len(numeric_values) < 2:
                return json.dumps({
                    "error": "Insufficient valid numeric data for forecasting",
                    "resourceId": resource_id,
                    "metric": metric,
                    "validDataPoints": len(numeric_values)
                })
            
            # Calculate simple linear trend
            n = len(numeric_values)
            x_values = list(range(n))
            
            # Calculate slope (m) and intercept (b) for y = mx + b
            sum_x = sum(x_values)
            sum_y = sum(numeric_values)
            sum_xy = sum(x * y for x, y in zip(x_values, numeric_values))
            sum_x2 = sum(x * x for x in x_values)
            
            # Avoid division by zero
            denominator = n * sum_x2 - sum_x * sum_x
            if denominator == 0:
                # No trend, use average
                slope = 0
                intercept = sum_y / n
            else:
                slope = (n * sum_xy - sum_x * sum_y) / denominator
                intercept = (sum_y - slope * sum_x) / n
            
            # Forecast future values
            forecast_points = []
            for day in range(1, days_ahead + 1):
                future_x = n + day - 1  # Continue from where historical data ends
                forecast_value = slope * future_x + intercept
                forecast_points.append({
                    "day": day,
                    "predictedValue": max(0, forecast_value),  # Ensure non-negative
                    "timestamp": int((time.time() + (day * 24 * 60 * 60)) * 1000)
                })
            
            # Calculate when threshold might be reached (if applicable)
            threshold_warning = None
            if slope < 0:  # Decreasing trend (like remaining capacity)
                # Find when value reaches 0 or critical threshold
                if slope != 0:
                    days_to_zero = -intercept / slope if slope < 0 else float('inf')
                    if 0 < days_to_zero <= days_ahead * 2:  # Within reasonable forecast range
                        threshold_warning = {
                            "daysUntilCritical": max(0, days_to_zero),
                            "criticalDate": int((time.time() + (days_to_zero * 24 * 60 * 60)) * 1000),
                            "trend": "decreasing",
                            "dailyChangeRate": slope
                        }
            elif slope > 0:  # Increasing trend (like used space)
                # Find when value reaches 100% or critical threshold
                # This would need to know the total capacity, which we don't have here
                pass
            
            # Calculate basic statistics
            mean_val = statistics.mean(numeric_values) if numeric_values else 0
            stdev_val = statistics.stdev(numeric_values) if len(numeric_values) > 1 else 0
            
            result = {
                "resourceId": resource_id,
                "metric": metric,
                "analysisPeriodDays": history_days,
                "forecastPeriodDays": days_ahead,
                "historicalDataPoints": len(numeric_values),
                "historicalStats": {
                    "mean": mean_val,
                    "stdDev": stdev_val,
                    "min": min(numeric_values) if numeric_values else 0,
                    "max": max(numeric_values) if numeric_values else 0,
                    "trend": "increasing" if slope > 0.01 else "decreasing" if slope < -0.01 else "stable",
                    "dailyChangeRate": slope
                },
                "forecast": forecast_points,
                "thresholdWarning": threshold_warning,
                "generatedAt": int(time.time() * 1000)
            }
            
            return json.dumps(result, indent=2)
            
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": str(e), "status_code": e.response.status_code, "detail": e.response.text[:500]})
        except httpx.HTTPError as e:
            return json.dumps({"error": "Network error", "detail": str(e)})
        except Exception as e:
            logger.exception("Error in capacity forecast")
            return json.dumps({"error": "Unexpected error", "detail": str(e)})

    async def get_trend_analysis(args: dict) -> str:
        if not args.get("id"):
            return json.dumps({"error": "Missing required argument: id"})
        if not args.get("metric"):
            return json.dumps({"error": "Missing required argument: metric"})
        
        resource_id = args["id"]
        metric = args["metric"]
        period_days = max(7, min(int(args.get("period_days", 30)), 365))
        
        try:
            client = get_client()
            _ = quote(resource_id, safe="")
            
            # Get historical stats for the metric
            end_time = int(time.time() * 1000)
            start_time = end_time - (period_days * 24 * 60 * 60 * 1000)
            
            # Query historical data
            body = {
                "resourceId": [{"resourceId": resource_id}],
                "statKey": [{"key": metric}],
                "begin": start_time,
                "end": end_time,
            }
            
            stats_data = await client.post("/resources/stats/history/query", body)
            
            # Extract data points (similar to forecast function)
            values = []
            
            # Handle different response formats
            if isinstance(stats_data, dict):
                if "values" in stats_data:
                    values = stats_data["values"]
                elif "resourceList" in stats_data:
                    resource_list = stats_data.get("resourceList", [])
                    if resource_list and len(resource_list) > 0:
                        resource_data = resource_list[0]
                        stat_list = resource_data.get("data", [])
                        if stat_list and len(stat_list) > 0:
                            values = stat_list[0].get("data", [])
            elif isinstance(stats_data, list) and len(stats_data) > 0:
                values = stats_data[0].get("data", []) if isinstance(stats_data[0], dict) else []
            
            # If we couldn't get proper historical data, return error
            if not values or len(values) < 2:
                return json.dumps({
                    "error": "Insufficient historical data for trend analysis",
                    "resourceId": resource_id,
                    "metric": metric,
                    "dataPoints": len(values) if values else 0
                })
            
            # Convert to numeric values, filtering out None/invalid
            numeric_values = []
            valid_timestamps = []
            
            # Try to extract timestamps if available in a standard format
            timestamps_available = False
            if isinstance(stats_data, dict) and "resourceList" in stats_data:
                resource_list = stats_data.get("resourceList", [])
                if resource_list and len(resource_list) > 0:
                    resource_data = resource_list[0]
                    stat_list = resource_data.get("data", [])
                    if len(stat_list) > 1 and "timestamps" in stat_list[1]:
                        raw_timestamps = stat_list[1].get("timestamps", [])
                        # Filter timestamps to match valid values
                        for i, (val, ts) in enumerate(zip(values, raw_timestamps)):
                            if val is not None and isinstance(val, (int, float)):
                                numeric_values.append(float(val))
                                valid_timestamps.append(ts)
                                timestamps_available = True
            
            # If no timestamps available, use indices
            if not timestamps_available:
                for i, val in enumerate(values):
                    if val is not None and isinstance(val, (int, float)):
                        numeric_values.append(float(val))
                        valid_timestamps.append(i)  # Use index as timestamp
            
            if len(numeric_values) < 2:
                return json.dumps({
                    "error": "Insufficient valid numeric data for trend analysis",
                    "resourceId": resource_id,
                    "metric": metric,
                    "validDataPoints": len(numeric_values)
                })
            
            # Calculate trend statistics
            n = len(numeric_values)
            if n >= 2:
                mean_val = statistics.mean(numeric_values)
                median_val = statistics.median(numeric_values)
                stdev_val = statistics.stdev(numeric_values) if n > 1 else 0
                
                # Calculate trend using linear regression
                x_values = list(range(n))
                sum_x = sum(x_values)
                sum_y = sum(numeric_values)
                sum_xy = sum(x * y for x, y in zip(x_values, numeric_values))
                sum_x2 = sum(x * x for x in x_values)
                
                denominator = n * sum_x2 - sum_x * sum_x
                if denominator == 0:
                    slope = 0
                else:
                    slope = (n * sum_xy - sum_x * sum_y) / denominator
                
                # Calculate volatility (coefficient of variation)
                volatility = (stdev_val / mean_val * 100) if mean_val != 0 else 0
                
                # Detect seasonality (simple approach: compare weekly patterns)
                seasonality_detected = False
                seasonality_strength = 0
                if n >= 14:  # Need at least 2 weeks
                    # Split into weeks and compare patterns
                    weekly_patterns = []
                    days_per_week = 7
                    weeks = n // days_per_week
                    
                    if weeks >= 2:
                        for week in range(weeks):
                            start_idx = week * days_per_week
                            end_idx = start_idx + days_per_week
                            week_data = numeric_values[start_idx:end_idx]
                            if len(week_data) == days_per_week:
                                weekly_patterns.append(week_data)
                        
                        if len(weekly_patterns) >= 2:
                            # Calculate correlation between first and subsequent weeks
                            first_week = weekly_patterns[0]
                            correlations = []
                            for week in weekly_patterns[1:]:
                                if len(week) == len(first_week):
                                    # Simple correlation calculation
                                    mean_first = statistics.mean(first_week)
                                    mean_week = statistics.mean(week)
                                    if stdev_val > 0 and statistics.stdev(week) > 0:
                                        cov = sum((x - mean_first) * (y - mean_week) for x, y in zip(first_week, week))
                                    # Compute correlation coefficient
                                    denom = len(first_week) * statistics.stdev(first_week) * statistics.stdev(week)
                                    corr = cov / denom
                                    correlations.append(corr)
                            
                            if correlations:
                                seasonality_strength = abs(statistics.mean(correlations))
                                seasonality_detected = seasonality_strength > 0.3  # Threshold for seasonality
                    
                  
                # Calculate recent trend (last 20% of data vs previous)
                recent_start = max(0, int(n * 0.8))
                if recent_start < n - 1:
                    recent_values = numeric_values[recent_start:]
                    previous_values = numeric_values[:recent_start]
                      
                    if len(recent_values) >= 2 and len(previous_values) >= 2:
                        recent_mean = statistics.mean(recent_values)
                        previous_mean = statistics.mean(previous_values)
                        # Percentage change between recent and previous means
                        if previous_mean != 0:
                            trend_change = (recent_mean - previous_mean) / previous_mean * 100
                        else:
                            trend_change = 0
                    else:
                        trend_change = 0
                else:
                    trend_change = 0
                  
                # Determine trend direction
                if slope > 0.01:
                    trend_direction = "increasing"
                elif slope < -0.01:
                    trend_direction = "decreasing"
                else:
                    trend_direction = "stable"
                
                result = {
                    "resourceId": resource_id,
                    "metric": metric,
                    "analysisPeriodDays": period_days,
                    "dataPoints": len(numeric_values),
                    "timeRange": {
                        "startTimestamp": valid_timestamps[0] if valid_timestamps else 0,
                        "endTimestamp": valid_timestamps[-1] if valid_timestamps else 0
                    },
                    "statistics": {
                        "mean": mean_val,
                        "median": median_val,
                        "stdDev": stdev_val,
                        "min": min(numeric_values),
                        "max": max(numeric_values),
                        "range": max(numeric_values) - min(numeric_values),
                        "volatilityPercent": round(volatility, 2)
                    },
                    "trend": {
                        "direction": trend_direction,
                        "slope": slope,
                        "dailyChangeRate": slope,
                        "recentChangePercent": round(trend_change, 2),
                        "isSignificant": abs(slope) > (stdev_val * 0.1) if stdev_val > 0 else False
                    },
                    "patterns": {
                        "seasonalityDetected": seasonality_detected,
                        "seasonalityStrength": round(seasonality_strength, 3) if seasonality_detected else 0
                    },
                    "generatedAt": int(time.time() * 1000)
                }
                
                return json.dumps(result, indent=2)
            else:
                return json.dumps({
                    "error": "Insufficient data for trend analysis",
                    "resourceId": resource_id,
                    "metric": metric,
                    "dataPoints": len(numeric_values)
                })
                
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": str(e), "status_code": e.response.status_code, "detail": e.response.text[:500]})
        except httpx.HTTPError as e:
            return json.dumps({"error": "Network error", "detail": str(e)})
        except Exception as e:
            logger.exception("Error in trend analysis")
            return json.dumps({"error": "Unexpected error", "detail": str(e)})

    return {
        "get_capacity_remaining": get_capacity_remaining,
        "get_capacity_overview": get_capacity_overview,
        "list_policies": list_policies,
        "get_capacity_forecast": get_capacity_forecast,
        "get_trend_analysis": get_trend_analysis,
    }
