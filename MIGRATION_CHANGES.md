# Message Structure & Bucket Organization Migration

## Overview
All three workers have been updated to support the new bucket structure and Pub/Sub message format as specified in the change documentation.

## Key Changes

### 1. Bucket Structure Migration

#### Old Structure
```
{candidateEmail}/{candidateUUID}/
├── sheet_snippet_videos/
│   ├── profile/
│   │   └── [video files]
│   └── interview_N/
│       └── [video files]
├── video_face_encodings/
│   ├── profile/
│   │   ├── sampled_frames/
│   │   │   └── [PNG files]
│   │   └── profile.npy
│   └── interview_N/
│       ├── sampled_frames/
│       │   └── [PNG files]
│       └── interview_N.npy
```

#### New Structure
```
{candidateEmail}/{candidateUUID}/
├── video_snippets/                    ← Renamed from sheet_snippet_videos/
│   ├── profile/
│   │   └── [video files]
│   └── interview_N/
│       └── [video files]
├── video_face_encodings/
│   ├── profile/
│   │   └── profile.npy                ← No nested sampled_frames/
│   └── interview_N/
│       └── interview_N.npy
├── sampled_frames/                    ← New top-level folder
│   ├── profile/
│   │   └── [PNG files]
│   └── interview_N/
│       └── [PNG files]
```

### 2. Pub/Sub Message Format Changes

#### Face Encoding Worker

**Input Message** (from GoLang Source Worker):
- **Old**: `snippet_base_path` (str) + `snippet_locations` (dict)
- **New**: `lookup_map` (dict)

```json
// OLD
{
  "snippet_base_path": "test-org/org-test-42/.../video_snippets",
  "snippet_locations": {
    "profile": "profile",
    "interviews": ["interview_1", "interview_2"]
  }
}

// NEW
{
  "lookup_map": {
    "profile": "profile",
    "interviews": ["interview_1", "interview_2"]
  }
}
```

**Output Message** (to Face Verification Worker):
- **Old**: `encoding_base_path` (str) + `encoding_locations` (dict) + `sample_frames` (dict)
- **New**: `lookup_map` (dict) + `sampled_frames` (dict)

```json
// OLD
{
  "encoding_base_path": "video_face_encodings/",
  "encoding_locations": {
    "profile": "profile",
    "interviews": ["interview_1", "interview_2"]
  },
  "sample_frames": {
    "profile": ["1.png"],
    "interviews": {"interview_1": ["1.png", ...], ...}
  }
}

// NEW
{
  "lookup_map": {
    "profile": "profile",
    "interviews": ["interview_1", "interview_2"]
  },
  "sampled_frames": {
    "profile": ["1.png"],
    "interviews": {"interview_1": ["1.png", ...], ...}
  }
}
```

#### Face Verification Worker

**Input Message** (from Face Encoding Worker):
- Uses `lookup_map` instead of `encoding_base_path`/`encoding_locations`
- Uses `sampled_frames` instead of `sample_frames`

**Output Message** (to GoLang Sink Worker):
- Includes `lookup_map` in the output
- Includes `sampled_frames` instead of `sample_frames`

#### Onboarding Verification Worker

**Input Message** (from GoLang Worker):
- **Old**: `encoding_base_path` + `encoding_locations` + `match_against`
- **New**: `lookup_map` (only; `match_against` removed)

```json
// OLD
{
  "encoding_base_path": "video_face_encodings/",
  "encoding_locations": {"profile": "profile", ...},
  "match_against": ["profile", "interview_1"]
}

// NEW
{
  "lookup_map": {
    "profile": "profile",
    "interviews": ["interview_1", "interview_2"]
  }
}
```

**Output Message**:
- Includes `lookup_map` in the output

### 3. Code Changes by File

#### Face Encoding Worker

- **Input Schema** (`src/handlers/schema/input_schema.py`)
  - Removed: `snippet_base_path`, `snippet_locations`
  - Added: `lookup_map`

- **Output Schema** (`src/handlers/schema/output_schema.py`)
  - Removed: `encoding_base_path`, `encoding_locations`, `sample_frames`
  - Added: `lookup_map`, `sampled_frames`

- **Encoding Service** (`services/encoding.py`)
  - Added constants: `SNIPPETS_BASE = "video_snippets"`, `FRAMES_BASE = "sampled_frames"`
  - Updated `process()` method to:
    - Read from `lookup_map` instead of `snippet_locations`
    - Build paths using `video_snippets/`, `video_face_encodings/`, `sampled_frames/`
    - Write sampled frames to `{frames_base}/{profile,interview_N}/` instead of nested in encodings
    - Publish output with `lookup_map` and `sampled_frames`

- **Test Data Generation** (`local_testing/prepare_processing_data.py`)
  - Updated `_build_test_data()` to generate `lookup_map` instead of `snippet_base_path`/`snippet_locations`

- **GCS Seeding** (`local_testing/seed_gcs.py`)
  - Updated `_gcs_paths_from_payload()` to read from `lookup_map`
  - Updated `_seed_from_data_dir()` to read from `lookup_map`
  - Changed base path to hardcoded `"video_snippets"` instead of reading from payload

#### Face Verification Worker

- **Input Schema** (`src/handlers/schema/input_schema.py`)
  - Removed: `encoding_base_path`, `encoding_locations`, `sample_frames`
  - Added: `lookup_map`, `sampled_frames`

- **Output Schema** (`src/handlers/schema/output_schema.py`)
  - Removed: `sample_frames`
  - Added: `lookup_map`, `sampled_frames`

- **Verification Service** (`services/verification.py`)
  - Updated `process()` method to:
    - Read from `lookup_map` instead of `encoding_locations`
    - Load encodings from `video_face_encodings/{name}/{name}.npy`
    - Pass through `lookup_map` in output
    - Use `sampled_frames` instead of `sample_frames`

#### Onboarding Verification Worker

- **Input Schema** (`src/handlers/schema/input_schema.py`)
  - Removed: `encoding_base_path`, `encoding_locations`, `match_against`
  - Added: `lookup_map`

- **Output Schema** (`src/handlers/schema/output_schema.py`)
  - Added: `lookup_map`

- **Verification Service** (`services/verification.py`)
  - Updated `process()` method to:
    - Read from `lookup_map.interviews` instead of `match_against`
    - Load encodings from `video_face_encodings/{name}/{name}.npy`
    - Pass through `lookup_map` in output

### 4. Backward Compatibility

**⚠️ BREAKING CHANGES**: 
- All old message formats are no longer supported
- GoLang workers and any external systems must be updated to use new message format
- Database migrations may be required for historical data

### 5. Testing

All changes verified with `test_local.sh --env dev`:
- ✅ Input message uses `lookup_map`
- ✅ Worker reads from `video_snippets/{profile,interview_N}/`
- ✅ Encodings written to `video_face_encodings/{profile,interview_N}/{name}.npy`
- ✅ Sampled frames written to `sampled_frames/{profile,interview_N}/{N}.png`
- ✅ Output message uses `lookup_map` and `sampled_frames`

## Migration Checklist

- [x] Update face_encoding_worker schemas
- [x] Update face_encoding_worker service
- [x] Update face_verification_worker schemas
- [x] Update face_verification_worker service
- [x] Update onboarding_verification_worker schemas
- [x] Update onboarding_verification_worker service
- [x] Update test data generation
- [x] Update GCS seeding
- [x] Test end-to-end flow
- [ ] Update GoLang source and sink workers (external)
- [ ] Update database migration scripts
- [ ] Release notes
