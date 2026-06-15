"""Orchestrator — manages infra, worker, and load test lifecycle.

Handles:
1. Starting/stopping Docker Compose (Pub/Sub emulator + fake-GCS)
2. Starting an instrumented worker subprocess
3. Waiting for message processing completion
4. Tearing everything down
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_COMPOSE_FILE = Path(__file__).resolve().parent.parent / "docker-compose.yml"
_PROJECT_ROOT = Path(__file__).resolve().parents[3]


# Per-worker Pub/Sub topic & subscription naming on the load-test emulator.
WORKER_TOPICS = {
    "face_encoding_worker": {
        "ingestion_topic": "loadtest-encoding-ingestion",
        "subscription": "loadtest-encoding-ingestion-sub",
        "egestion_topic": "loadtest-encoding-egestion",
    },
    "face_verification_worker": {
        "ingestion_topic": "loadtest-verification-ingestion",
        "subscription": "loadtest-verification-ingestion-sub",
        "egestion_topic": "loadtest-verification-egestion",
    },
    "onboarding_verification_worker": {
        "ingestion_topic": "loadtest-onboarding-ingestion",
        "subscription": "loadtest-onboarding-ingestion-sub",
        "egestion_topic": "loadtest-onboarding-egestion",
    },
}


def get_worker_topics(worker_name: str) -> dict:
    """Return ingestion/subscription/egestion topic names for a worker."""
    return WORKER_TOPICS.get(worker_name, WORKER_TOPICS["face_encoding_worker"])



class InfraManager:
    """Manages Docker Compose services for load testing."""

    def __init__(
        self,
        compose_file: Path = _COMPOSE_FILE,
        pubsub_port: int = 8685,
        gcs_port: int = 5443,
        project_id: str = "loadtest-project",
    ):
        self._compose_file = compose_file
        self.pubsub_host = f"localhost:{pubsub_port}"
        self.gcs_host = f"http://localhost:{gcs_port}"
        self.project_id = project_id
        self._pubsub_port = pubsub_port
        self._gcs_port = gcs_port

    def start(self, timeout: int = 60) -> None:
        """Start Docker Compose services and wait until healthy."""
        logger.info(f"Starting infra from {self._compose_file}")
        subprocess.run(
            ["docker", "compose", "-f", str(self._compose_file), "up", "-d", "--wait"],
            check=True,
            capture_output=True,
            text=True,
        )
        # Wait for health
        self._wait_for_service(self._pubsub_port, "Pub/Sub emulator", timeout)
        self._wait_for_service(self._gcs_port, "fake-GCS", timeout)
        logger.info("Infrastructure is ready")

    def stop(self) -> None:
        """Stop and remove Docker Compose services."""
        logger.info("Stopping infra...")
        subprocess.run(
            ["docker", "compose", "-f", str(self._compose_file), "down", "-v"],
            check=True,
            capture_output=True,
            text=True,
        )
        logger.info("Infrastructure stopped")

    def is_running(self) -> bool:
        """Check if services are running."""
        result = subprocess.run(
            ["docker", "compose", "-f", str(self._compose_file), "ps", "--status", "running", "-q"],
            capture_output=True,
            text=True,
        )
        return bool(result.stdout.strip())

    @staticmethod
    def _wait_for_service(port: int, name: str, timeout: int) -> None:
        """Wait for a TCP port to accept connections."""
        import socket

        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with socket.create_connection(("localhost", port), timeout=2):
                    logger.info(f"  ✓ {name} (port {port}) is ready")
                    return
            except (ConnectionRefusedError, OSError):
                time.sleep(1)
        raise TimeoutError(f"{name} (port {port}) did not become ready in {timeout}s")


class WorkerProcess:
    """Manages an instrumented worker subprocess."""

    def __init__(
        self,
        worker_name: str,
        env_overrides: Optional[dict] = None,
        project_root: Path = _PROJECT_ROOT,
        env_file: Optional[Path] = None,
        log_file: Optional[Path] = None,
    ):
        self._worker_name = worker_name
        self._project_root = project_root
        self._env_overrides = env_overrides or {}
        self._env_file = env_file
        self._log_file = log_file
        self._process: Optional[subprocess.Popen] = None
        self._log_fh = None

    def start(self) -> None:
        """Start the instrumented worker as a subprocess."""
        worker_script = (
            self._project_root / "tests" / "load" / "project" / "instrumented_worker.py"
        )

        env = os.environ.copy()

        # Load .env file if provided
        if self._env_file and self._env_file.exists():
            logger.info(f"Loading env file: {self._env_file}")
            with open(self._env_file) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, _, value = line.partition("=")
                        env[key.strip()] = value.strip().strip('"').strip("'")

        env.update(self._env_overrides)
        # Ensure project root is in PYTHONPATH
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{self._project_root}:{existing}"

        python = sys.executable

        # Set up log file output
        stdout_dest = subprocess.PIPE
        if self._log_file:
            self._log_file.parent.mkdir(parents=True, exist_ok=True)
            self._log_fh = open(self._log_file, "w")
            stdout_dest = self._log_fh
            logger.info(f"Worker output → {self._log_file}")

        logger.info(f"Starting instrumented worker: {self._worker_name}")
        self._process = subprocess.Popen(
            [python, str(worker_script)],
            env=env,
            cwd=str(self._project_root),
            stdout=stdout_dest,
            stderr=subprocess.STDOUT,
        )
        # Give it a moment to initialize
        time.sleep(2)
        if self._process.poll() is not None:
            output = self._process.stdout.read().decode() if self._process.stdout else ""
            raise RuntimeError(
                f"Worker process exited immediately (code={self._process.returncode}).\n"
                f"Output:\n{output}"
            )
        logger.info(f"  ✓ Worker process started (PID={self._process.pid})")

    def stop(self, timeout: int = 15) -> Optional[str]:
        """Stop the worker process gracefully.

        Returns:
            Combined stdout/stderr output from the worker.
        """
        if self._process is None:
            return None

        logger.info(f"Stopping worker (PID={self._process.pid})...")
        self._process.send_signal(signal.SIGINT)
        try:
            self._process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            logger.warning("Worker did not exit gracefully, killing...")
            self._process.kill()
            self._process.wait(timeout=5)

        output = self._process.stdout.read().decode() if self._process.stdout and not self._log_fh else ""
        if self._log_fh:
            self._log_fh.close()
            self._log_fh = None
        logger.info(f"  ✓ Worker stopped (exit code={self._process.returncode})")
        self._process = None
        return output

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None


class LoadTestOrchestrator:
    """End-to-end orchestrator: infra → worker → publish → wait → report → teardown."""

    def __init__(
        self,
        worker_name: str = "face_encoding_worker",
        pubsub_port: int = 8685,
        gcs_port: int = 5443,
        project_id: str = "loadtest-project",
        results_dir: Path = Path("tests/load/results"),
        run_id: str = "",
        encoder_backend: str = "dummy",
        fdetect_channel: str = "",
        env_file: Optional[Path] = None,
        log_file: Optional[Path] = None,
        strategy: str = "with_profile",
        face_tolerance: float = 0.6,
    ):
        self.infra = InfraManager(
            pubsub_port=pubsub_port,
            gcs_port=gcs_port,
            project_id=project_id,
        )
        self.results_dir = Path(results_dir)
        self.run_id = run_id or f"loadtest_{int(time.time())}"
        self.worker_name = worker_name

        # Resolve per-worker topic/subscription names.
        topics = get_worker_topics(worker_name)
        self.ingestion_topic = topics["ingestion_topic"]
        self.subscription_name = topics["subscription"]
        self.egestion_topic = topics["egestion_topic"]

        worker_env = {
            "PUBSUB_EMULATOR_HOST": f"localhost:{pubsub_port}",
            "STORAGE_EMULATOR_HOST": f"http://localhost:{gcs_port}",
            "LOADTEST_RUN_ID": self.run_id,
            "LOADTEST_RESULTS_DIR": str(self.results_dir),
            "LOADTEST_WORKER_NAME": worker_name,
            "LOADTEST_PROJECT_ID": project_id,
            "LOADTEST_ENCODER_BACKEND": encoder_backend,
            "LOADTEST_INGESTION_TOPIC": self.ingestion_topic,
            "LOADTEST_INGESTION_SUB": self.subscription_name,
            "LOADTEST_EGESTION_TOPIC": self.egestion_topic,
            "LOADTEST_STRATEGY": strategy,
            "LOADTEST_FACE_TOLERANCE": str(face_tolerance),
        }
        if fdetect_channel:
            worker_env["FDETECT_CHANNEL"] = fdetect_channel

        self.worker = WorkerProcess(
            worker_name=worker_name,
            env_overrides=worker_env,
            env_file=env_file,
            log_file=log_file,
        )

    def start_infra(self) -> None:
        """Start emulators and provision resources (bucket, topic, subscription)."""
        self.infra.start()
        self._provision_resources()

    def _provision_resources(self) -> None:
        """Create GCS bucket and Pub/Sub topic + subscription on emulators."""
        import os
        from google.cloud import pubsub_v1, storage

        # Create GCS bucket
        os.environ["STORAGE_EMULATOR_HOST"] = self.infra.gcs_host
        try:
            gcs_client = storage.Client(
                project=self.infra.project_id,
                credentials=None,
            )
            gcs_client.create_bucket("loadtest-bucket")
            logger.info("  Created bucket: loadtest-bucket")
        except Exception as e:
            logger.warning(f"  Bucket creation: {e}")

        # Create Pub/Sub topic + subscription
        os.environ["PUBSUB_EMULATOR_HOST"] = self.infra.pubsub_host
        publisher = pubsub_v1.PublisherClient()
        subscriber = pubsub_v1.SubscriberClient()

        proj = self.infra.project_id
        topic_name = f"projects/{proj}/topics/{self.ingestion_topic}"
        sub_name = f"projects/{proj}/subscriptions/{self.subscription_name}"
        egestion_topic = f"projects/{proj}/topics/{self.egestion_topic}"

        try:
            publisher.create_topic(name=topic_name)
            logger.info(f"  Created topic: {topic_name}")
        except Exception as e:
            logger.warning(f"  Topic creation: {e}")

        try:
            publisher.create_topic(name=egestion_topic)
            logger.info(f"  Created egestion topic: {egestion_topic}")
        except Exception as e:
            logger.warning(f"  Egestion topic creation: {e}")

        try:
            subscriber.create_subscription(name=sub_name, topic=topic_name, ack_deadline_seconds=600)
            logger.info(f"  Created subscription: {sub_name}")
        except Exception as e:
            logger.warning(f"  Subscription creation: {e}")

    def start_worker(self) -> None:
        """Start the instrumented worker."""
        self.worker.start()

    def stop_worker(self) -> Optional[str]:
        """Stop the worker and return its output."""
        return self.worker.stop()

    def stop_infra(self) -> None:
        """Stop emulators."""
        self.infra.stop()

    def teardown(self) -> None:
        """Stop everything."""
        if self.worker.is_running:
            self.worker.stop()
        if self.infra.is_running():
            self.infra.stop()

    def wait_for_completion(self, expected_messages: int, timeout: int = 300) -> bool:
        """Wait until the worker has processed all expected messages.

        Polls the JSONL results file until it has the expected number of lines.
        """
        jsonl_path = self.results_dir / f"{self.run_id}.jsonl"
        deadline = time.time() + timeout
        last_count = 0

        while time.time() < deadline:
            if jsonl_path.exists():
                with open(jsonl_path) as f:
                    count = sum(1 for line in f if line.strip())
                if count >= expected_messages:
                    logger.info(f"All {count} messages processed")
                    return True
                if count > last_count:
                    logger.info(f"  Progress: {count}/{expected_messages} messages processed")
                    last_count = count
            time.sleep(2)

        logger.warning(
            f"Timeout waiting for completion. Got {last_count}/{expected_messages} after {timeout}s"
        )
        return False
