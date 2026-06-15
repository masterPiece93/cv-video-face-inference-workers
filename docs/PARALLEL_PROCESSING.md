# Parallel Message Processing — Analysis & Implementation Guide

## Overview

GCP Pub/Sub's `SubscriberClient` supports concurrent message processing out of the box.
When `FlowControl.max_messages > 1`, the client delivers multiple messages simultaneously,
each invoking the callback in a separate thread from its internal `ThreadPoolExecutor`.

This document analyses why parallel processing is currently disabled (`max_messages=1`),
which workers can safely enable it, and how to configure it.

---

## Current Configuration

```env
# All workers currently use:
INGEST__FLOW_MAX_MESSAGES=1          # Only 1 message processed at a time
INGEST__FLOW_MAX_LEASE_DURATION=150  # Long lease for slow encoding
INGEST__MAX_WORKERS=2                # Unused effectively at max_messages=1
```

### How Pub/Sub Parallelism Works

```
┌─────────────────────────────────────────────────────────┐
│  SubscriberClient (internal ThreadPoolExecutor)         │
│                                                         │
│   max_messages=1  →  1 callback thread  →  sequential   │
│   max_messages=5  →  5 callback threads →  parallel     │
│                                                         │
│   Thread pool size = max_messages * 5 (default)         │
└─────────────────────────────────────────────────────────┘
```

The `max_messages` flow control parameter is the **effective parallelism knob**.
No additional code changes are needed — just configuration.

---

## Why Parallel Processing Is Disabled

### Root Cause: dlib Thread-Safety

The `face_recognition` library (backed by **dlib**) is **NOT thread-safe**.
Calling `face_recognition.face_encodings()` from multiple threads simultaneously
causes a **segfault** (SIGSEGV) — an unrecoverable crash.

Source code comments confirm this:
```python
# NOTE: dlib (face_recognition backend) is NOT thread-safe — calling
# encode_frame() from multiple threads causes a segfault. Process frames
# sequentially here; parallelism can be added at the snippet/blob level
# if a thread-safe backend (e.g. fdetect gRPC) is used.
```

### Additional Constraints

| Constraint | Worker | Impact |
|-----------|--------|--------|
| **dlib segfault** | `face_encoding_worker`, `onboarding_verification_worker` | Fatal crash — no recovery possible |
| **High memory per message** | `face_encoding_worker` | Each video loads full frames into RAM (~200-500MB). Multiple concurrent videos risk OOM. |
| **Long processing time** | `face_encoding_worker` | 30-120s per video. With `max_lease_duration=150s`, parallel messages risk lease expiry. |
| **CPU saturation** | `face_encoding_worker` | Face detection is CPU-bound. Multiple concurrent videos compete for same cores. |

---

## Per-Worker Feasibility Matrix

| Worker | Backend | Thread-Safe? | Parallel Feasible? | Recommended `max_messages` |
|--------|---------|:---:|:---:|:---:|
| `face_encoding_worker` | `face_recognition` (dlib) | ❌ | ❌ | **1** |
| `face_encoding_worker` | `fdetect` (gRPC) | ✅ | ✅ | **3-5** |
| `face_verification_worker` | N/A (numpy only) | ✅ | ✅ | **5-10** |
| `onboarding_verification_worker` | `face_recognition` (dlib) | ❌ | ❌ | **1** |
| `onboarding_verification_worker` | `fdetect` (gRPC) | ✅ | ✅ | **3-5** |

### Why `face_verification_worker` Is Safe

The verification worker:
- Downloads `.npy` files from GCS (I/O bound, thread-safe)
- Performs `numpy` distance calculations (thread-safe, GIL-released for numpy ops)
- Writes JSON to GCS (thread-safe)
- Publishes to Pub/Sub (thread-safe)

**No dlib/face_recognition calls** — purely numpy + GCS + Pub/Sub.

---

## Implementation Guide

### Option A: Configuration Only (No Code Changes)

For workers that are already thread-safe, simply update the `.env` files:

```env
# face_verification_worker/envs/.env.prod
INGEST__FLOW_MAX_MESSAGES=5
INGEST__FLOW_MAX_LEASE_DURATION=60   # Verification is fast (~5s)
INGEST__MAX_WORKERS=5

# face_encoding_worker/envs/.env.prod (fdetect backend ONLY)
INGEST__FLOW_MAX_MESSAGES=3
INGEST__FLOW_MAX_LEASE_DURATION=150
INGEST__MAX_WORKERS=3
```

This works immediately because `GCPSubscriber` already passes `max_messages` to `FlowControl`.

