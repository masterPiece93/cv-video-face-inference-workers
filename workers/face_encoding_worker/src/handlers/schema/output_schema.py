"""Output message schema for Face Encoding Worker.

Message published to the video verification topic:
{
    "candidate_email": "jane.doe@example.com",
    "candidate_uid": "cand-98765",
    "extra_info": {...},
    "org_id": "org-42",
    "org_alias": "acme-inc",
    "bucket_name": "tdx-dev-...",
    "event_id": "xxxzzzqqqwww",
    "lookup_map": {
        "profile": "profile",
        "interviews": ["interview_1", "interview_2"]
    },
    "sampled_frames": {
        "profile": ["1.png", "2.png"],
        "interviews": {
            "interview_1": ["1.png"],
            "interview_2": ["1.png"]
        }
    }
}
"""
from typing import Optional, Callable

from common.utils.validators import DictValidator

__all__ = ["EncodingOutputSchema"]


class EncodingOutputSchema(DictValidator):
    """Validates outgoing PubSub message from the face encoding worker."""

    ALLOWED_EXTRA_KEYS = True

    VALIDATION_SPECIFICATION = {
        "candidate_email":   (True, str,  None),
        "candidate_uid":     (True, str,  None),
        "org_id":            (True, str,  None),
        "org_alias":         (True, str,  None),
        "bucket_name":       (True, str,  None),
        "event_id":          (True, str,  None),
        "lookup_map":        (True, dict, None),
        "sampled_frames":    (True, dict, None),
        "extra_info":        (False, (dict, type(None)), None),
    }

    def validate(
        self,
        json_payload: dict,
        logger_func: Optional[Callable] = None,
        message_wrapper: Optional[Callable] = None,
    ) -> None:
        return super().validate(json_payload, logger_func, message_wrapper)
