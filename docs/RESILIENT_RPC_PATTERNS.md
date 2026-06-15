# Resilient RPC Client Patterns — Adoption Plan

## Source

These patterns are adapted from the [Portable Implementation Spec](../PORTABLE-SPEC.md)
originally implemented in `ta_video_face_comparison`. This document maps each pattern
to our ECI workers codebase and provides an implementation plan.

---

## Pattern Mapping to ECI Workers

| Spec Term | ECI Workers Equivalent |
|-----------|----------------------|
| **RemoteClient** | `FdetectEncoder` (`common/services/encoding/fdetect_encoder.py`) |
| **call()** | `_call_with_retry()` → `self._stub.Detect(request)` |
| **healthCheck()** | **Does not exist yet** — `# TODO : add ping` in code |
| **Connection** | `grpc.insecure_channel(channel_address)` |
| **Message** | Pub/Sub message (all 3 workers) |
| **ack() / nack()** | `message.ack()` / `message.nack()` |
| **AppEntryPoint** | `create_app()` + `main()` in each worker's `main.py` |
| **RecoverableError** | `common.services.errors.RecoverableError` ✅ already exists |
| **NonRecoverableError** | `common.services.errors.NonRecoverableError` ✅ already exists |

---

## Current State vs Desired State

| Pattern | Current Status | Gap |
|---------|---------------|-----|
| **1. Never return silent None** | ❌ `_call_with_retry()` returns `None` on retry exhaustion | `encode_frame()` then returns `[]`, masking the real failure |
| **2. Health-based error classification** | ❌ No health check exists | No way to differentiate "service down" from "bad request" |
| **3. Pre-request health pinging** | ❌ Not implemented | Every request goes through full retry cycle even if fdetect is down |
| **4. Dependency injection** | ✅ Partially done | `create_app()` builds encoder + injects into service. But no startup health check. |
| **5. Channel cleanup on shutdown** | ❌ No `close()` on `FdetectEncoder` | gRPC channel leaks on shutdown. `main.py` only closes subscriber, not encoder. |

---

## Implementation Plan

### Pattern 1 — Never Return Silent `None` on Failure

**File:** `common/services/encoding/fdetect_encoder.py`

**Current (broken):**
```python
def _call_with_retry(self, request) -> Optional[FaceDetectResponse]:
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            return self._stub.Detect(request)
        except grpc.RpcError as err:
            if err.code() == grpc.StatusCode.UNAVAILABLE:
                time.sleep(_RETRY_SLEEP)
            else:
                raise
    logger.error(f"fdetect max retries ({_MAX_RETRIES}) reached")
    return None  # ← BUG: silent failure, caller gets empty list
```

**Fix:**
```python
def _call_with_retry(self, request) -> safe_pb2.FaceDetectResponse:
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            return self._stub.Detect(request)
        except grpc.RpcError as err:
            if err.code() == grpc.StatusCode.UNAVAILABLE:
                logger.warning(
                    f"fdetect UNAVAILABLE (attempt {attempt}/{_MAX_RETRIES}), "
                    f"retrying in {_RETRY_SLEEP}s..."
                )
                time.sleep(_RETRY_SLEEP)
            else:
                logger.error(f"fdetect gRPC error: {err}")
                raise

    # Retries exhausted — classify using Pattern 2
    logger.error(f"fdetect max retries ({_MAX_RETRIES}) reached")
    if self.ping():
        raise RecoverableError(
            "fdetect service is healthy but Detect RPC failed after max retries"
        )
    else:
        raise NonRecoverableError(
            "fdetect service is unreachable (health ping failed after retries)"
        )
```

**Impact:** `encode_frame()` will now propagate errors up instead of silently returning `[]`.
The encoding service's per-frame `try/except` already handles this gracefully.

---

### Pattern 2 — Health-Based Error Classification

**File:** `common/services/encoding/fdetect_encoder.py`

**Add `ping()` method:**
```python
def ping(self) -> bool:
    """Ping the fdetect gRPC Health endpoint.

    Returns True if the service responds with Status == "OK", False otherwise.
    Short timeout (3s) — never raises.
    """
    try:
        health_stub = safe_pb2_grpc.HealthStub(self._channel)
        request = safe_pb2.HealthRequest(RequestId="ping", Full=False)
        response = health_stub.get(request, timeout=3)
        return response.Status == "OK"
    except Exception as e:
        logger.warning(f"fdetect health ping failed: {e}")
        return False
```

**Properties:**
- Short timeout (3s) — won't hang the worker
- Never raises — any error = unhealthy
- Reuses existing channel — reflects real connection state

**Prerequisite:** Verify that `safe_pb2_grpc.HealthStub` and `safe_pb2.HealthRequest`
exist in the `pbgen` package. If not, generate them from the fdetect Health proto definition.

---

### Pattern 3 — Pre-Request Health Pinging

**File:** `common/services/encoding/fdetect_encoder.py`

