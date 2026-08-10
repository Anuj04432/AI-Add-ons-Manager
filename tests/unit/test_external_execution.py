"""Unit tests for Phase 5B.2 secure external package execution."""

import subprocess
from unittest.mock import MagicMock

import pytest

from aiaddons.core.exceptions import (
    ExecutableNotFoundError,
    ProcessTimeoutError,
    SecurityValidationError,
)
from aiaddons.core.execution.external.models import ExternalExecutionRequest, ExternalRuntime
from aiaddons.core.execution.external.runner import ExternalRunner
from aiaddons.core.execution.external.runtimes import get_runtime_adapter
from aiaddons.core.execution.external.security import (
    mask_secrets_in_text,
    validate_argument_vector,
    validate_environment_dict,
    validate_runtime_name,
)


def test_validate_runtime_name_allowlist() -> None:
    """Verify runtime allowlist enforcement."""
    assert validate_runtime_name("npx") == ExternalRuntime.NPX
    assert validate_runtime_name("uvx") == ExternalRuntime.UVX
    assert validate_runtime_name("pip") == ExternalRuntime.PIP

    with pytest.raises(SecurityValidationError, match="not in the approved"):
        validate_runtime_name("malicious_runtime")

    with pytest.raises(SecurityValidationError):
        validate_runtime_name("bash")


def test_validate_argument_vector_shell_and_null_bytes() -> None:
    """Verify argument vector validation rejects shell metacharacters and null bytes."""
    valid_args = ["install", "@scope/package@1.0.0", "--no-deps"]
    assert validate_argument_vector(valid_args) == valid_args

    # Shell operators
    with pytest.raises(SecurityValidationError, match="Shell operator"):
        validate_argument_vector(["pkg; rm -rf /"])

    with pytest.raises(SecurityValidationError, match="Shell operator"):
        validate_argument_vector(["pkg && echo evil"])

    with pytest.raises(SecurityValidationError, match="Shell operator"):
        validate_argument_vector(["pkg | cat /etc/passwd"])

    with pytest.raises(SecurityValidationError, match="Shell operator"):
        validate_argument_vector(["$(whoami)"])

    # Null byte
    with pytest.raises(SecurityValidationError, match="Null byte"):
        validate_argument_vector(["pkg\0null"])

    # Forbidden evaluation flags
    with pytest.raises(SecurityValidationError, match="Forbidden flag"):
        validate_argument_vector(["--eval", "process.exit(1)"])

    with pytest.raises(SecurityValidationError, match="Forbidden inline code"):
        validate_argument_vector(["--eval=process.exit(1)"])


def test_validate_environment_dict_security() -> None:
    """Verify environment variable sanitization blocking dangerous overrides."""
    valid_env = {"MY_CUSTOM_VAR": "value", "API_KEY": "secret123"}
    assert validate_environment_dict(valid_env) == valid_env

    # Prohibited dangerous variables
    dangerous_vars = ["LD_PRELOAD", "PYTHONPATH", "NODE_OPTIONS", "PATH", "DYLD_INSERT_LIBRARIES"]
    for var in dangerous_vars:
        with pytest.raises(SecurityValidationError, match="is restricted for security reasons"):
            validate_environment_dict({var: "evil"})


def test_mask_secrets_in_text() -> None:
    """Verify masking secret token values in text output."""
    raw = "Failed to connect using token ghp_1234567890secret to server"
    masked = mask_secrets_in_text(raw, ["ghp_1234567890secret"])
    assert "ghp_1234567890secret" not in masked
    assert "***MASKED***" in masked


def test_runtime_adapters_argument_construction() -> None:
    """Verify typed runtime adapters construct valid argument vectors."""
    npx_adapter = get_runtime_adapter(ExternalRuntime.NPX)
    npx_req = ExternalExecutionRequest(
        runtime=ExternalRuntime.NPX,
        package_name="@modelcontextprotocol/server-github",
        package_version="1.2.0",
    )
    npx_vec = npx_adapter.build_command_vector(npx_req)
    assert npx_vec == ["npx", "-y", "@modelcontextprotocol/server-github@1.2.0"]

    pip_adapter = get_runtime_adapter(ExternalRuntime.PIP)
    pip_req = ExternalExecutionRequest(
        runtime=ExternalRuntime.PIP,
        package_name="mcp-server-postgres",
    )
    pip_vec = pip_adapter.build_command_vector(pip_req)
    assert pip_vec == ["pip", "install", "--no-deps", "mcp-server-postgres"]

    git_adapter = get_runtime_adapter(ExternalRuntime.GIT)
    git_req = ExternalExecutionRequest(
        runtime=ExternalRuntime.GIT,
        args=["https://github.com/example/repo.git", "skills/repo"],
    )
    git_vec = git_adapter.build_command_vector(git_req)
    assert git_vec == [
        "git", "clone", "--depth", "1", "https://github.com/example/repo.git", "skills/repo"
    ]


def test_external_runner_resolve_executable_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify ExecutableNotFoundError when binary is not on PATH."""
    monkeypatch.setattr("shutil.which", lambda name: None)
    runner = ExternalRunner()
    with pytest.raises(ExecutableNotFoundError, match="not found on system PATH"):
        runner.resolve_executable(ExternalRuntime.NPX)


def test_external_runner_resolve_unapproved_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify rejection if resolved binary is unapproved."""
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/malicious_tool")
    runner = ExternalRunner()
    with pytest.raises(SecurityValidationError, match="not in the approved"):
        runner.resolve_executable(ExternalRuntime.NPX)


def test_external_runner_dry_run_zero_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that dry-run mode executes zero subprocess calls."""
    mock_popen = MagicMock()
    monkeypatch.setattr("subprocess.Popen", mock_popen)
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")

    runner = ExternalRunner()
    req = ExternalExecutionRequest(
        runtime=ExternalRuntime.NPX,
        package_name="@modelcontextprotocol/server-github",
    )

    res = runner.execute(req, dry_run=True)

    assert res.success is True
    assert res.return_code == 0
    assert "[DRY-RUN] Would execute:" in res.stdout
    mock_popen.assert_not_called()


def test_external_runner_successful_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify process execution with stdout capture and return code 0."""
    mock_proc = MagicMock()
    mock_proc.communicate.return_value = ("Success output", "")
    mock_proc.returncode = 0
    monkeypatch.setattr("subprocess.Popen", lambda *args, **kwargs: mock_proc)
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")

    runner = ExternalRunner()
    req = ExternalExecutionRequest(
        runtime=ExternalRuntime.NPX,
        package_name="@test/pkg",
    )
    res = runner.execute(req, dry_run=False)

    assert res.success is True
    assert res.return_code == 0
    assert "Success output" in res.stdout


def test_external_runner_timeout_enforcement(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify ProcessTimeoutError when process exceeds timeout."""
    mock_proc = MagicMock()
    mock_proc.communicate.side_effect = subprocess.TimeoutExpired(cmd="npx", timeout=1.0)
    monkeypatch.setattr("subprocess.Popen", lambda *args, **kwargs: mock_proc)
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")

    runner = ExternalRunner()
    req = ExternalExecutionRequest(
        runtime=ExternalRuntime.NPX,
        package_name="@test/pkg",
        timeout=1.0,
    )

    with pytest.raises(ProcessTimeoutError, match="timed out after"):
        runner.execute(req, dry_run=False)
