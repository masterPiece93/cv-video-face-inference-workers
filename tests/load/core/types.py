"""Data types and models for load testing."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


class LoadType(str, Enum):
    """Supported load testing types."""

    RAMP = "ramp"         # Gradual increase to target rate
    SPIKE = "spike"       # Sudden burst of load
    SOAK = "soak"         # Sustained load over extended period
    STRESS = "stress"     # Increasing load until failure


@dataclass
class LoadProfile:
    """Defines how load is applied over time.

    Subclass this to implement custom load shapes.
    """

    total_messages: int
    duration_seconds: float
    load_type: LoadType = LoadType.RAMP
    description: str = ""

    def get_delay_for_message(self, message_index: int) -> float:
        """Return delay (seconds) before publishing the given message.

        Override in subclasses for custom load shaping.
        """
        if self.total_messages <= 1:
            return 0.0
        return self.duration_seconds / self.total_messages


@dataclass
class StageMetrics:
    """Timing for a named processing stage within a single message."""

    name: str
    duration_seconds: float


@dataclass
class MessageResult:
    """Result of processing a single message."""

    event_id: str
    publish_time: float  # time.time() epoch
    received_at: Optional[float] = None
    processing_start: Optional[float] = None
    processing_end: Optional[float] = None
    ack_time: Optional[float] = None
    status: str = "pending"  # pending | success | error
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    stages: List[StageMetrics] = field(default_factory=list)
    memory_mb: Optional[float] = None
    custom_metrics: Dict[str, Any] = field(default_factory=dict)

    @property
    def queue_wait_seconds(self) -> Optional[float]:
        if self.received_at and self.publish_time:
            return self.received_at - self.publish_time
        return None

    @property
    def processing_seconds(self) -> Optional[float]:
        if self.processing_end and self.processing_start:
            return self.processing_end - self.processing_start
        return None

    @property
    def end_to_end_seconds(self) -> Optional[float]:
        if self.ack_time and self.publish_time:
            return self.ack_time - self.publish_time
        return None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for JSONL output."""
        return {
            "event_id": self.event_id,
            "publish_time": self.publish_time,
            "received_at": self.received_at,
            "processing_start": self.processing_start,
            "processing_end": self.processing_end,
            "ack_time": self.ack_time,
            "status": self.status,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "stages": [{"name": s.name, "duration_seconds": s.duration_seconds} for s in self.stages],
            "memory_mb": self.memory_mb,
            "queue_wait_seconds": self.queue_wait_seconds,
            "processing_seconds": self.processing_seconds,
            "end_to_end_seconds": self.end_to_end_seconds,
            "custom_metrics": self.custom_metrics,
        }


@dataclass
class MetricsSummary:
    """Aggregated metrics across all messages in a load test run."""

    total_messages: int = 0
    successful: int = 0
    failed: int = 0
    error_rate_pct: float = 0.0

    # Throughput
    throughput_msg_per_min: float = 0.0
    wall_clock_seconds: float = 0.0

    # Latency percentiles (end-to-end)
    latency_p50: float = 0.0
    latency_p90: float = 0.0
    latency_p95: float = 0.0
    latency_p99: float = 0.0
    latency_max: float = 0.0
    latency_min: float = 0.0
    latency_avg: float = 0.0

    # Processing time percentiles
    processing_p50: float = 0.0
    processing_p90: float = 0.0
    processing_p95: float = 0.0
    processing_p99: float = 0.0
    processing_max: float = 0.0
    processing_avg: float = 0.0

    # Queue wait
    queue_wait_p50: float = 0.0
    queue_wait_p95: float = 0.0
    queue_wait_avg: float = 0.0

    # Memory
    memory_peak_mb: float = 0.0
    memory_avg_mb: float = 0.0

    # Per-stage breakdown (stage_name → median seconds)
    stage_breakdown: Dict[str, float] = field(default_factory=dict)

    # Error breakdown (error_type → count)
    error_breakdown: Dict[str, int] = field(default_factory=dict)


@dataclass
class LoadTestConfig:
    """Full load test configuration."""

    # Target
    project_id: str
    topic_name: str
    subscription_name: str

    # Load shape
    profile: LoadProfile

    # Environment
    sa_path: Optional[str] = None
    emulator_host: Optional[str] = None

    # Data
    fixtures_dir: Optional[Path] = None
    payload_template: Optional[Dict[str, Any]] = None

    # Output
    results_dir: Path = field(default_factory=lambda: Path("tests/load/results"))
    run_id: str = field(default_factory=lambda: f"run_{int(time.time())}")

    # Worker (for metrics collection)
    worker_name: str = "worker"
    max_messages: int = 1

    def __post_init__(self):
        self.results_dir = Path(self.results_dir)


@dataclass
class LoadTestResult:
    """Complete result of a load test run."""

    config: LoadTestConfig
    messages: List[MessageResult] = field(default_factory=list)
    summary: Optional[MetricsSummary] = None
    start_time: float = 0.0
    end_time: float = 0.0

    @property
    def duration_seconds(self) -> float:
        return self.end_time - self.start_time
