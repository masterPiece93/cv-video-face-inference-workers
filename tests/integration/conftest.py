"""
Shared fixtures for integration tests.

Provides:
- GCP Pub/Sub emulator (testcontainers)
- Fake GCS server (testcontainers)
- Pre-configured GCPStorageService, GCPPublisher, GCPSubscriber wrappers
- Helper functions to seed test data into emulators
- Reusable test payloads

All fixtures use session scope where possible to avoid repeated container startups.
"""
import io
import json
import os
import time
from typing import Generator

import numpy as np
import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.waiting_utils import wait_for_logs

# ─── Constants ────────────────────────────────────────────────────────────────

PROJECT_ID = "integration-test-project"
BUCKET_NAME = "integration-test-bucket"
ENCODING_TOPIC = "test-encoding-topic"
ENCODING_SUB = "test-encoding-sub"
VERIFICATION_TOPIC = "test-verification-topic"
VERIFICATION_SUB = "test-verification-sub"
ONBOARDING_TOPIC = "test-onboarding-topic"
ONBOARDING_SUB = "test-onboarding-sub"
DLQ_TOPIC = "test-dlq-topic"

# Candidate test data
TEST_CANDIDATE_EMAIL = "integration.test@example.com"
TEST_CANDIDATE_UID = "cand-integration-001"
TEST_ORG_ID = "org-integration-42"
TEST_ORG_ALIAS = "integration-org"
TEST_EVENT_ID = "evt-integration-001"


# ─── Container Fixtures (session-scoped) ─────────────────────────────────────


@pytest.fixture(scope="session")
def pubsub_emulator() -> Generator[str, None, None]:
    """Start a GCP Pub/Sub emulator container and return host:port."""
    container = (
        DockerContainer("google/cloud-sdk:emulators")
        .with_exposed_ports(8085)
        .with_command(
            "gcloud beta emulators pubsub start "
            f"--host-port=0.0.0.0:8085 --project={PROJECT_ID}"
        )
    )
    container.start()
    wait_for_logs(container, "Server started", timeout=30)
    host = container.get_container_host_ip()
    port = container.get_exposed_port(8085)
    emulator_host = f"{host}:{port}"

    # Set env var so google-cloud-pubsub uses emulator
    os.environ["PUBSUB_EMULATOR_HOST"] = emulator_host

    yield emulator_host

    del os.environ["PUBSUB_EMULATOR_HOST"]
    container.stop()


@pytest.fixture(scope="session")
def fake_gcs() -> Generator[str, None, None]:
    """Start a fake-gcs-server container and return the base URL."""
    container = (
        DockerContainer("fsouza/fake-gcs-server:latest")
        .with_exposed_ports(4443)
        .with_command("-scheme http -port 4443 -public-host localhost:4443")
    )
    container.start()
    wait_for_logs(container, "server started", timeout=30)
    host = container.get_container_host_ip()
    port = container.get_exposed_port(4443)
    base_url = f"http://{host}:{port}"

    # Set env var so google-cloud-storage uses fake-gcs
    os.environ["STORAGE_EMULATOR_HOST"] = base_url

    yield base_url

    del os.environ["STORAGE_EMULATOR_HOST"]
    container.stop()


# ─── Service Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def storage_client(fake_gcs: str):
    """Create a GCPStorageService pointing at the fake-gcs emulator."""
    from common.services.cloud.gcp.storage import GCPStorageService

    client = GCPStorageService()
    # Create the test bucket
    try:
        client._client.create_bucket(BUCKET_NAME)
    except Exception:
        pass  # Bucket may already exist
    return client


@pytest.fixture(scope="session")
def pubsub_setup(pubsub_emulator: str):
    """Create all topics and subscriptions needed for integration tests."""
    from google.cloud import pubsub_v1
    from google.api_core.exceptions import AlreadyExists

    publisher = pubsub_v1.PublisherClient()
    subscriber = pubsub_v1.SubscriberClient()

    topics = [ENCODING_TOPIC, VERIFICATION_TOPIC, ONBOARDING_TOPIC, DLQ_TOPIC]
    subscriptions = [
        (ENCODING_TOPIC, ENCODING_SUB),
        (VERIFICATION_TOPIC, VERIFICATION_SUB),
        (ONBOARDING_TOPIC, ONBOARDING_SUB),
    ]

    for topic_name in topics:
        topic_path = publisher.topic_path(PROJECT_ID, topic_name)
        try:
            publisher.create_topic(request={"name": topic_path})
        except AlreadyExists:
            pass

    for topic_name, sub_name in subscriptions:
        topic_path = publisher.topic_path(PROJECT_ID, topic_name)
        sub_path = subscriber.subscription_path(PROJECT_ID, sub_name)
        try:
            subscriber.create_subscription(
                request={"name": sub_path, "topic": topic_path}
            )
        except AlreadyExists:
            pass

    return {"publisher": publisher, "subscriber": subscriber}


@pytest.fixture
def publisher_client(pubsub_setup) -> "pubsub_v1.PublisherClient":
    """Return the raw Pub/Sub publisher client."""
    return pubsub_setup["publisher"]


@pytest.fixture
def subscriber_client(pubsub_setup) -> "pubsub_v1.SubscriberClient":
    """Return the raw Pub/Sub subscriber client."""
    return pubsub_setup["subscriber"]


# ─── Test Data Helpers ────────────────────────────────────────────────────────


@pytest.fixture
def base_encoding_payload() -> dict:
    """Minimal valid payload for the face_encoding_worker."""
    return {
        "candidate_email": TEST_CANDIDATE_EMAIL,
        "candidate_uid": TEST_CANDIDATE_UID,
        "extra_info": {"env": "integration-test"},
        "org_id": TEST_ORG_ID,
        "org_alias": TEST_ORG_ALIAS,
        "bucket_name": BUCKET_NAME,
        "event_id": TEST_EVENT_ID,
        "lookup_map": {
            "profile": "profile",
            "interviews": ["interview_1"],
        },
    }


