"""Instrumented worker — wraps the real worker with MetricsCollector.

This script is started as a subprocess by the orchestrator.
It runs a target worker (face_encoding_worker or face_verification_worker)
with load-test instrumentation injected into the message callback.

Environment variables (set by orchestrator):
    PUBSUB_EMULATOR_HOST    — Pub/Sub emulator address
    STORAGE_EMULATOR_HOST   — fake-GCS address
    LOADTEST_RUN_ID         — Unique run ID for results file
    LOADTEST_RESULTS_DIR    — Directory for .jsonl output
    LOADTEST_WORKER_NAME    — Which worker to run
    LOADTEST_PROJECT_ID     — GCP project ID
    LOADTEST_INGESTION_TOPIC / _SUB / _EGESTION_TOPIC — Pub/Sub names
    LOADTEST_ENCODER_BACKEND — dummy | face_recognition | fdetect (encoding only)
    LOADTEST_STRATEGY        — verification strategy (verification only)
    LOADTEST_FACE_TOLERANCE  — match tolerance (verification only)
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path

# Ensure project root is importable
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from common.services.cloud.gcp.pubsub import GCPPublisher, GCPSubscriber
from common.services.cloud.gcp.storage import GCPStorageService
from common.services.encoding import get_encoder
from tests.load.core.metrics import MetricsCollector

logger = logging.getLogger(__name__)

# ── Configuration from environment ────────────────────────────────────────────

RUN_ID = os.environ.get("LOADTEST_RUN_ID", f"instrumented_{int(time.time())}")
RESULTS_DIR = Path(os.environ.get("LOADTEST_RESULTS_DIR", "tests/load/results"))
WORKER_NAME = os.environ.get("LOADTEST_WORKER_NAME", "face_encoding_worker")
PROJECT_ID = os.environ.get("LOADTEST_PROJECT_ID", "loadtest-project")

# Topics/subscriptions for load testing (defaults match the encoding worker)
INGESTION_TOPIC = os.environ.get("LOADTEST_INGESTION_TOPIC", "loadtest-encoding-ingestion")
INGESTION_SUB = os.environ.get("LOADTEST_INGESTION_SUB", "loadtest-encoding-ingestion-sub")
EGESTION_TOPIC = os.environ.get("LOADTEST_EGESTION_TOPIC", "loadtest-encoding-egestion")

# Encoder config (encoding worker)
ENCODER_BACKEND = os.environ.get("LOADTEST_ENCODER_BACKEND", "face_recognition")
FDETECT_CHANNEL = os.environ.get("FDETECT_CHANNEL", "")
ENCODING_MODEL = os.environ.get("ENCODING_MODEL", "hog")
NUM_JITTERS = int(os.environ.get("NUM_JITTERS", "1"))
FRAME_SAMPLE_RATE = int(os.environ.get("FRAME_SAMPLE_RATE", "30"))
FACE_TOLERANCE = float(os.environ.get("FACE_TOLERANCE", os.environ.get("LOADTEST_FACE_TOLERANCE", "0.5")))

# Verification config (verification worker)
STRATEGY = os.environ.get("LOADTEST_STRATEGY", "with_profile")


class _DummyEncoder:
    """Dummy encoder for load testing — returns random 128-dim face encodings.

    Implements the BaseEncoder interface without any real ML model.
    """

    @property
    def name(self) -> str:
        return "dummy"

    def encode_frame(self, frame_bytes):
        import numpy as np
        # Return 1 random encoding per frame (simulates one face detected)
        return [np.random.rand(128).astype(np.float64)]

    def calculate_distance(self, known_encodings, candidate):
        import numpy as np
        # Return random distances
        return np.random.rand(len(known_encodings)).astype(np.float64)

    def is_duplicate(self, encoding, existing, tolerance=0.6):
        # Never mark as duplicate so all frames get "encoded"
        return False

    def close(self):
        pass


def _build_encoder():
    """Build the configured encoder backend (encoding worker)."""
    if ENCODER_BACKEND == "fdetect" and FDETECT_CHANNEL:
        return get_encoder("fdetect", channel_address=FDETECT_CHANNEL)
    if ENCODER_BACKEND == "dummy":
        return _DummyEncoder()
    return get_encoder("face_recognition", model=ENCODING_MODEL, num_jitters=NUM_JITTERS)


def _build_encoding_worker(storage):
    """Build the face_encoding_worker service, schema, and cleanup hook.

    Returns:
        (process_fn, validate_fn, cleanup_fn, extra_metrics_fn)
    """
    from workers.face_encoding_worker.services.encoding import VideoFaceEncodingService
    from workers.face_encoding_worker.src.handlers.output_handler import EncodingOutputHandler
    from workers.face_encoding_worker.src.handlers.schema.input_schema import EncodingInputSchema

    encoder = _build_encoder()
    publisher = GCPPublisher(project_id=PROJECT_ID, topic_name=EGESTION_TOPIC)
    output_handler = EncodingOutputHandler(publisher)

    service = VideoFaceEncodingService(
        encoder=encoder,
        storage=storage,
        output_handler=output_handler,
        frame_sample_rate=FRAME_SAMPLE_RATE,
        face_tolerance=FACE_TOLERANCE,
        max_workers=4,
    )
    schema = EncodingInputSchema()

    def cleanup():
        if hasattr(encoder, "close"):
            encoder.close()

    def extra_metrics(tracker, payload):
        tracker.set_custom_metric("candidate_email", payload.get("candidate_email"))
        tracker.set_custom_metric("encoder_backend", ENCODER_BACKEND)

    return service.process, schema.validate, cleanup, extra_metrics


def _build_verification_worker(storage):
    """Build the face_verification_worker service, schema, and cleanup hook.

    Returns:
        (process_fn, validate_fn, cleanup_fn, extra_metrics_fn)
    """
    from workers.face_verification_worker.services.strategies import get_strategy
    from workers.face_verification_worker.services.verification import (
        VideoFaceVerificationService,
    )
    from workers.face_verification_worker.src.handlers.output_handler import (
        VerificationOutputHandler,
    )
    from workers.face_verification_worker.src.handlers.schema.input_schema import (
        VerificationInputSchema,
    )

    publisher = GCPPublisher(project_id=PROJECT_ID, topic_name=EGESTION_TOPIC)
    output_handler = VerificationOutputHandler(publisher)
    strategy = get_strategy(STRATEGY, tolerance=FACE_TOLERANCE)

    service = VideoFaceVerificationService(
        strategy=strategy,
        storage=storage,
        output_handler=output_handler,
    )
    schema = VerificationInputSchema()

    def cleanup():
        pass

    def extra_metrics(tracker, payload):
        tracker.set_custom_metric("candidate_email", payload.get("candidate_email"))
        tracker.set_custom_metric("strategy", STRATEGY)

    return service.process, schema.validate, cleanup, extra_metrics


_WORKER_BUILDERS = {
    "face_encoding_worker": _build_encoding_worker,
    "face_verification_worker": _build_verification_worker,
    "onboarding_verification_worker": _build_verification_worker,
}


def main():
    """Run the instrumented worker."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    logger.info(f"Instrumented worker starting: {WORKER_NAME}")
    logger.info(f"  Run ID: {RUN_ID}")
    logger.info(f"  Results: {RESULTS_DIR}")
    logger.info(f"  Emulator: {os.environ.get('PUBSUB_EMULATOR_HOST', 'N/A')}")
    logger.info(f"  GCS: {os.environ.get('STORAGE_EMULATOR_HOST', 'N/A')}")
    logger.info(f"  Ingestion: {INGESTION_SUB} → Egestion: {EGESTION_TOPIC}")

    # ── Build services ────────────────────────────────────────────────────────
    storage = GCPStorageService()

    builder = _WORKER_BUILDERS.get(WORKER_NAME)
    if builder is None:
        raise ValueError(
            f"Unsupported worker '{WORKER_NAME}'. "
            f"Supported: {list(_WORKER_BUILDERS)}"
        )
    process_fn, validate_fn, cleanup_fn, extra_metrics_fn = builder(storage)

    subscriber = GCPSubscriber(
        project_id=PROJECT_ID,
        subscription_name=INGESTION_SUB,
        topic_name=INGESTION_TOPIC,
        max_messages=1,
        max_lease_duration=600,
        dlq_topic=None,
    )

    # ── Metrics collector ─────────────────────────────────────────────────────
    collector = MetricsCollector(results_dir=RESULTS_DIR, run_id=RUN_ID)

    # ── Instrumented callback ─────────────────────────────────────────────────
    def callback(message):
        """Process message with load test instrumentation."""
        event_id = "unknown"
        publish_time = float(message.attributes.get("publish_time_epoch", "0"))

        try:
            raw = message.data
            payload = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
            event_id = payload.get("event_id", "unknown")

            with collector.track_message(event_id, publish_time) as tracker:
                with tracker.stage("validate"):
                    validate_fn(payload)

                with tracker.stage("process"):
                    process_fn(payload)

                extra_metrics_fn(tracker, payload)

            message.ack()
            logger.info(f"[{event_id}] ✓ Processed and acked")

        except Exception as e:
            logger.error(f"[{event_id}] ✗ Failed: {e}")
            message.nack()

    # ── Start subscriber ──────────────────────────────────────────────────────
    subscriber.ensure_subscription_exists()
    logger.info(f"Listening on {INGESTION_SUB}...")

    try:
        subscriber.subscribe(callback)
    except KeyboardInterrupt:
        logger.info("Received interrupt, shutting down...")
    finally:
        subscriber.close()
        cleanup_fn()
        summary_path = collector.flush_summary()
        logger.info(f"Results: {collector.jsonl_path}")
        logger.info(f"Summary: {summary_path}")
        logger.info("Instrumented worker stopped")


if __name__ == "__main__":
    main()
