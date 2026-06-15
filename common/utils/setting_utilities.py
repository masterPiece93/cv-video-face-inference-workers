"""Setting utilities module for validators and mixins."""
import os
import json
import re
from typing import Any
from collections.abc import Iterable, Sized

from pydantic import field_validator

__all__ = [
    "DecoderMixin",
    "CommonMeta",
    "Validators",
]


def _prepare_list_of_string(v: str) -> list[str]:
    """Prepare list of strings from comma-separated values."""
    return [str(x).strip() for x in v.split(",")]


class Validators:
    """Validators for various field types."""

    @staticmethod
    def not_empty(v: Any) -> Any:
        """Validate if any python ADT (Abstract Data Type) is not empty."""
        if not v:
            raise ValueError("Field cannot be empty")
        if isinstance(v, Sized) and len(v) == 0:
            raise ValueError("Field cannot be empty")
        return v

    @staticmethod
    def file_exists(v: str) -> str:
        """Validate that the file exists."""
        if not os.path.isfile(v):
            raise ValueError("File should exist")
        return v

    @staticmethod
    def is_json_file(v: str) -> bool:
        """Validate that the file is a valid JSON file."""
        try:
            with open(v, "r", encoding="utf-8") as f:
                json.load(f)
            return True
        except (json.JSONDecodeError, FileNotFoundError, IOError):
            return False

    @staticmethod
    def validate_server_address(v: str) -> str:
        """Validate server address format (ip:port)."""
        ip_port_pattern = re.compile(
            r"^(?:[0-9]{1,3}\.){3}[0-9]{1,3}:[0-9]{1,5}$|"
            r"^\[(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\]:[0-9]{1,5}$"
        )
        if not ip_port_pattern.match(v):
            raise ValueError("Invalid server address format. Expected 'ip:port'.")
        return v


class DecoderMixin:
    """Mixin for decoding comma-separated env values into lists."""

    @field_validator("*", mode="before")
    @classmethod
    def decode_csv_fields(cls, v: Any) -> Any:
        """Override in subclass to target specific fields."""
        return v


class CommonMeta:
    """Common Config class arguments for pydantic-settings models."""

    validate_default = True
    str_strip_whitespace = True
