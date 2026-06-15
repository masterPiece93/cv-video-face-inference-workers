# 2. Architecture & Concepts

This guide explains how the load-testing framework is structured, how data flows
through it, and the key terms you'll see throughout the docs.

---

## 2.1 The Big Picture

The framework has two layers:

- **`core/`** — Generic, reusable load-testing engine that works with *any*
  Pub/Sub-based system. It knows nothing about face encoding.
- **`project/`** — ECI-specific glue: which topics/buckets to use, how to turn
  candidate videos into worker payloads, and how to launch the real workers.

```
            ┌───────────────────────── cli.py ─────────────────────────┐
            │  run  │  managed  │  generate  │  report  │ profiles/info │
            └───┬───────┬────────────┬────────────┬──────────────────────┘
                │       │            │            │
   ┌────────────┘       │            │            └─────────────┐
   ▼                    ▼            ▼                          ▼
core/publisher   project/orchestrator  project/data_generator   core/reporter
core/profiles    project/instrumented  project/data_loader      core/analyzer
core/metrics        _worker            project/config           core/types
```

---

## 2.2 The `managed` Pipeline (End-to-End)

The `managed` command is the flagship workflow. Here is exactly what happens,
step by step:

```
Step 1  Start infrastructure
        └─ docker compose up (Pub/Sub emulator :8685, fake-GCS :5443)
        └─ create bucket "loadtest-bucket"
        └─ create topic "loadtest-encoding-ingestion"
        └─ create topic "loadtest-encoding-egestion"
        └─ create subscription "loadtest-encoding-ingestion-sub"

Step 2  Seed data (if --seed-dir given)
        └─ scan candidate_data/ folders
        └─ upload videos to fake-GCS in the worker's expected layout
        └─ write matching payloads to tests/load/fixtures/

Step 3  Start instrumented worker (subprocess)
        └─ injects MetricsCollector into the worker's message callback
        └─ uses chosen encoder backend (dummy / face_recognition / fdetect)
        └─ streams worker output to --log-file

Step 4  Publish load
        └─ LoadPublisher sends N messages shaped by the chosen profile
        └─ each message carries publish_time_epoch for latency measurement

Step 5  Wait for completion
        └─ polls <run_id>.jsonl until it has N lines (or --timeout expires)

Step 6  Generate report
        └─ console (Rich) + Markdown + HTML (Plotly)

Step 7  Teardown
        └─ stop worker subprocess (SIGINT)
        └─ docker compose down -v
```

> The teardown in Step 7 runs inside a `finally` block, so even if the test
> fails midway, your infrastructure is cleaned up.

---

## 2.3 The Instrumented Worker

A load test is only meaningful if we measure the **real** worker. Rather than
re-implement the worker, the framework wraps the actual
`VideoFaceEncodingService` inside `instrumented_worker.py` and injects a
`MetricsCollector` into the message callback:

```python
def callback(message):
    publish_time = float(message.attributes["publish_time_epoch"])
    with collector.track_message(event_id, publish_time) as tracker:
        with tracker.stage("validate"):
            schema.validate(payload)
        with tracker.stage("process"):
            encoding_service.process(payload)
    message.ack()
```

This records, per message:

- **Queue wait** — time between publish and receipt
- **Per-stage timing** — `validate`, `process`
- **Processing time** — total worker time
- **End-to-end latency** — publish → ack
- **Memory (RSS)** and **success/error** status

Results are appended to `<run_id>.jsonl` (one JSON object per line).

---

## 2.4 Encoder Backends

The worker needs a face encoder. Three backends are available:

| Backend | Requires | Speed | Realism | Use when |
|---------|----------|-------|---------|----------|
| `dummy` | nothing | instant | none (random vectors) | Smoke tests, measuring framework/IO overhead, CI |
| `face_recognition` | `face_recognition` pip pkg (dlib) | slow (CPU) | high | Local realistic CPU encoding |
| `fdetect` | a running fdetect gRPC server | fast (GPU/optimized) | high | Realistic production-like encoding |

Select with `--encoder-backend`. For `fdetect`, also pass
`--fdetect-channel host:port`.

---

## 2.5 Data Flow Through the Workers

Understanding the GCS layout helps you understand what the generator produces.

### Face Encoding Worker

**Input** (uploaded by the data generator):
```
{org_alias}/{org_id}/{email}/{uid}/video_snippets/{slot}/{file}.mp4
```

**Output** (produced by the worker):
```
{org_alias}/{org_id}/{email}/{uid}/video_face_encodings/{slot}/{slot}.npy
```

The input Pub/Sub payload looks like:
```json
{
  "candidate_email": "ankita.kapoor@gmail.com",
  "candidate_uid": "cand-3f41092e",
  "org_id": "org-loadtest",
  "org_alias": "loadtest-org",
  "bucket_name": "loadtest-bucket",
  "event_id": "loadtest-ab12cd34ef56",
  "lookup_map": {
    "profile": "profile",
    "interviews": ["interview_1", "interview_2"]
  },
  "extra_info": { "load_test": true, "source": "seed_data" }
}
```

### Face / Onboarding Verification Worker

These consume **encodings** rather than raw videos, so the generator first
*encodes* the seed videos into `.npy` files:
```
{org_alias}/{org_id}/{email}/{uid}/video_face_encodings/{slot}/{slot}.npy
```
and includes a `sampled_frames` map in the payload. Because encoding is
required, generating verification data needs a real encoder
(`face_recognition` or `fdetect`).

---

## 2.6 Key Terms (Glossary)

| Term | Meaning |
|------|---------|
| **Profile** | The *shape* of load over time (ramp/spike/soak/stress). |
| **Run ID** | Unique identifier `{worker}_{profile}_{epoch}` used to name all output files. |
| **Payload** | The JSON body of a single Pub/Sub message sent to the worker. |
| **Fixture** | A JSON file of pre-built payloads in `tests/load/fixtures/`. |
| **Data source** | Where payloads come from: `fixtures`, `gcs`, or `generator` (synthetic). |
| **Seed data** | Raw candidate videos in `candidate_data/`, turned into worker data. |
| **Ingestion topic** | Topic the worker subscribes to (`loadtest-encoding-ingestion`). |
| **Egestion topic** | Topic the worker publishes results to (`loadtest-encoding-egestion`). |
| **Queue wait** | Time a message spends between publish and worker receipt. |
| **End-to-end latency** | Time from publish to ack (the user-facing latency). |
| **Throughput** | Successful messages processed per minute. |

---

## 2.7 Ports & Names Used by `managed`

| Resource | Value |
|----------|-------|
| Pub/Sub emulator | `localhost:8685` |
| fake-GCS | `http://localhost:5443` |
| Project ID | `loadtest-project` |
| Bucket | `loadtest-bucket` |
| Ingestion topic | `loadtest-encoding-ingestion` |
| Ingestion subscription | `loadtest-encoding-ingestion-sub` |
| Egestion topic | `loadtest-encoding-egestion` |

> These ports are intentionally different from the **integration test** stack
> (`8085` / `4443`) so the two can run side-by-side without conflict.

---

## Next Steps

- Learn every CLI option → [CLI Reference](./03-cli-reference.md)
- Understand load shapes → [Load Profiles](./04-load-profiles.md)
</content>
