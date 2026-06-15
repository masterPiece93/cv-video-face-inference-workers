# Interview Video Verification Platform

## Overview

A highly scalable, event-driven microservice architecture for verifying whether multiple recorded interview videos feature the same candidate as their profile video. Built on GCP with a Go API backend and Python-based face recognition workers.

---

## Architecture Summary

```
┌─────────────┐         ┌──────────────────┐        ┌─────────────────┐
│   React UI  │────────▶│  Go API Gateway  │───────▶│  Cloud Pub/Sub  │
│  (Cloud Run)│◀────────│   (Cloud Run)    │        │                 │
└─────────────┘         └──────────────────┘        └────────┬────────┘
                                │                            │
                                │                   ┌────────▼────────┐
                                ▼                   │  Python Worker  │
                        ┌──────────────┐            │  (Cloud Run Job │
                        │  Cloud SQL   │            │   or GKE)       │
                        │ (PostgreSQL) │◀───────────│                 │
                        └──────────────┘            └────────┬────────┘
                                                             │
                                                    ┌────────▼────────┐
                                                    │  Cloud Storage  │
                                                    │  (Encodings +   │
                                                    │   Videos)       │
                                                    └─────────────────┘
```

---

## Microservices

### 1. Frontend Service (React/Next.js — Cloud Run)

**Purpose:** User interface for uploading Google Sheets and monitoring processing status.

| Feature | Details |
|---------|---------|
| Sheet Upload | Drag-and-drop or file picker for `.xlsx` / Google Sheet URL |
| Status Dashboard | Real-time per-candidate status via SSE or polling |
| Filters | Filter by status: Pending, Processing, Match, Mismatch, Error |

---

### 2. Go API Gateway (Cloud Run — autoscaling)

**Purpose:** Central API layer handling uploads, data persistence, event publishing, and status queries.

#### Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `POST` | `/api/v1/sheets/upload` | Accept sheet, parse, store in DB, publish events. Returns `202 Accepted` immediately |
| `GET` | `/api/v1/sheets` | List all uploaded sheets with summary stats |
| `GET` | `/api/v1/sheets/:id` | Get sheet details with candidate count and status breakdown |
| `GET` | `/api/v1/candidates?sheetId=X&status=Y` | Paginated candidate list with filters |
| `GET` | `/api/v1/candidates/:id` | Individual candidate detail with verification result |
| `GET` | `/api/v1/stream/status` | SSE endpoint for real-time status push |

#### Upload Flow

1. Receive uploaded sheet file
2. Validate format and required columns
3. Parse rows → extract candidate name, profile video URL, interview video URLs
4. Batch insert into Cloud SQL (`candidates` table, status = `PENDING`)
5. Batch publish messages to Pub/Sub topic `candidate-processing` (one per candidate)
6. Return `202 Accepted` with `sheetId`

---

### 3. Cloud Pub/Sub (Event Bus)

**Purpose:** Decouples services, provides reliable async message delivery with retries and dead-letter support.

#### Topics & Subscriptions

| Topic | Publisher | Subscriber | Payload |
|-------|-----------|------------|---------|
| `candidate-processing` | Go API Gateway | Python Encoding Service | `{candidateId, profileVideoUrl, interviewVideoUrls, sheetId}` |
| `encoding-complete` | Python Encoding Service | Python Verification Service | `{candidateId, encodingPaths}` |
| `verification-complete` | Python Verification Service | Go Status Updater | `{candidateId, result, confidence}` |

#### Reliability Configuration

- **Acknowledgement deadline:** 600s (video processing is heavy)
- **Retry policy:** Exponential backoff, min 10s, max 600s
- **Dead-letter topic:** `candidate-processing-dlq` (after 5 failed attempts)
- **Message ordering:** Not required (each candidate is independent)

---

### 4. Python Encoding Service (GKE with GPU / Cloud Run Jobs)

**Purpose:** Downloads videos, extracts face encodings using deep learning models, stores results.

#### Processing Steps