### Option B: Explicit Thread Pool Control (Optional Enhancement)

For finer control over the callback thread pool:

```python
# common/services/cloud/gcp/pubsub/subscriber.py

from concurrent.futures import ThreadPoolExecutor
from google.cloud.pubsub_v1.subscriber.scheduler import ThreadScheduler

class GCPSubscriber:
    def __init__(self, ..., num_callback_threads: Optional[int] = None):
        ...
        self.num_callback_threads = num_callback_threads

        # Custom thread pool for callback execution
        scheduler = None
        if num_callback_threads:
            scheduler = ThreadScheduler(
                executor=ThreadPoolExecutor(max_workers=num_callback_threads)
            )

        if sa_path:
            credentials = service_account.Credentials.from_service_account_file(sa_path)
            self._client = pubsub_v1.SubscriberClient(
                credentials=credentials, scheduler=scheduler
            )
        else:
            self._client = pubsub_v1.SubscriberClient(scheduler=scheduler)
```

### Option C: Process-Level Parallelism (For dlib Workers)

Since dlib is not thread-safe but IS process-safe, an alternative for encoding workers:

```python
# Use multiprocessing instead of threading for dlib-based workers
# Deploy N container replicas, each with max_messages=1

# Kubernetes / Cloud Run:
#   replicas: 3  (each processes 1 message at a time)
#   Total throughput: 3 messages concurrently
```

This is the **current recommended approach** for dlib-based workers — scale horizontally
via container replicas rather than in-process threading.

---

## Recommended Configuration Per Environment

### face_verification_worker (Safe to Parallelize Now)

| Environment | `FLOW_MAX_MESSAGES` | `FLOW_MAX_LEASE_DURATION` | Rationale |
|-------------|:---:|:---:|-----------|
| Local | 1 | 60 | Simple debugging |
| Dev | 3 | 60 | Test parallelism |
| QA | 5 | 60 | Load testing |
| Production | 5-10 | 60 | Verification is fast (~5s) |

### face_encoding_worker — fdetect backend

| Environment | `FLOW_MAX_MESSAGES` | `FLOW_MAX_LEASE_DURATION` | Rationale |
|-------------|:---:|:---:|-----------|
| Local | 1 | 150 | Simple debugging |
| Dev | 2 | 150 | Test parallelism |
| QA | 3 | 150 | Load testing |
| Production | 3-5 | 150 | Bounded by fdetect server capacity |

### face_encoding_worker — face_recognition backend

| Environment | `FLOW_MAX_MESSAGES` | `FLOW_MAX_LEASE_DURATION` | Rationale |
|-------------|:---:|:---:|-----------|
| All | **1** | 150 | **dlib is not thread-safe — DO NOT increase** |

---

## Monitoring Considerations

When enabling parallel processing, monitor:

| Metric | Concern | Threshold |
|--------|---------|-----------|
| Container memory | OOM on multiple concurrent messages | Set memory limits per container |
| Message ack latency | Lease expiry under load | `ack_latency < max_lease_duration * 0.8` |
| CPU utilization | Saturation reducing throughput | < 80% sustained |
| `pubsub.googleapis.com/subscription/num_undelivered_messages` | Backlog growth | Alert if growing over time |
| Error rate | Increased failures under concurrency | Compare to baseline |

---

## Migration Steps (face_verification_worker)

1. **Dev**: Set `INGEST__FLOW_MAX_MESSAGES=3` in `.env.dev`
2. **Deploy & monitor** for 24h — check logs for race conditions
3. **QA**: Set to `5`, run load test with multiple simultaneous messages
4. **Production**: Set to `5`, monitor memory/CPU/ack latency
5. **Tune**: Increase to `10` if metrics look healthy

---

## Summary

| Question | Answer |
|----------|--------|
| Is parallel processing supported by the code? | ✅ Yes — `max_messages` in FlowControl |
| Why is it set to 1? | dlib (face_recognition) segfaults in multi-threaded use |
| Which worker can enable it today? | `face_verification_worker` (numpy-only, thread-safe) |
| What about encoding workers? | Safe only with `fdetect` gRPC backend; use container replicas for dlib |
| Code changes needed? | None — configuration only (`.env` files) |

---

## Status

| Date | Status |
|------|--------|
| 2026-06-02 | 📋 Documented |
| TBD | 🔧 Enable for `face_verification_worker` in dev |
| TBD | 🚀 Production rollout for verification worker |
| TBD | 🔧 Enable for encoding/onboarding workers after full fdetect migration |
