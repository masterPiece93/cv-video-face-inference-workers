# Request De-duplication Strategy

## Implementation Report

**Date**: 2026-06-01  
**Author**: AI Engineering Team  
**Status**: Proposed  
**Affects**: All 3 workers (`face_encoding_worker`, `face_verification_worker`, `onboarding_verification_worker`)

---

## 1. Problem Statement

GCP Pub/Sub provides **at-least-once** delivery guarantees. This means a message can be delivered more than once to a subscriber under several real-world conditions. Without deduplication, the workers will redundantly re-process expensive operations (video encoding, GCS downloads, downstream message publishing).

---

## 2. Architecture Context

```
GoLang Source Worker
        │
        ▼  (publishes)
┌─────────────────────────────────────┐
│  Pub/Sub: video-encoding topic      │
└────────────────┬────────────────────┘
                 │
                 ▼  (subscribes)
┌─────────────────────────────────────┐
│  face_encoding_worker               │  ← Most expensive (dlib CPU, GCS I/O)
│  Writes: encoding.json              │
│  Publishes → verification topic     │
└────────────────┬────────────────────┘
                 │
                 ▼  (subscribes)
┌─────────────────────────────────────┐
│  face_verification_worker           │
│  Writes: verification.json          │
│  Publishes → onboarding topic       │
└────────────────┬────────────────────┘
                 │
                 ▼  (subscribes)
┌─────────────────────────────────────┐
│  onboarding_verification_worker     │
│  Writes: onboarding_verification.json│
│  Publishes → result topic           │
└─────────────────────────────────────┘
```

---

## 3. Duplicate Delivery Scenarios

### 3.1 Pub/Sub Ack Deadline Expiry

| Aspect | Detail |
|--------|--------|
| **Trigger** | Worker takes longer than `max_lease_duration` to process a message |
| **Likelihood** | HIGH for `face_encoding_worker` (11 snippets × dlib encoding = 30–120s) |
| **Effect** | Pub/Sub assumes the message was not processed and redelivers it |
| **Workers affected** | Primarily `face_encoding_worker` |

### 3.2 Worker Crash / Container Eviction

| Aspect | Detail |
|--------|--------|
| **Trigger** | OOM kill, pod eviction, node preemption, unhandled exception |
| **Likelihood** | MEDIUM |
| **Effect** | Message never acked → Pub/Sub redelivers after ack deadline |
| **Workers affected** | All 3 |

### 3.3 Upstream Publisher Retry

| Aspect | Detail |
|--------|--------|
| **Trigger** | GoLang source worker publishes, network timeout before receiving Pub/Sub ack, retries → publishes same `event_id` twice |
| **Likelihood** | MEDIUM |
| **Effect** | Two distinct Pub/Sub messages with identical `event_id` and payload |
| **Workers affected** | `face_encoding_worker` (first in chain) |

### 3.4 Publish-Before-Ack Race (Cascading Duplicates)

| Aspect | Detail |
|--------|--------|
| **Trigger** | `face_encoding_worker` successfully publishes downstream to verification topic, then crashes/times out before acking its own ingestion message |
| **Likelihood** | HIGH under load |
| **Effect** | Re-processes encoding AND publishes a **second** downstream message → `face_verification_worker` receives two messages for same event |
| **Workers affected** | `face_verification_worker`, `onboarding_verification_worker` (cascading) |

### 3.5 Horizontal Scaling Rebalance

| Aspect | Detail |
|--------|--------|
| **Trigger** | During subscriber rebalancing, two instances briefly hold the same lease |
| **Likelihood** | LOW (Pub/Sub streaming pull handles this well) |
| **Effect** | Concurrent duplicate processing |
| **Workers affected** | All 3 |

---

## 4. Current Protections

