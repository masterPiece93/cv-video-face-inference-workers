# MinIO Local Server Bootstrap

This folder includes a small env-driven launcher for running MinIO in Docker.

## Files

- `server.py` - starts MinIO container from a `.env` file and prints full runtime info
- `.env.minio.example` - sample env file

## Usage

1. Copy env template:

```bash
cp common/services/cloud/minio/.env.minio.example common/services/cloud/minio/.env.minio
```

2. Start MinIO:

```bash
python -m common.services.cloud.minio.server --env-file common/services/cloud/minio/.env.minio
```

3. Optional dry-run (prints all info and docker command only):

```bash
python -m common.services.cloud.minio.server --env-file common/services/cloud/minio/.env.minio --dry-run
```

## Console Output

The launcher prints:

- container/image/ports/data directory
- MinIO API + Console URLs
- masked credentials
- worker env block to copy:
  - `STORAGE_PROVIDER=minio`
  - `MINIO__ENDPOINT`
  - `MINIO__ACCESS_KEY`
  - `MINIO__SECRET_KEY`
  - `MINIO__SECURE`
  - `MINIO__REGION`

> The worker reads MinIO connection details from `MINIO__`-prefixed settings
> (nested `minio` settings, consistent with the `GCP__` convention). The
> `STORAGE_PROVIDER` **setting** selects the backend — it is not an `ENV_NAME`.
