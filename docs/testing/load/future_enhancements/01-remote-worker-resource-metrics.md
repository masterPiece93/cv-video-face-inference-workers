# 1. Remote Worker Resource Metrics

> **Status:** 📝 Proposed (not yet implemented)
> **Affects:** `tests/load/core/metrics.py`, `tests/load/core/analyzer.py`,
> `tests/load/core/reporter.py`, `tests/load/project/instrumented_worker.py`,
> `tests/load/project/orchestrator.py`, `tests/load/cli.py`
> **Related docs:** [02-architecture](../02-architecture.md) ·
> [06-metrics-and-reports](../06-metrics-and-reports.md)

---

## 1.1 Problem / Motivation

Today the framework can report **CPU and memory utilisation only when it starts
the worker itself** as a local subprocess (the `managed` command). If you point
the load test at a **worker instance running elsewhere** — Cloud Run, GKE, a
remote VM, or another container — the framework has **no way to observe that
worker's CPU/memory**, because the resource sample is taken *inside the local
process* that the framework controls.

We want to be able to answer:

> *"I have a worker already deployed and running on host X. Can the load-testing
> module track its memory and CPU usage KPIs while I drive load at it?"*

The honest current answer is **no** — and this proposal describes how to make
the answer **yes**.

---

## 1.2 Current Behaviour (and why it falls short for remote workers)

### How resource metrics are captured today

In `managed` mode, `instrumented_worker.py` imports the **real** worker and runs
its `process()` **in the same Python process** as the `MetricsCollector`:

```python
# tests/load/project/instrumented_worker.py  (runs as a LOCAL subprocess)
collector = MetricsCollector(...)

def callback(message):
    with collector.track_message(event_id, publish_time) as tracker:
        with tracker.stage("validate"):
            validate_fn(payload)
        with tracker.stage("process"):
            process_fn(payload)          # ← the REAL worker logic, same PID
    message.ack()
```

The memory sample is the process asking the OS about **itself**:

```python
# tests/load/core/metrics.py
def _get_memory_mb() -> float:
    """Get current process memory usage in MB (RSS)."""
    usage = resource.getrusage(resource.RUSAGE_SELF)   # ← RUSAGE_SELF
    return usage.ru_maxrss / 1024  # Linux reports in KB
```

### Why this only works locally

`RUSAGE_SELF` returns the RSS of **the calling process**. That number equals the
worker's memory *only because the collector and the worker are the same process
on the same machine*. For a worker hosted elsewhere this breaks in two ways:

| KPI | What happens against a remote worker | Why |
|-----|--------------------------------------|-----|
| **Memory** | Measures the **local load-driver**, not the remote worker → wrong/misleading number | `RUSAGE_SELF` is local-process only |
| **CPU** | Not collected **at all**, even locally today | There is no CPU sampler in `metrics.py`; the report's "Resource Utilization" shows memory only |

### What *does* still work against a remote worker

Everything derived from **publish timestamps + the ack/result stream** is
black-box observable from the client and remains fully valid:

- ✅ Throughput (msg/min)
- ✅ End-to-end latency (p50/p90/p95/p99/max/min/avg)
- ✅ Queue wait time
- ✅ Success / failure counts & error rate

Only **per-worker CPU/memory** requires reaching into the remote host. So the
scope of this enhancement is specifically the **Resource Utilization** KPI block
for non-local workers (and adding CPU as a first-class metric everywhere).

---

## 1.3 Proposed Design

Introduce a pluggable **`ResourceSampler`** abstraction plus a **black-box /
remote run mode**, so resource metrics can come from one of several sources and
the report always makes the **source explicit** (so nobody mistakes local
numbers for remote ones).

### 1.3.1 Resource source options

Ranked by practicality:

| # | Source | How it works | Best when | Worker code change? |
|---|--------|--------------|-----------|---------------------|
| 1 | **In-band self-report** | Worker attaches its own `psutil` RSS/CPU to each egestion result; the load tester reads them off the result topic | You can modify the worker / its output handler | Yes (small) |
| 2 | **Platform monitoring API** | After the run, query Cloud Monitoring (`run.googleapis.com/container/memory|cpu/utilizations`), GKE metrics-server, cAdvisor/Prometheus, or `kubectl top` for the test window | Hosted on Cloud Run / GKE / k8s | No |
| 3 | **Sidecar / cgroup sampler** | A small sampler co-located with the worker reads `/sys/fs/cgroup/memory.current` & `cpu.stat` and pushes samples | You own the deployment but not the app code | No (deploy change) |
| 4 | **APM** | Pull from Datadog / New Relic / `process-exporter` for the run window | APM already in place | No |
| 5 | **SSH `/proc` sampling** | Poll `/proc/<pid>/status` + `/proc/<pid>/stat` over SSH on an interval | Quick/dirty, you have shell access | No |

