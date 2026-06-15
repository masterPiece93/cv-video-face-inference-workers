# ECI Workers Load Testing

## Quick Start

```bash
# Install dependencies
pip install -r requirements-dev.txt

# View available profiles
python tests/load/cli.py profiles

# Run a load test (synthetic data, emulator)
python tests/load/cli.py run --profile ramp --messages 20 --duration 30

# Generate a report from results
python tests/load/cli.py report tests/load/results/<run_id>.jsonl
```

## Architecture

```
tests/load/
├── core/                    ← Reusable across any Pub/Sub worker project
│   ├── types.py             ← Data models (LoadProfile, MessageResult, MetricsSummary)
│   ├── profiles.py          ← Load shapes (Ramp, Spike, Soak, Stress)
│   ├── publisher.py         ← Generic Pub/Sub load publisher
│   ├── metrics.py           ← Per-message metrics collector (use in worker callback)
│   ├── analyzer.py          ← Computes percentiles, throughput, breakdowns
│   └── reporter.py          ← Console (Rich), Markdown, HTML (Plotly) reports
│
├── project/                 ← ECI-workers-specific configuration
│   ├── config.py            ← ECILoadTestSettings (topics, buckets, env)
│   └── data_loader.py       ← Extensible payload loading (fixtures/GCS/synthetic)
│
├── fixtures/                ← Test payload JSON files
│   └── sample_payloads.json
│
├── results/                 ← Output directory (gitignored)
├── cli.py                   ← Click CLI entry point
└── README.md                ← This file
```

## Load Profiles

| Profile | Pattern | Use Case |
|---------|---------|----------|
| `ramp` | Gradual increase | Normal traffic growth |
| `spike` | 80% burst in 20% time | Flash events |
| `soak` | Constant rate, long duration | Memory leak detection |
| `stress` | 5 escalating waves | Finding throughput ceiling |

## CLI Commands

```bash
# Run a load test
python tests/load/cli.py run \
  --profile stress \
  --messages 50 \
  --duration 120 \
  --data-source generator \
  --worker face_encoding_worker

# Generate report (all formats)
python tests/load/cli.py report tests/load/results/run_xyz.jsonl

# Console-only report
python tests/load/cli.py report tests/load/results/run_xyz.jsonl --format console

# Show config
python tests/load/cli.py info

# List profiles
python tests/load/cli.py profiles
```

## Configuration

### CLI Arguments (highest priority)
All settings can be overridden via CLI flags.

### Config File
```bash
python tests/load/cli.py run --config tests/load/my_config.yaml
```

Example YAML:
```yaml
project_id: tdx-is-dev-gta-01
topic_name: face-encoding-ingestion
subscription_name: face-encoding-ingestion-sub
emulator_host: localhost:8085
worker_name: face_encoding_worker
data_source: generator
default_total_messages: 50
default_duration_seconds: 120
```

## Instrumenting Your Worker

To collect per-message metrics, wrap your handler with the MetricsCollector:

```python
from tests.load.core.metrics import MetricsCollector

collector = MetricsCollector(results_dir=Path("tests/load/results"), run_id="my_run")

def instrumented_callback(message):
    publish_time = float(message.attributes.get("publish_time_epoch", "0"))
    event_id = json.loads(message.data).get("event_id", "unknown")

    with collector.track_message(event_id, publish_time) as tracker:
        with tracker.stage("parse"):
            payload = json.loads(message.data)
        with tracker.stage("process"):
            encoding_service.process(payload)
        message.ack()
```

## Extending Data Sources

1. **Local fixtures** — Add `.json` files to `tests/load/fixtures/`
2. **Synthetic** — Use `--data-source generator` (auto-generates valid payloads)
3. **GCS** — Configure `gcs_fixtures_bucket` and `gcs_fixtures_prefix` in config

To add a custom data source, subclass `DataLoader` in `project/data_loader.py`.

## Report Output

Reports include all standard load test parameters:

- **Test Execution**: VUs, throughput, duration, environment config
- **KPIs**: Response time percentiles (p50/p90/p95/p99), error rate, throughput
- **Resource Utilization**: Memory peak/avg
- **Stage Breakdown**: Per-processing-stage timing (download, encode, cluster, upload)
- **Charts** (HTML): Latency over time, throughput, error rate, percentile distribution, memory
