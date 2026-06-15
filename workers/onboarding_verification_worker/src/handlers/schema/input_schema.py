"""Input schema for Onboarding Verification Worker."""
from typing import Optional, Callable

from common.utils.validators import DictValidator


class OnboardingInputSchema(DictValidator):
    """Validates the incoming Pub/Sub message payload.

    Field notes
    -----------
    onboarding_reference_path
        Accepts **any** of the following three formats:

        * **Relative GCS blob prefix** – a path relative to the candidate base
          path that contains one or more reference images, e.g.
          ``"onboarding_reference/"`` or ``"onboarding_reference"``.
          All ``.jpg``, ``.jpeg``, and ``.png`` blobs under that prefix
          will be downloaded and encoded.

        * **Absolute GCS URI** – a ``gs://`` URI pointing to a (potentially
          different) bucket.  Two sub-forms are supported:

          - *Single image file*: ``"gs://other-bucket/photos/alice.jpg"``
            The blob is downloaded directly.
          - *Blob prefix (folder)*: ``"gs://other-bucket/photos/alice/"``
            All ``.jpg``/``.jpeg``/``.png`` blobs under the prefix are
            listed and downloaded.

        * **Signed HTTPS URL** – a pre-signed URL pointing directly to a
          single reference image, e.g.
          ``"https://storage.googleapis.com/bucket/path/photo.jpg?X-Goog-Signature=..."``.
          The URL is recognised by its ``https://`` (or ``http://``) prefix and
          is downloaded directly without any GCS listing.
    """

    ALLOWED_EXTRA_KEYS = True
    VALIDATION_SPECIFICATION = {
        "candidate_email":           (True,  str,  None),
        "candidate_uid":             (True,  str,  None),
        "org_id":                    (True,  str,  None),
        "org_alias":                 (True,  str,  None),
        "bucket_name":               (True,  str,  None),
        "event_id":                  (True,  str,  None),
        "lookup_map":                (True,  dict, None),
        # Accepts a GCS blob prefix OR a signed HTTPS/HTTP URL (see class docstring)
        "onboarding_reference_path": (True,  str,  None),
        "extra_info":                (False, (dict, type(None)), None),
    }

    def validate(self, json_payload, logger_func=None, message_wrapper=None):
        return super().validate(json_payload, logger_func, message_wrapper)
