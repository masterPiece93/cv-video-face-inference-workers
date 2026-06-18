"""Unit tests for local MinIO Docker server bootstrap."""
from pathlib import Path
from unittest.mock import patch

import pytest

from common.services.cloud.minio.server import (
    MinioServerConfig,
    _parse_env_file,
    build_config,
    build_docker_command,
)


def test_parse_env_file_reads_key_value_pairs(tmp_path: Path) -> None:
    env = tmp_path / ".env.minio"
    env.write_text(
        "# comment\n"
        "MINIO_ROOT_USER=test-user\n"
        "MINIO_ROOT_PASSWORD=secret\n"
        "MINIO_API_PORT=9999\n",
        encoding="utf-8",
    )

    parsed = _parse_env_file(env)

    assert parsed["MINIO_ROOT_USER"] == "test-user"
    assert parsed["MINIO_ROOT_PASSWORD"] == "secret"
    assert parsed["MINIO_API_PORT"] == "9999"


def test_build_config_supports_access_key_aliases(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.minio"
    env_file.write_text("", encoding="utf-8")

    cfg = build_config(
        {
            "MINIO_ACCESS_KEY": "abc",
            "MINIO_SECRET_KEY": "xyz",
        },
        env_file,
    )

    assert cfg.root_user == "abc"
    assert cfg.root_password == "xyz"
    assert cfg.api_port == 9000
    assert cfg.console_port == 9001


def test_build_config_requires_credentials(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.minio"
    env_file.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="MINIO_ROOT_USER"):
        build_config({}, env_file)


def test_build_docker_command_contains_expected_flags(tmp_path: Path) -> None:
    cfg = MinioServerConfig(
        image="minio/minio:latest",
        container_name="minio-local",
        root_user="user",
        root_password="pass",
        api_port=9000,
        console_port=9001,
        data_dir=tmp_path / "data",
        network_name="my-net",
        recreate=True,
        secure=False,
    )

    cmd = build_docker_command(cfg)

    assert cmd[:3] == ["docker", "run", "-d"]
    assert "--network" in cmd
    assert "my-net" in cmd
    assert "MINIO_ROOT_USER=user" in cmd
    assert "MINIO_ROOT_PASSWORD=pass" in cmd


def test_start_minio_server_dry_run_prints_summary(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.minio"
    env_file.write_text(
        "MINIO_ROOT_USER=admin\n"
        "MINIO_ROOT_PASSWORD=password123\n",
        encoding="utf-8",
    )

    with patch("builtins.print") as mock_print:
        from common.services.cloud.minio.server import start_minio_server

        cfg = start_minio_server(env_file, dry_run=True)

    assert cfg.container_name == "minio-local"
    output = "\n".join(str(c.args[0]) for c in mock_print.call_args_list if c.args)
    assert "STORAGE_PROVIDER=minio" in output
    assert "Dry run enabled" in output
