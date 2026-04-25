# Usage Examples for New Capacity Tools

## get_capacity_forecast

Predict future capacity needs based on historical trends.

```javascript
// Example 1: Basic forecast
{
  "id": "vm-12345",
  "metric": "mem|host_usage",
  "days_ahead": 30,
  "history_days": 60
}

// Example 2: Short-term forecast with more history
{
  "id": "datastore-67890",
  "metric": "storage|usage",
  "days_ahead": 7,
  "history_days": 90
}
```

## get_trend_analysis

Analyze historical trends and patterns in capacity metrics.

```javascript
// Example 1: Basic trend analysis
{
  "id": "vm-12345",
  "metric": "mem|host_usage",
  "period_days": 30
}

// Example 2: Longer-term analysis for capacity planning
{
  "id": "cluster-abcde",
  "metric": "cpu|usage",
  "period_days": 90
}
```

## Response Format

Both tools return JSON responses with the following structure:

### get_capacity_forecast
```json
{
  "resourceId": "vm-12345",
  "metric": "mem|host_usage",
  "forecastDays": 30,
  "historyDays": 60,
  "dataPoints": 60,
  "forecast": [
    {
      "timestamp": 1714051200000,
      "predictedValue": 65.2
    },
    {
      "timestamp": 1714137600000,
      "predictedValue": 66.1
    }
  ],
  "thresholdWarning": {
    "warningThreshold": 80,
    "exceedsWarning": false,
    "daysUntilThreshold": 45
  },
  "generatedAt": 1713964800000
}
```

### get_trend_analysis
```json
{
  "resourceId": "vm-12345",
  "metric": "mem|host_usage",
  "analysisPeriodDays": 30,
  "dataPoints": 30,
  "timeRange": {
    "startTimestamp": 1711372800000,
    "endTimestamp": 1713964800000
  },
  "statistics": {
    "mean": 62.5,
    "median": 61.8,
    "stdDev": 8.2,
    "min": 45.2,
    "max": 78.9,
    "range": 33.7
  },
  "trendAnalysis": {
    "slope": 0.15,
    "trendDirection": "increasing",
    "volatility": 13.1,
    "seasonalityDetected": false,
    "seasonalityStrength": 0.12
  },
  "generatedAt": 1713964800000
}
```