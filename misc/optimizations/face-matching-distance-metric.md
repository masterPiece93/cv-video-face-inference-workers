# Face Matching — Distance Metric Analysis & Recommendation

> **Question addressed:** The `face_verification_worker` and `onboarding_verification_worker`
> appear to use the **Euclidean distance** formula for face matching. Is that correct?
> Should we keep Euclidean, switch to cosine similarity, or change the approach?

**Date:** 2026-06-15
**Scope:** `common/services/encoding/*`, `face_verification_worker`, `onboarding_verification_worker`

---

## TL;DR (the verdict)

1. **You are correct** — both workers match faces with **Euclidean (L2) distance**.
2. **Keep Euclidean for the dlib (`face_recognition`) backend.** That model was *trained*
   to be measured with L2, and your `FACE_TOLERANCE` is calibrated for it. Switching to
   cosine would invalidate the threshold for **no accuracy gain**.
3. **The metric is really a property of the *embedding model*, not a global setting.**
   If/when the `fdetect` gRPC backend serves an **ArcFace-style** embedding, **cosine is the
   correct metric for that backend** — while dlib stays on Euclidean.
4. **Bigger wins than the metric:** L2-normalize encodings, **calibrate the threshold on your
   own labelled data**, vectorize the 1-vs-N comparison, and keep the raw score for observability.

---

## 1. Confirmation — yes, it is Euclidean (L2)

The matching rule everywhere is:

> two 128-D encodings are the **same face** ⟺ `‖a − b‖₂ ≤ FACE_TOLERANCE`

This is implemented in **two separate layers** (important for any future change):

### 1a. Encoder layer — used during encoding/deduplication

`common/services/encoding/base.py` → `BaseEncoder.is_duplicate()`:

```python
distances = self.calculate_distance(existing, encoding)
return bool(np.any(distances <= tolerance))
```

Both backends implement `calculate_distance()` as **Euclidean**:

`common/services/encoding/face_recognition_encoder.py`:

```python
return np.linalg.norm(np.array(known_encodings) - candidate, axis=1)
```

`common/services/encoding/fdetect_encoder.py`:

```python
return np.linalg.norm(np.array(known_encodings) - candidate, axis=1)
```

### 1b. Verification layer — used during matching (⚠️ re-implemented inline)

The verification logic **does not call `encoder.calculate_distance()`**. Instead it
re-implements Euclidean inline:

`face_verification_worker/services/strategies/base.py` → `_encodings_match()`:

```python
distances = np.linalg.norm(b_list - a, axis=1)
return bool(np.any(distances <= tolerance))
```

`onboarding_verification_worker/services/verification.py` → `_match_any()`:

```python
distances = np.linalg.norm(video_encodings - ref_enc, axis=1)
return bool(np.any(distances <= self.tolerance))
```

> **Key takeaway:** there are **two independent places** that hard-code L2.
> Any move to a pluggable metric must address **both** the encoder layer *and* the
> verification/onboarding inline comparisons — otherwise they will silently diverge.

### Configured tolerances

| Worker | Setting | Default | Meaning |
|---|---|---|---|
| `face_encoding_worker` | `face_tolerance` | **0.5** | dedup threshold while building `.npy` |
| `face_verification_worker` | `face_tolerance` | **0.6** | match threshold |
| `onboarding_verification_worker` | `face_tolerance` | **0.6** | match threshold |

