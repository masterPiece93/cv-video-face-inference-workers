"""Logging service - shared across workers.

This module provides a correctly configured logger that separates
stdout (INFO and below) from stderr (WARNING and above), preventing
the common issue where INFO logs appear as errors.

Supports JSON output for production/cloud environments via LOG_FORMAT env var.

Usage:
    from common.services.logging_service import get_logger

    logger = get_logger("my-worker")
    logger.info("This goes to stdout")
    logger.error("This goes to stderr")

    # For JSON output (auto-detected from LOG_FORMAT env var or explicit):
    logger = get_logger("my-worker", log_format="json")
"""

import os
import sys
import json
import logging
from typing import Optional, Literal, cast


__all__ = ["get_logger", "setup_logging"]


class _StdoutFilter(logging.Filter):
    """Filter that only allows records at INFO level and below."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno <= logging.INFO


class _StderrFilter(logging.Filter):
    """Filter that only allows records at WARNING level and above."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= logging.WARNING


class JsonFormatter(logging.Formatter):
    """Formats log records as single-line JSON for cloud log aggregators."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "file": f"{record.filename}:{record.lineno}",
            "function": record.funcName,
        }
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, default=str)


DEFAULT_FORMAT = (
    "%(asctime)s - %(name)s - %(levelname)s - "
    "%(filename)s:%(lineno)d - %(funcName)s() - %(message)s"
)

INFO_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


def _get_formatter(
    log_format: Literal["text", "json"],
    detailed: bool = False,
) -> logging.Formatter:
    """Return the appropriate formatter based on format choice."""
    if log_format == "json":
        return JsonFormatter()
    return logging.Formatter(DEFAULT_FORMAT if detailed else INFO_FORMAT)


def setup_logging(
    name: str,
    level: Optional[int] = None,
    log_file: Optional[str] = None,
    log_format: Optional[Literal["text", "json"]] = None,
) -> logging.Logger:
    """Set up and return a properly configured logger.

    Key design decisions:
    - INFO and DEBUG logs go to sys.stdout (not stderr)
    - WARNING, ERROR, CRITICAL logs go to sys.stderr
    - This prevents log aggregators from misclassifying INFO as errors
    - JSON format available for cloud/production environments

    Args:
        name: Logger name (typically the service/worker name).
        level: Minimum log level. If None, reads from LOG_LEVEL env var
               (defaults to DEBUG).
        log_file: Optional file path for file-based logging.
        log_format: "text" or "json". If None, reads from LOG_FORMAT
                    env var (defaults to "text").

    Returns:
        Configured logging.Logger instance.
    """
    logger = logging.getLogger(name)

    # Avoid adding duplicate handlers if called multiple times
    if logger.handlers:
        return logger

    # Resolve log level from parameter or LOG_LEVEL env var
    if level is None:
        env_level = os.environ.get("LOG_LEVEL", "DEBUG").upper()
        level = getattr(logging, env_level, logging.DEBUG)
    logger.setLevel(level)

    # Resolve log format from parameter or env var
    _raw_format: str
    if log_format is None:
        _raw_format = os.environ.get("LOG_FORMAT", "text").lower()
    else:
        _raw_format = log_format
    resolved_format: Literal["text", "json"] = cast(
        'Literal["text", "json"]',
        _raw_format if _raw_format in ("text", "json") else "text",
    )

    # --- stdout handler (DEBUG, INFO) ---
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(logging.DEBUG)
    stdout_handler.addFilter(_StdoutFilter())
    stdout_handler.setFormatter(_get_formatter(resolved_format, detailed=False))

    # --- stderr handler (WARNING, ERROR, CRITICAL) ---
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.WARNING)
    stderr_handler.addFilter(_StderrFilter())
    stderr_handler.setFormatter(_get_formatter(resolved_format, detailed=True))

    logger.addHandler(stdout_handler)
    logger.addHandler(stderr_handler)

    # --- Optional file handler (all levels) ---
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(_get_formatter(resolved_format, detailed=True))
        logger.addHandler(file_handler)

    # Prevent propagation to root logger (avoids duplicate output)
    logger.propagate = False

    return logger


def get_logger(
    name: str,
    level: Optional[int] = None,
    log_file: Optional[str] = None,
    log_format: Optional[Literal["text", "json"]] = None,
) -> logging.Logger:
    """Convenience alias for setup_logging."""
    return setup_logging(name, level, log_file, log_format)