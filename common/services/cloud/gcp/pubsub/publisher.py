"""GCP Pub/Sub Publisher Service."""
import json
import logging
from typing import Optional, Union

from google.cloud import pubsub_v1

logger = logging.getLogger(__name__)

__all__ = ["GCPPublisher"]


class GCPPublisher:
    """Publishes messages to a GCP Pub/Sub topic."""

    def __init__(
        self,
        project_id: str,
        topic_name: str,
        sa_path: Optional[str] = None,
    ):
        """Initialize the publisher.

        Args:
            project_id: GCP project ID.
            topic_name: Pub/Sub topic name.
            sa_path: Path to service account JSON (optional, uses ADC if None).
        """
        self.project_id = project_id
        self.topic_name = topic_name

        if sa_path:
            from google.oauth2 import service_account
            credentials = service_account.Credentials.from_service_account_file(sa_path)
            self._client = pubsub_v1.PublisherClient(credentials=credentials)
        else:
            self._client = pubsub_v1.PublisherClient()

    @property
    def topic_path(self) -> str:
        return self._client.topic_path(self.project_id, self.topic_name)

    def publish(
        self,
        payload: Union[dict, str, bytes],
        **attributes: str,
    ) -> Optional[str]:
        """Publish a message to the topic.

        Args:
            payload: Message payload (dict serialized to JSON, str, or bytes).
            **attributes: Optional Pub/Sub message attributes (key=value strings).

        Returns:
            Published message ID, or None on failure.
        """
        try:
            if isinstance(payload, dict):
                data = json.dumps(payload).encode("utf-8")
            elif isinstance(payload, str):
                data = payload.encode("utf-8")
            else:
                data = payload

            future = self._client.publish(self.topic_path, data=data, **attributes)
            message_id = future.result()
            logger.info(
                f"Published message {message_id} to {self.topic_path}"
            )
            return message_id
        except Exception as e:
            logger.error(f"Failed to publish to {self.topic_path}: {e}")
            return None

    def close(self) -> None:
        """Close the publisher client."""
        self._client.close()
        logger.info("Publisher client closed")
