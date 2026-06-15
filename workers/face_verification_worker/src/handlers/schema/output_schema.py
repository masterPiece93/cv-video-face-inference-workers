"""Output schema for Face Verification Worker."""
from typing import Optional, Callable

from common.utils.validators import DictValidator

__all__ = ["VerificationOutputSchema"]


class VerificationOutputSchema(DictValidator):
    """Validates outgoing PubSub message from the face verification worker."""

    ALLOWED_EXTRA_KEYS = True

    VALIDATION_SPECIFICATION = {
        "candidate_email": (True, str,  None),
        "candidate_uid":   (True, str,  None),
        "org_id":          (True, str,  None),
        "org_alias":       (True, str,  None),
        "bucket_name":     (True, str,  None),
        "event_id":        (True, str,  None),
        "status":          (True, dict, None),
        "sampled_frames":  (True, dict, None),
        "lookup_map":      (True, dict, None),
        "extra_info":      (False, (dict, type(None)), None),
    }

    def validate(
        self,
        json_payload: dict,
        logger_func: Optional[Callable] = None,
        message_wrapper: Optional[Callable] = None,
    ) -> None:
        return super().validate(json_payload, logger_func, message_wrapper)
