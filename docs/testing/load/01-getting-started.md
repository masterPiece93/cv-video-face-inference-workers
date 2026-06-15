# 1. Getting Started

This guide gets you from a fresh checkout to your first successful load test.

---

## 1.1 Prerequisites

| Requirement | Why | Needed for |
|-------------|-----|------------|
| Python (project venv) | Runs the CLI and workers | All commands |
| `requirements-dev.txt` installed | Provides `click`, `rich`, `plotly`, `numpy`, `pyyaml` | All commands |
| Docker + Docker Compose | Starts the Pub/Sub emulator + fake-GCS | `managed` command |
| fdetect gRPC service (optional) | Real face encoding backend | `--encoder-backend fdetect` |

> 💡 The `run`, `report`, `profiles`, and `info` commands do **not** require Docker.
> Only `managed` spins up local emulators.

---

## 1.2 Install Dependencies

From the repository root (`gta-ai-eci-workers/`):

```bash
pip install -r requirements-dev.txt
```

This installs the load-testing extras:

- `click` — CLI argument parsing
- `rich` — colored console tables and progress bars
- `plotly` — interactive HTML charts
- `numpy` — percentile and statistics math
- `pyyaml` — YAML config file support

---

## 1.3 Verify the CLI Works

```bash
python tests/load/cli.py --help
```

You should see the list of commands: `run`, `managed`, `generate`, `report`,
`profiles`, `info`.

List the available load profiles:

```bash
python tests/load/cli.py profiles
```

Show the current default configuration:

```bash
python tests/load/cli.py info
```

---

## 1.4 Your First Test (Fully Managed)

The `managed` command is the easiest way to run a complete, self-contained test.
It will:

1. Start the Pub/Sub emulator + fake-GCS via Docker Compose
2. Create the bucket, topics, and subscription
3. Seed candidate data (if `--seed-dir` is given)
4. Launch an instrumented worker
5. Publish messages according to the chosen profile
6. Wait for the worker to process them
7. Generate console + Markdown + HTML reports
8. Tear everything down

```bash
python tests/load/cli.py managed \
    --profile ramp \
    --messages 4 \
    --duration 30 \
    --worker face_encoding_worker \
    --seed-dir candidate_data \
    --encoder-backend dummy \
    --log-file tests/load/results/worker.log
```

> Using `--encoder-backend dummy` means **no ML model is required** — the worker
> uses random 128-dim encodings. This is perfect for a first smoke test.

When it finishes you'll find these files in `tests/load/results/`:

- `<run_id>.jsonl` — raw per-message metrics
- `<run_id>_summary.json` — aggregated KPIs
- `<run_id>_report.md` — Markdown report
- `<run_id>_report.html` — interactive HTML report (open in a browser)
- `worker.log` — full worker output (from `--log-file`)

---

## 1.5 Your First Test With a Real Encoder (fdetect)

If you have a fdetect gRPC encoder running locally (for example at
`0.0.0.0:7777`):

```bash
python tests/load/cli.py managed \
    --profile ramp \
    --messages 4 \
    --duration 30 \
    --worker face_encoding_worker \
    --seed-dir candidate_data \
    --encoder-backend fdetect \
    --fdetect-channel localhost:7777 \
    --log-file tests/load/results/worker.log
```

Watch the worker process the messages in real time in a second terminal:

```bash
tail -f tests/load/results/worker.log
```

---

## 1.6 Publish-Only Test (No Docker)

If you **already** have a worker, topic, and subscription running (e.g. against
a shared emulator or a dev project), use `run` to just publish load and collect
metrics:

```bash
python tests/load/cli.py run \
    --profile spike \
    --messages 100 \
    --duration 30 \
    --emulator localhost:8085 \
    --topic face-encoding-ingestion
```

The worker (which you run separately and instrument with `MetricsCollector`)
writes results to `tests/load/results/<run_id>.jsonl`. Then:

```bash
python tests/load/cli.py report tests/load/results/<run_id>.jsonl
```

---

## 1.7 The Candidate Seed Data

The repo ships with sample candidate videos in `candidate_data/`:

```
candidate_data/
├── README.md
├── ankita.kapoor@gmail.com/      → profile.mp4, interview_1.mp4, interview_2.mp4
├── ekansh.sankhyadhar@gmail.com/ → profile.mp4, interview_1.mp4, interview_2.mp4
├── naman.sharma@gmail.com/       → profile.mp4, interview_1.mp4, interview_2.mp4
└── vishal.gupta@gmail.com/       → profile.mp4, interview_1.mp4, interview_2.mp4
```

When you pass `--seed-dir candidate_data`, the framework uploads each
candidate's videos into the fake-GCS bucket in the exact folder structure each
worker expects, and builds matching Pub/Sub payloads.

> There are **4 candidates**. If you publish more than 4 messages, the payloads
> are **cycled** (reused) with fresh `event_id`s.

---

## Next Steps

- Understand how it all fits together → [Architecture & Concepts](./02-architecture.md)
- See every option explained → [CLI Reference](./03-cli-reference.md)
- Jump straight to examples → [Scenarios & Recipes](./05-scenarios.md)
</content>
