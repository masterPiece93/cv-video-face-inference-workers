"""Settings module for Face Verification Worker."""
import os
from typing import Final, Annotated, Optional, Literal

from pydantic import Field, AfterValidator
from pydantic_settings import BaseSettings

from common.utils.setting_utilities import Validators, CommonMeta


DEFAULT_SERVICE_NAME: Final[str] = "Face Verification Worker"
_DEFAULT_ENV_FILE: Final[str] = os.path.join(os.path.dirname(__file__), "envs/.env")


class IngestProcessSettings(BaseSettings):
    """Control & Configuration Options for Ingestion Process."""

    flow_max_messages: Annotated[int, Field(gt=0)] = 10
    flow_max_lease_duration: Annotated[int, Field(gt=0)] = 300
    max_workers: Annotated[int, Field(gt=0)] = 4


class GcpSettings(BaseSettings):
    """GCP Resources Specifications."""

    sa_path: Optional[str] = None
    project_id: str
    pubsub_ingestion_subscription: str
    pubsub_ingestion_topic: str
    pubsub_egestion_topic: str
    bucket: str
    dlq_topic: Optional[str] = None

    class Config(CommonMeta):
        env_prefix = "GCP__"


class Settings(BaseSettings):
    """Module Settings for Face Verification Worker.

    - service_name (str): service name
    - gcp (GcpSettings): GCP resource settings
    - ingest (IngestProcessSettings): ingestion process settings
    - verification_strategy (str): which strategy to apply
    - encoder_backend (str): optional in-house encoder backend (for image encoding)
    - fdetect_channel (str): gRPC channel for fdetect
    - face_tolerance (float): match distance threshold
    - min_common_faces (int): min common faces to consider a match
    - log_format / log_level / debug_mode

    Tag:[settings|core]
    """

    service_name: Annotated[
        str, AfterValidator(Validators.not_empty)
    ] = DEFAULT_SERVICE_NAME
    gcp: GcpSettings
    ingest: IngestProcessSettings = IngestProcessSettings()

    # Verification strategy
    verification_strategy: Literal[
        "common_faces",           # count common faces across all videos
        "with_profile",           # verify against profile video
        "profile_match_all",      # profile matches every interview
        "any_common",             # just detect if common person exists
    ] = "with_profile"
    min_common_faces: Annotated[int, Field(ge=0)] = 1
    face_tolerance: Annotated[float, Field(gt=0.0, le=1.0)] = 0.6

    # Optional encoder (for on-demand image encoding within verification)
    encoder_backend: Optional[Literal["face_recognition", "fdetect"]] = None
    fdetect_channel: Optional[str] = None
    encoding_model: Literal["hog", "cnn"] = "hog"
    num_jitters: Annotated[int, Field(ge=1)] = 1

    # Logging
    debug_mode: bool = False
    log_format: Literal["text", "json"] = "text"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    class Config(CommonMeta):
        env_file = _DEFAULT_ENV_FILE
        env_file_encoding = "utf-8"
        env_nested_delimiter = "__"
        extra = "ignore"