| Protection | Status | Notes |
|------------|--------|-------|
| Pub/Sub Dead Letter Queue (DLQ) | ✅ Present | Prevents infinite retry loops after `max_delivery_attempts` |
| Flow control (`max_messages=1`) | ✅ Present | Limits concurrent processing per instance |
| Long lease duration (600s) | ✅ Present | Reduces ack-timeout redelivery probability |
| Idempotent GCS writes | ✅ Natural | Overwriting same `.npy` / `.json` is safe |
| Event-level deduplication | ❌ Missing | **This proposal addresses this gap** |

---

## 5. Proposed Solution: GCS Stage-File Deduplication

### 5.1 Strategy Overview

Each worker already writes a **stage tracking JSON** to GCS upon successful completion (e.g., `encoding.json` with `"status": "OK"`). We leverage this existing artifact as a deduplication marker.

The `event_id` is no longer part of the GCS path — it is stored **inside the JSON payload** instead. The deduplication check reads the flat stage file and compares the `event_id` field to guard against duplicate processing of the same event.

```
┌──────────────────────────────────────────────────────────┐
│  Message arrives with event_id = "evt-abc-123"           │
│                                                          │
│  Step 1: READ stage file from GCS                        │
│          gs://{bucket}/{base_path}/stages/               │
│                         {stage_filename}.json            │
│                                                          │
│  Step 2: Check event_id + status fields                  │
│          ┌─────────────────────────────────────────┐     │
│          │ event_id matches AND status == "OK"      │     │
│          │              → ACK immediately           │     │
│          │                (already processed)       │     │
│          │                                          │     │
│          │ event_id differs OR status != "OK"       │     │
│          │         OR file not found                │     │
│          │              → Proceed with pipeline     │     │
│          └─────────────────────────────────────────┘     │
│                                                          │
│  Step 3: Process normally                                │
│  Step 4: Write stage file with status="OK", event_id=""  │
│  Step 5: Publish downstream message                      │
│  Step 6: ACK                                             │
└──────────────────────────────────────────────────────────┘
```

### 5.2 Stage File Per Worker

| Worker | Stage File | Dedup Check |
|--------|-----------|-------------|
| `face_encoding_worker` | `stages/encoding.json` | `event_id` matches **AND** `status == "OK"` |
| `face_verification_worker` | `stages/verification.json` | `event_id` matches **AND** `status == "OK"` |
| `onboarding_verification_worker` | `stages/onboarding_verification.json` | `event_id` matches **AND** `status == "OK"` |

> **Why check `event_id` too?**  
> Because stage files are now **shared across events** for the same candidate (flat `stages/` folder).  
> Without the `event_id` check, a prior successful run for event `evt-001` would falsely block processing of a new event `evt-002` for the same candidate.

### 5.3 GCS Path Structure

```
gs://{bucket}/{org_alias}/{org_id}/{candidate_email}/{candidate_uid}/
    stages/
        encoding.json                ← face_encoding_worker
        verification.json            ← face_verification_worker
        onboarding_verification.json ← onboarding_verification_worker
```

Each file contains `event_id` in its JSON payload:

```json
{
    "message_id": "evt-abc-123",
    "event_id":   "evt-abc-123",
    "started":    "2026-06-01T10:00:00+00:00",
    "completed":  "2026-06-01T10:02:30+00:00",
    "status":     "OK"
}
```

---

## 6. Technical Implementation

### 6.1 face_encoding_worker (`services/encoding.py`)

```python
def process(self, payload: dict) -> None:
    event_id = payload["event_id"]
    bucket = payload["bucket_name"]
    # ... existing field extraction ...

    base_path = build_candidate_base_path(org_alias, org_id, candidate_email, candidate_uid)

    # ── Deduplication guard ───────────────────────────────────────────────
    # Stage files are flat under stages/ — check event_id inside the JSON
    # to distinguish events for the same candidate.
    stage_blob = build_stage_path(base_path, "encoding.json")
    existing_stage = self.storage.download_json(bucket, stage_blob)
    if (existing_stage
            and existing_stage.get("event_id") == event_id
            and existing_stage.get("status") == "OK"):
        logger.info(f"[{event_id}] Already processed (dedup) — skipping")
        return  # caller will ack the message
    # ─────────────────────────────────────────────────────────────────────

    # ... rest of pipeline unchanged ...
```

