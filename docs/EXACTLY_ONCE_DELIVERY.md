# Exactly-Once Delivery — Scope & Implementation Plan

## Overview

GCP Pub/Sub supports **exactly-once delivery** at the subscription level. When enabled,
Pub/Sub guarantees that each message is delivered to the subscriber callback **exactly once**
(no duplicates), as opposed to the default **at-least-once** semantics where the same message
may be delivered multiple times on nack, lease expiry, or network issues.

This document evaluates the applicability of `enable_exactly_once_delivery` to our
ECI workers pipeline and provides an implementation plan for future adoption.

---

## Current State

| Aspect | Current Behavior |
|--------|-----------------|
| Delivery guarantee | **At-least-once** (default Pub/Sub behavior) |
| Subscription creation | `common/services/cloud/gcp/pubsub/subscriber.py` → `ensure_subscription_exists()` |
| Duplicate handling | Workers are **idempotent by design** — same input always overwrites the same GCS paths |
| Ack behavior | `message.ack()` is fire-and-forget (no error handling on ack failures) |

### Why it hasn't been enabled yet

1. **Idempotency** — All three workers produce deterministic outputs at fixed GCS paths, so duplicate delivery is functionally harmless.
2. **Emulator incompatibility** — The local Pub/Sub emulator does **not** support exactly-once delivery. Enabling it unconditionally would break local development and integration tests.
3. **Ack error handling** — With exactly-once enabled, `message.ack()` can raise `google.cloud.pubsub_v1.types.AcknowledgeError`. Our current code does not handle this.

---

## Impact Assessment

### Benefits of Enabling

| Benefit | Impact | Priority |
|---------|--------|----------|
| **Avoid redundant video processing** | Each video encoding run is expensive (CPU-bound frame extraction + face detection). Preventing duplicate runs saves ~30-120s of compute per duplicate. | High |
| **Cleaner stage tracking** | No risk of race conditions where two instances write to the same `encoding.json` / `verification.json` simultaneously. | Medium |
| **Accurate DLQ counts** | Dead-letter delivery attempt counters are more reliable with exactly-once. | Low |
| **Simplified debugging** | Log streams contain no duplicate processing entries. | Low |

### Risks & Trade-offs

| Risk | Severity | Mitigation |
|------|----------|------------|
| `AcknowledgeError` on ack failure → message redelivered anyway | Low | Workers are idempotent; at worst we get one duplicate (same as today) |
| Increased ack latency (~100-200ms per message) | Negligible | Our `max_messages=1` with long lease durations means this adds <1% overhead |
| Pub/Sub emulator does not support it | Medium | Guard with environment check (see implementation below) |
| Subscription must be recreated (cannot toggle on existing) | Medium | Plan a one-time subscription recreation in production |

---

## Implementation Plan

### Phase 1: Code Change (subscriber.py)

```python
# In common/services/cloud/gcp/pubsub/subscriber.py

import os

class GCPSubscriber:
    def __init__(
        self,
        ...
        enable_exactly_once: bool = True,   # NEW PARAMETER
        ...
    ):
        ...
        self.enable_exactly_once = enable_exactly_once

    def ensure_subscription_exists(self) -> None:
        try:
            self._client.get_subscription(request={"subscription": self.subscription_path})
            logger.info(f"Subscription already exists: {self.subscription_path}")
        except NotFound:
            logger.warning(f"Subscription not found, creating: {self.subscription_path}")
            kwargs = dict(name=self.subscription_path, topic=self.topic_path)

            # Exactly-once delivery — only on real GCP (emulator doesn't support it)
            is_emulated = bool(os.environ.get("PUBSUB_EMULATOR_HOST"))
            if self.enable_exactly_once and not is_emulated:
                kwargs["enable_exactly_once_delivery"] = True
                logger.info("Exactly-once delivery ENABLED for new subscription")

            if self.dlq_topic:
                dlq_path = f"projects/{self.project_id}/topics/{self.dlq_topic}"
                kwargs["dead_letter_policy"] = DeadLetterPolicy(
                    dead_letter_topic=dlq_path,
                    max_delivery_attempts=self.max_delivery_attempts,
                )
            self._client.create_subscription(request=kwargs)
            logger.info(f"Subscription created: {self.subscription_path}")
```

