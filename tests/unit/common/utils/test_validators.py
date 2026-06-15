"""Unit tests for common/utils/validators.py – DictValidator."""
import pytest

from common.utils.validators import DictValidator


# ---------------------------------------------------------------------------
# Minimal concrete validator used across all tests
# ---------------------------------------------------------------------------

class SampleSchema(DictValidator):
    """Concrete validator with one required + one optional field."""

    ALLOWED_EXTRA_KEYS = False
    VALIDATION_SPECIFICATION = {
        "name":  (True,  str,  None),
        "count": (False, int,  0),
    }

    def validate(self, json_payload, logger_func=None, message_wrapper=None):
        return super().validate(json_payload, logger_func, message_wrapper)


class ExtraKeysSchema(DictValidator):
    """Validator that allows extra keys."""

    ALLOWED_EXTRA_KEYS = True
    VALIDATION_SPECIFICATION = {
        "name": (True, str, None),
    }

    def validate(self, json_payload, logger_func=None, message_wrapper=None):
        return super().validate(json_payload, logger_func, message_wrapper)


_mw = lambda m: m  # noqa: E731  — mirrors production handle_message usage


_schema = SampleSchema()
_extra_schema = ExtraKeysSchema()


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------

class TestDictValidatorValid:
    def test_all_required_fields_pass(self):
        payload = {"name": "Alice", "count": 3}
        _schema.validate(payload, message_wrapper=_mw)  # must not raise

    def test_optional_field_defaults_when_missing(self):
        payload = {"name": "Bob"}
        _schema.validate(payload, message_wrapper=_mw)
        assert payload["count"] == 0

    def test_extra_keys_allowed_when_flag_set(self):
        payload = {"name": "Carol", "unknown_field": "value"}
        _extra_schema.validate(payload, message_wrapper=_mw)  # must not raise


# ---------------------------------------------------------------------------
# Missing required field
# ---------------------------------------------------------------------------

class TestDictValidatorMissingField:
    def test_missing_required_raises(self):
        with pytest.raises(DictValidator.SchemaViolation):
            _schema.validate({}, message_wrapper=_mw)

    def test_missing_required_message(self):
        with pytest.raises(DictValidator.SchemaViolation, match="name"):
            _schema.validate({}, message_wrapper=_mw)


# ---------------------------------------------------------------------------
# Wrong type
# ---------------------------------------------------------------------------

class TestDictValidatorWrongType:
    def test_wrong_type_raises(self):
        with pytest.raises(DictValidator.SchemaViolation):
            _schema.validate({"name": 123, "count": 1}, message_wrapper=_mw)

    def test_wrong_type_optional_raises(self):
        with pytest.raises(DictValidator.SchemaViolation):
            _schema.validate({"name": "X", "count": "not-an-int"}, message_wrapper=_mw)


# ---------------------------------------------------------------------------
# Extra keys not allowed
# ---------------------------------------------------------------------------

class TestDictValidatorExtraKeys:
    def test_extra_keys_raises_when_not_allowed(self):
        with pytest.raises(DictValidator.SchemaViolation):
            _schema.validate({"name": "X", "count": 1, "unexpected": True}, message_wrapper=_mw)


# ---------------------------------------------------------------------------
# Union types (e.g. dict | None)
# ---------------------------------------------------------------------------

class TestDictValidatorUnionType:
    def test_none_accepted_for_union_type(self):
        """Verify that (dict, type(None)) accepts None values."""

        class NullableSchema(DictValidator):
            ALLOWED_EXTRA_KEYS = True
            VALIDATION_SPECIFICATION = {
                "info": (False, (dict, type(None)), None),
            }

            def validate(self, json_payload, logger_func=None, message_wrapper=None):
                return super().validate(json_payload, logger_func, message_wrapper)

        schema = NullableSchema()
        schema.validate({"info": None}, message_wrapper=_mw)  # must not raise

    def test_dict_accepted_for_union_type(self):
        class NullableSchema(DictValidator):
            ALLOWED_EXTRA_KEYS = True
            VALIDATION_SPECIFICATION = {
                "info": (False, (dict, type(None)), None),
            }

            def validate(self, json_payload, logger_func=None, message_wrapper=None):
                return super().validate(json_payload, logger_func, message_wrapper)

        schema = NullableSchema()
        schema.validate({"info": {"key": "val"}}, message_wrapper=_mw)  # must not raise