### 1.3.2 Recommended approach

Implement in two phases:

- **Phase A — In-band self-reporting (option 1).** Portable, cloud-agnostic,
  needs no platform credentials, and works for *any* remote worker that we can
  modify. The worker measures *itself* (the only place where the number is
  unambiguously correct) and ships the numbers back **in-band** over the result
  message. This is the primary recommendation.
- **Phase B — Cloud Monitoring backend (option 2).** For workers we *cannot*
  modify, add a post-run sampler that queries the platform's own telemetry for
  the test time window. No worker change required.

Both feed the **same** `ResourceSampler` interface and the **same** KPI table,
labelled by source.

---

## 1.4 Architecture Changes

```
                       ┌───────────────────────────────────────────┐
                       │            ResourceSampler (new)          │
                       │   .sample() -> ResourceSample             │
                       │   .source_label -> "local" | "in_band" |  │
                       │                    "cloud_monitoring" ... │
                       └───────────────────────────────────────────┘
                          ▲              ▲                  ▲
          ┌───────────────┘              │                  └────────────────┐
          │                              │                                   │
 LocalRusageSampler           InBandSampler                     CloudMonitoringSampler
 (today's behaviour,          (reads rss_mb / cpu_pct           (post-run query to
  RUSAGE_SELF, local PID)      off egestion messages)            run.googleapis.com)
```

- The **run mode** decides whether a local worker is launched at all:
  - `managed` (today) → local worker → `LocalRusageSampler`.
  - **`--remote` (new)** → **skip Step 3** (do *not* launch a worker), publish
    load at an existing topic, collect client-side KPIs, and pull resource
    metrics from `in_band` / `cloud_monitoring` instead of `local`.

---

## 1.5 Implementation Sketch

### 1.5.1 New: `ResourceSample` + `ResourceSampler` (in `core/metrics.py`)

```python
@dataclass
class ResourceSample:
    rss_mb: float | None = None
    cpu_pct: float | None = None
    source: str = "unknown"          # local | in_band | cloud_monitoring | ...
    sampled_at: float = 0.0


class ResourceSampler(Protocol):
    source_label: str
    def sample(self) -> ResourceSample: ...
```

### 1.5.2 Phase A — worker self-reports (in-band)

**Worker side** — attach self-measured resources to each result. Add to the
verification/encoding output handler (or wrap it in `instrumented_worker.py`
when we run it, and document the contract for externally-deployed workers):

```python
import os, psutil
_proc = psutil.Process(os.getpid())

def _self_resources() -> dict:
    return {
        "rss_mb": _proc.memory_info().rss / (1024 * 1024),
        "cpu_pct": _proc.cpu_percent(interval=None),  # since last call
    }

# include in the egestion message attributes / body:
result_message["worker_resources"] = _self_resources()
```

**Load-tester side** — an `InBandSampler` subscribes to the **egestion topic**
and matches `rss_mb` / `cpu_pct` back onto each `MessageResult` by `event_id`:

```python
class InBandSampler:
    source_label = "in_band"
    # consume egestion topic, index by event_id, expose latest sample
```

> **Contract for externally-deployed workers:** to use in-band reporting against
> a worker we don't launch, the deployed worker must emit
> `worker_resources: {rss_mb, cpu_pct}` on its egestion message. Document this
> as the integration requirement.

### 1.5.3 Phase B — Cloud Monitoring (no worker change)

```python
class CloudMonitoringSampler:
    source_label = "cloud_monitoring"
    def __init__(self, project, service, region, start, end): ...
    def sample(self) -> ResourceSample:
        # query monitoring_v3 for:
        #   run.googleapis.com/container/memory/utilizations
        #   run.googleapis.com/container/cpu/utilizations
        # over [start, end]; return peak/avg
```

### 1.5.4 Reporter changes (`core/reporter.py`)

- Add a **CPU** row to the "Resource Utilization" block (peak/avg %).
- Append the **source** so the value is never ambiguous:

```
Resource Utilization
  Source            in_band            ← NEW (local | in_band | cloud_monitoring)
  Memory (peak)     412.8 MB
  Memory (avg)      394.3 MB
  CPU (peak)        78.4 %             ← NEW
  CPU (avg)         51.2 %             ← NEW
```

- When source is `local` **and** the run is `--remote`, render
  `Memory/CPU = n/a (remote worker; enable --resource-source)` instead of a
  misleading local number.

### 1.5.5 New CLI options (`cli.py`)