### 6.2 face_verification_worker (`services/verification.py`)

```python
def process(self, payload: dict) -> None:
    event_id = payload["event_id"]
    bucket = payload["bucket_name"]
    # ... existing field extraction ...

    base_path = build_candidate_base_path(org_alias, org_id, candidate_email, candidate_uid)

    # ── Deduplication guard ───────────────────────────────────────────────
    stage_blob = build_stage_path(base_path, "verification.json")
    existing_stage = self.storage.download_json(bucket, stage_blob)
    if (existing_stage
            and existing_stage.get("event_id") == event_id
            and existing_stage.get("status") == "OK"):
        logger.info(f"[{event_id}] Already processed (dedup) — skipping")
        return
    # ─────────────────────────────────────────────────────────────────────

    # ... rest of pipeline unchanged ...
```

### 6.3 onboarding_verification_worker (`services/verification.py`)

```python
def process(self, payload: dict) -> None:
    event_id = payload["event_id"]
    bucket = payload["bucket_name"]
    # ... existing field extraction ...

    base_path = build_candidate_base_path(org_alias, org_id, candidate_email, candidate_uid)

    # ── Deduplication guard ───────────────────────────────────────────────
    stage_blob = build_stage_path(base_path, "onboarding_verification.json")
    existing_stage = self.storage.download_json(bucket, stage_blob)
    if (existing_stage
            and existing_stage.get("event_id") == event_id
            and existing_stage.get("status") == "OK"):
        logger.info(f"[{event_id}] Already processed (dedup) — skipping")
        return
    # ─────────────────────────────────────────────────────────────────────

    # ... rest of pipeline unchanged ...
```

### 6.4 Required Addition to `GCPStorageService`

A `download_json` method is needed (if not already present):

```python
def download_json(self, bucket: str, blob_path: str) -> Optional[dict]:
    """Download and parse a JSON blob. Returns None if not found."""
    data = self.download_bytes(bucket, blob_path)
    if data is None:
        return None
    try:
        import json
        return json.loads(data.read())
    except (json.JSONDecodeError, Exception):
        return None
```

---

## 7. Edge Cases & Mitigations

### 7.1 Race Condition: Two Instances Check Simultaneously

