# Face Encoding Worker

A Pub/Sub-driven worker that downloads a candidate's interview/profile videos
from GCS, samples frames, detects and encodes faces, deduplicates and clusters
them to isolate the dominant person, and persists the resulting face encodings
(`.npy`) plus representative sampled frames (`.png`) back to GCS. It then
publishes a downstream message to the **video verification** topic.

---

## Table of Contents

- [Overview](#overview)
- [Processing Pipeline](#processing-pipeline)
- [GCS Bucket Layout](#gcs-bucket-layout)
- [Message Contracts](#message-contracts)
  - [Input message](#input-message-ingestion)
  - [Output message](#output-message-egestion)
- [Configuration](#configuration)
- [Encoder Backends](#encoder-backends)
- [Backend-Aware Frame Encoding (Parallelism)](#backend-aware-frame-encoding-parallelism)
  - [Why it is backend-aware](#why-it-is-backend-aware)
  - [The `supports_parallel` capability flag](#the-supports_parallel-capability-flag)
  - [Dispatch logic](#dispatch-logic)
  - [Method breakdown](#method-breakdown)
  - [Determinism & ordering guarantees](#determinism--ordering-guarantees)
  - [Tuning `max_workers`](#tuning-max_workers)
  - [Sequential vs parallel at a glance](#sequential-vs-parallel-at-a-glance)
  - [History](#history)
- [Error Handling & Message Acknowledgement](#error-handling--message-acknowledgement)
- [Project Structure](#project-structure)
- [Running Locally](#running-locally)
- [Testing](#testing)
- [Infra / Deployment Guide](#infra--deployment-guide)

---

## Overview

| Aspect | Detail |
|--------|--------|
| **Trigger** | Pub/Sub message from the GoLang **source** worker on the ingestion topic |
| **Input data** | Video snippets stored under the candidate's GCS prefix |
| **Output data** | Per-slot face encodings (`.npy`) + sampled frames (`.png`) in GCS |
| **Downstream** | Publishes to the **verification** egestion topic |
| **Encoders** | Pluggable: `face_recognition` (local dlib) or `fdetect` (gRPC service) |
| **Scaling knobs** | Pub/Sub flow-control + backend-aware frame-level threading |

The worker treats **profile** and each **interview** as independent "slots".
Every slot is a folder of one or more video snippets that are aggregated into a
single encoding file for that slot.

---

## Processing Pipeline

```
Pub/Sub ingestion message
        │
        ▼
 handle_message()  ──► validate schema (EncodingInputSchema)
        │
        ▼
 VideoFaceEncodingService.process()
        │
        ├─ write "encoding.json" start stage  ──► GCS .../stages/
        │
        ├─ for the profile slot + each interview slot:
        │     ├─ _list_video_blobs()      list snippet videos in the slot folder
        │     └─ _encode_video_folder()
        │            ├─ download each snippet
        │            ├─ _extract_frames()       sample + downscale frames (cv2)
        │            ├─ _encode_frames()        encode faces  ⚑ parallel or sequential
        │            ├─ _collect_unique()       order-preserving dedup
        │            ├─ _cluster_dominant()     DBSCAN → keep dominant person
        │            ├─ upload {slot}.npy       aggregated encodings
        │            └─ upload N.png frames     representative sampled frames
        │
        ├─ write "encoding.json" OK/ERROR stage
        │
        └─ publish downstream message (EncodingOutputHandler → verification topic)
```

Per-slot steps in detail:

1. **List snippets** — `_list_video_blobs()` returns all video files
   (`.mp4 .webm .avi .mov .mkv`) under the slot folder, **sorted** for
   determinism.
2. **Download + extract frames** — each snippet is downloaded, its container
   format detected from magic bytes, and frames are sampled every
   `FRAME_SAMPLE_RATE`-th frame and downscaled 0.5× for speed.
3. **Encode faces** — `_encode_frames()` runs the active encoder over the
   frames (see [Backend-Aware Frame Encoding](#backend-aware-frame-encoding-parallelism)).
4. **Deduplicate** — `_collect_unique()` folds non-duplicate encodings into the
   slot aggregate using the configured `FACE_TOLERANCE`.
5. **Cluster dominant person** — `_cluster_dominant()` runs DBSCAN and keeps the
   largest cluster, discarding bystanders/false positives. Falls back to all
   encodings when there are `< 3` faces or clustering fails.
6. **Persist** — the aggregated encodings are written as `{slot}.npy` and the
   representative frames as `1.png, 2.png, …`.

---

## GCS Bucket Layout

All paths are relative to the candidate base path
`{org_alias}/{org_id}/{candidate_email}/{candidate_uid}/`.

**Input** (written by the upstream source worker):

```
video_snippets/
├── profile/
│   ├── 0.mp4
│   └── 1.mp4
├── interview_1/
│   └── 0.webm
└── interview_2/
    └── 0.mp4
```

**Output** (written by this worker):

```
video_face_encodings/
├── profile/profile.npy
├── interview_1/interview_1.npy
└── interview_2/interview_2.npy

sampled_frames/
├── profile/        1.png, 2.png, …
├── interview_1/    1.png, …
└── interview_2/    1.png, …

stages/
└── encoding.json   {status, started, completed, event_id}
```

> The `stages/` folder is **flat** — the `event_id` lives inside the JSON, it is
> not a path segment.

---

## Message Contracts

### Input message (ingestion)

Published by the GoLang **source** worker:

```jsonc
{
  "candidate_email": "jane.doe@example.com",
  "candidate_uid":   "cand-98765",
  "org_id":          "org-42",
  "org_alias":       "acme-inc",
  "bucket_name":     "tdx-...-candidature-records",
  "event_id":        "xxxzzzqqqwww",
  "lookup_map": {
    "profile":    "profile",
    "interviews": ["interview_1", "interview_2"]
  },
  "extra_info": { /* optional, passed through untouched */ }
}
```

Validated by `EncodingInputSchema` (`ALLOWED_EXTRA_KEYS = True`).

### Output message (egestion)

Published to the **verification** topic on success:

```jsonc
{
  "candidate_email": "jane.doe@example.com",
  "candidate_uid":   "cand-98765",
  "org_id":          "org-42",
  "org_alias":       "acme-inc",
  "bucket_name":     "tdx-...-candidature-records",
  "event_id":        "xxxzzzqqqwww",
  "lookup_map":      { "profile": "profile", "interviews": ["interview_1", "interview_2"] },
  "sampled_frames": {
    "profile":    ["1.png", "2.png"],
    "interviews": { "interview_1": ["1.png"], "interview_2": ["1.png"] }
  },
  "extra_info": { /* echoed from input */ }
}
```

Validated by `EncodingOutputSchema`.

---

## Configuration

Settings are loaded from `envs/.env.<ENV_NAME>` (or `envs/.env` when `ENV_NAME`
is unset) via `pydantic-settings`. Nested keys use the `__` delimiter.

| Variable | Default | Description |
|----------|---------|-------------|
| `SERVICE_NAME` | `Face Encoding Worker` | Logger / service name |
| `DEBUG_MODE` | `false` | Enable debug logging |
| `LOG_FORMAT` | `text` | `text` or `json` |
| `LOG_LEVEL` | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL` |
| `LOG_FILE` | _(unset)_ | Optional file path; stdout/stderr otherwise |
| **Encoder** | | |
| `ENCODER_BACKEND` | `face_recognition` | `face_recognition` or `fdetect` |
| `FDETECT_CHANNEL` | _(unset)_ | gRPC `host:port` — **required** when backend is `fdetect` |
| `ENCODING_MODEL` | `hog` | `hog` (CPU) or `cnn` (GPU) — face_recognition only |
| `NUM_JITTERS` | `1` | Encoding jitters (accuracy ↔ speed) — face_recognition only |
| `FRAME_SAMPLE_RATE` | `30` | Process every Nth frame |
| `FACE_TOLERANCE` | `0.5` | Duplicate-face distance threshold (0–1] |
| **GCP** | | |
| `GCP__SA_PATH` | _(unset → ADC)_ | Path to service-account JSON; blank → ADC |
| `GCP__PROJECT_ID` | — | GCP project ID |
| `GCP__BUCKET` | — | Candidature records bucket |
| `GCP__PUBSUB_INGESTION_TOPIC` | — | Inbound topic |
| `GCP__PUBSUB_INGESTION_SUBSCRIPTION` | — | Inbound subscription |
| `GCP__PUBSUB_EGESTION_TOPIC` | — | Downstream verification topic |
| `GCP__DLQ_TOPIC` | _(unset)_ | Dead-letter topic |
| **Ingest / flow control** | | |
| `INGEST__FLOW_MAX_MESSAGES` | `10` | Max outstanding Pub/Sub messages |
| `INGEST__FLOW_MAX_LEASE_DURATION` | `120` | Max ack-lease seconds |
| `INGEST__FLOW_MAX_DURATION_PER_LEASE_EXTENSION` | `15` | Lease-extension cadence |
| `INGEST__MAX_WORKERS` | `4` | **Thread-pool size for parallel frame encoding** (see below) |

> **Emulators:** if `PUBSUB_EMULATOR_HOST` / `STORAGE_EMULATOR_HOST` are set the
> corresponding SDK talks to the emulator and skips ADC. Mixed modes (e.g.
> emulator Pub/Sub + live GCS) are supported and validated at startup.

---

## Encoder Backends

The encoder is fully pluggable behind `BaseEncoder`. `get_encoder(backend, …)`
constructs the configured implementation; the service code never branches on the
backend name.

| | `face_recognition` (dlib) | `fdetect` (gRPC) |
|---|---|---|
| **Where it runs** | In-process, local CPU/GPU | Remote `gta_ml::fdetect` service |
| **Dependency** | `face_recognition` + dlib (optional, heavy) | gRPC channel only |
| **Thread-safe?** | ❌ No — native code segfaults under threads | ✅ Yes — network I/O bound |
| **`supports_parallel`** | `False` | `True` |
| **Startup check** | — | `ping()` health probe; worker refuses to start if unreachable |
| **Resource cleanup** | no-op | `close()` shuts the gRPC channel |
| **Best for** | Self-contained / no external service | Throughput, horizontal scaling |

---

## Backend-Aware Frame Encoding (Parallelism)

Frame encoding is the hottest part of the pipeline — for every sampled frame the
encoder must detect and encode faces. This step is **conditionally parallelised
based on the active encoder's declared capability**, giving fdetect a real
throughput boost while keeping dlib safe.

### Why it is backend-aware

- **`face_recognition` (dlib)** wraps a C++ backend that is **not thread-safe**.
  Calling `encode_frame()` concurrently from multiple threads causes a
  **segmentation fault** inside dlib's native code. It must run sequentially.
- **`fdetect`** is a gRPC client. Each `encode_frame()` is a **network round-trip**
  (health ping + detect RPC) — I/O-bound and thread-safe. Running many frames
  concurrently overlaps the network latency and dramatically reduces wall-clock
  time for a video.

Rather than hard-coding `if backend == "fdetect"`, the decision is driven by a
**capability flag on the encoder itself**, so any future backend simply declares
its own thread-safety and the service does the right thing with zero changes.

### The `supports_parallel` capability flag

Declared on the abstract base and overridden per backend:

```python
# common/services/encoding/base.py
class BaseEncoder(ABC):
    #: Whether encode_frame() is safe to call concurrently from multiple
    #: threads. Defaults to False (the safe choice).
    supports_parallel: bool = False

# common/services/encoding/fdetect_encoder.py
class FdetectEncoder(BaseEncoder):
    supports_parallel: bool = True       # gRPC I/O — safe & beneficial

# common/services/encoding/face_recognition_encoder.py
class FaceRecognitionEncoder(BaseEncoder):
    supports_parallel: bool = False      # dlib native code — NOT thread-safe
```

It is a **class attribute**, so a backend can advertise its capability without
constructing a client (handy for tests and fail-fast wiring).

### Dispatch logic

`_encode_frames()` chooses the execution mode. Parallelism is engaged **only**
when the backend is thread-safe **and** more than one worker is configured:

```python
def _encode_frames(self, frames, event_id, source):
    if self.encoder.supports_parallel and self.max_workers > 1:
        return self._encode_frames_parallel(frames, event_id, source)
    return self._encode_frames_sequential(frames, event_id, source)
```

| `supports_parallel` | `max_workers` | Mode |
|:---:|:---:|---|
| `False` | any | **Sequential** (dlib-safe) |
| `True` | `1` | **Sequential** (no benefit, avoids thread overhead) |
| `True` | `> 1` | **Parallel** (thread pool) |

### Method breakdown

The refactor splits the old monolithic loop into four focused helpers — three of
them stateless and one explicitly stateful:

| Method | Role | Threaded? |
|--------|------|:---------:|
| `_encode_frames()` | Capability-based dispatcher | — |
| `_encode_frames_sequential()` | Encode frames one-by-one (dlib path) | No |
| `_encode_frames_parallel()` | Submit frames to a `ThreadPoolExecutor`, reassemble **in original order** | Yes |
| `_collect_unique()` | Fold non-duplicate encodings + their frames into the slot aggregate | **No (always single-threaded)** |

Only the **stateless encode step** is parallelised. The **deduplication**
(`_collect_unique`) compares each candidate against the encodings accepted so far
— it is inherently stateful and therefore always runs on a single thread,
regardless of backend.

The parallel implementation preserves order by indexing results back into their
original frame positions:

```python
def _encode_frames_parallel(self, frames, event_id, source):
    ordered = [None] * len(frames)
    with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
        future_to_idx = {
            executor.submit(self._process_frame, frame): idx
            for idx, frame in enumerate(frames)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                ordered[idx] = future.result()
            except Exception as e:
                logger.warning(f"[{event_id}] Frame skipped ({source}): {e}")
    return [
        (frames[idx], encodings)
        for idx, encodings in enumerate(ordered)
        if encodings is not None
    ]
```

### Determinism & ordering guarantees

- **Order-preserving:** even though threads complete out of order, results are
  re-sorted into the original frame sequence before dedup.
- **Backend-independent output:** because dedup runs sequentially over the same
  ordered input in both modes, the resulting `{slot}.npy` is **identical**
  whether produced by the parallel or sequential path. (Covered by the
  `TestParallelSequentialEquivalence` unit test.)
- **Fault isolation:** a frame whose encoding raises is logged and skipped; it
  never aborts the slot, in either mode.

### Tuning `max_workers`

`max_workers` is sourced from `INGEST__MAX_WORKERS` and reused for the frame
thread pool — no extra config knob.

- For **fdetect**, raise it to overlap more gRPC round-trips per video. Mind the
  load you place on the shared fdetect service: effective concurrency is roughly
  `pubsub_messages_in_flight × max_workers`.
- For **face_recognition**, the value is **ignored for frame encoding** (always
  sequential); leave it low.

### Sequential vs parallel at a glance

```
Sequential (dlib)                 Parallel (fdetect, max_workers=4)
─────────────────                 ─────────────────────────────────
frame0 ─ encode ─┐                frame0 ─┐
frame1 ─ encode ─┤                frame1 ─┤  all in-flight
frame2 ─ encode ─┤  one at        frame2 ─┤  concurrently
frame3 ─ encode ─┘  a time        frame3 ─┘
        │                                 │ reassemble in order
        ▼                                 ▼
   _collect_unique  ◄───────────────  _collect_unique
   (single-threaded dedup, identical result either way)
```

### History

A previous implementation used a `ThreadPoolExecutor` unconditionally. It was
removed after it caused dlib segfaults, leaving the loop fully sequential. This
version **re-introduces threading safely** by gating it on the
`supports_parallel` capability flag instead of removing it outright — dlib stays
sequential, fdetect gets the speed-up. See `TODO.md` → *"Backend-aware frame
encoding"* for the full change note.

> **Next optimization tier (not yet implemented):** folder-level parallelism —
> encode each interview/profile slot concurrently, since their `.npy` outputs are
> independent. This multiplies fdetect load (`slots × frame-workers`) and so
> warrants its own dedicated concurrency budget.

---

## Error Handling & Message Acknowledgement

`handle_message()` maps outcomes to Pub/Sub ack/nack semantics:

| Outcome | Action | Rationale |
|---------|--------|-----------|
| Success | `ack()` | Done |
| `NonRecoverableError` | `ack()` | Permanent failure — acking prevents an infinite redelivery loop (DLQ-bound) |
| `RecoverableError` | `nack()` | Transient (e.g. fdetect healthy but RPC failed) — let Pub/Sub redeliver |
| `JSONDecodeError` | `ack()` | Malformed message will never parse — drop it |
| Any other `Exception` | `ack()` | Logged `CRITICAL`; acked to avoid poison-message loops |

The service writes an `encoding.json` stage record (`status: OK | ERROR`) to GCS
around the pipeline so progress/failures are observable out-of-band.

---

## Project Structure

```
workers/face_encoding_worker/
├── main.py                         # entry point, env resolution, create_app() wiring
├── settings.py                     # pydantic-settings (Settings/GcpSettings/IngestProcessSettings)
├── services/
│   └── encoding.py                 # VideoFaceEncodingService (the pipeline + parallel helpers)
├── src/handlers/
│   ├── input_handler.py            # handle_message() — validate, run, ack/nack
│   ├── output_handler.py           # EncodingOutputHandler — downstream publish
│   └── schema/
│       ├── input_schema.py         # EncodingInputSchema
│       └── output_schema.py        # EncodingOutputSchema
├── envs/                           # .env, .env.local, …
├── docker/                         # Dockerfile
├── requirements/                   # worker requirements
├── local_testing/                  # local harness + .env.local.example
└── tests/                          # worker-scoped tests

common/services/encoding/           # pluggable encoders (shared)
├── base.py                         # BaseEncoder + supports_parallel flag
├── face_recognition_encoder.py     # dlib backend (supports_parallel = False)
├── fdetect_encoder.py              # gRPC backend (supports_parallel = True)
└── __init__.py                     # get_encoder() factory
```

---

## Running Locally

```bash
# from repo root, with the project venv active
ENV_NAME=local python3 workers/face_encoding_worker/main.py
```

`ENV_NAME` selects `envs/.env.<name>` (`local`, `dev`, `qa`, `stage`, `prod`);
unset → `envs/.env`. The worker fails fast with a clear message if the resolved
env file is missing.

For the local emulator stack (Pub/Sub emulator + fake-gcs), see
`local_testing/.env.local.example`.

---

## Testing

```bash
# unit tests for this worker
python -m pytest tests/unit/workers/face_encoding_worker/ -q

# encoder backends (capability flags, RPC patterns)
python -m pytest tests/unit/common/services/test_fdetect_encoder.py \
                 tests/unit/common/services/test_face_recognition_encoder.py -q
```

Parallelism is covered by `tests/unit/workers/face_encoding_worker/test_encoding_internals.py`:
dispatch selection, out-of-order completion → ordered results, per-frame fault
isolation, dedup correctness, and **parallel ≡ sequential equivalence**.

---

## Infra / Deployment Guide

<details>
<summary>Expand</summary>

- The **Dockerfile** is at: `/workers/face_encoding_worker/docker/Dockerfile`
- The **common codebase** is at the root `/common`; `/common_requirements` is
  required for building the Dockerfile.
- The **core codebase** is at: `/workers/face_encoding_worker`
- A **.env** file must be mounted at:
  `/app/workers/face_encoding_worker/envs/.env`
- If a **service account** is used (instead of ADC) in GKE, mount the
  `gcp.json` at: `/app/workers/face_encoding_worker/data/gcp.json`
  - **If** a service account is used → set `GCP__SA_PATH=../data/gcp.json` in the
    `.env`.
  - **Else** leave it blank → `GCP__SA_PATH=` (worker uses ADC).
- This container requires **gRPC communication** with the `gta_ml::fdetect`
  service when `ENCODER_BACKEND=fdetect`.

</details>