# 6. Metrics & Reports

This guide explains every output file, every KPI, and how to read the charts.

---

## 6.1 Output Files

After a run (with `run_id = {worker}_{profile}_{epoch}`), the output directory
contains:

| File | Produced by | Contents |
|------|-------------|----------|
| `<run_id>.jsonl` | instrumented worker | One JSON object **per processed message** (raw metrics). |
| `<run_id>_summary.json` | worker on shutdown / analyzer | Aggregated KPIs (the `MetricsSummary`). |
| `<run_id>_report.md` | reporter | Markdown report (tables). |
| `<run_id>_report.html` | reporter | Interactive HTML report with Plotly charts. |
| `<run_id>_manifest.json` | `run` command only | Every published message id + publish time. |
| `<run_id>_worker.log` / your `--log-file` | worker subprocess | Full worker stdout/stderr. |

---

## 6.2 The `.jsonl` Record (per message)

Each line is one `MessageResult`. Key fields:

```json
{
  "event_id": "loadtest-ab12cd34ef56",
  "publish_time": 1780573580.12,
  "received_at": 1780573580.20,
  "processing_start": 1780573580.20,
  "processing_end": 1780573580.28,
  "ack_time": 1780573580.28,
  "status": "success",
  "error_type": null,
  "error_message": null,
  "stages": [
    {"name": "validate", "duration_seconds": 0.0001},
    {"name": "process",  "duration_seconds": 0.081}
  ],
  "memory_mb": 591.4,
  "queue_wait_seconds": 0.08,
  "processing_seconds": 0.081,
  "end_to_end_seconds": 0.16,
  "custom_metrics": {"candidate_email": "...", "encoder_backend": "fdetect"}
}
```

| Field | Meaning |
|-------|---------|
| `publish_time` | When the publisher sent the message. |
| `received_at` | When the worker received it. |
| `processing_start/end` | Worker processing window. |
| `ack_time` | When the worker acked (success). |
| `queue_wait_seconds` | `received_at − publish_time`. |
| `processing_seconds` | `processing_end − processing_start`. |
| `end_to_end_seconds` | `ack_time − publish_time` (the headline latency). |
| `stages` | Per-stage timings (`validate`, `process`). |
| `status` | `success` or `error`. |
| `memory_mb` | Process RSS at completion. |

---

## 6.3 The KPI Table (console / Markdown / HTML)

### Test Execution & Run Parameters

| Param | Meaning |
|-------|---------|
| Run ID | Unique id naming all output files. |
| Profile | The load shape + its description. |
| Total Messages | How many were published. |
| Duration (planned) | Your `--duration`. |
| Duration (actual) | Wall-clock from first publish to last ack. |
| Max Concurrent Messages | Flow-control `max_messages` (worker outstanding). |
| Start/End Time | UTC timestamps. |
| Target Topic / Subscription | Where load went. |
| Environment | `Emulator` or `Live GCP`. |

### Key Performance Indicators

| Metric | Meaning | What "good" looks like |
|--------|---------|------------------------|
| **Throughput** (msg/min) | Successful messages / wall-clock × 60. | Higher is better; compare across runs. |
| **Success / Failed** | Counts. | Failed should be 0 under normal load. |
| **Error Rate %** | `failed / total × 100`. | 0% ideal; >0% under stress = saturation. |
| **End-to-End Latency** p50/p90/p95/p99/max/min/avg | Publish→ack distribution. | Watch **p95/p99**, not just avg. |
| **Processing Time** p50/p90/p95/p99/max/avg | Pure worker time. | Isolates compute cost from queueing. |
| **Queue Wait Time** p50/p95/avg | Time waiting before processing. | Rising queue wait = worker can't keep up. |
| **Memory** peak/avg (MB) | Process RSS. | Should be stable in soak tests. |

> ⚠️ **Resource KPIs are local-only today.** Memory (and, in future, CPU) is
> sampled *in-process* by the instrumented worker that `managed` launches. When
> driving load at a worker **hosted elsewhere** (Cloud Run / GKE / remote VM),
> the framework cannot see that worker's CPU/memory — only the client-side KPIs
> (throughput, latency, queue wait, error rate) remain valid. A design for
> capturing remote resource metrics is proposed in
> [Future Enhancements → Remote Worker Resource Metrics](./future_enhancements/01-remote-worker-resource-metrics.md).

### Processing Stage Breakdown

Median duration of each stage and its share of total. For the encoding worker
the dominant stage is `process` (download + frame sampling + encoding); `validate`
is negligible. A stage hogging an unexpected share points you straight at the
bottleneck.

---

## 6.4 Reading Percentiles (Why p95/p99 Matter)

- **p50 (median):** half of requests are faster than this.
- **p95:** 95% are faster; the slow tail begins here.
- **p99:** the worst 1% — what your unhappiest users feel.
- **max:** the single worst request.

> Averages hide tail latency. A great avg with a terrible p99 still means a poor
> experience for many users. Always evaluate p95/p99 when sizing capacity.

---

## 6.5 The HTML Report Charts

Open `<run_id>_report.html` in a browser. It includes interactive Plotly charts
such as:

- **Latency over time** — spot when latency degrades during the run.
- **Throughput vs. load** — see how throughput responds as pressure increases.
- **Percentile bars** — p50/p90/p95/p99 at a glance.
- **Error rate** — when/if failures appear.
- **Memory** — track growth (key for soak tests).

These are generated by `core/reporter.py` from the same `MetricsSummary`.

---

## 6.6 Interpreting Common Patterns

| Observation | Likely meaning | Action |
|-------------|----------------|--------|
| Queue wait rises steadily | Worker can't keep up with publish rate | Lower rate, scale workers, or raise `max_messages`. |
| p99 ≫ p50 | Long tail (GC pauses, cold caches, contention) | Investigate the slowest stage; warm up before measuring. |
| Error rate > 0% only under stress/spike | Saturation point reached | That rate is your ceiling. |
| Memory grows across a soak | Possible leak / unbounded buffer | Profile the worker; check caches/connections. |
| Throughput flat as load rises | Saturated; more load just queues | You've found the max sustainable throughput. |

---

## 6.7 Re-rendering Reports

You can regenerate reports any time from the raw `.jsonl`:

```bash
python tests/load/cli.py report tests/load/results/<run_id>.jsonl -f all
```

This is handy after a `run` (publish-only) once the worker has drained the queue,
or to re-style an old run.

---

## Next Steps

- Tune what gets sent → [Configuration & Data Sources](./07-configuration-and-data.md)
- Fix issues → [Troubleshooting](./08-troubleshooting.md)
</content>
