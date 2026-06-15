# 5. Scenarios & Recipes

A large catalogue of practical, copy-paste examples covering many combinations
of commands and options. Grouped by intent.

> Paths assume you run from the repository root (`gta-ai-eci-workers/`).
> `candidate_data` is the bundled seed directory at the repo's parent or root —
> adjust the path to wherever yours lives.

---

## Table of Contents

- [A. Quick Smoke Tests](#a-quick-smoke-tests)
- [B. Realistic Tests with Seed Data](#b-realistic-tests-with-seed-data)
- [C. Using the fdetect Encoder](#c-using-the-fdetect-encoder)
- [D. Profile-Focused Scenarios](#d-profile-focused-scenarios)
- [E. Watching Worker Logs Live](#e-watching-worker-logs-live)
- [F. Passing a Worker .env File](#f-passing-a-worker-env-file)
- [G. Publish-Only (run) Against Existing Infra](#g-publish-only-run-against-existing-infra)
- [H. Generating Data (generate)](#h-generating-data-generate)
- [I. Verification Worker Scenarios](#i-verification-worker-scenarios)
- [J. Reporting Scenarios](#j-reporting-scenarios)
- [K. Config-File Driven Runs](#k-config-file-driven-runs)
- [L. CI / Automation Recipes](#l-ci--automation-recipes)
- [M. Capacity Planning Workflow](#m-capacity-planning-workflow)

---

## A. Quick Smoke Tests

**A1 — Fastest possible sanity check** (dummy encoder, synthetic payloads):
```bash
python tests/load/cli.py managed -n 5 -d 20
```

**A2 — Smoke test the encoding worker end-to-end with seed data:**
```bash
python tests/load/cli.py managed -n 4 -d 20 \
    -w face_encoding_worker --seed-dir candidate_data
```

**A3 — Verify the CLI and profiles without running anything:**
```bash
python tests/load/cli.py profiles
python tests/load/cli.py info
```

---

## B. Realistic Tests with Seed Data

**B1 — One message per candidate (4 total), gradual ramp:**
```bash
python tests/load/cli.py managed -p ramp -n 4 -d 30 \
    -w face_encoding_worker --seed-dir candidate_data
```

**B2 — Each candidate processed 3× (cycled payloads):**
```bash
python tests/load/cli.py managed -p ramp -n 12 -d 60 \
    -w face_encoding_worker --seed-dir candidate_data
```

**B3 — Larger run with explicit output folder:**
```bash
python tests/load/cli.py managed -p soak -n 40 -d 120 \
    -w face_encoding_worker --seed-dir candidate_data \
    -o tests/load/results/soak_run
```

**B4 — Seed videos hosted in a GCS bucket** (instead of a local folder):
```bash
# --seed-dir accepts a gs://bucket/prefix URI with the same
# <candidate_email>/<video>.mp4 layout as the local candidate_data/ folder.
python tests/load/cli.py managed -p ramp -n 8 -d 60 \
    -w face_encoding_worker \
    --seed-dir gs://my-bucket/candidate_data \
    --encoder-backend fdetect --fdetect-channel localhost:7777
```

**B5 — GCS seed with an explicit service account** (instead of ADC):
```bash
python tests/load/cli.py managed -p ramp -n 8 -d 60 \
    --seed-dir gs://my-bucket/candidate_data \
    --seed-sa-path ~/keys/loadtest-reader.json \
    --encoder-backend fdetect --fdetect-channel localhost:7777
```

---

## C. Using the fdetect Encoder

**C1 — fdetect at localhost:7777, small ramp:**
```bash
python tests/load/cli.py managed -p ramp -n 4 -d 30 \
    -w face_encoding_worker --seed-dir candidate_data \
    --encoder-backend fdetect --fdetect-channel localhost:7777
```

**C2 — fdetect with longer timeout for many messages:**
```bash
python tests/load/cli.py managed -p ramp -n 50 -d 120 \
    --seed-dir candidate_data \
    --encoder-backend fdetect --fdetect-channel localhost:7777 \
    --timeout 600
```

**C3 — fdetect on a non-default host/port:**
```bash
python tests/load/cli.py managed -n 8 -d 40 \
    --seed-dir candidate_data \
    --encoder-backend fdetect --fdetect-channel 10.0.0.12:50051
```

---

## D. Profile-Focused Scenarios

**D1 — Ramp (gradual build-up):**
```bash
python tests/load/cli.py managed -p ramp -n 60 -d 120 \
    --seed-dir candidate_data --encoder-backend fdetect --fdetect-channel localhost:7777
```

**D2 — Spike (80% burst up front):**
```bash
python tests/load/cli.py managed -p spike -n 200 -d 60 \
    --seed-dir candidate_data --encoder-backend fdetect --fdetect-channel localhost:7777
```

**D3 — Soak (steady, long):**
```bash
python tests/load/cli.py managed -p soak -n 600 -d 1800 --timeout 2400 \
    --seed-dir candidate_data --encoder-backend fdetect --fdetect-channel localhost:7777
```

**D4 — Stress (escalating waves to find the ceiling):**
```bash
python tests/load/cli.py managed -p stress -n 250 -d 100 --timeout 600 \
    --seed-dir candidate_data --encoder-backend fdetect --fdetect-channel localhost:7777
```

---

## E. Watching Worker Logs Live

**E1 — Stream the worker log to a known file, then tail it:**
```bash
# Terminal 1
python tests/load/cli.py managed -p ramp -n 8 -d 60 \
    --seed-dir candidate_data \
    --encoder-backend fdetect --fdetect-channel localhost:7777 \
    --log-file tests/load/results/worker.log

# Terminal 2
tail -f tests/load/results/worker.log
```

**E2 — Default log location (auto-named) when `--log-file` is omitted:**
```bash
python tests/load/cli.py managed -n 4 --seed-dir candidate_data
# → log appears at tests/load/results/<run_id>_worker.log
```

---

## F. Passing a Worker .env File

The `--env-file` injects every `KEY=VALUE` into the worker subprocess. Framework
`LOADTEST_*` values still take precedence, so use the env file to tune encoder
and processing knobs.

**F1 — Use the worker's existing .env:**
```bash
python tests/load/cli.py managed -n 4 --seed-dir candidate_data \
    --encoder-backend face_recognition \
    --env-file workers/face_encoding_worker/.env
```

**F2 — A purpose-built load-test .env** (`tests/load/worker.env`):
```dotenv
# tests/load/worker.env
ENCODING_MODEL=cnn
NUM_JITTERS=2
FRAME_SAMPLE_RATE=15
FACE_TOLERANCE=0.45
LOG_LEVEL=DEBUG
```
```bash
python tests/load/cli.py managed -n 4 --seed-dir candidate_data \
    --encoder-backend face_recognition \
    --env-file tests/load/worker.env \
    --log-file tests/load/results/worker.log
```

---

## G. Publish-Only (run) Against Existing Infra

Use `run` when a worker + topic are already up (you manage them yourself).

**G1 — Publish a spike to a local emulator:**
```bash
python tests/load/cli.py run -p spike -n 100 -d 30 \
    --emulator localhost:8085 -t face-encoding-ingestion
```

**G2 — Publish to live GCP dev project (no emulator):**
```bash
python tests/load/cli.py run -p ramp -n 200 -d 300 \
    --project-id tdx-is-dev-gta-01 \
    -t face-encoding-ingestion \
    --data-source fixtures \
    --fixtures-dir tests/load/fixtures
```

**G3 — Synthetic payloads (no fixtures needed):**
```bash
python tests/load/cli.py run -p soak -n 500 -d 600 \
    --emulator localhost:8085 \
    --data-source generator
```

**G4 — Then build the report after the worker drains the queue:**
```bash
python tests/load/cli.py report tests/load/results/<run_id>.jsonl
```

---

## H. Generating Data (generate)

**H1 — Fixtures only (no upload), encoding worker:**
```bash
python tests/load/cli.py generate -s candidate_data -t face_encoding_worker
```

**H2 — Upload encoding-worker snippets to a running fake-GCS:**
```bash
# Make sure the emulator is up first:
docker compose -f tests/load/docker-compose.yml up -d --wait

python tests/load/cli.py generate -s candidate_data \
    -t face_encoding_worker --storage emulator
```

**H3 — Upload to LIVE GCS in a real (non-prod) bucket:**
```bash
python tests/load/cli.py generate -s candidate_data \
    -t face_encoding_worker \
    --storage live --bucket tdx-dev-external-sheet-candidature-records
```

**H4 — Custom fixtures output directory:**
```bash
python tests/load/cli.py generate -s candidate_data \
    -t face_encoding_worker \
    --fixtures-dir tests/load/fixtures/encoding_seed
```

**H5 — Read raw seed videos FROM a GCS bucket, upload to live GCS:**
```bash
# Source and destination are both live GCS (one storage system).
python tests/load/cli.py generate \
    -s gs://my-bucket/candidate_data \
    -t face_encoding_worker \
    --storage live --bucket my-loadtest-bucket
```

**H6 — Read seed videos from GCS, write fixtures only (no upload):**
```bash
python tests/load/cli.py generate \
    -s gs://my-bucket/candidate_data \
    -t face_encoding_worker --storage none
```

---

## I. Verification Worker Scenarios

Verification workers consume **encodings** (`.npy`), so the generator must encode
the videos first — which requires a real encoder.

**I1 — Generate verification data with fdetect, upload to emulator:**
```bash
docker compose -f tests/load/docker-compose.yml up -d --wait

python tests/load/cli.py generate -s candidate_data \
    -t face_verification_worker \
    --storage emulator \
    --encoder-backend fdetect --fdetect-channel localhost:7777
```

**I2 — Generate verification data with face_recognition (CPU):**
```bash
python tests/load/cli.py generate -s candidate_data \
    -t face_verification_worker \
    --storage emulator \
    --encoder-backend face_recognition
```

**I3 — Onboarding verification target:**
```bash
python tests/load/cli.py generate -s candidate_data \
    -t onboarding_verification_worker \
    --storage emulator \
    --encoder-backend fdetect --fdetect-channel localhost:7777
```

> The `managed` command also supports `-w face_verification_worker`; when
> `--seed-dir` is provided it will encode seed videos automatically during Step 2.

---

## J. Reporting Scenarios

**J1 — All formats from a results file:**
```bash
python tests/load/cli.py report tests/load/results/run.jsonl
```

**J2 — Only HTML, into a reports folder:**
```bash
python tests/load/cli.py report tests/load/results/run.jsonl -f html -o reports/
```

**J3 — Only the console table (quick glance):**
```bash
python tests/load/cli.py report tests/load/results/run.jsonl -f console
```

**J4 — Re-label the worker in the report header:**
```bash
python tests/load/cli.py report tests/load/results/run.jsonl -w face_verification_worker
```

---

## K. Config-File Driven Runs

Create a YAML config to avoid long command lines (see
[Configuration](./07-configuration-and-data.md) for all keys).

`tests/load/configs/dev.yaml`:
```yaml
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

**K1 — Run with the config:**
```bash
python tests/load/cli.py run -c tests/load/configs/dev.yaml
```

**K2 — Config but override messages and profile:**
```bash
python tests/load/cli.py run -c tests/load/configs/dev.yaml -p spike -n 500
```

**K3 — Inspect what a config resolves to:**
```bash
python tests/load/cli.py info -c tests/load/configs/dev.yaml
```

---

## L. CI / Automation Recipes

**L1 — Headless smoke test for CI** (dummy encoder, fast, deterministic-ish):
```bash
python tests/load/cli.py managed -p ramp -n 6 -d 15 --timeout 120 \
    -o "$CI_ARTIFACTS/loadtest" \
    --log-file "$CI_ARTIFACTS/loadtest/worker.log"
```

**L2 — Fail the build if any message errored** (parse the summary JSON):
```bash
RUN_DIR=tests/load/results
python tests/load/cli.py managed -n 6 -d 15 -o "$RUN_DIR"
SUMMARY=$(ls -t "$RUN_DIR"/*_summary.json | head -1)
FAILED=$(python -c "import json,sys; print(json.load(open('$SUMMARY'))['failed'])")
test "$FAILED" -eq 0 || { echo "Load test had $FAILED failures"; exit 1; }
```

**L3 — Nightly soak test:**
```bash
python tests/load/cli.py managed -p soak -n 1000 -d 3600 --timeout 4200 \
    --seed-dir candidate_data \
    --encoder-backend fdetect --fdetect-channel fdetect.internal:50051 \
    -o /var/loadtest/$(date +%F)
```

---

## M. Capacity Planning Workflow

A complete workflow to find your worker's ceiling:

```bash
# 1. Warm-up baseline (soak at low rate)
python tests/load/cli.py managed -p soak -n 60 -d 60 \
    --seed-dir candidate_data \
    --encoder-backend fdetect --fdetect-channel localhost:7777 \
    -o results/baseline

# 2. Stress to find the knee
python tests/load/cli.py managed -p stress -n 300 -d 100 --timeout 600 \
    --seed-dir candidate_data \
    --encoder-backend fdetect --fdetect-channel localhost:7777 \
    -o results/stress

# 3. Spike to test burst tolerance at the discovered rate
python tests/load/cli.py managed -p spike -n 200 -d 30 --timeout 600 \
    --seed-dir candidate_data \
    --encoder-backend fdetect --fdetect-channel localhost:7777 \
    -o results/spike

# 4. Compare the HTML reports side by side
xdg-open results/baseline/*_report.html
xdg-open results/stress/*_report.html
xdg-open results/spike/*_report.html
```

What to look at across the three runs (see [Metrics & Reports](./06-metrics-and-reports.md)):
- Where does **p95 latency** start climbing? → that's your safe operating rate.
- Does **error rate** go above 0% under stress/spike? → saturation point.
- Does **memory** keep growing during soak? → possible leak.

---

## Next Steps

- Interpret the numbers → [Metrics & Reports](./06-metrics-and-reports.md)
- Tune config and data → [Configuration & Data Sources](./07-configuration-and-data.md)
</content>
