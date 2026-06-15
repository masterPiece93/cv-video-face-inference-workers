# 8. Troubleshooting

Common errors and how to fix them. Each entry shows the symptom, the cause, and
the fix.

---

## 8.1 `ModuleNotFoundError: No module named 'face_recognition'`

**Symptom:** the instrumented worker exits immediately when using
`--encoder-backend face_recognition`.

**Cause:** the `face_recognition` (dlib) package isn't installed.

**Fix:** either
- Use `--encoder-backend dummy` (no ML model needed), or
- Use `--encoder-backend fdetect --fdetect-channel host:port` with a running
  fdetect service, or
- Install the package: `pip install face_recognition` (needs CMake/dlib build
  tools).

---

## 8.2 `404 Topic not found` when publishing

**Symptom:** every publish fails with `404 Topic not found`.

**Cause:** the topic/subscription weren't created on the emulator. With
`managed`, provisioning happens in Step 1 (`start_infra`). This error means infra
provisioning didn't run (e.g. you used `run` against an emulator that has no
topic).

**Fix:**
- With `managed`: ensure Step 1 printed "Pub/Sub emulator + fake-GCS ready".
- With `run`: create the topic/subscription yourself first, or point `--topic`
  at an existing one.

---

## 8.3 `404 Not Found` when uploading to fake-GCS

**Symptom:** `Failed to upload ... 404 POST .../upload/storage/v1/b/<bucket>/o`.

**Cause:** the target bucket doesn't exist in fake-GCS.

**Fix:** `managed` creates `loadtest-bucket` during Step 1. If you're running
`generate --storage emulator` standalone, create the bucket first:

```python
import os
from google.cloud import storage
os.environ["STORAGE_EMULATOR_HOST"] = "http://localhost:5443"
storage.Client(project="loadtest-project", credentials=None).create_bucket("loadtest-bucket")
```

Or just run `managed` once (it provisions everything).

---

## 8.4 `AttributeError: 'PublisherClient' object has no attribute 'close'`

**Symptom:** crash at the end of publishing.

**Cause:** older Pub/Sub client without a `close()` method.

**Status:** already handled — `LoadPublisher.close()` swallows this
`AttributeError`. If you see it, make sure you're on the current `publisher.py`.

---

## 8.5 Worker processed 0/N messages (timeout)

**Symptom:** Step 5 reports `Got 0/N after <timeout>s`.

**Likely causes & fixes:**
1. **Worker crashed on startup** — check the `--log-file`. Common cause is a
   missing encoder (see 8.1). 
2. **Encoder too slow for the timeout** — `face_recognition` on CPU can be
   seconds per video. Increase `--timeout`, or switch to `fdetect`/`dummy`.
3. **Subscription mismatch** — the worker subscribes to
   `loadtest-encoding-ingestion-sub`; the publisher targets
   `loadtest-encoding-ingestion`. Don't override one without the other.

Always inspect the worker log:
```bash
tail -n 100 tests/load/results/<run_id>_worker.log
```

---

## 8.6 "No video blobs found under ..." warnings

**Symptom:** worker logs many `No video blobs found` warnings, but the run still
"succeeds".

**Cause:** the payload points at a GCS path that has no uploaded videos. This is
**expected** when:
- Using `--data-source generator` (synthetic payloads with no real GCS data), or
- Publishing more messages than candidates so cycled payloads reuse UIDs that
  don't match uploaded data, or
- Seeding and publishing used different `candidate_uid`s.

**Impact:** the worker still acks and records metrics, so framework/IO timing is
valid — but no real encoding happens for those messages.

**Fix (for fully realistic runs):** use `managed --seed-dir ...` so seeding and
publishing share the same fixtures, and keep `--messages` ≤ number of candidates
(or accept cycling).

---

## 8.7 Docker / Compose issues

**Symptom:** Step 1 hangs or errors.

**Checks:**
```bash
docker ps                                   # is Docker running?
docker compose -f tests/load/docker-compose.yml ps
docker compose -f tests/load/docker-compose.yml logs
```

**Port conflicts:** the load stack uses `8685`/`5443`. If something else holds
those ports, stop it or edit `docker-compose.yml` (and the matching ports in
`orchestrator.py`).

**Manual cleanup:**
```bash
docker compose -f tests/load/docker-compose.yml down -v
```

---

## 8.8 `RuntimeWarning: Unexpected value in sys.prefix`

**Symptom:** noisy warnings about `sys.prefix` / `sys.exec_prefix` when invoking
Python via a relative venv path like `../.venv/bin/python`.

**Cause:** cosmetic — the venv is referenced through a path containing `..`.

**Fix:** harmless; ignore it, or invoke Python via an absolute path / activated
environment.

---

## 8.9 Report shows `Environment: Live GCP` unexpectedly

**Symptom:** you expected emulator but the report says Live GCP.

**Cause:** `emulator_host` wasn't set, so the publisher targeted real GCP.

**Fix:** pass `--emulator localhost:8085` (for `run`) or use `managed` (which
always sets the emulator host).

---

## 8.10 HTML report has no charts / fails to open

**Symptom:** `<run_id>_report.html` is empty or errors.

**Cause:** `plotly` not installed.

**Fix:**
```bash
pip install -r requirements-dev.txt   # includes plotly
```

---

## 8.11 Quick Diagnostic Checklist

```bash
# 1. Dependencies present?
python -c "import click, rich, plotly, numpy, yaml; print('deps ok')"

# 2. CLI loads?
python tests/load/cli.py --help

# 3. Docker up (for managed)?
docker ps

# 4. Emulators reachable?
nc -z localhost 8685 && echo pubsub-ok
nc -z localhost 5443 && echo gcs-ok

# 5. Inspect last worker log
tail -n 50 "$(ls -t tests/load/results/*_worker.log | head -1)"

# 6. Inspect last summary
cat "$(ls -t tests/load/results/*_summary.json | head -1)"
```

---

## Still stuck?

- Re-read the relevant guide:
  [CLI Reference](./03-cli-reference.md),
  [Configuration & Data](./07-configuration-and-data.md).
- Run a minimal `managed -n 2 -d 10` with `--log-file` and read the log top to
  bottom — startup errors appear in the first ~20 lines.
</content>
