"""Output handler for Face Encoding Worker.

Publishes the encoded result downstream to the video verification topic.
"""
import json
import logging
from typing import Optional

from common.services.cloud.gcp.pubsub.publisher import GCPPublisher
from workers.face_encoding_worker.src.handlers.schema.output_schema import EncodingOutputSchema

logger = logging.getLogger(__name__)
_schema = EncodingOutputSchema()


class EncodingOutputHandler:
    """Publishes the encoding result to the downstream Pub/Sub topic."""

    def __init__(self, publisher: GCPPublisher):
        """Initialize with a GCPPublisher.

        Args:
            publisher: Configured publisher pointing to the verification topic.
        """
        self._publisher = publisher

    def publish(self, payload: dict) -> Optional[str]:
        """Validate and publish the encoding output.

        Args:
            payload: Output message dict to publish.

        Returns:
            Published message ID, or None on failure.
        """
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
            logger.info(f"[{event_id}] Published encoding result, message_id={msg_id}")
            return msg_id
        except Exception as e:
            logger.error(f"[{event_id}] Failed to publish encoding result: {e}")
            raise
