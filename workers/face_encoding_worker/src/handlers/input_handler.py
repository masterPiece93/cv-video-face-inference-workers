"""Input handler for Face Encoding Worker.

Responsibilities:
- Parse and validate the incoming PubSub message
- Invoke the VideoFaceEncodingService
- Handle recoverable vs non-recoverable errors
"""
import json
import logging

from common.services.errors import NonRecoverableError, RecoverableError
from workers.face_encoding_worker.services.encoding import VideoFaceEncodingService
from workers.face_encoding_worker.src.handlers.schema.input_schema import EncodingInputSchema

logger = logging.getLogger(__name__)
_schema = EncodingInputSchema()


def handle_message(message, encoding_service: VideoFaceEncodingService) -> None:
    """Process a single PubSub message.

    Args:
        message: google.cloud.pubsub_v1 PubsubMessage.
        encoding_service: Initialized VideoFaceEncodingService.
    """
    event_id = "unknown"
    try:
        raw = message.data
        payload = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
        event_id = payload.get("event_id", "unknown")

        logger.info(
            f"[{event_id}] Message received: {json.dumps(payload, default=str)}"
        )

        # Validate schema
        _schema.validate(
            payload,
            logger_func=lambda msg, level="info": getattr(logger, level)(f"[{event_id}] {msg}"),
            message_wrapper=lambda m: m,
        )

        logger.info(
            f"[{event_id}] Processing candidate={payload['candidate_email']} "
            f"uid={payload['candidate_uid']}"
        )

        # Run core encoding pipeline
        encoding_service.process(payload)

        message.ack()
        logger.info(f"[{event_id}] Message acknowledged")

    except NonRecoverableError as e:
        logger.error(f"[{event_id}] Non-recoverable error: {e}")
        message.ack()  # ack to prevent infinite retry

    except RecoverableError as e:
        logger.warning(f"[{event_id}] Recoverable error (will retry): {e}")
        message.nack()

    except json.JSONDecodeError as e:
        logger.error(f"[{event_id}] JSON decode error: {e}")
        message.ack()

    except Exception as e:
        logger.critical(f"[{event_id}] Unexpected error: {e}", exc_info=True)
        message.ack()
