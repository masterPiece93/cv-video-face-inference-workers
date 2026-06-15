# 7. Configuration & Data Sources

How to control *what* gets published and *where* it goes — via config files,
fixtures, synthetic generation, GCS, and the seed-data generator.

---

## 7.1 Config File (`ECILoadTestSettings`)

The `run` and `info` commands accept `--config` (YAML or JSON). The file is
loaded into `ECILoadTestSettings`. Any key not recognized is ignored.

### All Config Keys

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `project_id` | str | `tdx-is-dev-gta-01` | GCP project ID. |
| `topic_name` | str | `face-encoding-ingestion` | Topic to publish to. |
| `subscription_name` | str | `face-encoding-ingestion-sub` | Subscription (for reference). |
| `sa_path` | str/null | `null` | Path to a service-account JSON (live GCP). |
| `emulator_host` | str/null | `localhost:8085` | Pub/Sub emulator host; set null for live. |
| `worker_name` | str | `face_encoding_worker` | Worker label / run-id prefix. |
| `max_messages` | int | `1` | Flow-control outstanding messages. |
| `encoder_backend` | str | `fdetect` | Informational default. |
| `fixtures_dir` | path | `tests/load/fixtures` | Fixture JSON directory. |
| `data_source` | str | `fixtures` | `fixtures` / `gcs` / `generator`. |
| `gcs_fixtures_bucket` | str/null | `null` | Bucket for GCS data source. |
| `gcs_fixtures_prefix` | str/null | `null` | Prefix for GCS data source. |
| `default_total_messages` | int | `10` | Used when `--messages` omitted. |
| `default_duration_seconds` | float | `60.0` | Used when `--duration` omitted. |
| `results_dir` | path | `tests/load/results` | Output directory. |

### Example YAML

```yaml
# tests/load/configs/dev.yaml
project_id: tdx-is-dev-gta-01
topic_name: face-encoding-ingestion
subscription_name: face-encoding-ingestion-sub
emulator_host: localhost:8085
worker_name: face_encoding_worker
data_source: fixtures
fixtures_dir: tests/load/fixtures
default_total_messages: 100
default_duration_seconds: 120
results_dir: tests/load/results
```

### Example JSON

```json
{
  "project_id": "tdx-is-dev-gta-01",
  "topic_name": "face-encoding-ingestion",
  "emulator_host": "localhost:8085",
  "data_source": "generator",
  "default_total_messages": 200
}
```

### CLI Overrides

CLI flags always **override** config values. Precedence (highest first):

```
CLI flag  >  config file value  >  built-in default
```

```bash
# Config says 100 messages; CLI wins with 500
python tests/load/cli.py run -c tests/load/configs/dev.yaml -n 500
```

---

## 7.2 Data Sources

Selected with `--data-source` (on `run`/`managed`) or `data_source` in config.

### `fixtures`

Reads every `*.json` file in `fixtures_dir`. Each file is either a single payload
object or a list of payloads. All are pooled together and **cycled** to satisfy
`--messages` (each message gets a fresh `event_id`).

```bash
python tests/load/cli.py run --data-source fixtures \
    --fixtures-dir tests/load/fixtures
```

Fixture file example (`tests/load/fixtures/sample_payloads.json`):
```json
[
  {
    "candidate_email": "ankita.kapoor@gmail.com",
    "candidate_uid": "cand-3f41092e",
    "org_id": "org-loadtest",
    "org_alias": "loadtest-org",
    "bucket_name": "loadtest-bucket",
    "event_id": "seed-001",
    "lookup_map": { "profile": "profile", "interviews": ["interview_1"] },
    "extra_info": { "load_test": true }
  }
]
```

### `generator` (synthetic)

Generates fully synthetic payloads matching the encoding schema — no files
needed. Great for pure publish-rate / framework-overhead testing where the GCS
data doesn't need to exist.

```bash
python tests/load/cli.py run --data-source generator -n 500
```