| Flag | Applies to | Default | Meaning |
|------|------------|---------|---------|
| `--remote` | `run` | off | Don't launch a local worker; drive load at an existing topic/worker and collect client-side KPIs only (plus resource metrics from the chosen source). |
| `--resource-source` | `run` / `managed` | `local` | One of `local`, `in_band`, `cloud_monitoring`, `none`. Selects the `ResourceSampler`. |
| `--egestion-topic` | `run` (with `in_band`) | per-worker default | Topic the worker publishes results to, for the in-band sampler to subscribe. |
| `--gcp-project` / `--cr-service` / `--cr-region` | `run` (with `cloud_monitoring`) | — | Identify the remote service for the monitoring query. |

Example (remote worker on Cloud Run, in-band resources):

```bash
python tests/load/cli.py run \
    --remote \
    --profile ramp --messages 200 --duration 120 \
    --project-id my-prod-project \
    --topic eci-verification-ingestion \
    --resource-source in_band \
    --egestion-topic eci-verification-egestion
```

Example (remote worker, pull from Cloud Monitoring, no worker change):

```bash
python tests/load/cli.py run \
    --remote \
    --profile soak --messages 1000 --duration 1800 \
    --project-id my-prod-project \
    --topic eci-verification-ingestion \
    --resource-source cloud_monitoring \
    --gcp-project my-prod-project \
    --cr-service face-verification-worker --cr-region us-central1
```

---

## 1.6 Affected Files

| File | Change |
|------|--------|
| `tests/load/core/metrics.py` | Add `ResourceSample`, `ResourceSampler` protocol, `LocalRusageSampler`; add CPU sampling (`psutil`) alongside existing RSS. |
| `tests/load/core/analyzer.py` | Aggregate `cpu_pct` (peak/avg) and carry `resource_source` into `MetricsSummary`. |
| `tests/load/core/reporter.py` | Render CPU rows + `Source` label; show `n/a` for remote+local. |
| `tests/load/core/types.py` | Add `cpu_pct` to `MessageResult`; `cpu_peak/cpu_avg/resource_source` to `MetricsSummary`. |
| `tests/load/project/instrumented_worker.py` | Emit in-band `worker_resources` when it runs the worker; populate sampler. |
| `tests/load/project/orchestrator.py` | Support `--remote` (skip worker launch); build the selected sampler. |
| `tests/load/cli.py` | New flags `--remote`, `--resource-source`, `--egestion-topic`, monitoring identifiers; wire to sampler + run mode. |

---

## 1.7 Testing & Acceptance Criteria

**Tests**
- Unit: `LocalRusageSampler` returns non-zero RSS; CPU sampler returns a sane
  percentage under a busy loop.
- Unit: `InBandSampler` correctly matches `worker_resources` to `event_id`.
- Integration: `--remote` mode runs **without** launching a worker subprocess
  (assert no child process spawned) and still produces latency/throughput KPIs.
- Integration (emulator): worker emits in-band resources → report shows
  `Source: in_band` with populated CPU/memory.

**Acceptance criteria**
- ✅ Driving load at an already-running worker yields correct **latency,
  throughput, queue-wait, error-rate** KPIs (unchanged).
- ✅ With `--resource-source in_band`, the report shows the **remote worker's**
  CPU & memory, labelled `in_band`.
- ✅ With `--resource-source cloud_monitoring`, CPU/memory come from the platform
  for the exact run window, labelled `cloud_monitoring`, with **no worker code
  change**.
- ✅ With `--remote` and `--resource-source local`, the report shows
  `n/a (remote worker)` instead of a misleading local value.
- ✅ CPU utilisation becomes a first-class KPI in console / Markdown / HTML
  reports for **all** modes (including today's local `managed`).

---

## 1.8 Phased Rollout

| Phase | Deliverable | Notes |
|-------|-------------|-------|
| **0** | Add CPU sampling to the existing local path | Immediate value for `managed`; no remote work yet. |
| **A** | `--remote` run mode + `InBandSampler` + worker self-report contract | Portable, cloud-agnostic remote metrics. |
| **B** | `CloudMonitoringSampler` | For workers we cannot modify. |
| **C** | Optional: `cgroup` sidecar + SSH `/proc` samplers | Niche deployments. |

---

## 1.9 Risks & Notes

- **Source clarity is critical.** Always label the resource source in the report
  so local numbers are never mistaken for remote ones.
- **In-band overhead.** `psutil.cpu_percent` and `memory_info` are cheap, but
  sampling every message adds tiny overhead; acceptable for load testing.
- **Cloud Monitoring latency.** Platform metrics can lag 1–3 minutes and are
  often **container-level averages**, not per-message — good for trend/peak, not
  for per-message attribution. Prefer in-band when per-message precision matters.
- **Scaling/multiple replicas.** A remote service may autoscale to N replicas.
  In-band reporting captures per-replica numbers per message; Cloud Monitoring
  can aggregate across replicas. The report should note replica count when known.
- **New dependency.** `psutil` (worker side) and `google-cloud-monitoring`
  (Phase B) would be added to the relevant `requirements*.txt`.
</content>
