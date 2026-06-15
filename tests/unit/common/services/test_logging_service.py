"""Unit tests for common/services/logging_service.py."""
import json
import logging
import os
import sys

import pytest

from common.services.logging_service import (
    JsonFormatter,
    _StderrFilter,
    _StdoutFilter,
    get_logger,
    setup_logging,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_logger(name: str) -> None:
    """Remove any existing handlers from the named logger so tests start clean."""
    log = logging.getLogger(name)
    log.handlers.clear()
    return log


# ---------------------------------------------------------------------------
# _StdoutFilter
# ---------------------------------------------------------------------------

class TestStdoutFilter:
    def _record(self, level: int) -> logging.LogRecord:
        r = logging.LogRecord(
            name="test", level=level, pathname="", lineno=0,
            msg="msg", args=(), exc_info=None,
        )
        return r

    def test_allows_debug(self):
        assert _StdoutFilter().filter(self._record(logging.DEBUG)) is True

    def test_allows_info(self):
        assert _StdoutFilter().filter(self._record(logging.INFO)) is True

    def test_blocks_warning(self):
        assert _StdoutFilter().filter(self._record(logging.WARNING)) is False

    def test_blocks_error(self):
        assert _StdoutFilter().filter(self._record(logging.ERROR)) is False

    def test_blocks_critical(self):
        assert _StdoutFilter().filter(self._record(logging.CRITICAL)) is False


# ---------------------------------------------------------------------------
# _StderrFilter
# ---------------------------------------------------------------------------

class TestStderrFilter:
    def _record(self, level: int) -> logging.LogRecord:
        r = logging.LogRecord(
            name="test", level=level, pathname="", lineno=0,
            msg="msg", args=(), exc_info=None,
        )
        return r

    def test_blocks_debug(self):
        assert _StderrFilter().filter(self._record(logging.DEBUG)) is False

    def test_blocks_info(self):
        assert _StderrFilter().filter(self._record(logging.INFO)) is False

    def test_allows_warning(self):
        assert _StderrFilter().filter(self._record(logging.WARNING)) is True

    def test_allows_error(self):
        assert _StderrFilter().filter(self._record(logging.ERROR)) is True

    def test_allows_critical(self):
        assert _StderrFilter().filter(self._record(logging.CRITICAL)) is True


# ---------------------------------------------------------------------------
# JsonFormatter
# ---------------------------------------------------------------------------

class TestJsonFormatter:
    def _make_record(self, msg="hello", level=logging.INFO, exc_info=None):
        r = logging.LogRecord(
            name="test-logger",
            level=level,
            pathname="myfile.py",
            lineno=42,
            msg=msg,
            args=(),
            exc_info=exc_info,
        )
        return r

    def test_output_is_valid_json(self):
        fmt = JsonFormatter()
        output = fmt.format(self._make_record())
        data = json.loads(output)  # must not raise
        assert isinstance(data, dict)

    def test_required_keys_present(self):
        fmt = JsonFormatter()
        data = json.loads(fmt.format(self._make_record("test msg")))
        for key in ("timestamp", "level", "logger", "message", "file"):
            assert key in data, f"Missing key: {key}"

    def test_level_name_in_output(self):
        fmt = JsonFormatter()
        data = json.loads(fmt.format(self._make_record(level=logging.WARNING)))
        assert data["level"] == "WARNING"

    def test_message_in_output(self):
        fmt = JsonFormatter()
        data = json.loads(fmt.format(self._make_record(msg="my log message")))
        assert data["message"] == "my log message"

    def test_logger_name_in_output(self):
        fmt = JsonFormatter()
        data = json.loads(fmt.format(self._make_record()))
        assert data["logger"] == "test-logger"

    def test_no_exception_key_when_no_exc_info(self):
        fmt = JsonFormatter()
        data = json.loads(fmt.format(self._make_record()))
        assert "exception" not in data

    def test_exception_key_present_when_exc_info_set(self):
        fmt = JsonFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys as _sys
            exc_info = _sys.exc_info()
        record = self._make_record(exc_info=exc_info)
        data = json.loads(fmt.format(record))
        assert "exception" in data
        assert "ValueError" in data["exception"]


# ---------------------------------------------------------------------------
# setup_logging()
# ---------------------------------------------------------------------------

class TestSetupLogging:
    def setup_method(self):
        """Clear handlers before each test to ensure isolation."""
        for name in ("test-sl-1", "test-sl-2", "test-sl-3", "test-sl-4",
                     "test-sl-5", "test-sl-file", "test-sl-json"):
            logging.getLogger(name).handlers.clear()

    def test_returns_logger_instance(self):
        logger = setup_logging("test-sl-1")
        assert isinstance(logger, logging.Logger)

    def test_logger_name_is_set(self):
        logger = setup_logging("test-sl-2")
        assert logger.name == "test-sl-2"

    def test_propagate_is_false(self):
        logger = setup_logging("test-sl-3")
        assert logger.propagate is False

    def test_two_stream_handlers_added(self):
        logger = setup_logging("test-sl-4")
        stream_handlers = [h for h in logger.handlers if isinstance(h, logging.StreamHandler)
                           and not isinstance(h, logging.FileHandler)]
        assert len(stream_handlers) == 2

    def test_idempotent_on_repeated_calls(self):
        name = "test-sl-5"
        logger1 = setup_logging(name)
        handler_count = len(logger1.handlers)
        logger2 = setup_logging(name)  # second call — must not add more handlers
        assert len(logger2.handlers) == handler_count

    def test_level_from_env_var(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "WARNING")
        name = "test-sl-env-level"
        logging.getLogger(name).handlers.clear()
        logger = setup_logging(name)
        assert logger.level == logging.WARNING
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        logging.getLogger(name).handlers.clear()

    def test_level_param_overrides_env(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "ERROR")
        name = "test-sl-level-override"
        logging.getLogger(name).handlers.clear()
        logger = setup_logging(name, level=logging.DEBUG)
        assert logger.level == logging.DEBUG
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        logging.getLogger(name).handlers.clear()

    def test_json_format_env_var_uses_json_formatter(self, monkeypatch):
        monkeypatch.setenv("LOG_FORMAT", "json")
        name = "test-sl-json"
        logging.getLogger(name).handlers.clear()
        logger = setup_logging(name)
        stream_handlers = [h for h in logger.handlers if isinstance(h, logging.StreamHandler)
                           and not isinstance(h, logging.FileHandler)]
        assert any(isinstance(h.formatter, JsonFormatter) for h in stream_handlers)
        monkeypatch.delenv("LOG_FORMAT", raising=False)
        logging.getLogger(name).handlers.clear()

    def test_json_format_param_uses_json_formatter(self):
        name = "test-sl-json-param"
        logging.getLogger(name).handlers.clear()
        logger = setup_logging(name, log_format="json")
        stream_handlers = [h for h in logger.handlers if isinstance(h, logging.StreamHandler)
                           and not isinstance(h, logging.FileHandler)]
        assert any(isinstance(h.formatter, JsonFormatter) for h in stream_handlers)
        logging.getLogger(name).handlers.clear()

    def test_file_handler_created_when_log_file_given(self, tmp_path):
        log_file = str(tmp_path / "app.log")
        name = "test-sl-file"
        logger = setup_logging(name, log_file=log_file)
        file_handlers = [h for h in logger.handlers if isinstance(h, logging.FileHandler)]
        assert len(file_handlers) == 1
        assert file_handlers[0].baseFilename == log_file
        # cleanup
        for h in logger.handlers:
            h.close()
        logging.getLogger(name).handlers.clear()

    def test_stdout_handler_targets_stdout(self):
        name = "test-sl-stdout"
        logging.getLogger(name).handlers.clear()
        logger = setup_logging(name)
        stdout_handlers = [
            h for h in logger.handlers
            if isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.FileHandler)
            and h.stream is sys.stdout
        ]
        assert len(stdout_handlers) == 1
        logging.getLogger(name).handlers.clear()

    def test_stderr_handler_targets_stderr(self):
        name = "test-sl-stderr"
        logging.getLogger(name).handlers.clear()
        logger = setup_logging(name)
        stderr_handlers = [
            h for h in logger.handlers
            if isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.FileHandler)
            and h.stream is sys.stderr
        ]
        assert len(stderr_handlers) == 1
        logging.getLogger(name).handlers.clear()


# ---------------------------------------------------------------------------
# get_logger()
# ---------------------------------------------------------------------------

class TestGetLogger:
    def test_get_logger_returns_logger(self):
        name = "test-get-logger"
        logging.getLogger(name).handlers.clear()
        logger = get_logger(name)
        assert isinstance(logger, logging.Logger)
        logging.getLogger(name).handlers.clear()

    def test_get_logger_same_as_setup_logging(self):
        name_a, name_b = "test-alias-a", "test-alias-b"
        logging.getLogger(name_a).handlers.clear()
        logging.getLogger(name_b).handlers.clear()

        l1 = get_logger(name_a, level=logging.INFO)
        l2 = setup_logging(name_b, level=logging.INFO)
        # Both should have the same handler structure
        assert l1.level == l2.level
        assert l1.propagate == l2.propagate

        logging.getLogger(name_a).handlers.clear()
        logging.getLogger(name_b).handlers.clear()