### Phase 2: Ack Error Handling (input handlers)

```python
# In each worker's input_handler.py — wrap ack/nack with error handling

from google.cloud.pubsub_v1.types import AcknowledgeError

def handle_message(message, service) -> None:
    ...
    try:
        service.process(payload)
        _safe_ack(message, event_id)
    except NonRecoverableError as e:
        logger.error(f"[{event_id}] Non-recoverable error: {e}")
        _safe_ack(message, event_id)
    except RecoverableError as e:
        logger.warning(f"[{event_id}] Recoverable error (will retry): {e}")
        _safe_nack(message, event_id)
    ...


def _safe_ack(message, event_id: str) -> None:
    """Ack with exactly-once error handling."""
    try:
        message.ack()
    except Exception as e:
        # With exactly-once, ack can fail. Message will be redelivered.
        # Since workers are idempotent, this is safe.
        logger.warning(f"[{event_id}] Ack failed (will be redelivered): {e}")


def _safe_nack(message, event_id: str) -> None:
    """Nack with error handling."""
    try:
        message.nack()
    except Exception as e:
        logger.warning(f"[{event_id}] Nack failed: {e}")
```

### Phase 3: Production Subscription Recreation

Exactly-once **cannot be toggled** on an existing subscription. Steps:

1. **Create new subscription** with `enable_exactly_once_delivery=True`
2. **Drain the old subscription** (let pending messages finish processing)
3. **Update worker config** to point to the new subscription name
4. **Delete old subscription**

Alternatively, if using `ensure_subscription_exists()` with a **new subscription name**:
```env
# .env.prod (updated)
GCP__PUBSUB_INGESTION_SUBSCRIPTION=tdx-gta-prod-external-candidate-video-encoding-sub-v2
```

### Phase 4: Integration Test Update

```python
# tests/integration/conftest.py — no change needed
# The emulator check (os.environ.get("PUBSUB_EMULATOR_HOST")) ensures
# exactly-once is NOT applied during integration tests.
```

---

## Affected Workers

| Worker | Subscription | Action Required |
|--------|-------------|-----------------|
| `face_encoding_worker` | `*-video-encoding-sub` | Recreate with exactly-once |
| `face_verification_worker` | `*-video-verification-sub` | Recreate with exactly-once |
| `onboarding_verification_worker` | `*-onboarding-verification-sub` | Recreate with exactly-once |

---

## Decision Matrix

| Environment | `enable_exactly_once_delivery` | Reason |
|-------------|-------------------------------|--------|
| **Local (emulator)** | ❌ Disabled | Emulator doesn't support it |
| **Integration tests** | ❌ Disabled | Same — emulator env var present |
| **Dev (live GCP)** | ✅ Enabled | Safe to test; low traffic |
| **QA** | ✅ Enabled | Validates ack error handling under load |
| **Production** | ✅ Enabled | Prevents wasted compute on duplicates |

---

## Rollback Plan

If exactly-once causes issues (e.g., high ack failure rates):
1. Deploy with `enable_exactly_once=False` in the `GCPSubscriber` constructor
2. Recreate subscriptions without the flag (or create `-v3` subscriptions)
3. No data loss — failed acks simply cause redelivery (idempotent processing)

---

## References

- [GCP Pub/Sub Exactly-Once Delivery](https://cloud.google.com/pubsub/docs/exactly-once-delivery)
- [AcknowledgeError handling](https://cloud.google.com/pubsub/docs/exactly-once-delivery#handling_acknowledgment_failures)
- [Subscription properties (immutable flags)](https://cloud.google.com/pubsub/docs/admin#update_subscription)

---

## Status

| Date | Status |
|------|--------|
| 2026-06-02 | 📋 Documented — awaiting sprint prioritisation |
| TBD | 🔧 Phase 1 implementation |
| TBD | 🔧 Phase 2 ack error handling |
| TBD | 🚀 Phase 3 production rollout |
