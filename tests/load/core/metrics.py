"""Metrics collector — instruments worker message processing.

Usage:
    collector = MetricsCollector(results_dir=Path("results"))

    # In your worker callback wrapper:
    with collector.track_message(event_id, publish_time) as tracker:
        tracker.stage("download"):
            download_video(...)
        tracker.stage("encode"):
            encode_frames(...)
        # auto-records success/failure + memory
"""
from __future__ import annotations

import json
import logging
import os
import resource
import time
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from typing import Generator, List, Optional

from tests.load.core.types import MessageResult, StageMetrics

logger = logging.getLogger(__name__)


class MessageTracker:
    """Tracks metrics for a single message being processed."""

    def __init__(self, event_id: str, publish_time: float):
        self._result = MessageResult(
            event_id=event_id,
            publish_time=publish_time,
            received_at=time.time(),
        )
        self._current_stage_start: Optional[float] = None
        self._current_stage_name: Optional[str] = None

    @property
    def result(self) -> MessageResult:
        return self._result

    @contextmanager
    def stage(self, name: str) -> Generator[None, None, None]:
        """Time a named processing stage.

        Usage:
            with tracker.stage("video_download"):
                download(...)
        """
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - start
            self._result.stages.append(StageMetrics(name=name, duration_seconds=elapsed))

    def set_custom_metric(self, key: str, value) -> None:
        """Record an arbitrary key-value metric."""
        self._result.custom_metrics[key] = value

    def _mark_processing_start(self):
        self._result.processing_start = time.time()

    def _mark_success(self):
        self._result.processing_end = time.time()
        self._result.ack_time = time.time()
        self._result.status = "success"
        self._result.memory_mb = _get_memory_mb()

    def _mark_failure(self, error: Exception):
        self._result.processing_end = time.time()
        self._result.status = "error"
        self._result.error_type = type(error).__name__
        self._result.error_message = str(error)[:500]
        self._result.memory_mb = _get_memory_mb()


class MetricsCollector:
    """Thread-safe metrics collector for load test runs.

    Collects per-message results and writes them to a JSONL file.
    """

    def __init__(self, results_dir: Path, run_id: str = ""):
        self._results: List[MessageResult] = []
        self._lock = Lock()
        self._results_dir = Path(results_dir)
        self._run_id = run_id or f"run_{int(time.time())}"
        self._results_dir.mkdir(parents=True, exist_ok=True)
        self._jsonl_path = self._results_dir / f"{self._run_id}.jsonl"
        self._start_time = time.time()

    @property
    def results(self) -> List[MessageResult]:
        with self._lock:
            return list(self._results)

    @property
    def jsonl_path(self) -> Path:
        return self._jsonl_path

    @property
    def start_time(self) -> float:
        return self._start_time

    @contextmanager
    def track_message(
        self, event_id: str, publish_time: float
    ) -> Generator[MessageTracker, None, None]:
        """Context manager to track a single message lifecycle.

        Args:
            event_id: Unique message identifier.
            publish_time: Epoch time when the message was published.

        Yields:
            MessageTracker for recording stages and custom metrics.
        """
        tracker = MessageTracker(event_id=event_id, publish_time=publish_time)
        tracker._mark_processing_start()
        try:
            yield tracker
            tracker._mark_success()
        except Exception as e:
            tracker._mark_failure(e)
            raise
        finally:
            self._record(tracker.result)

    def _record(self, result: MessageResult) -> None:
        """Append result to memory + flush to JSONL."""
        with self._lock:
            self._results.append(result)
        # Append to file (thread-safe via OS-level atomic append)
        with open(self._jsonl_path, "a") as f:
            f.write(json.dumps(result.to_dict()) + "\n")

    def flush_summary(self) -> Path:
        """Write a summary JSON alongside the JSONL."""
        from tests.load.core.analyzer import compute_summary

        summary = compute_summary(self._results, self._start_time)
        summary_path = self._results_dir / f"{self._run_id}_summary.json"
        with open(summary_path, "w") as f:
            json.dump(summary.__dict__, f, indent=2, default=str)
        return summary_path


def _get_memory_mb() -> float:
    """Get current process memory usage in MB (RSS)."""
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return usage.ru_maxrss / 1024  # Linux reports in KB