1. **Receive message** from `candidate-processing` subscription
2. **Download videos** (profile + all interview videos) from URLs to temp storage
3. **Extract frames** — sample key frames from each video (e.g., every 30th frame or scene-change detection)
4. **Detect faces** — using MTCNN or RetinaFace
5. **Generate embeddings** — using ArcFace/InsightFace (512-dimensional vectors)
6. **Aggregate** — compute mean embedding per video for robustness
7. **Store encodings** in Cloud Storage: `gs://encodings-bucket/{candidateId}/{videoType}_{index}.npy`
8. **Update DB** status → `ENCODING_COMPLETE`
9. **Publish** to `encoding-complete` topic

#### Scaling Strategy

- **GKE Node Pools:** GPU node pool (NVIDIA T4) with cluster autoscaler (0 → N nodes)
- **HPA:** Scale pods based on Pub/Sub message backlog metric
- **Concurrency:** Each pod processes 1 candidate at a time (GPU memory constraint)
- **Alternative:** Cloud Run Jobs with task parallelism for CPU-only models

#### Error Handling

- Video download failure → retry 3x with backoff → status = `ERROR`
- No face detected → status = `ERROR` with message "No face detected in video X"
- Partial failure → encode what's possible, flag incomplete

---

### 5. Python Verification Service (Cloud Run Jobs / GKE)

**Purpose:** Compares face encodings to determine if the same person appears across all videos.

#### Processing Steps

1. **Receive message** from `encoding-complete` subscription
2. **Load encodings** from Cloud Storage
3. **Compare profile vs each interview:**
   - Compute cosine similarity between profile embedding and each interview embedding
   - Threshold: similarity > 0.6 = same person (configurable)
4. **Cross-compare interviews:** Ensure all interviews also match each other
5. **Determine result:**
   - `MATCH` — all videos same person, matches profile
   - `MISMATCH` — one or more videos differ from profile
   - `INCONCLUSIVE` — confidence below threshold but not clearly different
6. **Update DB** with result + per-video confidence scores
7. **Publish** to `verification-complete` topic

#### Output Schema (stored in DB)

```json
{
  "candidateId": "uuid",
  "overallResult": "MATCH|MISMATCH|INCONCLUSIVE",
  "profileConfidence": 0.92,
  "interviewResults": [
    {"videoUrl": "...", "similarity": 0.94, "result": "MATCH"},
    {"videoUrl": "...", "similarity": 0.31, "result": "MISMATCH"}
  ]
}
```

---

### 6. Go Status Updater (Cloud Run — lightweight)

**Purpose:** Subscribes to verification results and pushes updates to connected UI clients.

- Receives `verification-complete` messages
- Updates Cloud SQL final status
- Checks if all candidates in a sheet are done → updates sheet status to `COMPLETE`
- Broadcasts to SSE/WebSocket connected clients

---

## Data Model (Cloud SQL — PostgreSQL)

```sql
-- Sheets table
CREATE TABLE sheets (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    filename    TEXT NOT NULL,
    uploaded_by TEXT,
    uploaded_at TIMESTAMPTZ DEFAULT NOW(),
    row_count   INT NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'PROCESSING',
        -- PROCESSING | COMPLETE | PARTIAL_ERROR
    CONSTRAINT chk_sheet_status CHECK (status IN ('PROCESSING','COMPLETE','PARTIAL_ERROR'))
);

-- Candidates table
CREATE TABLE candidates (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sheet_id              UUID NOT NULL REFERENCES sheets(id) ON DELETE CASCADE,
    row_number            INT NOT NULL,
    candidate_name        TEXT,
    candidate_email       TEXT,
    profile_video_url     TEXT NOT NULL,
    interview_video_urls  JSONB NOT NULL,  -- ["url1", "url2", ...]
    status                TEXT NOT NULL DEFAULT 'PENDING',
        -- PENDING | ENCODING | ENCODING_COMPLETE | VERIFYING | MATCH | MISMATCH | INCONCLUSIVE | ERROR
    verification_result   JSONB,  -- detailed per-video results
    error_message         TEXT,
    created_at            TIMESTAMPTZ DEFAULT NOW(),
    updated_at            TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT chk_candidate_status CHECK (
        status IN ('PENDING','ENCODING','ENCODING_COMPLETE','VERIFYING','MATCH','MISMATCH','INCONCLUSIVE','ERROR')
    )
);

-- Indexes
CREATE INDEX idx_candidates_sheet_id ON candidates(sheet_id);
CREATE INDEX idx_candidates_status ON candidates(status);
CREATE INDEX idx_candidates_sheet_status ON candidates(sheet_id, status);
```