| Situation | Two worker instances receive the same message (scenario 3.5), both read the stage file at ~the same time, both see "no OK", both proceed. |
|-----------|---|
| **Impact** | Both encode/verify the same event concurrently. |
| **Mitigation** | Acceptable — GCS writes are idempotent (same `.npy` gets overwritten with identical content). Downstream duplicates are caught by the next worker's dedup guard. The probability is extremely low with `max_messages=1` flow control. |
| **Alternative** | Use GCS [object preconditions](https://cloud.google.com/storage/docs/request-preconditions) (`ifGenerationMatch=0`) to achieve write-once semantics. Adds complexity, not recommended for v1. |

### 7.2 Partial Failure: Stage Written But Downstream Not Published

| Situation | Worker writes `encoding.json` with `status=OK`, then crashes before publishing to the verification topic. On retry, dedup guard skips processing. |
|-----------|---|
| **Impact** | Verification worker never receives the message. |
| **Mitigation** | **Re-publish only** path. If dedup detects `status=OK` but the worker was called again (message wasn't acked), it should re-publish the downstream message without re-processing: |

```python
existing_stage = self.storage.download_json(bucket, stage_blob)
if (existing_stage
        and existing_stage.get("event_id") == event_id
        and existing_stage.get("status") == "OK"):
    logger.info(f"[{event_id}] Already processed — re-publishing downstream only")
    # Reconstruct minimal output and re-publish
    self.output_handler.publish(self._build_output_payload(payload, existing_stage))
    return
```

This ensures the downstream message is eventually delivered even if the original publish was lost.

### 7.3 Stale "ERROR" Status

| Situation | Previous attempt wrote `status=ERROR` (e.g., transient GCS issue). Message is redelivered. |
|-----------|---|
| **Impact** | None — dedup guard only skips on `status == "OK"`. Errors allow reprocessing. |
| **Mitigation** | Built-in by design. |

### 7.4 Event ID Collision

| Situation | Two different events for the same candidate share the same `event_id` (upstream bug). |
|-----------|---|
| **Impact** | The second event would be falsely identified as a duplicate of the first and skipped. |
| **Mitigation** | The dedup check compares `event_id` inside the stage JSON — not the path — so the guard is `event_id matches AND status == "OK"`. Crucially, because stage files are written per-candidate (not per-event), a new event for the same candidate always overwrites the prior stage file with its own `event_id`. As long as upstream guarantees unique `event_id` values, this is not a risk. |

### 7.5 Stage File TTL / Cleanup

| Situation | Stage files accumulate over time in GCS. |
|-----------|---|
| **Impact** | Storage cost (negligible — each file is ~200 bytes). |
| **Mitigation** | Apply a GCS [Object Lifecycle rule](https://cloud.google.com/storage/docs/lifecycle) on the `stages/` prefix to auto-delete files older than 30 days. |

---

## 8. Alternative Approaches Considered

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **GCS stage file (proposed)** | Zero new infra; uses existing artifacts; persistent; region-local | ~50ms latency for one GET per message; eventual consistency (negligible risk) | ✅ **Recommended** |
| **Redis / Memorystore** | Sub-ms lookup; natural TTL | New infra cost (~$50/mo); another failure point; not persistent across deploys | ❌ Over-engineered for current scale |
| **Cloud Firestore** | Strong consistency; TTL support | Adds latency; overkill; new dependency | ❌ Over-engineered |
| **Pub/Sub exactly-once delivery** | Built-in; no code changes | Requires ordered messages + single region; doesn't protect against upstream re-publish; restricted throughput | ❌ Too restrictive |
| **In-memory set (process-level)** | Zero latency | Lost on restart; doesn't work across instances | ❌ Insufficient |
| **Database (Cloud SQL)** | ACID guarantees | Heavy dependency; latency; connection pooling complexity | ❌ Over-engineered |

---

## 9. Performance Impact

| Metric | Without Dedup | With Dedup |
|--------|--------------|------------|
| **Normal path** (first delivery) | Process immediately | +1 GCS GET (~50ms) then process |
| **Duplicate path** (redelivery) | Full re-processing (30–120s for encoding) | 1 GCS GET (~50ms) + immediate ack |
| **GCS cost** | N/A | ~$0.004 per 10,000 Class B ops (GET) |
| **Net savings on duplicate** | — | 30–120s CPU + GCS bandwidth saved |

The overhead of one additional GCS read per message is negligible compared to the savings on duplicate avoidance.

---

## 10. Implementation Priority

| Worker | Priority | Justification |
|--------|----------|---------------|
| `face_encoding_worker` | **P0 — Critical** | Most expensive (CPU + GCS I/O); first in chain; duplicates cascade downstream |
| `face_verification_worker` | **P1 — High** | Receives cascading duplicates from encoding worker; relatively fast but still does GCS I/O |
| `onboarding_verification_worker` | **P2 — Medium** | Last in chain; fast execution; but still worth guarding for correctness |

---

## 11. Testing Strategy

### Unit Tests

```python
def test_dedup_skips_already_processed():
    """Verify that process() returns immediately when stage shows OK for same event_id."""
    mock_storage = Mock()
    mock_storage.download_json.return_value = {"status": "OK", "event_id": "evt-abc-123", ...}

    service = VideoFaceEncodingService(encoder=..., storage=mock_storage, ...)
    service.process(valid_payload)  # valid_payload["event_id"] == "evt-abc-123"

    # Should NOT call any encoding methods
    mock_storage.download_bytes.assert_not_called()

def test_dedup_allows_processing_for_different_event_id():
    """Verify that a different event_id on the stage file does NOT trigger dedup skip."""
    mock_storage = Mock()
    mock_storage.download_json.return_value = {"status": "OK", "event_id": "evt-old-999", ...}

    service = VideoFaceEncodingService(encoder=..., storage=mock_storage, ...)
    service.process(valid_payload)  # valid_payload["event_id"] == "evt-abc-123"

    # Should proceed with encoding (different event for same candidate)
    mock_storage.download_bytes.assert_called()

def test_dedup_allows_reprocessing_on_error_status():
    """Verify that ERROR status does NOT trigger dedup skip."""
    mock_storage = Mock()
    mock_storage.download_json.return_value = {"status": "ERROR", "event_id": "evt-abc-123", ...}

    service = VideoFaceEncodingService(encoder=..., storage=mock_storage, ...)
    service.process(valid_payload)

    # Should proceed with encoding
    mock_storage.download_bytes.assert_called()

def test_dedup_allows_processing_when_no_stage_file():
    """Verify that missing stage file allows normal processing."""
    mock_storage = Mock()
    mock_storage.download_json.return_value = None

    service = VideoFaceEncodingService(encoder=..., storage=mock_storage, ...)
    service.process(valid_payload)

    # Should proceed with encoding
    mock_storage.download_bytes.assert_called()
```

### Integration Tests

1. Publish same message twice → verify only one set of encodings created
2. Publish message, kill worker mid-processing → verify retry succeeds
3. Publish message, verify stage file written → publish again → verify immediate ack

---

## 12. Rollout Plan

| Phase | Action | Timeline |
|-------|--------|----------|
| 1 | Add `download_json()` to `GCPStorageService` (if missing) | Day 1 |
| 2 | Implement dedup guard in `face_encoding_worker` | Day 1 |
| 3 | Add unit tests for dedup logic | Day 1 |
| 4 | Deploy to dev, run duplicate-publish tests | Day 2 |
| 5 | Implement in `face_verification_worker` | Day 2 |
| 6 | Implement in `onboarding_verification_worker` | Day 2 |
| 7 | Add GCS lifecycle rule for `stages/` prefix (30-day TTL) | Day 3 |
| 8 | Deploy to production | Day 3–4 |

---

## 13. Monitoring & Observability

Add a counter metric for dedup hits to track how often duplicates are being caught:

```python
# In each worker's process() method:
if existing_stage and existing_stage.get("status") == "OK":
    logger.info(f"[{event_id}] Already processed (dedup) — skipping")
    # Metric: increment dedup_hits counter
    # e.g., statsd.increment("worker.dedup.hit", tags=["worker:face_encoding"])
    return
```

Dashboard alerts to configure:
- **High dedup rate** (>10% of messages are duplicates) → investigate upstream publisher or lease duration settings
- **Zero dedup hits over 7 days** → dedup logic may be broken or dead code

---

## 14. Summary

The proposed GCS stage-file deduplication strategy provides:

- ✅ **Zero new infrastructure** — leverages existing GCS stage files
- ✅ **Minimal code change** — ~5 lines per worker
- ✅ **Negligible performance overhead** — one GCS GET per message (~50ms)
- ✅ **Significant savings on duplicates** — avoids 30–120s of wasted CPU + GCS bandwidth
- ✅ **Cascading protection** — dedup at each stage prevents downstream amplification
- ✅ **Safe re-publish** — handles the partial-failure edge case gracefully
- ✅ **Natural cleanup** — GCS lifecycle rules handle TTL

This is a pragmatic, production-ready approach that matches the current architecture without introducing operational complexity.
