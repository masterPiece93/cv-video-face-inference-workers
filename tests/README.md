# Tests

This directory contains the full test suite for the ECI workers, organized by
test type.

## Layout

```
tests/
├── conftest.py      # Shared fixtures (mock storage, encoders, payloads) for ALL tests
├── unit/            # Fast, isolated unit tests — all dependencies mocked, no Docker
│   ├── common/      #   Shared services & utils (storage, fdetect encoder, helpers, validators)
│   └── workers/     #   Per-worker handlers, schemas, services, strategies, output handlers
├── integration/     # Tests against Pub/Sub + GCS emulators (marked `integration`)
└── load/            # Load-testing framework + CLI (see docs/testing/load/)
```

> The root `conftest.py` fixtures are inherited by every subdirectory, so unit
> tests under `tests/unit/` continue to use them without any extra wiring.

## Running tests

### Unit tests (no emulators required)

```bash
# Everything, with coverage (configured in pyproject.toml)
python -m pytest tests/unit/

# A single area
python -m pytest tests/unit/common/
python -m pytest tests/unit/workers/face_encoding_worker/

# A single file or test
python -m pytest tests/unit/workers/test_output_handlers.py
python -m pytest tests/unit/common/utils/test_validators.py -k "validate_payload"
```

### Integration tests (require Docker emulators)

```bash
docker compose -f tests/integration/docker-compose.yml up -d
python -m pytest tests/integration/ -m integration
docker compose -f tests/integration/docker-compose.yml down -v
```

### Load tests

See the dedicated guide in [`docs/testing/load/`](../docs/testing/load/README.md).

## Conventions

- **Unit tests** mock every external dependency (GCS, Pub/Sub, encoders) and must
  run without network or Docker. Place them under `tests/unit/`, mirroring the
  `common/` or `workers/<worker_name>/` package layout of the code under test.
- **Integration tests** go under `tests/integration/` and must be marked with
  `@pytest.mark.integration` so they are excluded from the unit-test run.
- Use **absolute imports** (`from workers...`, `from common...`); `pythonpath`
  is set to the repo root in `pyproject.toml`.

## CI

- `.github/workflows/unit-tests.yml` runs `pytest tests/unit/` with coverage on
  every push / PR.
- `.github/workflows/integration-tests.yml` spins up the emulators and runs
  `pytest tests/integration/ -m integration`.
