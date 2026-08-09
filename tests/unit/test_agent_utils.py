"""Unit tests for agent detection utility functions."""

import subprocess
from unittest.mock import MagicMock

import pytest

from aiaddons.agents.utils import find_executable, parse_version_output, run_version_command


def test_parse_version_output_standard() -> None:
    """Test parsing standard SemVer version string."""
    assert parse_version_output("0.2.1") == "0.2.1"
    assert parse_version_output("claude/0.2.1 darwin-arm64") == "0.2.1"
    assert parse_version_output("codex version v1.4.0 (build 123)") == "1.4.0"


def test_parse_version_output_prerelease() -> None:
    """Test parsing prerelease SemVer string."""
    assert parse_version_output("1.0.0-alpha.1") == "1.0.0-alpha.1"


def test_parse_version_output_malformed() -> None:
    """Test parsing malformed output returns None."""
    assert parse_version_output("") is None
    assert parse_version_output("Error: binary not found") is None
    assert parse_version_output("command failed with code 1") is None


def test_find_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test find_executable with mocked shutil.which."""
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/claude" if cmd == "claude" else None)

    exec_path = find_executable("claude")
    assert exec_path is not None
    assert str(exec_path).replace("\\", "/").endswith("usr/bin/claude")

    assert find_executable("nonexistent") is None


def test_run_version_command_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test run_version_command when binary exists and outputs version."""
    monkeypatch.setattr("shutil.which", lambda cmd: "/bin/claude")

    mock_res = MagicMock()
    mock_res.returncode = 0
    mock_res.stdout = "claude version 0.5.2\n"
    mock_res.stderr = ""
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: mock_res)

    version = run_version_command("claude")
    assert version == "0.5.2"


def test_run_version_command_missing_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test run_version_command when binary does not exist."""
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    assert run_version_command("missing") is None


def test_run_version_command_subprocess_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test run_version_command handling subprocess exception gracefully."""
    monkeypatch.setattr("shutil.which", lambda cmd: "/bin/claude")

    def mock_raise(*args: object, **kwargs: object) -> MagicMock:
        raise subprocess.SubprocessError("Subprocess failed")

    monkeypatch.setattr("subprocess.run", mock_raise)
    assert run_version_command("claude") is None
