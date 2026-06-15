"""Unit tests for GCPPublisher."""
import json
from unittest.mock import MagicMock, patch, call

import pytest

from common.services.cloud.gcp.pubsub.publisher import GCPPublisher


# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------

def _make_publisher(sa_path=None, mock_client=None):
    """Return a GCPPublisher with a fully mocked pubsub_v1.PublisherClient."""
    with patch("common.services.cloud.gcp.pubsub.publisher.pubsub_v1") as mock_pubsub:
        if mock_client is None:
            mock_client = MagicMock()
        mock_pubsub.PublisherClient.return_value = mock_client
        pub = GCPPublisher(
            project_id="test-project",
            topic_name="test-topic",
            sa_path=sa_path,
        )
        pub._client = mock_client  # keep the mock accessible after context exit
    return pub, mock_client


# ---------------------------------------------------------------------------
# __init__ — ADC vs service-account path
# ---------------------------------------------------------------------------

class TestGCPPublisherInit:
    def test_default_adc_client_created(self):
        with patch("common.services.cloud.gcp.pubsub.publisher.pubsub_v1") as mock_pubsub:
            mock_pubsub.PublisherClient.return_value = MagicMock()
            pub = GCPPublisher(project_id="proj", topic_name="topic")
        mock_pubsub.PublisherClient.assert_called_once_with()

    def test_sa_path_loads_credentials_and_passes_to_client(self, tmp_path):
        fake_sa = tmp_path / "sa.json"
        fake_sa.write_text("{}")
        fake_creds = MagicMock()

        with patch("common.services.cloud.gcp.pubsub.publisher.pubsub_v1") as mock_pubsub, \
             patch("google.oauth2.service_account.Credentials.from_service_account_file",
                   return_value=fake_creds) as mock_sa:
            mock_pubsub.PublisherClient.return_value = MagicMock()
            pub = GCPPublisher(project_id="proj", topic_name="topic", sa_path=str(fake_sa))

        mock_sa.assert_called_once_with(str(fake_sa))
        mock_pubsub.PublisherClient.assert_called_once_with(credentials=fake_creds)


# ---------------------------------------------------------------------------
# topic_path property
# ---------------------------------------------------------------------------

class TestTopicPath:
    def test_topic_path_delegates_to_client(self):
        pub, mock_client = _make_publisher()
        mock_client.topic_path.return_value = "projects/test-project/topics/test-topic"
        assert pub.topic_path == "projects/test-project/topics/test-topic"
        mock_client.topic_path.assert_called_with("test-project", "test-topic")


# ---------------------------------------------------------------------------
# publish() — payload type handling
# ---------------------------------------------------------------------------

class TestPublishPayloadTypes:
    def _setup(self, return_message_id="msg-001"):
        pub, mock_client = _make_publisher()
        future = MagicMock()
        future.result.return_value = return_message_id
        mock_client.publish.return_value = future
        mock_client.topic_path.return_value = "projects/test-project/topics/test-topic"
        return pub, mock_client

    def test_dict_payload_serialised_as_json(self):
        pub, mock_client = self._setup()
        pub.publish({"key": "value"})
        _, kwargs = mock_client.publish.call_args
        assert kwargs["data"] == json.dumps({"key": "value"}).encode("utf-8")

    def test_str_payload_encoded_as_utf8(self):
        pub, mock_client = self._setup()
        pub.publish("hello world")
        _, kwargs = mock_client.publish.call_args
        assert kwargs["data"] == b"hello world"

    def test_bytes_payload_passed_through_unchanged(self):
        pub, mock_client = self._setup()
        raw = b"\x00\x01\x02"
        pub.publish(raw)
        _, kwargs = mock_client.publish.call_args
        assert kwargs["data"] == raw

    def test_returns_message_id_on_success(self):
        pub, mock_client = self._setup(return_message_id="abc-123")
        result = pub.publish({"msg": 1})
        assert result == "abc-123"

    def test_attributes_forwarded_to_client(self):
        pub, mock_client = self._setup()
        pub.publish(b"data", origin="test", version="1")
        _, kwargs = mock_client.publish.call_args
        assert kwargs["origin"] == "test"
        assert kwargs["version"] == "1"


# ---------------------------------------------------------------------------
# publish() — failure path
# ---------------------------------------------------------------------------

class TestPublishFailure:
    def test_exception_returns_none(self):
        pub, mock_client = _make_publisher()
        mock_client.topic_path.return_value = "projects/p/topics/t"
        mock_client.publish.side_effect = RuntimeError("network error")
        result = pub.publish({"msg": "hello"})
        assert result is None

    def test_future_result_exception_returns_none(self):
        pub, mock_client = _make_publisher()
        mock_client.topic_path.return_value = "projects/p/topics/t"
        future = MagicMock()
        future.result.side_effect = Exception("timeout")
        mock_client.publish.return_value = future
        result = pub.publish(b"data")
        assert result is None


# ---------------------------------------------------------------------------
# close()
# ---------------------------------------------------------------------------

class TestPublisherClose:
    def test_close_calls_client_close(self):
        pub, mock_client = _make_publisher()
        pub.close()
        mock_client.close.assert_called_once()
