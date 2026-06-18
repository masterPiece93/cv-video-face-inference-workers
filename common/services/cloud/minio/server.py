"""Utilities to run a local MinIO server in Docker using .env configuration."""
from __future__ import annotations

import argparse
import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class MinioServerConfig:
    """Runtime configuration for local MinIO Docker container."""

    image: str
    container_name: str
    root_user: str
    root_password: str
    api_port: int
    console_port: int
    data_dir: Path
    network_name: str | None
    recreate: bool
    secure: bool

    @property
    def endpoint(self) -> str:
        return f"localhost:{self.api_port}"

    @property
    def api_url(self) -> str:
        scheme = "https" if self.secure else "http"
        return f"{scheme}://{self.endpoint}"

    @property
    def console_url(self) -> str:
        scheme = "https" if self.secure else "http"
        return f"{scheme}://localhost:{self.console_port}"


def _parse_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_env_file(env_file: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines from env file.

    Supports comments and quoted values.
    """
    if not env_file.exists():
        raise FileNotFoundError(f"Env file not found: {env_file}")

    parsed: dict[str, str] = {}
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        parsed[key] = value
    return parsed


def _merged_env(env_file: Path) -> dict[str, str]:
    merged = dict(os.environ)
    merged.update(_parse_env_file(env_file))
    return merged


def _resolve_data_dir(raw: str, env_file: Path) -> Path:
    p = Path(raw)
    if p.is_absolute():
        return p
    return (env_file.parent / p).resolve()


def build_config(values: Mapping[str, str], env_file: Path) -> MinioServerConfig:
    """Build validated MinIO server config from env values."""

    root_user = values.get("MINIO_ROOT_USER") or values.get("MINIO_ACCESS_KEY")
    root_password = values.get("MINIO_ROOT_PASSWORD") or values.get("MINIO_SECRET_KEY")

    if not root_user:
        raise ValueError("MINIO_ROOT_USER (or MINIO_ACCESS_KEY) is required")
    if not root_password:
        raise ValueError("MINIO_ROOT_PASSWORD (or MINIO_SECRET_KEY) is required")

    api_port = int(values.get("MINIO_API_PORT", "9000"))
    console_port = int(values.get("MINIO_CONSOLE_PORT", "9001"))
    if api_port == console_port:
        raise ValueError("MINIO_API_PORT and MINIO_CONSOLE_PORT must be different")

    data_dir = _resolve_data_dir(values.get("MINIO_DATA_DIR", ".minio-data"), env_file)

    return MinioServerConfig(
        image=values.get("MINIO_IMAGE", "minio/minio:latest"),
        container_name=values.get("MINIO_CONTAINER_NAME", "minio-local"),
        root_user=root_user,
        root_password=root_password,
        api_port=api_port,
        console_port=console_port,
        data_dir=data_dir,
        network_name=values.get("MINIO_DOCKER_NETWORK") or None,
        recreate=_parse_bool(values.get("MINIO_RECREATE"), default=True),
        secure=_parse_bool(values.get("MINIO_SECURE"), default=False),
    )


def _mask_secret(secret: str) -> str:
    if len(secret) <= 4:
        return "*" * len(secret)
    return f"{secret[:2]}{'*' * (len(secret) - 4)}{secret[-2:]}"


def build_docker_command(config: MinioServerConfig) -> list[str]:
    cmd = [
        "docker",
        "run",
        "-d",
        "--name",
        config.container_name,
        "-p",
        f"{config.api_port}:9000",
        "-p",
        f"{config.console_port}:9001",
        "-e",
        f"MINIO_ROOT_USER={config.root_user}",
        "-e",
        f"MINIO_ROOT_PASSWORD={config.root_password}",
        "-v",
        f"{config.data_dir}:/data",
    ]
    if config.network_name:
        cmd.extend(["--network", config.network_name])
    cmd.extend(
        [
            config.image,
            "server",
            "/data",
            "--console-address",
            ":9001",
        ]
    )
    return cmd


def _run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=check, capture_output=True, text=True)


def _container_exists(name: str) -> bool:
    result = _run([
        "docker",
        "ps",
        "-a",
        "--filter",
        f"name=^/{name}$",
        "--format",
        "{{.Names}}",
    ])
    return name in result.stdout.splitlines()


def _print_summary(config: MinioServerConfig) -> None:
    print("\n=== MinIO Local Server Configuration ===")
    print(f"Container Name : {config.container_name}")
    print(f"Image          : {config.image}")
    print(f"API URL        : {config.api_url}")
    print(f"Console URL    : {config.console_url}")
    print(f"Root User      : {config.root_user}")
    print(f"Root Password  : {_mask_secret(config.root_password)}")
    print(f"Data Dir       : {config.data_dir}")
    print(f"Docker Network : {config.network_name or '(default bridge)'}")
    print(f"Recreate       : {config.recreate}")

    print("\n--- Worker ENV values ---")
    print("STORAGE_PROVIDER=minio")
    print(f"MINIO__ENDPOINT={config.endpoint}")
    print(f"MINIO__ACCESS_KEY={config.root_user}")
    print(f"MINIO__SECRET_KEY={config.root_password}")
    print(f"MINIO__SECURE={'true' if config.secure else 'false'}")
    print("MINIO__REGION=us-east-1")


def _print_command_preview(config: MinioServerConfig) -> None:
    preview_parts = []
    for part in build_docker_command(config):
        if config.root_password in part:
            part = part.replace(config.root_password, _mask_secret(config.root_password))
        preview_parts.append(shlex.quote(part))
    print("\n--- Docker command preview ---")
    print(" ".join(preview_parts))


def start_minio_server(env_file: Path, *, dry_run: bool = False) -> MinioServerConfig:
    """Load env config and start MinIO server container via Docker."""

    values = _merged_env(env_file)
    config = build_config(values, env_file)
    config.data_dir.mkdir(parents=True, exist_ok=True)

    _print_summary(config)
    _print_command_preview(config)

    if dry_run:
        print("\nDry run enabled; container was not started.")
        return config

    _run(["docker", "--version"])

    if _container_exists(config.container_name):
        if config.recreate:
            print(f"\nContainer '{config.container_name}' exists; removing it first...")
            _run(["docker", "rm", "-f", config.container_name])
        else:
            print(
                f"\nContainer '{config.container_name}' already exists and MINIO_RECREATE=false; "
                "leaving as-is."
            )
            return config

    print("\nStarting MinIO container...")
    result = _run(build_docker_command(config))
    print(f"Container started: {result.stdout.strip()}")
    print("MinIO startup complete.")
    return config


def _default_env_file() -> Path:
    return Path(__file__).resolve().parent / ".env.minio"


def main() -> None:
    parser = argparse.ArgumentParser(description="Start local MinIO Docker container from .env settings")
    parser.add_argument(
        "--env-file",
        default=str(_default_env_file()),
        help="Path to MinIO env file (default: common/services/cloud/minio/.env.minio)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print config and docker command without starting container",
    )
    args = parser.parse_args()
    start_minio_server(Path(args.env_file), dry_run=args.dry_run)


if __name__ == "__main__":
    main()
