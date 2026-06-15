# ECI Workers — Load Testing Guide

A complete user guide for load testing the ECI (External Candidate Interview)
Pub/Sub workers: `face_encoding_worker`, `face_verification_worker`, and
`onboarding_verification_worker`.

This documentation explains **every command, every option, and every realistic
combination** of options, plus a large catalogue of scenario-based examples.

---

## 📚 Documentation Index

| # | Guide | What you'll learn |
|---|-------|-------------------|
| 1 | [Getting Started](./01-getting-started.md) | Install, prerequisites, first run in 5 minutes |
| 2 | [Architecture & Concepts](./02-architecture.md) | How the framework is built, data flow, key terms |
| 3 | [CLI Reference](./03-cli-reference.md) | Every command and every option, explained in full |
| 4 | [Load Profiles](./04-load-profiles.md) | Ramp / Spike / Soak / Stress — math, timing, when to use |
| 5 | [Scenarios & Recipes](./05-scenarios.md) | 30+ real-world examples and option combinations |
| 6 | [Metrics & Reports](./06-metrics-and-reports.md) | Reading KPIs, percentiles, charts, output files |
| 7 | [Configuration & Data Sources](./07-configuration-and-data.md) | Config files, fixtures, GCS, synthetic data, generators |
| 8 | [Troubleshooting](./08-troubleshooting.md) | Common errors and how to fix them |

---

## ⚡ TL;DR — The Three Things You'll Run Most

```bash
# 1. Fully managed end-to-end test (infra + worker + publish + report, auto-teardown)
python tests/load/cli.py managed \
    -p ramp -n 20 -d 60 \
    -w face_encoding_worker \
    --seed-dir candidate_data \
    --encoder-backend fdetect --fdetect-channel localhost:7777 \
    --log-file tests/load/results/worker.log

# 2. Publish-only test against an already-running worker
python tests/load/cli.py run --profile spike --messages 100 --duration 30

# 3. Generate a report from a previous run's results
python tests/load/cli.py report tests/load/results/<run_id>.jsonl
```

---

## 🧭 Which command do I need?

```
┌─────────────────────────────────────────────────────────────────┐
│ Do you want the framework to start everything for you            │
│ (emulators + worker + publish + report + teardown)?              │
│                                                                  │
│   YES ──────────────►  use `managed`   (the one-shot command)    │
│                                                                  │
│   NO, I already have a worker + topic running                    │
│        ──────────────►  use `run`      (publish-only)            │
│                                                                  │
│ Just want to turn raw videos into worker-ready GCS data/fixtures?│
│        ──────────────►  use `generate`                           │
│                                                                  │
│ Have a .jsonl results file and want charts/tables?               │
│        ──────────────►  use `report`                             │
│                                                                  │
│ Want to see available profiles or current settings?              │
│        ──────────────►  use `profiles` / `info`                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 🗂️ Code Layout (for reference)

```
tests/load/
├── cli.py                  ← Click CLI entry point (all commands live here)
├── docker-compose.yml      ← Pub/Sub emulator (8685) + fake-GCS (5443)
├── core/                   ← Reusable across any Pub/Sub worker project
│   ├── types.py            ← Data models (LoadProfile, MessageResult, MetricsSummary)
│   ├── profiles.py         ← Load shapes (Ramp, Spike, Soak, Stress)
│   ├── publisher.py        ← Generic Pub/Sub load publisher
│   ├── metrics.py          ← Per-message metrics collector
│   ├── analyzer.py         ← Percentiles, throughput, stage breakdowns
│   └── reporter.py         ← Console (Rich), Markdown, HTML (Plotly) reports
├── project/                ← ECI-workers-specific layer
│   ├── config.py           ← ECILoadTestSettings (topics, buckets, env)
│   ├── data_loader.py      ← fixtures / GCS / synthetic payload loaders
│   ├── data_generator.py   ← Raw seed videos → worker-ready GCS data
│   ├── orchestrator.py     ← Manages infra + worker subprocess lifecycle
│   └── instrumented_worker.py ← Real worker wrapped with MetricsCollector
├── fixtures/               ← Test payload JSON files
└── results/                ← Output (.jsonl, _summary.json, _report.md/.html, worker.log)
```

---

## ✅ Requirements at a Glance

- Python environment with `requirements-dev.txt` installed (`click`, `rich`, `plotly`, `numpy`, `pyyaml`).
- Docker + Docker Compose (only for the `managed` command, which starts emulators).
- (Optional) A running **fdetect** gRPC encoder if you want realistic encoding
  (e.g. `localhost:7777`). Otherwise use the built-in `dummy` encoder.

See [Getting Started](./01-getting-started.md) for full setup details.

---

## 🔭 Roadmap / Future Enhancements

Proposed-but-not-yet-implemented features live in
[`future_enhancements/`](./future_enhancements/README.md). Each is a complete
design spec ready to be picked up.

| Proposal | Summary |
|----------|---------|
| [Remote Worker Resource Metrics](./future_enhancements/01-remote-worker-resource-metrics.md) | Capture CPU & memory KPIs when load testing a worker hosted **elsewhere** (Cloud Run / GKE / remote VM), where the current in-process sampler cannot see the worker. |
</content>
</invoke>