Synthetic payloads use emails like `loadtest-0@example.com` and random
`candidate_uid`s. (The worker will log "no video blobs found" because synthetic
data has no real GCS objects — that's expected for overhead-only tests.)

### `gcs`

Loads payload JSON files from a GCS bucket/prefix. Requires `gcs_fixtures_bucket`
and `gcs_fixtures_prefix` (in config) — or use the loader programmatically.

```yaml
data_source: gcs
gcs_fixtures_bucket: my-loadtest-bucket
gcs_fixtures_prefix: payloads/encoding/
```

---

## 7.3 The Seed-Data Generator

`generate` (and `managed --seed-dir`) turn raw videos into worker-ready data.

### What the generator does

1. **Scans** the seed source for candidate folders (one per email). The source
   may be a **local directory** (`candidate_data/`) or a **GCS URI**
   (`gs://bucket/prefix`) — see [GCS seed source](#gcs-seed-source) below.
2. Classifies videos: `profile.mp4` → profile slot; `interview_*.mp4` → interview slots.
3. Builds the GCS base path:
   ```
   {org_alias}/{org_id}/{email}/{uid}/
   ```
4. **For the encoding worker:** uploads videos under `video_snippets/{slot}/`.
5. **For verification workers:** extracts frames, encodes faces (needs encoder),
   uploads `.npy` under `video_face_encodings/{slot}/`, plus placeholder
   `sampled_frames`.
6. Writes payload fixtures to `fixtures_dir` as `seed_<target>_<n>.json`.

### Generator knobs (defaults shown)

These are set in code (`LoadTestDataGenerator`) and can be tuned if you call it
programmatically:

| Param | Default | Meaning |
|-------|---------|---------|
| `org_alias` | `loadtest-org` | Path segment. |
| `org_id` | `org-loadtest` | Path segment. |
| `bucket_name` | `loadtest-bucket` | Upload bucket (CLI `--bucket`). |
| `frame_sample_rate` | `30` | Frames sampled per second. |
| `face_tolerance` | `0.5` | Dedup distance threshold. |
| `max_frames_per_video` | `30` | Cap on frames decoded per video (keeps seeding fast on large videos). |
| `max_encodings_per_slot` | `20` | Cap on encodings collected per slot. |
| `seed_storage` | `= storage_service` | Storage client used to **read** a `gs://` seed source. |

> For verification targets the generator additionally runs **DBSCAN** clustering
> to keep only the dominant face's encodings.

<a id="gcs-seed-source"></a>
### GCS seed source (`gs://bucket/prefix`)

Both `--seed-dir` (on `managed`) and `-s/--seed-dir` (on `generate`) accept a
`gs://` URI in addition to a local path. This lets you keep the (large) raw
candidate videos in a bucket instead of on the machine running the load test.

**Expected layout** — identical to the local `candidate_data/` folder, just
under the prefix:

```
gs://my-bucket/candidate_data/
├── ankita.kapoor@gmail.com/
│   ├── profile.mp4
│   ├── interview_1.mp4
│   └── interview_2.mp4
├── naman.sharma@gmail.com/
│   └── ...
```

`--seed-dir gs://my-bucket/candidate_data` → the generator lists the candidate
folders directly under that prefix.

**How it works**

- The generator lists `*.mp4` blobs under the prefix and groups them by the
  first path segment (the candidate email folder).
- OpenCV cannot read directly from GCS, so each video is **downloaded to a
  temporary file on demand**, processed (frame extraction / upload), then the
  temp file is **deleted**. Only one slot's video is materialized at a time.
- Non-`.mp4` blobs and videos that sit directly under the prefix (with no
  candidate folder) are ignored.

**Credentials & storage systems**

- The seed source is always read from **real Cloud Storage** using Application
  Default Credentials, or a service-account JSON via `--seed-sa-path`.
- In a `managed` run the worker-ready output is written to the **fake-GCS
  emulator** — so the seed is downloaded from real GCS and re-uploaded to the
  emulator (cross-system). This is automatic.
- In `generate --storage live`, the **same** live-GCS client reads the seed and
  writes the output (one storage system).

> 💡 **Performance note:** the current implementation downloads then re-uploads
> each video. For very large datasets staying within a single live-GCS project,
> a server-side copy would be faster — tracked as a possible future enhancement.

---

## 7.4 Worker `.env` File (`--env-file`)

`managed --env-file` injects `KEY=VALUE` lines into the worker subprocess. The
parser:

- Ignores blank lines and `#` comments.
- Strips surrounding single/double quotes from values.
- Is applied **before** the framework's `LOADTEST_*` overrides.

Useful keys (consumed by `instrumented_worker.py`):

```dotenv
ENCODING_MODEL=hog        # or cnn (face_recognition only)
NUM_JITTERS=1             # face_recognition re-sampling
FRAME_SAMPLE_RATE=30      # frames/sec sampled from video
FACE_TOLERANCE=0.5        # dedup threshold
LOG_LEVEL=INFO            # worker log verbosity
```

> You can also override topic/subscription names via
> `LOADTEST_INGESTION_TOPIC`, `LOADTEST_INGESTION_SUB`, `LOADTEST_EGESTION_TOPIC`.

---

## 7.5 Docker Compose Stack (`managed`)

`tests/load/docker-compose.yml` defines:

| Service | Image | Port |
|---------|-------|------|
| `pubsub-emulator` | `google/cloud-sdk:emulators` | `8685` |
| `fake-gcs` | `fsouza/fake-gcs-server` | `5443` |

Start/stop manually if you want to reuse them across several `generate`/`run`
invocations:

```bash
docker compose -f tests/load/docker-compose.yml up -d --wait
docker compose -f tests/load/docker-compose.yml down -v
```

> `managed` starts and stops these automatically. They use different ports from
> the integration-test stack (`8085`/`4443`) so both can run simultaneously.

---

## Next Steps

- Fix common problems → [Troubleshooting](./08-troubleshooting.md)
</content>
