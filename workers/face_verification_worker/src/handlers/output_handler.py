"""Output handler for Face Verification Worker."""
import json
import logging
from typing import Optional

from common.services.cloud.gcp.pubsub.publisher import GCPPublisher
from workers.face_verification_worker.src.handlers.schema.output_schema import VerificationOutputSchema

logger = logging.getLogger(__name__)
_schema = VerificationOutputSchema()


class VerificationOutputHandler:
    """Publishes the verification result to the downstream Pub/Sub topic."""

    def __init__(self, publisher: GCPPublisher):
        self._publisher = publisher

    def publish(self, payload: dict) -> Optional[str]:
        event_id = payload.get("event_id", "unknown")
        try:
            _schema.validate(
                payload,
                logger_func=lambda msg, level="info": getattr(logger, level)(
                    f"[{event_id}] {msg}"
                ),
                message_wrapper=lambda m: m,
            )
            logger.info(
                f"[{event_id}] Publishing message: {json.dumps(payload, default=str)}"
            )
            msg_id = self._publisher.publish(payload)
            logger.info(f"[{event_id}] Published verification result, message_id={msg_id}")
            return msg_id
        except Exception as e:
            logger.error(f"[{event_id}] Failed to publish verification result: {e}")
            raise
