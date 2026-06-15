# Load Test Fixtures

Place your test payload JSON files here.

## Format

Each `.json` file should contain either:

1. A **single payload dict**:
```json
{
  "candidate_email": "jane@example.com",
  "candidate_uid": "cand-123",
  "org_id": "org-42",
  "org_alias": "acme-inc",
  "bucket_name": "your-bucket",
  "event_id": "evt-001",
  "lookup_map": {
    "profile": "profile",
    "interviews": ["interview_1", "interview_2"]
  }
}
```

2. A **list of payloads**:
```json
[
  {"candidate_email": "a@example.com", ...},
  {"candidate_email": "b@example.com", ...}
]
```

## Extensibility

As you get more real test data, simply add more `.json` files here.
The data loader reads all `.json` files in this directory and cycles
through them when the load test needs more messages than available payloads.

## Alternative Data Sources

Configure `--data-source` in the CLI:
- `fixtures` — reads from this directory (default)
- `generator` — generates synthetic payloads automatically
- `gcs` — reads from a GCS bucket (for production-scale data)
