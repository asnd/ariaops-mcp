"""Shared constants and utilities for tool modules."""

PAGE_SIZE_DEFAULT = 50
PAGE_SIZE_MAX = 200

MAX_LIST_ITEMS = 50


def truncate_list_response(data: dict, list_key: str) -> dict:
    items = data.get(list_key, [])
    if len(items) > MAX_LIST_ITEMS:
        data[list_key] = items[:MAX_LIST_ITEMS]
        data["_truncated"] = True
        data["_truncatedAt"] = MAX_LIST_ITEMS
    return data