---

## Cloud Storage Structure

```
gs://interview-verification-encodings/
  └── {candidateId}/
      ├── profile_encoding.npy
      ├── interview_0_encoding.npy
      ├── interview_1_encoding.npy
      └── metadata.json
```

---

## GCP Services Map

| Component | GCP Service | Configuration |
|-----------|-------------|---------------|
| Frontend | Cloud Run | min-instances=0, max=10, 512MB RAM |
| Go API Gateway | Cloud Run | min-instances=1, max=100, 1GB RAM, concurrency=80 |
| Event Bus | Cloud Pub/Sub | 3 topics, dead-letter enabled |
| Encoding Worker | GKE (GPU pool) | T4 GPUs, autoscaler 0→20 nodes |
| Verification Worker | Cloud Run Jobs | 4 vCPU, 8GB RAM, parallelism=50 |
| Database | Cloud SQL PostgreSQL | db-custom-4-16384, HA enabled |
| Blob Storage | Cloud Storage | Standard class, lifecycle policy 30d |
| Secrets | Secret Manager | DB creds, API keys |
| Monitoring | Cloud Monitoring | Custom dashboards, alerting |
| Auth | Identity-Aware Proxy (IAP) | OAuth 2.0 for UI access |
| DNS/LB | Cloud Load Balancing | HTTPS, managed SSL |

---

## Sequence Flow

```
1. User uploads Google Sheet via UI
2. UI → POST /api/v1/sheets/upload → Go API
3. Go API parses sheet, inserts N candidates into DB (status=PENDING)
4. Go API publishes N messages to Pub/Sub[candidate-processing]
5. Go API returns 202 Accepted to UI
6. UI starts polling GET /api/v1/candidates?sheetId=X

--- Async Processing ---

7. Encoding Worker picks message from candidate-processing
8. Worker downloads videos, generates face encodings
9. Worker stores encodings in GCS
10. Worker updates DB status=ENCODING_COMPLETE
11. Worker publishes to Pub/Sub[encoding-complete]

12. Verification Worker picks message from encoding-complete
13. Worker loads encodings from GCS, compares faces
14. Worker updates DB status=MATCH/MISMATCH/ERROR
15. Worker publishes to Pub/Sub[verification-complete]

16. Status Updater receives verification-complete
17. Status Updater pushes update to UI via SSE
18. UI displays updated status for candidate
```

---

## Scalability & Performance

| Concern | Solution |
|---------|----------|
| Thousands of candidates per sheet | Pub/Sub handles millions of messages; each candidate = independent parallel task |
| Video processing is slow | GKE GPU autoscaling; process candidates in parallel |
| Database bottleneck | Connection pooling (PgBouncer), batch inserts, indexed queries |
| Real-time UI updates | SSE with Go API; fallback to polling every 5s |
| Cost control | Cloud Run scale-to-zero; GKE cluster autoscaler min=0 |
| Fault tolerance | Pub/Sub retries + dead-letter queues; idempotent workers |
| Idempotency | Workers check DB status before reprocessing; use candidateId as key |

---

## Error Handling & Observability

- **Dead Letter Queues:** Failed messages after 5 retries go to DLQ for manual inspection
- **Structured Logging:** JSON logs with `candidateId`, `sheetId`, `traceId` for correlation
- **Alerting:** Alert on DLQ message count > 0, error rate > 5%, processing latency > 10min
- **Tracing:** Cloud Trace for end-to-end request tracing across services
- **Health Checks:** Each service exposes `/healthz` endpoint

---

## Security

- **IAP:** Restricts UI access to authorized users
- **VPC Connector:** Cloud Run services connect to Cloud SQL via private IP
- **Signed URLs:** Videos downloaded via time-limited signed URLs where possible
- **Secret Manager:** All credentials stored securely, never in code
- **IAM:** Least-privilege service accounts per microservice
