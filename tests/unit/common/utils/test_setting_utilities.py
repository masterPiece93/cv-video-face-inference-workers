"""Unit tests for common/utils/setting_utilities.py."""
import json
import os
import tempfile

import pytest

from common.utils.setting_utilities import DecoderMixin, Validators, _prepare_list_of_string


# ---------------------------------------------------------------------------
# _prepare_list_of_string (module-level helper)
# ---------------------------------------------------------------------------

class TestPrepareListOfString:
    def test_single_value(self):
        assert _prepare_list_of_string("alpha") == ["alpha"]

    def test_multiple_values_split_on_comma(self):
        assert _prepare_list_of_string("a,b,c") == ["a", "b", "c"]

    def test_values_are_stripped(self):
        assert _prepare_list_of_string("  a , b , c  ") == ["a", "b", "c"]

    def test_empty_string_gives_list_with_one_empty_string(self):
        result = _prepare_list_of_string("")
        assert result == [""]


# ---------------------------------------------------------------------------
# Validators.not_empty
# ---------------------------------------------------------------------------

class TestValidatorsNotEmpty:
    def test_valid_non_empty_string_passes(self):
        assert Validators.not_empty("hello") == "hello"

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            Validators.not_empty("")

    def test_none_raises(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            Validators.not_empty(None)

    def test_zero_int_raises(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            Validators.not_empty(0)

    def test_empty_list_raises(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            Validators.not_empty([])

    def test_non_empty_list_passes(self):
        assert Validators.not_empty([1, 2]) == [1, 2]

    def test_non_empty_dict_passes(self):
        d = {"k": "v"}
        assert Validators.not_empty(d) == d


# ---------------------------------------------------------------------------
# Validators.file_exists
# ---------------------------------------------------------------------------

class TestValidatorsFileExists:
    def test_existing_file_passes(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("data")
        assert Validators.file_exists(str(f)) == str(f)

    def test_missing_file_raises(self):
        with pytest.raises(ValueError, match="exist"):
            Validators.file_exists("/nonexistent/path/to/file.txt")

    def test_directory_path_raises(self, tmp_path):
        with pytest.raises(ValueError, match="exist"):
            Validators.file_exists(str(tmp_path))


# ---------------------------------------------------------------------------
# Validators.is_json_file
# ---------------------------------------------------------------------------

class TestValidatorsIsJsonFile:
    def test_valid_json_file_returns_true(self, tmp_path):
        f = tmp_path / "valid.json"
        f.write_text(json.dumps({"key": "value"}))
        assert Validators.is_json_file(str(f)) is True

    def test_invalid_json_file_returns_false(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("not json at all {{{{")
        assert Validators.is_json_file(str(f)) is False

    def test_missing_file_returns_false(self):
        assert Validators.is_json_file("/nonexistent/file.json") is False

    def test_empty_file_returns_false(self, tmp_path):
        f = tmp_path / "empty.json"
        f.write_text("")
        assert Validators.is_json_file(str(f)) is False


# ---------------------------------------------------------------------------
# Validators.validate_server_address
# ---------------------------------------------------------------------------

class TestValidatorsServerAddress:
    def test_valid_ipv4_address_passes(self):
        assert Validators.validate_server_address("192.168.1.1:8080") == "192.168.1.1:8080"

    def test_localhost_address_passes(self):
        assert Validators.validate_server_address("127.0.0.1:5000") == "127.0.0.1:5000"

    def test_missing_port_raises(self):
        with pytest.raises(ValueError, match="Invalid server address"):
            Validators.validate_server_address("192.168.1.1")

    def test_hostname_without_ip_raises(self):
        with pytest.raises(ValueError, match="Invalid server address"):
            Validators.validate_server_address("localhost:8080")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="Invalid server address"):
            Validators.validate_server_address("")

    def test_garbage_input_raises(self):
        with pytest.raises(ValueError, match="Invalid server address"):
            Validators.validate_server_address("not-an-address")


# ---------------------------------------------------------------------------
# DecoderMixin.decode_csv_fields
# ---------------------------------------------------------------------------

class TestDecoderMixin:
    def test_decode_csv_fields_returns_value_unchanged(self):
        result = DecoderMixin.decode_csv_fields("some value")
        assert result == "some value"

    def test_decode_csv_fields_works_with_none(self):
        result = DecoderMixin.decode_csv_fields(None)
        assert result is None

    def test_decode_csv_fields_works_with_list(self):
        lst = ["a", "b"]
        result = DecoderMixin.decode_csv_fields(lst)
        assert result == lst
