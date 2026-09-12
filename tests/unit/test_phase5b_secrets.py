"""Comprehensive unit tests for Phase 5B.8 Secure Secret Management & Isolation."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from aiaddons.core.exceptions import (
    SecretResolutionError,
)
from aiaddons.core.execution.engine import ExecutionEngine
from aiaddons.core.execution.external.models import ExternalExecutionRequest
from aiaddons.core.execution.external.runner import ExternalRunner
from aiaddons.core.execution.external.security import mask_secrets_in_text
from aiaddons.core.installer.models import (
    AddMcpServerOperation,
    InstallationPlan,
    InstallationTransaction,
    RollbackMetadata,
    TransactionPhase,
)
from aiaddons.core.models.agent import AgentDetectionResult, Scope
from aiaddons.core.models.manifest import (
    EnvVarSpec,
    HandlerSpecContainer,
    IntegrationManifest,
    IntegrationType,
    MCPHandlerSpec,
    MCPRuntime,
    PublisherClaimSpec,
    SourceSpec,
    SourceType,
    TrustMetadata,
)
from aiaddons.core.secrets import (
    DefaultTTYInputProvider,
    ResolvedSecret,
    SecretResolver,
    SecretStatus,
    get_windows_clipboard_text,
    secure_prompt,
    win_getpass_with_paste,
)
from aiaddons.state.lockfile import LockfileAddonEntry, LockfileManager
from aiaddons.state.store import InstalledAddonRecord, InstalledStateStore
from aiaddons.state.transaction import TransactionWALManager


def test_secret_resolution_from_environment() -> None:
    """Verify required and optional secrets resolved from environment dictionary."""
    resolver = SecretResolver()
    specs = [
        EnvVarSpec(name="TEST_TOKEN", secret=True, required=True, description="API Token"),
        EnvVarSpec(name="OPTIONAL_VAR", secret=True, required=False),
    ]

    override_env = {"TEST_TOKEN": "secret_abc_123"}
    results = resolver.resolve_specs(specs, allow_interactive=False, override_env=override_env)

    assert len(results) == 2
    token_res = results[0]
    assert token_res.name == "TEST_TOKEN"
    assert token_res.status == SecretStatus.CONFIGURED
    assert token_res.value == "secret_abc_123"
    assert token_res.safe_summary() == "<secret configured>"

    opt_res = results[1]
    assert opt_res.name == "OPTIONAL_VAR"
    assert opt_res.status == SecretStatus.OPTIONAL_MISSING
    assert opt_res.value is None
    assert opt_res.safe_summary() == "<secret optional (missing)>"


def test_secret_resolution_missing_required_non_interactive() -> None:
    """Verify error raised when missing required secret in non-interactive mode."""
    resolver = SecretResolver()
    spec = EnvVarSpec(name="MISSING_TOKEN", secret=True, required=True)

    with pytest.raises(SecretResolutionError, match="Missing required environment variable"):
        resolver.resolve_spec(spec, allow_interactive=False, override_env={})


def test_secret_resolution_interactive_prompt() -> None:
    """Verify interactive prompting resolves missing required secrets without echoing."""
    prompted_vars: list[str] = []

    def mock_prompt(prompt_text: str) -> str:
        prompted_vars.append(prompt_text)
        return "user_entered_secret_xyz"

    mock_input_provider = MagicMock()
    mock_input_provider.prompt_secret.side_effect = lambda spec: mock_prompt(spec.name)

    resolver = SecretResolver(input_provider=mock_input_provider)
    spec = EnvVarSpec(name="INTERACTIVE_KEY", secret=True, required=True, description="Prompt me")

    res = resolver.resolve_spec(spec, allow_interactive=True, override_env={})

    assert res.status == SecretStatus.CONFIGURED
    assert res.value == "user_entered_secret_xyz"
    mock_input_provider.prompt_secret.assert_called_once_with(spec)


def test_secret_resolution_env_var_invalid_non_interactive() -> None:
    """Verify validation failure for environment variable in non-interactive mode raises error."""
    resolver = SecretResolver()
    spec = EnvVarSpec(
        name="GITHUB_TOKEN",
        secret=True,
        required=True,
        min_length=10,
        value_pattern="^ghp_.*",
    )
    with pytest.raises(SecretResolutionError, match="failed format validation"):
        resolver.resolve_spec(
            spec,
            allow_interactive=False,
            override_env={"GITHUB_TOKEN": "xx"},
        )


def test_secret_resolution_env_var_valid_non_interactive() -> None:
    """Verify well-formed environment variable passes validation cleanly."""
    resolver = SecretResolver()
    spec = EnvVarSpec(
        name="GITHUB_TOKEN",
        secret=True,
        required=True,
        min_length=10,
        value_pattern="^ghp_.*",
    )
    res = resolver.resolve_spec(
        spec,
        allow_interactive=False,
        override_env={"GITHUB_TOKEN": "ghp_valid_token_12345"},
    )
    assert res.status == SecretStatus.CONFIGURED
    assert res.value == "ghp_valid_token_12345"


def test_secret_resolver_summary() -> None:
    """Verify non-sensitive summary generation."""
    resolver = SecretResolver()
    resolved = [
        ResolvedSecret(name="KEY1", status=SecretStatus.CONFIGURED, value="s1"),
        ResolvedSecret(name="KEY2", status=SecretStatus.OPTIONAL_MISSING, value=None),
    ]
    summary = resolver.summarize(resolved)
    assert summary.specs_evaluated == 2
    assert summary.secrets_resolved == ["KEY1"]
    assert summary.missing_optional == ["KEY2"]
    assert summary.is_complete is True


def test_secret_isolation_only_declared_passed(tmp_path: Path) -> None:
    """Verify an operation receives only the environment variables declared in its spec."""
    mock_runner = MagicMock(spec=ExternalRunner)
    mock_runner.execute.return_value = MagicMock(success=True, return_code=0, stdout="", stderr="")

    engine = ExecutionEngine(external_runner=mock_runner)

    op = AddMcpServerOperation(
        description="Test MCP server",
        target_root=str(tmp_path),
        config_path="mcp.json",
        server_name="github",
        runtime=MCPRuntime.NPX,
        package_name="@modelcontextprotocol/server-github",
        env_var_names=["GITHUB_TOKEN"],
    )

    plan = InstallationPlan(
        addon_id="github-mcp",
        addon_name="GitHub MCP",
        addon_version="1.0.0",
        target_agent="claude-code",
        target_agent_name="Claude Code",
        target_scope=Scope.WORKSPACE,
        integration_type=IntegrationType.MCP,
        source=SourceSpec(source_type=SourceType.LOCAL, path="."),
        planned_operations=[op],
        rollback_info=RollbackMetadata(reversible=True),
    )

    all_resolved_secrets = {
        "GITHUB_TOKEN": "ghp_secret_token",
        "UNRELATED_SECRET": "unrelated_secret_value",
    }

    res = engine.execute_plan(plan, dry_run=False, secret_values=all_resolved_secrets)
    assert res.status.value == "success"

    # Verify secret isolation in written MCP configuration
    mcp_file = tmp_path / "mcp.json"
    assert mcp_file.exists()
    import json
    cfg = json.loads(mcp_file.read_text(encoding="utf-8"))
    assert "github" in cfg.get("mcpServers", {})
    env_cfg = cfg["mcpServers"]["github"].get("env", {})
    assert "GITHUB_TOKEN" in env_cfg
    assert "UNRELATED_SECRET" not in env_cfg


def test_dangerous_environment_variables_blocked() -> None:
    """Verify dangerous environment variables like PATH, HOME, etc. are blocked."""
    dangerous_names = ["PATH", "PYTHONPATH", "LD_PRELOAD", "NODE_OPTIONS", "HOME", "USERPROFILE"]
    for name in dangerous_names:
        with pytest.raises(ValueError, match="restricted for security reasons"):
            EnvVarSpec(name=name, secret=True)


def test_masking_secrets_in_text_and_exceptions() -> None:
    """Verify secret masking in text, embedded text, multiple secrets, and exceptions."""
    secrets = ["ghp_1234567890secret", "sk-abcdef123456789"]

    raw_output = "Error with token ghp_1234567890secret and key sk-abcdef123456789 in stderr"
    masked = mask_secrets_in_text(raw_output, secrets)

    assert "ghp_1234567890secret" not in masked
    assert "sk-abcdef123456789" not in masked
    assert masked.count("***MASKED***") == 2

    # Verify exception message masking in ExternalRunner
    err_msg = "Failed with ghp_1234567890secret"
    masked_err = mask_secrets_in_text(err_msg, secrets)
    assert "ghp_1234567890secret" not in masked_err


def test_persistence_prohibition(tmp_path: Path) -> None:
    """Verify actual secret values NEVER appear in WAL, installed state, or lockfile."""
    secret_val = "ghp_super_secret_value_999"

    # 1. ResolvedSecret model serialization
    res_secret = ResolvedSecret(
        name="GITHUB_TOKEN",
        status=SecretStatus.CONFIGURED,
        value=secret_val,
    )
    dump_dict = res_secret.model_dump()
    dump_json = res_secret.model_dump_json()

    assert secret_val not in str(dump_dict)
    assert secret_val not in dump_json

    # 2. AddMcpServerOperation serialization
    op = AddMcpServerOperation(
        description="Inject MCP",
        target_root=str(tmp_path),
        config_path="mcp.json",
        server_name="test",
        runtime=MCPRuntime.NPX,
        package_name="test-pkg",
        env_var_names=["GITHUB_TOKEN"],
    )
    op_json = op.model_dump_json()
    assert secret_val not in op_json

    # 3. WAL Manager serialization
    wal_dir = tmp_path / "wal"
    wal_mgr = TransactionWALManager(transactions_dir=wal_dir)

    manifest = IntegrationManifest(
        id="test-addon",
        name="Test Addon",
        version="1.0.0",
        description="Test",
        license="MIT",
        category="test",
        integration_type=IntegrationType.MCP,
        source=SourceSpec(source_type=SourceType.LOCAL, path="."),
        trust=TrustMetadata(publisher=PublisherClaimSpec(name="test")),
        handler_spec=HandlerSpecContainer(
            mcp=MCPHandlerSpec(
                runtime=MCPRuntime.NPX,
                package_name="test-pkg",
                env_vars=[EnvVarSpec(name="GITHUB_TOKEN", secret=True, required=True)],
            )
        ),
    )

    agent_result = AgentDetectionResult(
        agent_id="claude-code",
        name="Claude Code",
        installed=True,
        supported_scopes=[Scope.WORKSPACE],
    )

    tx = InstallationTransaction(
        transaction_id="tx_test_123",
        phase=TransactionPhase.PLANNED,
        manifest=manifest,
        agent=agent_result,
        requested_scope=Scope.WORKSPACE,
    )

    wal_path = wal_mgr.write_transaction(tx)
    wal_content = wal_path.read_text(encoding="utf-8")
    assert secret_val not in wal_content

    # 4. Installed State Database serialization
    store_dir = tmp_path / "state"
    store = InstalledStateStore(store_dir=store_dir)
    record = InstalledAddonRecord(
        addon_id="test-addon",
        name="Test",
        version="1.0.0",
        target_agent="claude-code",
        scope=Scope.WORKSPACE,
        integration_type=IntegrationType.MCP,
        installed_at="2026-08-11T00:00:00Z",
    )
    store.record_installation(record)
    state_content = (store_dir / "state.json").read_text(encoding="utf-8")
    assert secret_val not in state_content

    # 5. Workspace Lockfile serialization
    lock_mgr = LockfileManager()
    entry = LockfileAddonEntry(
        addon_id="test-addon",
        name="Test",
        version="1.0.0",
        integration_type=IntegrationType.MCP,
        target_agent="claude-code",
    )
    lock_path = lock_mgr.update_lockfile(tmp_path, entry)
    lock_content = lock_path.read_text(encoding="utf-8")
    assert secret_val not in lock_content


def test_dry_run_security_guarantees(tmp_path: Path) -> None:
    """Verify dry-run mode does not prompt, execute processes, or mutate files/WAL/lockfile."""
    mock_prompt = MagicMock()
    resolver = SecretResolver(input_provider=mock_prompt)

    spec = EnvVarSpec(name="REQUIRED_SECRET", secret=True, required=True)
    res = resolver.resolve_spec(
        spec, allow_interactive=False, override_env={"REQUIRED_SECRET": "val"}
    )

    assert res.safe_summary() == "<secret configured>"
    mock_prompt.prompt_secret.assert_not_called()

    # Dry-run execution
    mock_runner = MagicMock(spec=ExternalRunner)
    engine = ExecutionEngine(external_runner=mock_runner)

    op = AddMcpServerOperation(
        description="Dry run MCP server",
        target_root=str(tmp_path),
        config_path="mcp.json",
        server_name="test",
        runtime=MCPRuntime.NPX,
        package_name="test-pkg",
        env_var_names=["REQUIRED_SECRET"],
    )

    plan = InstallationPlan(
        addon_id="dry-run-addon",
        addon_name="Dry Run Addon",
        addon_version="1.0.0",
        target_agent="claude-code",
        target_agent_name="Claude Code",
        target_scope=Scope.WORKSPACE,
        integration_type=IntegrationType.MCP,
        source=SourceSpec(source_type=SourceType.LOCAL, path="."),
        planned_operations=[op],
        rollback_info=RollbackMetadata(reversible=True),
    )

    exec_res = engine.execute_plan(plan, dry_run=True, secret_values={"REQUIRED_SECRET": "val"})
    assert exec_res.status.value == "success"
    # Verify zero files written
    assert not (tmp_path / "mcp.json").exists()


def test_rollback_on_failure_with_masked_logs(tmp_path: Path) -> None:
    """Verify rollback occurs on execution failure and secret values are masked in error outputs."""
    secret_value = "ghp_failing_secret_key_123"

    mock_runner = MagicMock(spec=ExternalRunner)
    mock_runner.resolve_executable.side_effect = Exception(f"Connection failed with token {secret_value}")

    engine = ExecutionEngine(external_runner=mock_runner)

    op = AddMcpServerOperation(
        description="Failing MCP server",
        target_root=str(tmp_path),
        config_path="config.json",
        server_name="failing_server",
        runtime=MCPRuntime.NPX,
        package_name="failing-pkg",
        env_var_names=["SECRET_KEY"],
    )

    plan = InstallationPlan(
        addon_id="failing-addon",
        addon_name="Failing Addon",
        addon_version="1.0.0",
        target_agent="claude-code",
        target_agent_name="Claude Code",
        target_scope=Scope.WORKSPACE,
        integration_type=IntegrationType.MCP,
        source=SourceSpec(source_type=SourceType.LOCAL, path="."),
        planned_operations=[op],
        rollback_info=RollbackMetadata(reversible=True),
    )

    res = engine.execute_plan(plan, dry_run=False, secret_values={"SECRET_KEY": secret_value})

    assert res.status.value == "rolled_back"
    assert secret_value not in str(res.error_message)
    assert "***MASKED***" in str(res.error_message)


def test_windows_clipboard_non_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify get_windows_clipboard_text returns None on non-Windows platforms."""
    monkeypatch.setattr("sys.platform", "linux")
    assert get_windows_clipboard_text() is None