All are constrained to `(0.0, 1.0]`. These numbers are **only meaningful under Euclidean**
on dlib embeddings (dlib's documented "same person" boundary is `< 0.6`).

---

## 2. Should you switch to cosine? — For dlib, **no**

### 2a. The model dictates the metric

dlib's 128-D `face_recognition` embedding is trained with a **triplet loss that minimizes
Euclidean distance** between same-identity faces. Davis King calibrated the network so that
`distance < 0.6` ≈ "same person". **Euclidean is the metric the model was built for.**

ArcFace / CosFace / FaceNet-style models, by contrast, are trained with an **angular
(cosine) margin**, so their embeddings live on a hypersphere and are meant to be compared
with **cosine** similarity.

| Backend | Training objective | Native metric |
|---|---|---|
| `face_recognition` (dlib) — `FaceRecognitionEncoder` | triplet loss, L2 | **Euclidean** |
| ArcFace/FaceNet-style (possibly your `fdetect`) | angular-margin | **Cosine** |

### 2b. For normalized vectors the two are mathematically equivalent for ranking

If the encodings are unit-normalized (`‖a‖ = ‖b‖ = 1`):

$$
\|a-b\|^2 = \|a\|^2 + \|b\|^2 - 2\,(a\cdot b) = 2\,\bigl(1 - \cos\theta\bigr)
$$

So **Euclidean distance and cosine distance produce the *same ranking*** once vectors are
normalized. For dlib, switching to cosine therefore **cannot improve accuracy** — it would
only **break your calibrated `0.5`/`0.6` thresholds** and force a re-tuning exercise for zero
benefit.

> ⚠️ dlib embeddings are **not** guaranteed to be exactly unit-norm, so the equivalence is
> *approximate* unless you explicitly L2-normalize (see §4).

---

## 3. When cosine *is* the right choice

👉 **If your `fdetect` gRPC service returns an ArcFace-style embedding, use cosine for that
backend.** Note that today `FdetectEncoder.calculate_distance()` also hard-codes Euclidean —
if its model is angular-margin trained, that is a latent mismatch worth fixing.

This is exactly why the metric should be a **per-encoder capability**, not a global flag.

---

## 4. Recommended design — make the metric a per-encoder capability

This mirrors the existing `supports_parallel` capability-flag pattern and keeps distance
**inside the encoder**, where the model knowledge lives. `BaseEncoder` already owns
`calculate_distance()`, so this is a natural fit.

```python
# common/services/encoding/base.py  (illustration)
class BaseEncoder(ABC):
    supports_parallel: bool = False
    distance_metric: str = "euclidean"   # "euclidean" | "cosine"
    normalize: bool = False              # L2-normalize before comparison

    def _prep(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float64)
        if self.normalize:
            x = x / (np.linalg.norm(x, axis=-1, keepdims=True) + 1e-10)
        return x

    def calculate_distance(self, known_encodings, candidate) -> np.ndarray:
        if len(known_encodings) == 0:
            return np.array([])
        b = self._prep(np.array(known_encodings))
        a = self._prep(candidate)
        if self.distance_metric == "cosine":
            return 1.0 - (b @ a)                 # cosine *distance* (0 = identical)
        return np.linalg.norm(b - a, axis=1)     # euclidean (vectorized)
```

- `FaceRecognitionEncoder` → `distance_metric = "euclidean"` (default).
- `FdetectEncoder` → set `"cosine"` **iff** its model is angular-margin trained.

### Critical follow-up: route the verification layer through the encoder

Because §1b re-implements L2 inline, the strategies and onboarding `_match_any()` would
**bypass** the new metric. Fix by delegating to the encoder instead of `np.linalg.norm`:

```python
# strategies/base.py  &  onboarding verification.py  (illustration)
def _encodings_match(self, a, b_list, tolerance) -> bool:
    if b_list is None or len(b_list) == 0:
        return False
    distances = self.encoder.calculate_distance(b_list, a)   # single source of truth
    return bool(np.any(distances <= tolerance))
```

This makes the encoder the **single source of truth** for "how far apart are two faces",
so encoding-time dedup and verification-time matching can never silently diverge.

> Maps to the existing TODO item:
> *"make distance calculation optional. add option for `cosine similarity distance` along
> with `euclidian distance`."*

---

## 5. Other improvements (higher impact than Euclidean-vs-cosine)

1. **L2-normalize encodings before comparison.** Makes the metrics interchangeable and
   improves numerical robustness. (Built into the `_prep()` helper above.)
2. **Calibrate the threshold on *your own* labelled pairs.** Build a small genuine/impostor
   set, sweep the threshold, and pick the operating point by **ROC / Equal-Error-Rate** (or
   the precision/recall trade-off your product needs). This matters **far more** than the
   choice of metric. The inherited `0.6` is a generic default, not tuned to your data.
3. **Vectorize 1-vs-N comparisons.** The `axis=1` form already used in `_encodings_match`
   is good; ensure the same vectorized approach is used everywhere and avoid Python-level
   per-pair loops on large interview encoding sets.
4. **Keep the score, not just the boolean.** Persist the **minimum distance** (or similarity)
   alongside the match flag. Invaluable for debugging false matches and for re-calibrating
   the threshold later.
5. **Model quality is the ceiling.** dlib HOG is fast but dated. For higher accuracy, the
   dlib CNN detector or a modern ArcFace embedder (likely what `fdetect` provides) will
   outperform it — and that is precisely where **cosine** becomes the right metric.

---

## 6. Action checklist

- [ ] Add `distance_metric` (+ optional `normalize`) capability to `BaseEncoder`.
- [ ] Default `FaceRecognitionEncoder` → `"euclidean"`; confirm `fdetect` model family and set accordingly.
- [ ] Route `_encodings_match()` (strategies) and `_match_any()` (onboarding) through
      `encoder.calculate_distance()` so both layers share one metric.
- [ ] Keep the existing `0.5`/`0.6` thresholds for the dlib/Euclidean path (do **not**
      reuse them as-is for a cosine path — re-calibrate).
- [ ] (Recommended) Build a labelled pair set and calibrate thresholds via ROC/EER.
- [ ] (Recommended) Emit the raw min-distance/score in the result payload for observability.

---

## Appendix — quick reference

**Euclidean distance** (current): `d = ‖a − b‖₂`, match if `d ≤ tolerance`. Smaller = more similar.

**Cosine similarity:** `s = (a·b) / (‖a‖‖b‖)` ∈ [−1, 1]. Larger = more similar.
**Cosine distance:** `1 − s` ∈ [0, 2]. Smaller = more similar.

**Equivalence (unit vectors):** `‖a − b‖² = 2(1 − cosθ)` → identical ranking after L2-normalization.
