"""Common error types for worker error handling.

Two-tier error strategy:
    - RecoverableError  → message.nack()  (will be retried by PubSub)
    - NonRecoverableError → message.ack() (won't be retried; dead-letter if needed)
"""

__all__ = ["RecoverableError", "NonRecoverableError"]


class RecoverableError(Exception):
    """Transient error that can be resolved on retry.

    Examples: temporary network failures, GCP service unavailability.
    """


class NonRecoverableError(Exception):
    """Permanent error that cannot be resolved by retrying.

    Examples: missing required fields, malformed JSON, encoding failure.
    """