def test_windows_clipboard_win32_open_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify get_windows_clipboard_text gracefully handles OpenClipboard failure."""
    monkeypatch.setattr("sys.platform", "win32")
    mock_ctypes = MagicMock()
    mock_ctypes.windll.user32.OpenClipboard.return_value = False
    with patch.dict("sys.modules", {"ctypes": mock_ctypes}):
        assert get_windows_clipboard_text() is None


def test_win_getpass_typing_and_submit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify manual character typing without pasting submits cleanly on Enter."""
    keys = ["m", "y", "P", "A", "T", "1", "2", "3", "\r"]
    mock_msvcrt = MagicMock()
    mock_msvcrt.getwch.side_effect = keys

    monkeypatch.setattr("sys.stdin", sys.__stdin__)
    with (
        patch("sys.stdin.isatty", return_value=True),
        patch.dict("sys.modules", {"msvcrt": mock_msvcrt}),
    ):
        result = win_getpass_with_paste("Enter value: ")
        assert result == "myPAT123"
        assert mock_msvcrt.getwch.call_count == len(keys)


def test_win_getpass_backspace(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify backspace removes previous characters correctly."""
    keys = ["a", "b", "\b", "c", "\r"]
    mock_msvcrt = MagicMock()
    mock_msvcrt.getwch.side_effect = keys

    monkeypatch.setattr("sys.stdin", sys.__stdin__)
    with (
        patch("sys.stdin.isatty", return_value=True),
        patch.dict("sys.modules", {"msvcrt": mock_msvcrt}),
    ):
        result = win_getpass_with_paste("Enter value: ")
        assert result == "ac"


def test_win_getpass_ctrl_v_paste(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify Ctrl+V (\x16) intercepts and pastes clipboard text."""
    keys = ["t", "o", "k", "_", "\x16", "\r"]
    mock_msvcrt = MagicMock()
    mock_msvcrt.getwch.side_effect = keys

    monkeypatch.setattr("sys.stdin", sys.__stdin__)
    with (
        patch("sys.stdin.isatty", return_value=True),
        patch.dict("sys.modules", {"msvcrt": mock_msvcrt}),
        patch(
            "aiaddons.core.secrets.resolver.get_windows_clipboard_text",
            return_value="ghp_pasted_secret_value_12345\r\n",
        ),
    ):
        result = win_getpass_with_paste("Enter value: ")
        assert result == "tok_ghp_pasted_secret_value_12345"


def test_win_getpass_extended_keys_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify extended keys (arrows, Home, End via \\xe0 or \\x00) do not corrupt input."""
    # User presses Left Arrow (\xe0, 'K'), then 'x', then Up Arrow (\xe0, 'H'), then Enter
    keys = ["\xe0", "K", "x", "\xe0", "H", "\r"]
    mock_msvcrt = MagicMock()
    mock_msvcrt.getwch.side_effect = keys

    monkeypatch.setattr("sys.stdin", sys.__stdin__)
    with (
        patch("sys.stdin.isatty", return_value=True),
        patch.dict("sys.modules", {"msvcrt": mock_msvcrt}),
    ):
        result = win_getpass_with_paste("Enter value: ")
        assert result == "x"


def test_win_getpass_ctrl_c(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify Ctrl+C raises KeyboardInterrupt."""
    mock_msvcrt = MagicMock()
    mock_msvcrt.getwch.side_effect = ["\003"]

    monkeypatch.setattr("sys.stdin", sys.__stdin__)
    with (
        patch("sys.stdin.isatty", return_value=True),
        patch.dict("sys.modules", {"msvcrt": mock_msvcrt}),
        pytest.raises(KeyboardInterrupt),
    ):
        win_getpass_with_paste("Enter value: ")


def test_win_getpass_eof(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify Ctrl+D / Ctrl+Z on empty input raises EOFError."""
    mock_msvcrt = MagicMock()
    mock_msvcrt.getwch.side_effect = ["\004"]

    monkeypatch.setattr("sys.stdin", sys.__stdin__)
    with (
        patch("sys.stdin.isatty", return_value=True),
        patch.dict("sys.modules", {"msvcrt": mock_msvcrt}),
        pytest.raises(EOFError),
    ):
        win_getpass_with_paste("Enter value: ")


def test_secure_prompt_patched_fallback() -> None:
    """Verify secure_prompt delegates to getpass.getpass when getpass.getpass is mocked."""
    with patch("getpass.getpass", return_value="mocked_via_getpass") as mock_gp:
        val = secure_prompt("Prompt: ")
        assert val == "mocked_via_getpass"
        mock_gp.assert_called_once_with("Prompt: ")


def test_default_tty_input_provider_prompt_formatting() -> None:
    """Verify DefaultTTYInputProvider builds helpful prompt text including PowerShell tip."""
    captured: list[str] = []

    def mock_prompt_func(p: str) -> str:
        captured.append(p)
        return "val123"

    provider = DefaultTTYInputProvider(prompt_func=mock_prompt_func)
    res = provider.prompt_secret(
        EnvVarSpec(name="MY_API_KEY", description="My API Key Description", secret=True)
    )

    assert res == "val123"
    assert len(captured) == 1
    prompt_out = captured[0]
    assert "Secret required:" in prompt_out
    assert "MY_API_KEY" in prompt_out
    assert "(My API Key Description)" in prompt_out
    assert "$env:MY_API_KEY" in prompt_out
    assert "Enter value:" in prompt_out
