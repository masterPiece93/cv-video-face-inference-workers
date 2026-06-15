"""Integration tests for common GCP services (Pub/Sub + Storage).

Tests the GCPStorageService, GCPPublisher, and GCPSubscriber against
real emulators to verify:
- Upload/download bytes, JSON, blob listing
- Pub/Sub publish + pull round-trip
- Error handling (missing blobs, missing topics)
"""
import io
import json
import time
from unittest.mock import MagicMock

import numpy as np
import pytest

from tests.integration.conftest import (
    BUCKET_NAME,
    DLQ_TOPIC,
    ENCODING_SUB,
    ENCODING_TOPIC,
    PROJECT_ID,
    publish_message,
    pull_messages,
)


pytestmark = pytest.mark.integration


class TestGCPStorageServiceIntegration:
    """Integration tests for GCPStorageService against fake-gcs."""

    def test_upload_and_download_bytes(self, storage_client):
        """Upload bytes → download → content matches."""
        content = b"hello integration test"
        blob_path = "test/upload_download.bin"

        result = storage_client.upload_bytes(content, BUCKET_NAME, blob_path)
        assert result is True

        downloaded = storage_client.download_bytes(BUCKET_NAME, blob_path)
        assert downloaded is not None
        assert downloaded.read() == content

    def test_upload_and_download_bytesio(self, storage_client):
        """Upload BytesIO → download → content matches."""
        content = io.BytesIO(b"bytesio content here")
        blob_path = "test/upload_download_bytesio.bin"

        result = storage_client.upload_bytes(content, BUCKET_NAME, blob_path)
        assert result is True

        downloaded = storage_client.download_bytes(BUCKET_NAME, blob_path)
        assert downloaded is not None
        assert downloaded.read() == b"bytesio content here"

    def test_upload_and_download_json(self, storage_client):
        """Upload dict as JSON → download → parsed correctly."""
        data = {"key": "value", "nested": {"a": 1}, "list": [1, 2, 3]}
        blob_path = "test/upload_download.json"

        result = storage_client.upload_json(data, BUCKET_NAME, blob_path)
        assert result is True

        downloaded = storage_client.download_json(BUCKET_NAME, blob_path)
        assert downloaded == data

    def test_upload_and_download_numpy(self, storage_client):
        """Upload numpy array as .npy → download → array matches."""
        arr = np.random.rand(5, 128).astype(np.float64)
        blob_path = "test/encodings.npy"

        buf = io.BytesIO()
        np.save(buf, arr)
        buf.seek(0)
        storage_client.upload_bytes(buf, BUCKET_NAME, blob_path, "application/octet-stream")

        downloaded = storage_client.download_bytes(BUCKET_NAME, blob_path)
        assert downloaded is not None
        loaded = np.load(downloaded)
        np.testing.assert_array_almost_equal(loaded, arr)

    def test_list_blobs(self, storage_client):
        """list_blobs returns all blobs under a prefix."""
        prefix = "test/list_blobs_test/"
        for i in range(5):
            storage_client.upload_bytes(
                f"content-{i}".encode(), BUCKET_NAME, f"{prefix}file_{i}.txt"
            )

        blobs = storage_client.list_blobs(BUCKET_NAME, prefix)
        assert len(blobs) == 5
        assert all(b.startswith(prefix) for b in blobs)

    def test_list_blobs_empty_prefix(self, storage_client):
        """list_blobs with non-existent prefix returns empty list."""
        blobs = storage_client.list_blobs(BUCKET_NAME, "nonexistent/prefix/")
        assert blobs == []

    def test_download_nonexistent_blob(self, storage_client):
        """Downloading a non-existent blob returns None."""
        result = storage_client.download_bytes(BUCKET_NAME, "does/not/exist.bin")
        assert result is None

    def test_download_json_nonexistent(self, storage_client):
        """download_json for non-existent blob returns None."""
        result = storage_client.download_json(BUCKET_NAME, "does/not/exist.json")
        assert result is None

    def test_blob_exists(self, storage_client):
        """blob_exists returns correct boolean."""
        blob_path = "test/exists_check.txt"
        storage_client.upload_bytes(b"exists", BUCKET_NAME, blob_path)

        assert storage_client.blob_exists(BUCKET_NAME, blob_path) is True
        assert storage_client.blob_exists(BUCKET_NAME, "does/not/exist.txt") is False

    def test_large_upload(self, storage_client):
        """Upload a larger payload (~1MB) succeeds."""
        large_data = b"x" * (1024 * 1024)
        blob_path = "test/large_file.bin"

        result = storage_client.upload_bytes(large_data, BUCKET_NAME, blob_path)
        assert result is True

        downloaded = storage_client.download_bytes(BUCKET_NAME, blob_path)
        assert downloaded is not None
        assert len(downloaded.read()) == 1024 * 1024


class TestGCPPubSubIntegration:
    """Integration tests for GCPPublisher + GCPSubscriber against emulator."""

    def test_publish_and_pull_message(self, publisher_client, subscriber_client):
        """Publish a message → pull from subscription → content matches."""
        payload = {"test": "message", "number": 42}
        msg_id = publish_message(publisher_client, ENCODING_TOPIC, payload)
        assert msg_id is not None

        messages = pull_messages(subscriber_client, ENCODING_SUB)
        assert len(messages) >= 1
        assert messages[0]["test"] == "message"
        assert messages[0]["number"] == 42

    def test_publish_multiple_messages(self, publisher_client, subscriber_client):
        """Publish multiple messages → all are received."""
        payloads = [{"index": i} for i in range(5)]
        for p in payloads:
            publish_message(publisher_client, ENCODING_TOPIC, p)

        time.sleep(1)
        messages = pull_messages(subscriber_client, ENCODING_SUB, max_messages=10)
        assert len(messages) >= 5
        received_indices = {m["index"] for m in messages}
        assert received_indices == {0, 1, 2, 3, 4}

    def test_publisher_wrapper_class(self, pubsub_setup):
        """GCPPublisher wrapper publishes successfully."""
        from common.services.cloud.gcp.pubsub.publisher import GCPPublisher

        publisher = GCPPublisher(project_id=PROJECT_ID, topic_name=ENCODING_TOPIC)
        msg_id = publisher.publish({"wrapper": "test"})
        assert msg_id is not None

    def test_subscriber_ensure_subscription_exists(self, pubsub_setup):
        """GCPSubscriber.ensure_subscription_exists is idempotent."""
        from common.services.cloud.gcp.pubsub.subscriber import GCPSubscriber

        sub = GCPSubscriber(
            project_id=PROJECT_ID,
            subscription_name=ENCODING_SUB,
            topic_name=ENCODING_TOPIC,
        )
        # Calling twice should not raise
        sub.ensure_subscription_exists()
        sub.ensure_subscription_exists()
        sub.close()

    def test_subscriber_creates_new_subscription(self, pubsub_setup):
        """GCPSubscriber creates a subscription that doesn't exist yet."""
        from google.api_core.exceptions import AlreadyExists
        from common.services.cloud.gcp.pubsub.subscriber import GCPSubscriber

        # First create a new topic
        publisher_client = pubsub_setup["publisher"]
        topic_name = "test-auto-create-topic"
        topic_path = publisher_client.topic_path(PROJECT_ID, topic_name)
        try:
            publisher_client.create_topic(request={"name": topic_path})
        except AlreadyExists:
            pass

        sub = GCPSubscriber(
            project_id=PROJECT_ID,
            subscription_name="test-auto-create-sub",
            topic_name=topic_name,
        )
        sub.ensure_subscription_exists()
        sub.close()
