"""Unit tests for GCPSubscriber."""
from unittest.mock import MagicMock, patch, call

import pytest
from google.api_core.exceptions import NotFound

from common.services.cloud.gcp.pubsub.subscriber import GCPSubscriber


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_subscriber(dlq_topic=None, sa_path=None, mock_client=None):
    """Return a GCPSubscriber with a mocked SubscriberClient."""
    with patch("common.services.cloud.gcp.pubsub.subscriber.pubsub_v1") as mock_pubsub:
        if mock_client is None:
            mock_client = MagicMock()
        mock_pubsub.SubscriberClient.return_value = mock_client
        # Ensure FlowControl / types namespace is accessible
        mock_pubsub.types = MagicMock()
        sub = GCPSubscriber(
            project_id="test-project",
            subscription_name="test-sub",
            topic_name="test-topic",
            dlq_topic=dlq_topic,
            sa_path=sa_path,
        )
        sub._client = mock_client  # keep after context exit
    return sub, mock_client


# ---------------------------------------------------------------------------
# __init__ — ADC vs service-account path
# ---------------------------------------------------------------------------

class TestGCPSubscriberInit:
    def test_default_adc_client_created(self):
        with patch("common.services.cloud.gcp.pubsub.subscriber.pubsub_v1") as mock_pubsub:
            mock_pubsub.SubscriberClient.return_value = MagicMock()
            mock_pubsub.types = MagicMock()
            sub = GCPSubscriber(project_id="proj", subscription_name="sub", topic_name="topic")
        mock_pubsub.SubscriberClient.assert_called_once_with()

    def test_sa_path_loads_credentials_and_passes_to_client(self, tmp_path):
        fake_sa = tmp_path / "sa.json"
        fake_sa.write_text("{}")
        fake_creds = MagicMock()

        with patch("common.services.cloud.gcp.pubsub.subscriber.pubsub_v1") as mock_pubsub, \
             patch("google.oauth2.service_account.Credentials.from_service_account_file",
                   return_value=fake_creds) as mock_sa:
            mock_pubsub.SubscriberClient.return_value = MagicMock()
            mock_pubsub.types = MagicMock()
            sub = GCPSubscriber(
                project_id="proj",
                subscription_name="sub",
                topic_name="topic",
                sa_path=str(fake_sa),
            )

        mock_sa.assert_called_once_with(str(fake_sa))
        mock_pubsub.SubscriberClient.assert_called_once_with(credentials=fake_creds)


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

class TestSubscriberProperties:
    def test_subscription_path_delegates_to_client(self):
        sub, mock_client = _make_subscriber()
        mock_client.subscription_path.return_value = "projects/test-project/subscriptions/test-sub"
        assert sub.subscription_path == "projects/test-project/subscriptions/test-sub"
        mock_client.subscription_path.assert_called_with("test-project", "test-sub")

    def test_topic_path_formatted_correctly(self):
        sub, _ = _make_subscriber()
        assert sub.topic_path == "projects/test-project/topics/test-topic"


# ---------------------------------------------------------------------------
# ensure_subscription_exists()
# ---------------------------------------------------------------------------

