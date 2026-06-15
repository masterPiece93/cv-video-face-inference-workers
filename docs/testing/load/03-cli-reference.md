# 3. CLI Reference

Complete reference for every command and **every option**, including types,
defaults, and how options interact.

All commands are invoked as:

```bash
python tests/load/cli.py <command> [options]
```

| Command | Purpose | Needs Docker? |
|---------|---------|---------------|
| [`managed`](#31-managed) | Full end-to-end test (infra + worker + publish + report + teardown) | ✅ Yes |
| [`run`](#32-run) | Publish-only load against an existing worker/topic | ❌ No |
| [`generate`](#33-generate) | Turn raw seed videos into worker-ready data/fixtures | Only with `--storage emulator` |
| [`report`](#34-report) | Build console/Markdown/HTML reports from a `.jsonl` results file | ❌ No |
| [`profiles`](#35-profiles) | List the available load profiles | ❌ No |
| [`info`](#36-info) | Show the current/effective configuration | ❌ No |

---

## 3.1 `managed`

Runs a fully self-contained load test. This is the command you'll use most.

```bash
python tests/load/cli.py managed [options]
```

### Options

| Option | Short | Type | Default | Description |
|--------|-------|------|---------|-------------|
| `--profile` | `-p` | choice: `ramp`, `spike`, `soak`, `stress` | `ramp` | The load shape over time. See [Load Profiles](./04-load-profiles.md). |
| `--messages` | `-n` | int | `10` | Total number of messages to publish. |
| `--duration` | `-d` | float | `60.0` | Target test duration in seconds (how the profile spreads messages). |
| `--worker` | `-w` | str | `face_encoding_worker` | Which worker to run and label in the report. |
| `--data-source` | | choice: `fixtures`, `gcs`, `generator` | `generator` | Where publish payloads come from. **Overridden to `fixtures` automatically when `--seed-dir` is used.** |
| `--seed-dir` | | local path **or** `gs://bucket/prefix` | `None` | Raw candidate videos — a local directory or a GCS URI. Triggers Step 2 (seed + upload to fake-GCS). |
| `--seed-sa-path` | | path (must exist) | `None` | Service-account JSON used to read a `gs://` seed source. Defaults to Application Default Credentials (ADC). |
| `--timeout` | | int | `300` | Max seconds to wait for the worker to process all messages. |
| `--output-dir` | `-o` | path | `tests/load/results` | Where all output files are written. |
| `--encoder-backend` | | choice: `dummy`, `face_recognition`, `fdetect` | `dummy` | Face encoder the worker uses. |
| `--fdetect-channel` | | str | `""` | gRPC address for fdetect, e.g. `localhost:7777`. Required when `--encoder-backend fdetect`. |
| `--env-file` | | path (must exist) | `None` | A worker `.env` file. Every `KEY=VALUE` line is injected into the worker subprocess environment. |
| `--log-file` | | path | `None` → `<output-dir>/<run_id>_worker.log` | File to stream the worker's stdout/stderr to. |

### Option Interactions & Notes

- **`--seed-dir` forces `--data-source fixtures`.** When you seed, the generator
  writes payloads into `tests/load/fixtures/` and the publisher reads them back.
  This guarantees the published payloads point at the data that was actually
  uploaded.
- **`--seed-dir` accepts a `gs://bucket/prefix` URI.** The seed videos can live
  in a GCS bucket using the same `…/<candidate_email>/<video>.mp4` layout as the
  local `candidate_data/` folder. Each video is downloaded to a temp file on
  demand (OpenCV can't stream from GCS), processed, and the worker-ready output
  is uploaded to the fake-GCS emulator. The GCS source is read from **real**
  Cloud Storage using ADC (or `--seed-sa-path`), even though the rest of the run
  targets the emulator. See [Configuration & Data](./07-configuration-and-data.md#gcs-seed-source).
- **`--encoder-backend fdetect` requires `--fdetect-channel`.** Without a channel
  it silently falls back to `face_recognition` (which needs the `face_recognition`
  pip package / dlib).
- **`--messages` > number of seed candidates** → payloads are cycled (reused with
  new `event_id`s). With the bundled 4 candidates, `-n 12` reuses each candidate 3×.
- **`--timeout`** should scale with `--messages` × per-message processing time.
  fdetect is fast; `face_recognition` (CPU) can be seconds per video.
- **`--env-file` precedence:** values from the env file are applied first, then
  the framework's own `LOADTEST_*` variables override them. So you can set things
  like `ENCODING_MODEL`, `NUM_JITTERS`, `FRAME_SAMPLE_RATE`, `FACE_TOLERANCE`,
  `LOG_LEVEL`, etc. in the env file.

### Environment variables the worker honors (via `--env-file` or shell)

| Variable | Default | Meaning |
|----------|---------|---------|
| `LOADTEST_ENCODER_BACKEND` | `dummy` (set by orchestrator) | `dummy` / `face_recognition` / `fdetect` |
| `FDETECT_CHANNEL` | `""` | gRPC address for fdetect |
| `ENCODING_MODEL` | `hog` | `hog` (fast) or `cnn` (accurate) for face_recognition |
| `NUM_JITTERS` | `1` | Re-sampling passes per face for face_recognition |
| `FRAME_SAMPLE_RATE` | `30` | Frames sampled per second of video |
| `FACE_TOLERANCE` | `0.5` | Dedup distance threshold |
| `LOADTEST_INGESTION_TOPIC` | `loadtest-encoding-ingestion` | Override ingestion topic |
| `LOADTEST_INGESTION_SUB` | `loadtest-encoding-ingestion-sub` | Override subscription |
| `LOADTEST_EGESTION_TOPIC` | `loadtest-encoding-egestion` | Override egestion topic |

### Examples

```bash
# Minimal smoke test (dummy encoder, synthetic payloads, no seeding)
python tests/load/cli.py managed -n 5 -d 20

# Realistic test with seed videos + fdetect, watch the log live
python tests/load/cli.py managed \
    -p ramp -n 8 -d 60 \
    -w face_encoding_worker \
    --seed-dir candidate_data \
    --encoder-backend fdetect --fdetect-channel localhost:7777 \
    --log-file tests/load/results/worker.log

# Pass a worker .env file to tune encoding params
python tests/load/cli.py managed \
    -n 4 --seed-dir candidate_data \
    --encoder-backend face_recognition \
    --env-file workers/face_encoding_worker/.env

# Seed videos hosted in a GCS bucket (gs:// URI) instead of a local folder
python tests/load/cli.py managed \
    -p ramp -n 8 -d 60 \
    --seed-dir gs://my-bucket/candidate_data \
    --encoder-backend fdetect --fdetect-channel localhost:7777 \
    --log-file tests/load/results/worker.log
```

---

## 3.2 `run`

Publishes load to an **existing** topic. Does **not** start any infrastructure
or worker — you must already have a worker subscribing and instrumented with
`MetricsCollector`. Use this for shared emulators, dev projects, or when you
manage the worker yourself.

```bash
python tests/load/cli.py run [options]
```

### Options

| Option | Short | Type | Default | Description |
|--------|-------|------|---------|-------------|
| `--profile` | `-p` | choice: `ramp`, `spike`, `soak`, `stress` | `ramp` | Load shape. |
| `--messages` | `-n` | int | `None` → `default_total_messages` (10) | Total messages to publish. |
| `--duration` | `-d` | float | `None` → `default_duration_seconds` (60) | Target duration in seconds. |
| `--topic` | `-t` | str | `None` → config value | Pub/Sub topic to publish to. |
| `--project-id` | | str | `None` → config value | GCP project ID. |
| `--emulator` | | str | `None` → config value (`localhost:8085`) | Pub/Sub emulator host. Sets `PUBSUB_EMULATOR_HOST`. |
| `--data-source` | | choice: `fixtures`, `gcs`, `generator` | `None` → config (`fixtures`) | Payload source. |
| `--fixtures-dir` | | path | `None` → config (`tests/load/fixtures`) | Directory of fixture JSON files. |
| `--config` | `-c` | path (must exist) | `None` | YAML or JSON config file (see [Configuration](./07-configuration-and-data.md)). |
| `--output-dir` | `-o` | path | `None` → config (`tests/load/results`) | Output directory. |
| `--worker` | `-w` | str | `None` → config (`face_encoding_worker`) | Worker name used for labeling + run ID. |

### Behavior

- Builds a `run_id` of `{worker}_{profile}_{epoch}`.
- Writes a **publish manifest** to `<output>/<run_id>_manifest.json` containing
  every published message id and publish time.
- Does **not** generate a report (the worker writes the `.jsonl`; run `report`
  afterwards).
- If `--emulator` (or config `emulator_host`) is set, it exports
  `PUBSUB_EMULATOR_HOST` so the publisher targets the emulator instead of live GCP.

### Examples

```bash
# Publish 100 messages in a spike against a local emulator
python tests/load/cli.py run -p spike -n 100 -d 30 \
    --emulator localhost:8085 -t face-encoding-ingestion

# Use a YAML config and override just the message count
python tests/load/cli.py run -c tests/load/configs/dev.yaml -n 500

# Publish to live GCP (no emulator) using fixtures
python tests/load/cli.py run -p soak -n 200 -d 600 \
    --project-id tdx-is-dev-gta-01 \
    -t face-encoding-ingestion \
    --data-source fixtures
```

---

## 3.3 `generate`

Transforms raw seed videos in `candidate_data/` into the exact format a worker
expects, and writes matching payload fixtures. Optionally uploads to fake-GCS or
live GCS.

```bash
python tests/load/cli.py generate [options]
```

### Options

| Option | Short | Type | Default | Description |
|--------|-------|------|---------|-------------|
| `--seed-dir` | `-s` | local path **or** `gs://bucket/prefix` | **required** | Candidate data with raw videos — a local directory or a GCS URI. |
| `--target` | `-t` | choice: `face_encoding_worker`, `face_verification_worker`, `onboarding_verification_worker` | `face_encoding_worker` | Which worker to shape the data for. |
| `--bucket` | | str | `loadtest-bucket` | GCS bucket name to upload into. |
| `--storage` | | choice: `emulator`, `live`, `none` | `none` | Upload target. `none` = write fixtures only, no upload. |
| `--emulator-host` | | str | `http://localhost:5443` | fake-GCS host (used when `--storage emulator`). |
| `--fixtures-dir` | | path | `tests/load/fixtures` | Where to save the generated payload JSON. |
| `--encoder-backend` | | choice: `face_recognition`, `fdetect` | `face_recognition` | Encoder used **only** for verification targets (to produce `.npy`). |
| `--fdetect-channel` | | str | `""` | fdetect gRPC address (when encoder backend is fdetect). |
| `--seed-sa-path` | | path (must exist) | `None` | Service-account JSON used to read a `gs://` seed source. Defaults to ADC. |

### Seed source: local directory or GCS

`--seed-dir` accepts either a local path or a `gs://bucket/prefix` URI. With a
GCS URI the generator lists candidate folders under the prefix, downloads each
`*.mp4` to a temp file on demand (OpenCV can't read from GCS directly), processes
it, and cleans the temp file up afterward.

- With `--storage live`, the same live-GCS client reads the seed **and** writes
  the worker-ready output (one storage system).
- With `--storage emulator` / `none`, the seed is read from **real** Cloud
  Storage (ADC or `--seed-sa-path`) while output goes to the emulator / fixtures.

### Behavior by target

- **`face_encoding_worker`** — uploads each candidate's videos as snippets under
  `video_snippets/{slot}/`. No encoder needed.
- **`face_verification_worker` / `onboarding_verification_worker`** — *encodes*
  the videos into `.npy` files under `video_face_encodings/{slot}/` and uploads
  placeholder sampled frames. **Requires an encoder** (`face_recognition` or
  `fdetect`).

### Examples

```bash
# Fixtures only (no upload) for the encoding worker
python tests/load/cli.py generate -s candidate_data -t face_encoding_worker

# Upload encoding-worker data to a running fake-GCS emulator
python tests/load/cli.py generate -s candidate_data \
    -t face_encoding_worker --storage emulator

# Generate verification data (encodes videos) using fdetect
python tests/load/cli.py generate -s candidate_data \
    -t face_verification_worker \
    --storage emulator \
    --encoder-backend fdetect --fdetect-channel localhost:7777

# Upload encoding data to LIVE GCS in a real bucket
python tests/load/cli.py generate -s candidate_data \
    -t face_encoding_worker \
    --storage live --bucket tdx-dev-external-sheet-candidature-records

# Read raw seed videos FROM a GCS bucket and upload worker-ready data to live GCS
python tests/load/cli.py generate -s gs://my-bucket/candidate_data \
    -t face_encoding_worker \
    --storage live --bucket my-loadtest-bucket

# Read seed videos from GCS but only write fixtures locally (no upload)
python tests/load/cli.py generate -s gs://my-bucket/candidate_data \
    -t face_encoding_worker --storage none
```

> ⚠️ `--storage live` writes to real GCS using your ambient credentials (ADC).
> Use a non-production bucket.

---

## 3.4 `report`

Generates reports from a previously collected `.jsonl` results file. Useful when
you ran `run` separately, or want to re-render an old run.

```bash
python tests/load/cli.py report <RESULTS_FILE> [options]
```

### Argument

| Argument | Type | Description |
|----------|------|-------------|
| `RESULTS_FILE` | path (must exist) | The `.jsonl` file produced by a load test run. |

### Options

| Option | Short | Type | Default | Description |
|--------|-------|------|---------|-------------|
| `--format` | `-f` | choice: `console`, `markdown`, `html`, `all` | `all` | Which report formats to produce. |
| `--output-dir` | `-o` | path | `None` → same dir as the results file | Where to write Markdown/HTML. |
| `--worker` | `-w` | str | `face_encoding_worker` | Worker label for the report header. |

### Examples

```bash
# All formats (console + md + html)
python tests/load/cli.py report tests/load/results/face_encoding_worker_ramp_1780573580.jsonl

# Only the interactive HTML, into a custom folder
python tests/load/cli.py report results/run.jsonl -f html -o reports/

# Just print the console table
python tests/load/cli.py report results/run.jsonl -f console
```

---

## 3.5 `profiles`

Prints a table describing the four load profiles and their typical use cases.
No options.

```bash
python tests/load/cli.py profiles
```

---

## 3.6 `info`

Prints the current effective configuration (defaults, or a config file if you
pass one). No live infrastructure is touched.

```bash
python tests/load/cli.py info [--config <file>]
```

| Option | Short | Type | Default | Description |
|--------|-------|------|---------|-------------|
| `--config` | `-c` | path (must exist) | `None` | YAML/JSON config to display instead of defaults. |

```bash
python tests/load/cli.py info
python tests/load/cli.py info -c tests/load/configs/prod.yaml
```

---

## 3.7 Global Help & Version

```bash
python tests/load/cli.py --help            # list commands
python tests/load/cli.py <command> --help  # help for one command
python tests/load/cli.py --version         # prints "eci-loadtest, version 1.0.0"
```

---

## Next Steps

- Understand each profile's math → [Load Profiles](./04-load-profiles.md)
- Apply it to real situations → [Scenarios & Recipes](./05-scenarios.md)
</content>
