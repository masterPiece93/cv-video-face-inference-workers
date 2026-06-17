"""Main entry point for Face Encoding Worker."""
import os
import sys
from enum import Enum
from pathlib import Path
from typing import Any, Final, cast

# Add paths to sys.path so that 'common' package is importable in all environments:
#   - Docker (common/ copied alongside main.py in /app)
#   - Local development (common/ at project root, 2 levels up)
_SCRIPT_DIR = Path(__file__).resolve().parent
_CANDIDATE_PATHS = [_SCRIPT_DIR]

try:
    _CANDIDATE_PATHS.append(_SCRIPT_DIR.parents[1])
except IndexError:
    pass

for _path in _CANDIDATE_PATHS:
    _p = str(_path)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import logging

from common.services.cloud.gcp.pubsub import GCPPublisher, GCPSubscriber
from common.services.cloud.storage import get_storage_service
from common.services.encoding import get_encoder
from common.services.logging_service import get_logger
from workers.face_encoding_worker.services.encoding import VideoFaceEncodingService
from workers.face_encoding_worker.settings import Settings
from workers.face_encoding_worker.src.handlers.input_handler import handle_message
from workers.face_encoding_worker.src.handlers.output_handler import EncodingOutputHandler


# ── Environment name → env file resolution ────────────────────────────────────

class EnvName(str, Enum):
    """Supported environment names.

    Set via the ENV_NAME environment variable before starting the worker.
    Each value maps to a corresponding envs/.env.<name> file.

    Examples
    --------
    ENV_NAME=local  →  envs/.env.local   (Pub/Sub emulator + fake-gcs)
    ENV_NAME=dev    →  envs/.env.dev
    ENV_NAME=qa     →  envs/.env.qa
    ENV_NAME=prod   →  envs/.env.prod
    (unset)         →  envs/.env         (default)
    """
    local       = "local"
    dev         = "dev"
    qa          = "qa"
    stage       = "stage"
    prod        = "prod"
    unspecified = ""


_ENVS_DIR: Final[Path] = Path(__file__).resolve().parent / "envs"

ENV_NAME: Final[EnvName] = EnvName[
    os.environ.get("ENV_NAME", "unspecified").lower()
]

match ENV_NAME:
    case EnvName.unspecified:
        ENV_FILE = str(_ENVS_DIR / ".env")
    case _:
        ENV_FILE = str(_ENVS_DIR / f".env.{ENV_NAME.value}")


def create_app(settings: Settings) -> tuple:
    """Wire up and return (subscriber, encoding_service).

    Args:
        settings: Loaded worker settings.

    Returns:
        Tuple of (GCPSubscriber, VideoFaceEncodingService).
    """
    sa_path = str(settings.gcp.sa_path) if settings.gcp.sa_path else None

    # Storage (provider selected by STORAGE_PROVIDER env var; default=gcp)
    storage = get_storage_service(sa_path=sa_path)

    # Encoder — pluggable backend
    if settings.encoder_backend == "fdetect":
        if not settings.fdetect_channel:
            raise ValueError("FDETECT_CHANNEL must be set when encoder_backend=fdetect")
        encoder = get_encoder("fdetect", channel_address=settings.fdetect_channel)
        # Pattern 4: Fail fast — verify fdetect is reachable at startup
        if not cast(Any, encoder).ping():
            raise RuntimeError(
                f"fdetect gRPC service at {settings.fdetect_channel} is unreachable. "
                "Application will not start."
            )
    else:
        encoder = get_encoder(
            "face_recognition",
            model=settings.encoding_model,
            num_jitters=settings.num_jitters,
        )
    
    # Publisher (downstream → verification worker)
    publisher = GCPPublisher(
        project_id=settings.gcp.project_id,
        topic_name=settings.gcp.pubsub_egestion_topic,
        sa_path=sa_path,
    )
    output_handler = EncodingOutputHandler(publisher)

    # Encoding service
    encoding_service = VideoFaceEncodingService(
        encoder=encoder,
        storage=storage,
        output_handler=output_handler,
        frame_sample_rate=settings.frame_sample_rate,
        face_tolerance=settings.face_tolerance,
        max_workers=settings.ingest.max_workers,
    )

    # Subscriber
    subscriber = GCPSubscriber(
        project_id=settings.gcp.project_id,
        subscription_name=settings.gcp.pubsub_ingestion_subscription,
        topic_name=settings.gcp.pubsub_ingestion_topic,
        max_messages=settings.ingest.flow_max_messages,
        max_lease_duration=settings.ingest.flow_max_lease_duration,
        dlq_topic=settings.gcp.dlq_topic,
        sa_path=sa_path,
    )

    return subscriber, encoding_service


