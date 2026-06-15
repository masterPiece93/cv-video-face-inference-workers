"""Input schema for Face Verification Worker."""
from typing import Optional, Callable

from common.utils.validators import DictValidator

__all__ = ["VerificationInputSchema"]


class VerificationInputSchema(DictValidator):
    """Validates incoming PubSub message for the face verification worker."""

    ALLOWED_EXTRA_KEYS = True

    VALIDATION_SPECIFICATION = {
        "candidate_email":    (True,  str,  None),
        "candidate_uid":      (True,  str,  None),
        "org_id":             (True,  str,  None),
        "org_alias":          (True,  str,  None),
        "bucket_name":        (True,  str,  None),
        "event_id":           (True,  str,  None),
        "lookup_map":         (True,  dict, None),
        "sampled_frames":     (False, dict, {}),
        "extra_info":         (False, (dict, type(None)), None),
    }

    def validate(
        self,
        json_payload: dict,
        logger_func: Optional[Callable] = None,
        message_wrapper: Optional[Callable] = None,
    ) -> None:
        return super().validate(json_payload, logger_func, message_wrapper)
