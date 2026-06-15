"""Unit tests for ECI workers and shared common services.

Fast, isolated tests that mock all external dependencies (GCS, Pub/Sub,
encoders). No emulators or Docker required.

Layout:
    tests/unit/common/   — shared services & utils (storage, encoders, helpers)
    tests/unit/workers/  — per-worker handlers, schemas, services, strategies

Run with:
    pytest tests/unit/
"""
