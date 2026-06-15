"""Unit tests for OnboardingInputSchema."""
import pytest

from common.utils.validators import DictValidator
from workers.onboarding_verification_worker.src.handlers.schema.input_schema import OnboardingInputSchema


_schema = OnboardingInputSchema()
_mw = lambda m: m  # noqa: E731

BASE_PAYLOAD = {
    "candidate_email": "a@b.com",
    "candidate_uid": "uid-1",
    "org_id": "org-1",
    "org_alias": "test",
    "bucket_name": "bucket",
    "event_id": "evt-3",
    "lookup_map": {"profile": "profile", "interviews": ["i1"]},
    "onboarding_reference_path": "onboarding_reference/",
}


class TestOnboardingInputSchema:
    def test_valid_gcs_path_passes(self):
        payload = {**BASE_PAYLOAD}
        _schema.validate(payload, message_wrapper=_mw)  # must not raise

    def test_valid_signed_url_passes(self):
        payload = {
            **BASE_PAYLOAD,
            "onboarding_reference_path": "https://storage.googleapis.com/bucket/photo.jpg?X-Goog-Signature=abc",
        }
        _schema.validate(payload, message_wrapper=_mw)  # any str is accepted

    def test_extra_keys_allowed(self):
        payload = {**BASE_PAYLOAD, "match_against": ["profile"], "bonus": "ignored"}
        _schema.validate(payload, message_wrapper=_mw)

    def test_optional_extra_info_defaults_to_none(self):
        payload = {**BASE_PAYLOAD}
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
        "onboarding_reference_path",
    ])
    def test_missing_required_raises(self, missing_key):
        payload = {k: v for k, v in BASE_PAYLOAD.items() if k != missing_key}
        with pytest.raises(DictValidator.SchemaViolation):
            _schema.validate(payload, message_wrapper=_mw)

    def test_wrong_type_for_onboarding_path_raises(self):
        payload = {**BASE_PAYLOAD, "onboarding_reference_path": 42}
        with pytest.raises(DictValidator.SchemaViolation):
            _schema.validate(payload, message_wrapper=_mw)

    def test_wrong_type_for_lookup_map_raises(self):
        payload = {**BASE_PAYLOAD, "lookup_map": "string"}
        with pytest.raises(DictValidator.SchemaViolation):
            _schema.validate(payload, message_wrapper=_mw)
