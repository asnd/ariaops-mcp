"""Tests for structured logging configuration."""

import json
import logging
import sys

from ariaops_mcp.logging_config import (
    JSONFormatter,
    configure_logging,
    correlation_id_var,
    get_correlation_id,
    new_correlation_id,
)


class TestCorrelationId:
    def test_new_id_is_hex_16_chars(self):
        cid = new_correlation_id()
        assert isinstance(cid, str)
        assert len(cid) == 16
        int(cid, 16)  # raises if not valid hex

    def test_new_id_sets_context_var(self):
        cid = new_correlation_id()
        assert get_correlation_id() == cid

    def test_get_returns_empty_when_unset(self):
        correlation_id_var.set("")
        assert get_correlation_id() == ""


class TestJSONFormatter:
    def _make_record(self, msg: str = "test", level: int = logging.INFO, **extra) -> logging.LogRecord:
        record = logging.LogRecord(
            name="test.logger",
            level=level,
            pathname="test_logging_config.py",
            lineno=1,
            msg=msg,
            args=(),
            exc_info=None,
        )
        for key, val in extra.items():
            setattr(record, key, val)
        return record

    def test_basic_fields_present(self):
        correlation_id_var.set("")
        fmt = JSONFormatter()
        record = self._make_record("hello world")
        output = json.loads(fmt.format(record))
        assert output["msg"] == "hello world"
        assert output["level"] == "INFO"
        assert output["logger"] == "test.logger"
        assert "ts" in output

    def test_no_correlation_id_when_empty(self):
        correlation_id_var.set("")
        fmt = JSONFormatter()
        output = json.loads(fmt.format(self._make_record()))
        assert "correlation_id" not in output

    def test_correlation_id_included_when_set(self):
        cid = new_correlation_id()
        fmt = JSONFormatter()
        output = json.loads(fmt.format(self._make_record()))
        assert output["correlation_id"] == cid
        correlation_id_var.set("")  # cleanup

    def test_extra_fields_tool_and_event(self):
        correlation_id_var.set("")
        fmt = JSONFormatter()
        record = self._make_record(tool="list_alerts", event="tool_call")
        output = json.loads(fmt.format(record))
        assert output["tool"] == "list_alerts"
        assert output["event"] == "tool_call"

    def test_extra_fields_http_context(self):
        correlation_id_var.set("")
        fmt = JSONFormatter()
        record = self._make_record(method="GET", path="/api/resources", status=200, duration_ms=42)
        output = json.loads(fmt.format(record))
        assert output["method"] == "GET"
        assert output["path"] == "/api/resources"
        assert output["status"] == 200
        assert output["duration_ms"] == 42

    def test_absent_extra_fields_not_emitted(self):
        correlation_id_var.set("")
        fmt = JSONFormatter()
        output = json.loads(fmt.format(self._make_record()))
        for field in ("method", "path", "status", "duration_ms", "tool", "event"):
            assert field not in output

    def test_exception_info_included(self):
        correlation_id_var.set("")
        fmt = JSONFormatter()
        try:
            raise ValueError("test error")
        except ValueError:
            exc_info = sys.exc_info()
        record = self._make_record()
        record.exc_info = exc_info
        output = json.loads(fmt.format(record))
        assert "exception" in output
        assert "ValueError" in output["exception"]
        assert "test error" in output["exception"]

    def test_none_exc_info_not_included(self):
        correlation_id_var.set("")
        fmt = JSONFormatter()
        record = self._make_record()
        record.exc_info = (None, None, None)
        output = json.loads(fmt.format(record))
        assert "exception" not in output


class TestConfigureLogging:
    def test_text_format_configures_root_handler(self):
        configure_logging("WARNING", "text")
        root = logging.getLogger()
        assert root.level == logging.WARNING
        assert len(root.handlers) == 1
        assert not isinstance(root.handlers[0].formatter, JSONFormatter)

    def test_json_format_uses_json_formatter(self):
        configure_logging("DEBUG", "json")
        root = logging.getLogger()
        assert root.level == logging.DEBUG
        assert isinstance(root.handlers[0].formatter, JSONFormatter)

    def test_calling_twice_replaces_handlers_not_duplicates(self):
        configure_logging("INFO", "text")
        configure_logging("INFO", "json")
        root = logging.getLogger()
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0].formatter, JSONFormatter)
