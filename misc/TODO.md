# TODO

- [ ] add logic for duplicate message handling
- [x] add logic for acknowledgement deadline increase
- [ ] add logic for service ping on startup for fdetect from gta_ml. use IC reference
- [x] enhance dockerfile to install common module specific requirements
    - i.e install only the requirements that belong to the common modules that you use.
    - also organise the requirements in coomon module accordingly.
- [~] Implement parallel frame processing with safe_ml:fdetect and all the different interviews shall also be inside different processPoolExecuter ( i.e in parallel ).
    - [x] frame-level parallelism, capability-gated on the encoder backend (fdetect → parallel, face_recognition/dlib → sequential). See "Backend-aware frame encoding" below.
    - [ ] folder-level parallelism: encode each interview/profile folder concurrently (their `.npy` outputs are independent).

- [x] add mypy and pylint
- [x] in `face_encoding_worker` , the path for keeping **video_snippets** folder is not foloowing the proper pattern from test_data.json . It keeps the files at root .
- [x] in `face encoding worker` , the processing.json is also being created in stage folder in bucket . This is wrong . This will be done by GoLang ( source ) worker . Remove it .
- [x] unit test cases
- [x] integration test cases
- [ ] make distance calculation optional . add option for `cosine similarity distance` along with `euclidian distance`
- [x] modify bucket stage structure . {eventID} nesting remove

### Parallel farme processing Logic is replaced for `Encoding` in case of local `face_recofnition` library :

- previously it was executing in a thread , but not directly using face_detect library , but by offloading this library call to grpc service `fdetect` .

#### Root Cause & Fix
**Root Cause**: dlib (the C++ backend used by face_recognition) is not thread-safe. The ThreadPoolExecutor in _encode_video_folder and _encode_video was calling encode_frame() concurrently from multiple threads, causing a segmentation fault inside dlib's native code.

**Fix applied to** encoding.py:

- Replaced ThreadPoolExecutor + as_completed() in both _encode_video_folder and _encode_video with a simple sequential for loop
- Added a comment explaining why threading is forbidden for this backend
- Removed unused ThreadPoolExecutor / as_completed imports

> Note: If you ever switch to the fdetect gRPC backend, threading can safely be re-enabled there since gRPC calls are I/O-bound and thread-safe.


#### Update — Backend-aware frame encoding (re-enabled, conditionally)

Frame-level parallelism is back, but **driven by an encoder capability flag** instead of being hard-removed:

- `BaseEncoder.supports_parallel: bool = False` (safe default). `FdetectEncoder` sets it `True`; `FaceRecognitionEncoder` keeps it `False`.
- `VideoFaceEncodingService._encode_frames()` dispatches to `_encode_frames_parallel()` (ThreadPoolExecutor) only when `encoder.supports_parallel and max_workers > 1`; otherwise `_encode_frames_sequential()`.
- **Only the stateless encode step is threaded.** Deduplication (`_collect_unique`) stays single-threaded and order-preserving, so the `.npy` output is byte-for-byte identical regardless of mode.
- dlib therefore never runs concurrently (no segfault), while fdetect gets the speed-up.


#### Old Implementation Snippet

```python
with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = {executor.submit(self._process_frame, f): f for f in frames}
                for future in as_completed(futures):
                    frame = futures[future]
                    try:
                        encodings = future.result()
                        for enc in encodings:
                            if not self.encoder.is_duplicate(
                                enc, all_encodings, self.face_tolerance
                            ):
                                all_encodings.append(enc)
                                all_unique_frames.append(frame)
                    except Exception as e:
                        logger.warning(f"[{event_id}] Frame skipped ({blob_path}): {e}")
```


```
docker run -v /home/ubuntu/.config/gcloud/application_default_credentials.json:/app/data/gcp.json -v /home/ubuntu/Documents/office/GTA/ai_external_candidate_interview_workers/gta-ai-eci-workers/workers/face_encoding_worker/envs/.env.dev.container:/app/envs/.env -it face_encoding_worker:0.0.1 
```