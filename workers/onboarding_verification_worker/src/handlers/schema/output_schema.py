"""Output schema for Onboarding Verification Worker."""
from typing import Optional, Callable

from common.utils.validators import DictValidator


class OnboardingOutputSchema(DictValidator):
    ALLOWED_EXTRA_KEYS = True
    VALIDATION_SPECIFICATION = {
        "candidate_email": (True, str,  None),
        "candidate_uid":   (True, str,  None),
        "org_id":          (True, str,  None),
        "org_alias":       (True, str,  None),
        "bucket_name":     (True, str,  None),
        "event_id":        (True, str,  None),
        "status":          (True, dict, None),
        "lookup_map":      (True, dict, None),
        "extra_info":      (False, (dict, type(None)), None),
    }

    def validate(self, json_payload, logger_func=None, message_wrapper=None):
        return super().validate(json_payload, logger_func, message_wrapper)