@pytest.fixture
def base_verification_payload() -> dict:
    """Minimal valid payload for the face_verification_worker."""
    return {
        "candidate_email": TEST_CANDIDATE_EMAIL,
        "candidate_uid": TEST_CANDIDATE_UID,
        "extra_info": {"env": "integration-test"},
        "org_id": TEST_ORG_ID,
        "org_alias": TEST_ORG_ALIAS,
        "bucket_name": BUCKET_NAME,
        "event_id": TEST_EVENT_ID,
        "lookup_map": {
            "profile": "profile",
            "interviews": ["interview_1"],
        },
        "sampled_frames": {
            "profile": ["1.png"],
            "interviews": {"interview_1": ["1.png"]},
        },
    }


@pytest.fixture
def base_onboarding_payload() -> dict:
    """Minimal valid payload for the onboarding_verification_worker."""
    return {
        "candidate_email": TEST_CANDIDATE_EMAIL,
        "candidate_uid": TEST_CANDIDATE_UID,
        "extra_info": {"env": "integration-test"},
        "org_id": TEST_ORG_ID,
        "org_alias": TEST_ORG_ALIAS,
        "bucket_name": BUCKET_NAME,
        "event_id": TEST_EVENT_ID,
        "encoding_base_path": "video_face_encodings/",
        "encoding_locations": {
            "profile": "profile",
            "interviews": ["interview_1"],
        },
        "onboarding_reference_path": "onboarding_reference/",
        "match_against": ["profile", "interview_1"],
        "lookup_map": {
            "profile": "profile",
            "interviews": ["interview_1"],
        },
    }


# ─── GCS Seeding Helpers ─────────────────────────────────────────────────────


def seed_fake_encodings(
    storage_client,
    bucket: str,
    base_path: str,
    locations: list[str],
    num_encodings: int = 3,
    encoding_dim: int = 128,
) -> dict[str, np.ndarray]:
    """Seed fake .npy encoding files into GCS for testing.

    Returns a dict mapping location → numpy array that was uploaded.
    """
    seeded = {}
    for loc in locations:
        arr = np.random.rand(num_encodings, encoding_dim).astype(np.float64)
        blob_path = f"{base_path}/video_face_encodings/{loc}/{loc}.npy"
        buf = io.BytesIO()
        np.save(buf, arr)
        buf.seek(0)
        storage_client.upload_bytes(buf, bucket, blob_path, "application/octet-stream")
        seeded[loc] = arr
    return seeded


def seed_fake_image(
    storage_client,
    bucket: str,
    blob_path: str,
) -> bytes:
    """Upload a minimal valid PNG to GCS. Returns the raw bytes."""
    # Minimal 1x1 red PNG
    import struct
    import zlib

    def _minimal_png() -> bytes:
        """Generate a minimal 1x1 red PNG."""
        sig = b"\x89PNG\r\n\x1a\n"
        # IHDR
        ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
        ihdr_crc = zlib.crc32(b"IHDR" + ihdr_data) & 0xFFFFFFFF
        ihdr = struct.pack(">I", 13) + b"IHDR" + ihdr_data + struct.pack(">I", ihdr_crc)
        # IDAT
        raw = b"\x00\xff\x00\x00"  # filter=0, R=255, G=0, B=0
        compressed = zlib.compress(raw)
        idat_crc = zlib.crc32(b"IDAT" + compressed) & 0xFFFFFFFF
        idat = struct.pack(">I", len(compressed)) + b"IDAT" + compressed + struct.pack(">I", idat_crc)
        # IEND
        iend_crc = zlib.crc32(b"IEND") & 0xFFFFFFFF
        iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", iend_crc)
        return sig + ihdr + idat + iend

    png_bytes = _minimal_png()
    storage_client.upload_bytes(png_bytes, bucket, blob_path, "image/png")
    return png_bytes


def seed_fake_video(
    storage_client,
    bucket: str,
    blob_path: str,
) -> bytes:
    """Upload a minimal MP4-like file to GCS.

    NOTE: This is NOT a real video — it's just the magic bytes.
    For actual frame extraction tests, use a real short MP4.
    """
    # Minimal ftyp header — enough for magic byte detection
    video_bytes = b"\x00\x00\x00\x1c\x66\x74\x79\x70\x69\x73\x6f\x6d"
    storage_client.upload_bytes(video_bytes, bucket, blob_path, "video/mp4")
    return video_bytes


def publish_message(publisher_client, topic_name: str, payload: dict) -> str:
    """Publish a JSON message to a topic and return the message ID."""
    topic_path = publisher_client.topic_path(PROJECT_ID, topic_name)
    data = json.dumps(payload).encode("utf-8")
    future = publisher_client.publish(topic_path, data=data)
    return future.result()


def pull_messages(subscriber_client, sub_name: str, max_messages: int = 10, timeout: float = 5.0) -> list[dict]:
    """Synchronously pull messages from a subscription.

    Returns parsed JSON payloads.
    """
    sub_path = subscriber_client.subscription_path(PROJECT_ID, sub_name)
    messages = []
    deadline = time.time() + timeout

    while time.time() < deadline:
        response = subscriber_client.pull(
            request={"subscription": sub_path, "max_messages": max_messages},
            timeout=2.0,
        )
        for msg in response.received_messages:
            payload = json.loads(msg.message.data.decode("utf-8"))
            messages.append(payload)
            subscriber_client.acknowledge(
                request={"subscription": sub_path, "ack_ids": [msg.ack_id]}
            )
        if messages:
            break
        time.sleep(0.5)

    return messages
