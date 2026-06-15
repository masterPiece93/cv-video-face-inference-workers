"""Input message schema and validator for Face Encoding Worker.

Expected PubSub message from GoLang Source Worker:
{
    "candidate_email": "jane.doe@example.com",
    "candidate_uid": "cand-98765",
    "extra_info": {...},
    "org_id": "org-42",
    "org_alias": "acme-inc",
    "bucket_name": "tdx-dev-external-sheet-candidature-records",
    "event_id": "xxxzzzqqqwww",
    "lookup_map": {
        "profile": "profile",
        "interviews": ["interview_1", "interview_2"]
    }
}
"""
from typing import Optional, Callable

from common.utils.validators import DictValidator

__all__ = ["EncodingInputSchema"]


class EncodingInputSchema(DictValidator):
    """Validates incoming PubSub message for the face encoding worker."""

    ALLOWED_EXTRA_KEYS = True

    VALIDATION_SPECIFICATION = {
        "candidate_email":  (True,  str,  None),
        "candidate_uid":    (True,  str,  None),
        "org_id":           (True,  str,  None),
        "org_alias":        (True,  str,  None),
        "bucket_name":      (True,  str,  None),
        "event_id":         (True,  str,  None),
        "lookup_map":       (True,  dict, None),
        "extra_info":       (False, (dict, type(None)), None),
    }

    def validate(
        self,
        json_payload: dict,
        logger_func: Optional[Callable] = None,
        message_wrapper: Optional[Callable] = None,
    ) -> None:
        """Validate the encoding input payload.

        Args:
            json_payload: Parsed message body dict.
            logger_func: Optional logging callback.
            message_wrapper: Optional message string formatter.

        Returns:
            None on success; raises SchemaViolation on failure.
        """
        return super().validate(json_payload, logger_func, message_wrapper)