def main() -> None:
    """Start the Face Encoding Worker subscriber loop."""
    # Guard: fail fast with a clear message if the env file is missing.
    if not Path(ENV_FILE).exists():
        sys.exit(
            f"\n[ERROR] Env file not found: {ENV_FILE}\n"
            f"  Set ENV_NAME to one of the files in envs/ before running.\n"
            f"  Example:  ENV_NAME=local python3 main.py\n"
            f"  Available: {sorted(p.name for p in _ENVS_DIR.glob('.env*'))}\n"
        )

    # Load the resolved env file into os.environ *before* any Google SDK client
    # is constructed — the SDK reads PUBSUB_EMULATOR_HOST / STORAGE_EMULATOR_HOST
    # directly from os.environ at construction time, not from pydantic settings.
    from dotenv import load_dotenv
    load_dotenv(ENV_FILE, override=True)

    settings = Settings()  # type: ignore[call-arg]

    logger = get_logger(
        settings.service_name,
        log_format=settings.log_format,
        level=getattr(logging, settings.log_level),
        log_file=settings.log_file or None,
    )

    # Propagate the same level + handlers to the root logger so that child
    # loggers in common/* and handler modules also write to the log file.
    root = logging.getLogger()
    root.setLevel(getattr(logging, settings.log_level))
    for h in logger.handlers:
        if h not in root.handlers:
            root.addHandler(h)

    logger.info(f"Environment : '{ENV_NAME.value or 'default'}' → {ENV_FILE}")
    logger.info(
        f"Starting {settings.service_name} "
        f"[encoder={settings.encoder_backend}, model={settings.encoding_model}]"
    )

    # ── ADC check ─────────────────────────────────────────────────────────────
    # Check independently for each service — mixed modes are supported:
    #   • PUBSUB_EMULATOR_HOST set   → Pub/Sub uses local emulator (no ADC needed for Pub/Sub)
    #   • STORAGE_EMULATOR_HOST set  → GCS uses fake-gcs (no ADC needed for GCS)
    #   • Neither set                → both use live GCP (ADC required)
    #   • Only PUBSUB set            → mixed: emulator Pub/Sub + live GCS (ADC needed for GCS)
    _pubsub_emulated = bool(os.environ.get("PUBSUB_EMULATOR_HOST"))
    _storage_provider = os.getenv("STORAGE_PROVIDER", "gcp").strip().lower()
    _uses_gcp_storage = _storage_provider in {"gcp", "gcs"}
    _storage_emulated = _uses_gcp_storage and bool(os.environ.get("STORAGE_EMULATOR_HOST"))
    _needs_adc = (not _pubsub_emulated or (_uses_gcp_storage and not _storage_emulated)) and not settings.gcp.sa_path

    if _needs_adc:
        from common.services.cloud.gcp_authenticate import check_gcp_adc_status
        _mode = (
            "live GCS + emulator Pub/Sub" if (_uses_gcp_storage and _pubsub_emulated)
            else "live Pub/Sub + emulator GCS" if _storage_emulated
            else "live Pub/Sub + non-GCP storage" if (not _uses_gcp_storage)
            else "fully live GCP"
        )
        logger.info(f"ADC check  [{_mode}] …")
        if not check_gcp_adc_status(
            logger=lambda msg, level="info": getattr(logger, level)(f"[ADC] {msg}"),
        ):
            logger.critical(
                "ADC is not configured. Run:  gcloud auth application-default login"
            )
            sys.exit(1)
        logger.info(f"[ADC] ✅  Credentials verified  [{_mode}].")

    subscriber, encoding_service = create_app(settings)
    subscriber.ensure_subscription_exists()

    def callback(message):
        handle_message(message, encoding_service)

    logger.info(
        f"Listening on subscription: {settings.gcp.pubsub_ingestion_subscription}"
    )
    logger.info('v3')
    try:
        subscriber.subscribe(callback)
    except Exception as e:
        logger.critical(f"Fatal subscriber error: {e}", exc_info=True)
    finally:
        subscriber.close()
        # deterministic gRPC channel cleanup
        if hasattr(encoding_service.encoder, 'close'):
            encoding_service.encoder.close()
        logger.info(f"{settings.service_name} stopped")


if __name__ == "__main__":
    main()
