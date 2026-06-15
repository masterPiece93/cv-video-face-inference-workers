"""GCP Pub/Sub Subscriber Service."""
import logging
from typing import Callable, Optional

from google.api_core.exceptions import NotFound
from google.cloud import pubsub_v1
from google.cloud.pubsub_v1.futures import Future as StreamingPullFuture
from google.cloud.pubsub_v1.types import DeadLetterPolicy

logger = logging.getLogger(__name__)

__all__ = ["GCPSubscriber"]


class GCPSubscriber:
    """Manages a GCP Pub/Sub subscription.

    Handles subscriber lifecycle: create, subscribe, listen, teardown.
    Supports dead-letter policy and flow control.
    """

    def __init__(
        self,
        project_id: str,
        subscription_name: str,
        topic_name: str,
        max_messages: int = 10,
        max_lease_duration: int = 60,
        max_duration_per_lease_extension: int = 10,
        dlq_topic: Optional[str] = None,
        max_delivery_attempts: int = 5,
        sa_path: Optional[str] = None,
    ):
        """Initialize the subscriber.

        Args:
            project_id: GCP project ID.
            subscription_name: Pub/Sub subscription name.
            topic_name: Pub/Sub topic name.
            max_messages: Max outstanding messages (flow control).
            max_lease_duration: Max lease duration in seconds.
            dlq_topic: Dead-letter topic name (optional).
            max_delivery_attempts: Max delivery attempts before DLQ.
            sa_path: Path to service account JSON (optional, uses ADC if None).
        """
        self.project_id = project_id
        self.subscription_name = subscription_name
        self.topic_name = topic_name
        self.max_messages = max_messages
        self.max_lease_duration = max_lease_duration
        self.max_duration_per_lease_extension = max_duration_per_lease_extension
        self.dlq_topic = dlq_topic
        self.max_delivery_attempts = max_delivery_attempts

        if sa_path:
            from google.oauth2 import service_account
            credentials = service_account.Credentials.from_service_account_file(sa_path)
            self._client = pubsub_v1.SubscriberClient(credentials=credentials)
        else:
            self._client = pubsub_v1.SubscriberClient()

        self._future: Optional[StreamingPullFuture] = None

    @property
    def subscription_path(self) -> str:
        return self._client.subscription_path(self.project_id, self.subscription_name)

    @property
    def topic_path(self) -> str:
        return f"projects/{self.project_id}/topics/{self.topic_name}"

    def ensure_subscription_exists(self) -> None:
        """Create subscription if it does not exist."""
        try:
            self._client.get_subscription(request={"subscription": self.subscription_path})
            logger.info(f"Subscription already exists: {self.subscription_path}")
        except NotFound:
            logger.warning(f"Subscription not found, creating: {self.subscription_path}")
            kwargs = dict(name=self.subscription_path, topic=self.topic_path)
            if self.dlq_topic:
                dlq_path = f"projects/{self.project_id}/topics/{self.dlq_topic}"
                kwargs["dead_letter_policy"] = DeadLetterPolicy(
                    dead_letter_topic=dlq_path,
                    max_delivery_attempts=self.max_delivery_attempts,
                )
            self._client.create_subscription(request=kwargs)
            logger.info(f"Subscription created: {self.subscription_path}")

    def subscribe(self, callback: Callable) -> None:
        """Start listening to the subscription.

        Blocks until interrupted (KeyboardInterrupt) or a fatal error occurs.

        Args:
            callback: Function called for each received message.
                      Signature: callback(message: pubsub_v1.types.PubsubMessage)
        """
        flow_control = pubsub_v1.types.FlowControl(
            max_messages=self.max_messages,
            max_lease_duration=self.max_lease_duration,
            max_duration_per_lease_extension=self.max_duration_per_lease_extension
        )
        logger.info(
            f"Subscribing to {self.subscription_path} "
            f"[max_messages={self.max_messages}]"
        )
        self._future = self._client.subscribe(
            self.subscription_path,
            callback=callback,
            flow_control=flow_control,
        )
        try:
            self._future.result()
        except KeyboardInterrupt:
            self._future.cancel()
            logger.info("Subscriber stopped via KeyboardInterrupt")

    def close(self) -> None:
        """Close the subscriber client."""
        if self._future:
            self._future.cancel()
        self._client.close()
        logger.info("Subscriber client closed")
