"""Main entry point for Face Verification Worker."""
import os
import sys
from enum import Enum
from pathlib import Path
from typing import Final

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
from common.services.cloud.gcp.storage import GCPStorageService
from common.services.logging_service import get_logger
from workers.face_verification_worker.services.strategies import get_strategy
from workers.face_verification_worker.services.verification import VideoFaceVerificationService
from workers.face_verification_worker.settings import Settings
from workers.face_verification_worker.src.handlers.input_handler import handle_message
from workers.face_verification_worker.src.handlers.output_handler import VerificationOutputHandler


# ── Environment name → env file resolution ────────────────────────────────────

class EnvName(str, Enum):
    """Supported environment names.

    Set via the ENV_NAME environment variable before starting the worker.
    Each value maps to a corresponding envs/.env.<name> file.

    Examples
    --------
    ENV_NAME=local  →  envs/.env.local   (Pub/Sub emulator + fake-gcs)
    ENV_NAME=dev    →  envs/.env.dev
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
        ENV_FILE: Final[str] = str(_ENVS_DIR / ".env")
    case _:
        ENV_FILE: Final[str] = str(_ENVS_DIR / f".env.{ENV_NAME.value}")


def create_app(settings: Settings) -> tuple:
    sa_path = str(settings.gcp.sa_path) if settings.gcp.sa_path else None
    storage = GCPStorageService(sa_path=sa_path)

    publisher = GCPPublisher(
        project_id=settings.gcp.project_id,
        topic_name=settings.gcp.pubsub_egestion_topic,
        sa_path=sa_path,
    )
    output_handler = VerificationOutputHandler(publisher)

    strategy = get_strategy(
        settings.verification_strategy,
        tolerance=settings.face_tolerance,
    )

    verification_service = VideoFaceVerificationService(
        strategy=strategy,
        storage=storage,
        output_handler=output_handler,
    )

    subscriber = GCPSubscriber(
        project_id=settings.gcp.project_id,
        subscription_name=settings.gcp.pubsub_ingestion_subscription,
        topic_name=settings.gcp.pubsub_ingestion_topic,
        max_messages=settings.ingest.flow_max_messages,
        max_lease_duration=settings.ingest.flow_max_lease_duration,
        dlq_topic=settings.gcp.dlq_topic,
        sa_path=sa_path,
    )

    return subscriber, verification_service


def main() -> None:
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

    settings = Settings(_env_file=ENV_FILE)

    logger = get_logger(
        settings.service_name,
        log_format=settings.log_format,
        level=getattr(logging, settings.log_level),
    )

    root = logging.getLogger()
    root.setLevel(getattr(logging, settings.log_level))
    for h in logger.handlers:
        if h not in root.handlers:
            root.addHandler(h)

    logger.info(f"Environment : '{ENV_NAME.value or 'default'}' → {ENV_FILE}")
    logger.info(
        f"Starting {settings.service_name} "
        f"[strategy={settings.verification_strategy}]"
    )

    subscriber, verification_service = create_app(settings)
    subscriber.ensure_subscription_exists()

    def callback(message):
        handle_message(message, verification_service)

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
        logger.info(f"{settings.service_name} stopped")


if __name__ == "__main__":
    main()
