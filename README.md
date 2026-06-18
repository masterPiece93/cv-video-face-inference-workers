# Face Inference Workers

[![Unit Tests & Coverage](https://github.com/masterPiece93/cv-video-face-inference-workers/actions/workflows/unit-tests.yml/badge.svg?branch=develop)](https://github.com/masterPiece93/cv-video-face-inference-workers/actions/workflows/unit-tests.yml)
[![Integration Tests](https://github.com/masterPiece93/cv-video-face-inference-workers/actions/workflows/integration-tests.yml/badge.svg?branch=develop)](https://github.com/masterPiece93/cv-video-face-inference-workers/actions/workflows/integration-tests.yml)
[![Mypy Type Check](https://github.com/masterPiece93/cv-video-face-inference-workers/actions/workflows/mypy.yml/badge.svg?branch=develop)](https://github.com/masterPiece93/cv-video-face-inference-workers/actions/workflows/mypy.yml)


This process involves multiple workers , invoked in a chain or events .

High Level Flow

```txt
/---------\
|    o    | 
|    |    |
|   / \   |
\---------/
     |
     | [spreadsheet]
     |
+----------+
|          |
|    UI    |
|          |
+----------+
     |
     | (1)
     |
+----------+
|          |
|    API   |                    +---------------------+
|          |             (5)    |       Python        |
+----------+       .------------|    Verification     |
      \           /             |       Worker        |
       \(2)      /              +---------------------+
        \       /                          | (4)
+---------------------+         +---------------------+
|                     |         |       Python        |
|    GoLang Worker    |---------|      Encoding       |
|                     |   (3)   |       Worker        |
+---------------------+         +---------------------+

```
1. `UI` forwards sheet data to API via HTTP
2. `API` invokes GoLang Worker via Pub/Sub
3. `GoLang Worker` invokes `Python Encoding Worker` via Pub/Sub
4. `Python Encoding Worker` invokes `Python Verification Worker` via Pub/Sub
5. `Python Verification Worker` invokes `GoLang Worker` via Pub/Sub

### Infra Related 

**PubSub**

Verification Flow
- `GoLang Source Worker o----Request----> Python Encoding Worker` 
     | | |
     | --- | --- |
     | **Topic** | tdx-gta-*<ENV_SHORTHAND>*-external-candidate-video-encoding |
     | **Subscription** | tdx-gta-*<ENV_SHORTHAND>*-external-candidate-video-encoding-subs |

     <details>
     <summary>Message Structure</summary>
     
     > Sample message structure for PubSub request to `face encoding worker`
     ```json
     {
          "candidate_email": "jane.doe@example.com",
          "candidate_uid": "cand-98765",
          "extra_info": {
          "first_name": "Jane",
          "last_name": "Doe",
          "phone": "+1-555-0100",
          "application_id": 4321
          },
          "org_id": "org-42",
          "org_alias": "acme-inc",
          "bucket_name": "tdx-gta-{ENV}-external-candidature-records",
          "event_id": "xxxzzzqqqwww",
          "lookup_map": {
               "profile": "profile",
               "interviews": ["interview_1", "interview_2", "interview_3"]
          }
     }
     ```
     </details>
     .

- `Python Encoding Worker o----Request----> Python Verification Worker` 
     | | |
     | --- | --- |
     | **Topic** | tdx-gta-*<ENV_SHORTHAND>*-external-candidate-video-verification |
     | **Subscription** | tdx-gta-*<ENV_SHORTHAND>*-external-candidate-video-verification-subs |

     <details>
     <summary>Message Structure</summary>
     
     > Sample message structure for PubSub request to `face verification worker`
     ```json
     {
          "candidate_email": "jane.doe@example.com",
          "candidate_uid": "cand-98765",
          "extra_info": {
               "first_name": "Jane",
               "last_name": "Doe",
               "phone": "+1-555-0100",
               "application_id": 4321
          },
          "org_id": "org-42",
          "org_alias": "acme-inc",
          "bucket_name": "tdx-gta-{ENV}-external-candidature-records",
          "event_id": "xxxzzzqqqwww",
          "lookup_map": {
               "profile": "profile",
               "interviews": ["interview_1", "interview_2", "interview_3"]
          }
          "sampled_frames": {
               "profile": ["1.png","2.png","3.png","4.png",],
               "interviews": {
                    "interview_1": ["1.png","2.png","3.png","4.png",],
                    "interview_2": ["1.png","2.png","3.png","4.png",]
               }
          }
     }

     ```
     </details>
     .
- `Python Verification Worker o----Request----> GoLang Sink Worker` 
     | | |
     | --- | --- |
     | **Topic** | tdx-gta-*<ENV_SHORTHAND>*-external-candidate-verification-result |
     | **Subscription** | tdx-gta-*<ENV_SHORTHAND>*-external-candidate-verification-result-subs |
     <details>
     <summary>Message Structure</summary>
     
     > Sample message structure for PubSub request to `GoLang Sink worker`
     ```json
     { 
          "candidate_email": "jane.doe@example.com",
          "candidate_uid": "cand-98765",
          "extra_info": {
               "first_name": "Jane",
               "last_name": "Doe",
               "phone": "+1-555-0100",
               "application_id": 4321
          },
          "org_id": "org-42",
          "org_alias": "acme-inc",
          "bucket_name": "tdx-gta-{ENV}-external-candidature-records",
          "event_id": "xxxzzzqqqwww",
          "status": {
               "matches": {
                    "profile": True,
                    "interview_1": False,
               }
          },
     }

     ```
     </details>
     .

<br>

OnBoarding Flow
- `GoLang Worker o----Request----> Python On-boarding Verification Worker`
     | | |
     | --- | --- |
     | **Topic** | tdx-gta-*<ENV_SHORTHAND>*-external-candidate-ob-verification |
     | **Subscription** | tdx-gta-*<ENV_SHORTHAND>*-external-candidate-ob-verification-subs |

     <details>
     <summary>Message Structure</summary>
     
     > Sample message structure for PubSub request to `Onboarding Verification Worker`
     ```json
     {
          "candidate_email": "jane.doe@example.com",
          "candidate_uid": "cand-98765",
          "extra_info": {
               "first_name": "Jane",
               "last_name": "Doe",
               "phone": "+1-555-0100",
               "application_id": 4321
          },
          "org_id": "org-42",
          "org_alias": "acme-inc",
          "bucket_name": "tdx-{ENV}-external-sheet-candidature-records",
          "event_id": "xxxzzzqqqwww",
          "encoding_base_path": "video_face_encodings/",
          "encoding_locations": {
               "profile": "profile",
               "interviews": ["interview_1", "interview_2"]
          },
          "onboarding_reference_path": "onboarding_reference/",
          "match_against": ["profile", "interview_1"]
     }
     ```
     </details>
     .

- `Python On-boarding Verification Worker o----Request----> GoLang Worker`
     | | |
     | --- | --- |
     | **Topic** | tdx-gta-*<ENV_SHORTHAND>*-tdx-gta-dev-external-candidate-ob-verification-result |
     | **Subscription** | tdx-gta-*<ENV_SHORTHAND>*-tdx-gta-dev-external-candidate-ob-verification-result-subs |

     <details>
     <summary>Message Structure</summary>
     
     > Sample message structure for PubSub request to `GoLang Sink Worker`
     ```json
     { 
          "candidate_email": "jane.doe@example.com",
          "candidate_uid": "cand-98765",
          "extra_info": {
               "first_name": "Jane",
               "last_name": "Doe",
               "phone": "+1-555-0100",
               "application_id": 4321
          },
          "org_id": "org-42",
          "org_alias": "acme-inc",
          "bucket_name": "tdx-{ENV}-external-sheet-candidature-records",
          "event_id": "xxxzzzqqqwww",
          "status": {
               "matches": {
                    "profile": True,
                    "interview_1": False,
               }
          },
     }
     ```
     </details>
     .

<br>

**Cloud Storage ( Bucket )**

The workers support selectable object storage via the `STORAGE_PROVIDER`
**setting** (resolved by each worker's `Settings`, not read ad-hoc from the
environment):

- `STORAGE_PROVIDER=gcp` (default, backward compatible)
- `STORAGE_PROVIDER=gcs` (alias of `gcp`)
- `STORAGE_PROVIDER=minio`

When using `minio`, configure the connection with the `MINIO__` prefix
(consistent with the `GCP__` nested-settings convention):

- `MINIO__ENDPOINT` (example: `localhost:9000`)
- `MINIO__ACCESS_KEY`
- `MINIO__SECRET_KEY`
- `MINIO__SECURE` (`true` / `false`, default `false`)
- `MINIO__REGION` (optional, default `us-east-1`)

Notes:

- The selected provider is passed to `get_storage_service(provider, ...)`; the
  factory does not read environment variables itself.
- When `STORAGE_PROVIDER=minio`, the required `MINIO__*` settings are validated
  at startup (missing values fail fast with a clear error).
- Message schema stays unchanged (`bucket_name` and object paths are reused).
- Pub/Sub remains on GCP in the current architecture.
- Existing GCP flow remains default if `STORAGE_PROVIDER` is not set.

- Bucket Name : tdx-gta-*<ENV_SHORTHAND>*-external-candidature-records
- Base Paths :
     - snippet_base_path  :  "video_snippets/"
     - encoding_base_path :  "video_face_encodings/"
     - Frame_location     :  "video_sampled_frames/"
- NOTE : complete path is inferred by -> `<bucket-name>/<base-path>/<lookup-map-entry>`

> *<ENV_SHORTHAND>* : `dev` | `qa` | `stage` | `prod`

**Delployement**

- containers are deployed on GKE
- CI/CD Pattren
     - `DEV`
          - builds are deployed from _develop_ branch ( Branch Based )
          - follows `path filtering` ci/cd pattern , i.e build triggers are sensitive to only the changes in specific worker folders
               - info
                    | | |
                    | --- | --- |
                    | workers/face_encoding_worker/ | external-candidate-video-encoding-worker |
                    | workers/face_verification_worker/ | external-candidate-video-verification-worker |
                    | workers/onboarding_verification_worker/ | external-candidate-onboarding-verification-worker |
                    
     - `QA`
          - build are deployed from _git-release_ ( Tag Based )
     - `Stage`
          - build are deployed manually from `QA` artifactory ( once QA PASSED )
     - `Prod`
          - build are deployed manually from `QA` artifactory ( once STAGE PASSED )
     
### Developer Section

**[Face Encoding Worker](./workers/face_encoding_worker/README.md)**

Running in docker container and tesing on local
- run gta_ml:fdetect in a seperate terminal on `0.0.0.0`
    - specify it's channel correctly in .env file that you have mounted in container
- run live pubsub or any emulator ( on `0.0.0.0` )
     - specify it's connection correctly in .env file ( using PUBSUB_EMULATOR_HOST ) that you have mounted in container
- keep you application-default logged in
- prepare a local `.env` file that should be mounted in container's `/envs` folder

- build command
     ```shell
     docker build --build-arg ENCODER=gta_fdetect -t face_encoding_worker:0.0.1 -f workers/face_encoding_worker/docker/Dockerfile . 
     ```
     - BUILD ARGS : 
          - `ENCODER` : it is linked to the types of ENCODER that you specify within your **.env** , and solely depends on your processing needs .
               - allowed values : 
                    - `gta_fdetect` : uses in-house fdetect grpc service . Installs grpc related packages only.
                    - `face_recognition` : uses python's face_recognition library. Installs it's library & required system dependencies .
- run command
     ```shell
     docker run \
          --add-host=host.docker.internal:host-gateway \
          -v ~/.config/gcloud:/root/.config/gcloud \
          -e GOOGLE_APPLICATION_CREDENTIALS=/root/.config/gcloud/application_default_credentials.json \
          -e GOOGLE_CLOUD_PROJECT=tdx-is-dev-gta-01 \
          -v /home/ubuntu/Documents/office/GTA/ai_external_candidate_interview_workers/gta-ai-eci-workers/workers/face_encoding_worker/envs/.env.dev.container:/app/workers/face_encoding_worker/envs/.env \
          -it face_encoding_worker:{YOUR-TAG} 
     ```

**[Face Verification Worker](./workers/face_verification_worker/README.md)**

Running in docker container and tesing on local
- run live pubsub or any emulator ( on `0.0.0.0` )
     - specify it's connection correctly in .env file ( using PUBSUB_EMULATOR_HOST ) that you have mounted in container
- keep you application-default logged in
- prepare a local `.env` file that should be mounted in container's `/envs` folder

- command
     ```shell
     docker run \
          --add-host=host.docker.internal:host-gateway \
          -v ~/.config/gcloud:/root/.config/gcloud \
          -e GOOGLE_APPLICATION_CREDENTIALS=/root/.config/gcloud/application_default_credentials.json \
          -e GOOGLE_CLOUD_PROJECT=tdx-is-dev-gta-01 \
          -v /home/ubuntu/Documents/office/GTA/ai_external_candidate_interview_workers/gta-ai-eci-workers/workers/face_verification_worker/envs/.env.dev.container:/app/workers/face_verification_worker/envs/.env \
          -it face_verification_worker:{YOUR-TAG} 
     ```

**[Onboarding Verification Worker](./workers/onboarding_verification_worker/README.md)**

Running in docker container and tesing on local
- run gta_ml:fdetect in a seperate terminal on `0.0.0.0`
    - specify it's channel correctly in .env file that you have mounted in container
- run live pubsub or any emulator ( on `0.0.0.0` )
     - specify it's connection correctly in .env file ( using PUBSUB_EMULATOR_HOST ) that you have mounted in container
- keep you application-default logged in
- prepare a local `.env` file that should be mounted in container's `/envs` folder

- build command
     ```shell
     docker build --build-arg ENCODER=gta_fdetect -t onboarding_verification_worker:0.0.1 -f workers/onboarding_verification_worker/docker/Dockerfile . 
     ```
     - BUILD ARGS : 
          - `ENCODER` : it is linked to the types of ENCODER that you specify within your **.env** , and solely depends on your processing needs .
               - allowed values : 
                    - `gta_fdetect` : uses in-house fdetect grpc service . Installs grpc related packages only.
                    - `face_recognition` : uses python's face_recognition library. Installs it's library & required system dependencies .
- run command
     ```shell
     docker run \
          --add-host=host.docker.internal:host-gateway \
          -v ~/.config/gcloud:/root/.config/gcloud \
          -e GOOGLE_APPLICATION_CREDENTIALS=/root/.config/gcloud/application_default_credentials.json \
          -e GOOGLE_CLOUD_PROJECT=tdx-is-dev-gta-01 \
          -v /home/ubuntu/Documents/office/GTA/ai_external_candidate_interview_workers/gta-ai-eci-workers/workers/onboarding_verification_worker/envs/.env.dev.container:/app/workers/onboarding_verification_worker/envs/.env \
          -it onboarding_verification_worker:{YOUR-TAG} 
     ```
