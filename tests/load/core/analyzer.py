"""Metrics analyzer — computes summary statistics from raw results.

Produces MetricsSummary with percentiles, throughput, stage breakdown, etc.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path
from typing import List, Optional

import numpy as np

from tests.load.core.types import MessageResult, MetricsSummary, StageMetrics


def compute_summary(
    results: List[MessageResult],
    start_time: float,
    end_time: Optional[float] = None,
) -> MetricsSummary:
    """Compute aggregated metrics from a list of message results.

    Args:
        results: List of per-message results.
        start_time: Test start epoch time.
        end_time: Test end epoch time (defaults to now).

    Returns:
        MetricsSummary with all KPIs computed.
    """
    if not results:
        return MetricsSummary()

    end_time = end_time or time.time()
    wall_clock = end_time - start_time

    successful = [r for r in results if r.status == "success"]
    failed = [r for r in results if r.status == "error"]

    summary = MetricsSummary(
        total_messages=len(results),
        successful=len(successful),
        failed=len(failed),
        error_rate_pct=(len(failed) / len(results) * 100) if results else 0.0,
        wall_clock_seconds=wall_clock,
        throughput_msg_per_min=(len(successful) / wall_clock * 60) if wall_clock > 0 else 0.0,
    )

    # End-to-end latency
    e2e_times = [r.end_to_end_seconds for r in successful if r.end_to_end_seconds is not None]
    if e2e_times:
        arr = np.array(e2e_times)
        summary.latency_p50 = float(np.percentile(arr, 50))
        summary.latency_p90 = float(np.percentile(arr, 90))
        summary.latency_p95 = float(np.percentile(arr, 95))
        summary.latency_p99 = float(np.percentile(arr, 99))
        summary.latency_max = float(np.max(arr))
        summary.latency_min = float(np.min(arr))
        summary.latency_avg = float(np.mean(arr))

    # Processing time
    proc_times = [r.processing_seconds for r in successful if r.processing_seconds is not None]
    if proc_times:
        arr = np.array(proc_times)
        summary.processing_p50 = float(np.percentile(arr, 50))
        summary.processing_p90 = float(np.percentile(arr, 90))
        summary.processing_p95 = float(np.percentile(arr, 95))
        summary.processing_p99 = float(np.percentile(arr, 99))
        summary.processing_max = float(np.max(arr))
        summary.processing_avg = float(np.mean(arr))

    # Queue wait
    wait_times = [r.queue_wait_seconds for r in successful if r.queue_wait_seconds is not None]
    if wait_times:
        arr = np.array(wait_times)
        summary.queue_wait_p50 = float(np.percentile(arr, 50))
        summary.queue_wait_p95 = float(np.percentile(arr, 95))
        summary.queue_wait_avg = float(np.mean(arr))

    # Memory
    mem_values = [r.memory_mb for r in results if r.memory_mb is not None]
    if mem_values:
        summary.memory_peak_mb = max(mem_values)
        summary.memory_avg_mb = sum(mem_values) / len(mem_values)

    # Stage breakdown (median per stage)
    stage_times: dict[str, list[float]] = defaultdict(list)
    for r in successful:
        for s in r.stages:
            stage_times[s.name].append(s.duration_seconds)
    summary.stage_breakdown = {
        name: float(np.median(times)) for name, times in stage_times.items()
    }

    # Error breakdown
    error_counts: dict[str, int] = defaultdict(int)
    for r in failed:
        key = r.error_type or "Unknown"
        error_counts[key] += 1
    summary.error_breakdown = dict(error_counts)

    return summary


def load_results_from_jsonl(jsonl_path: Path) -> List[MessageResult]:
    """Load MessageResult objects from a JSONL file.

    Args:
        jsonl_path: Path to the .jsonl results file.

    Returns:
        List of MessageResult objects.
    """
    results = []
    with open(jsonl_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            stages = [
                StageMetrics(name=s["name"], duration_seconds=s["duration_seconds"])
                for s in data.get("stages", [])
            ]
            result = MessageResult(
                event_id=data["event_id"],
                publish_time=data["publish_time"],
                received_at=data.get("received_at"),
                processing_start=data.get("processing_start"),
                processing_end=data.get("processing_end"),
                ack_time=data.get("ack_time"),
                status=data.get("status", "unknown"),
                error_type=data.get("error_type"),
                error_message=data.get("error_message"),
                stages=stages,
                memory_mb=data.get("memory_mb"),
                custom_metrics=data.get("custom_metrics", {}),
            )
            results.append(result)
    return results
