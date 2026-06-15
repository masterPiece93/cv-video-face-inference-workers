"""Unit tests for EncodingInputSchema."""
import pytest

from workers.face_encoding_worker.src.handlers.schema.input_schema import EncodingInputSchema
from common.utils.validators import DictValidator


_schema = EncodingInputSchema()
_mw = lambda m: m  # noqa: E731

VALID_PAYLOAD = {
    "candidate_email": "a@b.com",
    "candidate_uid": "uid-1",
    "org_id": "org-1",
    "org_alias": "test",
    "bucket_name": "bucket",
    "event_id": "evt-1",
    "lookup_map": {"profile": "profile", "interviews": []},
}


class TestEncodingInputSchema:
    def test_valid_payload_passes(self):
        payload = {**VALID_PAYLOAD}
        _schema.validate(payload, message_wrapper=_mw)  # must not raise

    def test_extra_keys_allowed(self):
        payload = {**VALID_PAYLOAD, "extra_info": None, "bonus": "ignored"}
        _schema.validate(payload, message_wrapper=_mw)  # ALLOWED_EXTRA_KEYS = True

    def test_optional_extra_info_defaults_to_none(self):
        payload = {**VALID_PAYLOAD}
        _schema.validate(payload, message_wrapper=_mw)
        assert payload.get("extra_info") is None

    @pytest.mark.parametrize("missing_key", [
        "candidate_email",
        "candidate_uid",
        "org_id",
        "org_alias",
        "bucket_name",
        "event_id",
        "lookup_map",
    ])
    def test_missing_required_field_raises(self, missing_key):
        payload = {k: v for k, v in VALID_PAYLOAD.items() if k != missing_key}
        with pytest.raises(DictValidator.SchemaViolation):
            _schema.validate(payload, message_wrapper=_mw)

    def test_wrong_type_event_id_raises(self):
        payload = {**VALID_PAYLOAD, "event_id": 123}
        with pytest.raises(DictValidator.SchemaViolation):
            _schema.validate(payload, message_wrapper=_mw)

    def test_wrong_type_lookup_map_raises(self):
        payload = {**VALID_PAYLOAD, "lookup_map": "not-a-dict"}
        with pytest.raises(DictValidator.SchemaViolation):
            _schema.validate(payload, message_wrapper=_mw)
