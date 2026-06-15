"""Generic Pub/Sub load publisher.

Publishes messages to a topic according to a LoadProfile.
Reusable across any Pub/Sub-based worker project.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from google.cloud import pubsub_v1

from tests.load.core.types import LoadProfile, LoadTestConfig

logger = logging.getLogger(__name__)


class LoadPublisher:
    """Publishes load test messages to a Pub/Sub topic.

    Supports:
    - Multiple load profiles (ramp, spike, soak, stress)
    - Injects publish_time attribute for latency measurement
    - Configurable payload generator callback
    """

    def __init__(self, config: LoadTestConfig):
        self._config = config
        self._client: Optional[pubsub_v1.PublisherClient] = None
        self._published_ids: List[str] = []

    def _get_client(self) -> pubsub_v1.PublisherClient:
        if self._client is None:
            if self._config.sa_path:
                from google.oauth2 import service_account
                creds = service_account.Credentials.from_service_account_file(
                    self._config.sa_path
                )
                self._client = pubsub_v1.PublisherClient(credentials=creds)
            else:
                self._client = pubsub_v1.PublisherClient()
        return self._client

    @property
    def topic_path(self) -> str:
        client = self._get_client()
        return client.topic_path(self._config.project_id, self._config.topic_name)

    def publish_load(
        self,
        payload_generator: Callable[[int], Dict[str, Any]],
        on_publish: Optional[Callable[[int, str, float], None]] = None,
    ) -> List[Dict[str, Any]]:
        """Publish messages according to the configured load profile.

        Args:
            payload_generator: Function(message_index) → payload dict.
            on_publish: Optional callback(index, message_id, publish_time).

        Returns:
            List of {event_id, message_id, publish_time} dicts.
        """
        client = self._get_client()
        profile = self._config.profile
        results = []

        logger.info(
            f"Starting load publish: {profile.total_messages} messages, "
            f"profile={profile.load_type.value}, "
            f"duration={profile.duration_seconds}s"
        )

        for i in range(profile.total_messages):
            delay = profile.get_delay_for_message(i)
            if delay > 0 and i > 0:
                time.sleep(delay)

            payload = payload_generator(i)
            publish_time = time.time()

            # Inject publish_time as attribute for latency measurement
            data = json.dumps(payload).encode("utf-8")
            future = client.publish(
                self.topic_path,
                data=data,
                publish_time_epoch=str(publish_time),
                load_test_run_id=self._config.run_id,
            )

            try:
                message_id = future.result(timeout=30)
                event_id = payload.get("event_id", f"msg_{i}")
                record = {
                    "event_id": event_id,
                    "message_id": message_id,
                    "publish_time": publish_time,
                    "index": i,
                }
                results.append(record)

                if on_publish:
                    on_publish(i, message_id, publish_time)

            except Exception as e:
                logger.error(f"Failed to publish message {i}: {e}")
                results.append({
                    "event_id": payload.get("event_id", f"msg_{i}"),
                    "message_id": None,
                    "publish_time": publish_time,
                    "index": i,
                    "error": str(e),
                })

        logger.info(f"Load publish complete: {len(results)} messages sent")
        return results

    def close(self):
        """Close the publisher client."""
        if self._client:
            try:
                self._client.close()
            except AttributeError:
                pass
            self._client = None
