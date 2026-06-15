"""Input handler for Onboarding Verification Worker."""
import json
import logging

from common.services.errors import NonRecoverableError, RecoverableError
from workers.onboarding_verification_worker.services.verification import OnboardingVerificationService
from workers.onboarding_verification_worker.src.handlers.schema.input_schema import OnboardingInputSchema

logger = logging.getLogger(__name__)
_schema = OnboardingInputSchema()


def handle_message(message, verification_service: OnboardingVerificationService) -> None:
    event_id = "unknown"
    try:
        raw = message.data
        payload = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
        event_id = payload.get("event_id", "unknown")
        logger.info(
            f"[{event_id}] Message received: {json.dumps(payload, default=str)}"
        )

        _schema.validate(
            payload,
            logger_func=lambda msg, level="info": getattr(logger, level)(f"[{event_id}] {msg}"),
            message_wrapper=lambda m: m,
        )

        verification_service.process(payload)
        message.ack()
        logger.info(f"[{event_id}] Message acknowledged")

    except NonRecoverableError as e:
        logger.error(f"[{event_id}] Non-recoverable error: {e}")
        message.ack()

    except RecoverableError as e:
        logger.warning(f"[{event_id}] Recoverable error (will retry): {e}")
        message.nack()

    except json.JSONDecodeError as e:
        logger.error(f"[{event_id}] JSON decode error: {e}")
        message.ack()

    except Exception as e:
        logger.critical(f"[{event_id}] Unexpected error: {e}", exc_info=True)
        message.ack()