**Add at the top of `encode_frame()`:**
```python
def encode_frame(self, frame_bytes: BytesIO) -> List[FaceEncoding]:
    if not self.ping():
        raise NonRecoverableError(
            "fdetect service is unreachable (pre-request health ping failed)"
        )

    frame_bytes.seek(0)
    request = safe_pb2.FaceDetectRequest(...)
    response = self._call_with_retry(request)
    ...
```

**Two complementary layers:**

| Layer | When | On failure |
|-------|------|------------|
| Pre-request ping (Pattern 3) | Before building request | `NonRecoverableError` immediately |
| Post-retry ping (Pattern 2) | After retries exhausted | `Recoverable` or `NonRecoverable` |

---

### Pattern 4 — Startup Health Check (Fail Fast)

**File:** Each worker's `main.py` (face_encoding, onboarding_verification)

**Current:** `create_app()` builds the encoder but does NOT verify connectivity.

**Add after encoder construction:**
```python
def create_app(settings: Settings) -> tuple:
    ...
    if settings.encoder_backend == "fdetect":
        encoder = get_encoder("fdetect", channel_address=settings.fdetect_channel)
        # Fail fast: verify fdetect service is reachable at startup
        if not encoder.ping():
            raise RuntimeError(
                f"fdetect gRPC service at {settings.fdetect_channel} is unreachable. "
                "Application will not start."
            )
        logger.info(f"fdetect health check passed ({settings.fdetect_channel})")
    ...
```

**Behaviour:** If fdetect is down at startup → clear error log + immediate exit,
rather than starting and failing on every message.

---

### Pattern 5 — Deterministic Channel Cleanup

**File:** `common/services/encoding/fdetect_encoder.py` + each worker's `main.py`

**Add `close()` to FdetectEncoder:**
```python
def close(self) -> None:
    """Close the underlying gRPC channel. Idempotent and never raises."""
    try:
        self._channel.close()
        logger.info(f"fdetect gRPC channel closed ({self._channel_address})")
    except Exception as e:
        logger.warning(f"Error closing fdetect channel: {e}")
```

**Update `main.py` shutdown:**
```python
def main() -> None:
    ...
    subscriber, encoding_service = create_app(settings)
    ...
    try:
        subscriber.subscribe(callback)
    except Exception as e:
        logger.critical(f"Fatal subscriber error: {e}", exc_info=True)
    finally:
        subscriber.close()
        # Clean up gRPC channel if using fdetect backend
        if hasattr(encoding_service.encoder, 'close'):
            encoding_service.encoder.close()
        logger.info(f"{settings.service_name} stopped")
```

---

## Applicability Per Worker

| Worker | Uses fdetect? | Patterns Applicable |
|--------|:---:|---|
| `face_encoding_worker` | ✅ (configurable) | All 5 patterns |
| `face_verification_worker` | ❌ (numpy only) | None — no remote dependency |
| `onboarding_verification_worker` | ✅ (configurable) | All 5 patterns |

---

## Implementation Order

```
Pattern 1 (no silent None)
    ↓
Pattern 2 (add ping() + classify)
    ↓
Pattern 3 (pre-request ping)
    ↓
Pattern 4 (startup fail-fast)
    ↓
Pattern 5 (close() + finally)
```

Each pattern builds on the previous. They can be implemented in a single PR
or split across multiple.

---

## Files to Change

| File | Patterns | Changes |
|------|----------|---------|
| `common/services/encoding/fdetect_encoder.py` | 1, 2, 3, 5 | Add `ping()`, `close()`, fix `_call_with_retry`, add pre-request ping |
| `common/services/encoding/base.py` | 5 | Add optional `close()` to `BaseEncoder` (default no-op) |
| `workers/face_encoding_worker/main.py` | 4, 5 | Add startup health check, add encoder cleanup in `finally` |
| `workers/onboarding_verification_worker/main.py` | 4, 5 | Same as above |

---

## Acceptance Checklist

- [ ] No code path in `_call_with_retry()` returns `None`
- [ ] Every terminal failure is classified as `RecoverableError` or `NonRecoverableError`
- [ ] `ping()` is cheap (3s timeout), never throws, reuses existing channel
- [ ] Health is probed **before** the request AND **after** retries
- [ ] Startup aborts with a clear log if fdetect is unreachable
- [ ] `close()` is idempotent, never throws
- [ ] gRPC channel is released on all exit paths (interrupt, exception, normal)
- [ ] `face_recognition` backend is unaffected (no remote dependency)
- [ ] Integration tests still pass (fdetect tests use mock encoder, not real gRPC)

---

## Prerequisite: pbgen Health Stubs

Before implementing Pattern 2, verify these exist in `common/services/encoding/pbgen/`:
- `safe_pb2.HealthRequest`
- `safe_pb2.HealthResponse`
- `safe_pb2_grpc.HealthStub`

If missing, they need to be generated from the fdetect service's `.proto` Health definition.

---

## Status

| Date | Status |
|------|--------|
| 2026-06-03 | 📋 Planned — patterns mapped, implementation detailed |
| TBD | 🔧 Implementation (single PR recommended) |
| TBD | ✅ Review + merge |