class TestEnsureSubscriptionExists:
    def test_does_not_create_when_subscription_exists(self):
        sub, mock_client = _make_subscriber()
        mock_client.subscription_path.return_value = "projects/p/subscriptions/s"
        mock_client.get_subscription.return_value = MagicMock()  # exists

        sub.ensure_subscription_exists()

        mock_client.get_subscription.assert_called_once()
        mock_client.create_subscription.assert_not_called()

    def test_creates_subscription_when_not_found(self):
        sub, mock_client = _make_subscriber()
        mock_client.subscription_path.return_value = "projects/p/subscriptions/s"
        mock_client.get_subscription.side_effect = NotFound("not found")

        sub.ensure_subscription_exists()

        mock_client.create_subscription.assert_called_once()

    def test_creates_subscription_without_dlq_by_default(self):
        sub, mock_client = _make_subscriber()
        mock_client.subscription_path.return_value = "projects/p/subscriptions/s"
        mock_client.get_subscription.side_effect = NotFound("not found")

        sub.ensure_subscription_exists()

        request = mock_client.create_subscription.call_args[1]["request"]
        assert "dead_letter_policy" not in request

    def test_creates_subscription_with_dlq_when_configured(self):
        sub, mock_client = _make_subscriber(dlq_topic="dead-letters")
        mock_client.subscription_path.return_value = "projects/p/subscriptions/s"
        mock_client.get_subscription.side_effect = NotFound("not found")

        sub.ensure_subscription_exists()

        request = mock_client.create_subscription.call_args[1]["request"]
        assert "dead_letter_policy" in request

    def test_dlq_path_includes_project_and_topic(self):
        sub, mock_client = _make_subscriber(dlq_topic="dead-letters")
        mock_client.subscription_path.return_value = "projects/p/subscriptions/s"
        mock_client.get_subscription.side_effect = NotFound("not found")

        sub.ensure_subscription_exists()

        request = mock_client.create_subscription.call_args[1]["request"]
        dlq = request["dead_letter_policy"]
        assert "test-project" in dlq.dead_letter_topic
        assert "dead-letters" in dlq.dead_letter_topic


# ---------------------------------------------------------------------------
# subscribe()
# ---------------------------------------------------------------------------

class TestSubscribe:
    def test_subscribe_calls_client_subscribe(self):
        sub, mock_client = _make_subscriber()
        mock_client.subscription_path.return_value = "projects/p/subscriptions/s"

        mock_future = MagicMock()
        mock_future.result.return_value = None
        mock_client.subscribe.return_value = mock_future

        cb = MagicMock()
        with patch("common.services.cloud.gcp.pubsub.subscriber.pubsub_v1") as mock_pubsub:
            mock_pubsub.types.FlowControl.return_value = MagicMock()
            sub._client = mock_client  # re-assign after patch scope
            sub.subscribe(cb)

        mock_client.subscribe.assert_called_once()
        call_kwargs = mock_client.subscribe.call_args
        assert call_kwargs[1]["callback"] is cb or call_kwargs[0][1] is cb or cb in call_kwargs[0]

    def test_subscribe_cancels_future_on_keyboard_interrupt(self):
        sub, mock_client = _make_subscriber()
        mock_client.subscription_path.return_value = "projects/p/subscriptions/s"

        mock_future = MagicMock()
        mock_future.result.side_effect = KeyboardInterrupt()
        mock_client.subscribe.return_value = mock_future

        cb = MagicMock()
        with patch("common.services.cloud.gcp.pubsub.subscriber.pubsub_v1") as mock_pubsub:
            mock_pubsub.types.FlowControl.return_value = MagicMock()
            sub._client = mock_client
            sub.subscribe(cb)  # must not raise

        mock_future.cancel.assert_called_once()

    def test_subscribe_stores_future(self):
        sub, mock_client = _make_subscriber()
        mock_client.subscription_path.return_value = "projects/p/subscriptions/s"

        mock_future = MagicMock()
        mock_future.result.return_value = None
        mock_client.subscribe.return_value = mock_future

        cb = MagicMock()
        with patch("common.services.cloud.gcp.pubsub.subscriber.pubsub_v1") as mock_pubsub:
            mock_pubsub.types.FlowControl.return_value = MagicMock()
            sub._client = mock_client
            sub.subscribe(cb)

        assert sub._future is mock_future


# ---------------------------------------------------------------------------
# close()
# ---------------------------------------------------------------------------

class TestSubscriberClose:
    def test_close_calls_client_close(self):
        sub, mock_client = _make_subscriber()
        sub._future = None
        sub.close()
        mock_client.close.assert_called_once()

    def test_close_cancels_active_future(self):
        sub, mock_client = _make_subscriber()
        mock_future = MagicMock()
        sub._future = mock_future
        sub.close()
        mock_future.cancel.assert_called_once()

    def test_close_without_future_does_not_raise(self):
        sub, mock_client = _make_subscriber()
        sub._future = None
        sub.close()  # must not raise
