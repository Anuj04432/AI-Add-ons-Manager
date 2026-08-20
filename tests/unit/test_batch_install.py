"""Unit tests for batch installation features (Phase 5B batch install)."""

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

from typer.testing import CliRunner

from aiaddons.cli.exit_codes import ExitCode
from aiaddons.cli.main import app
from aiaddons.core.exceptions import ManifestValidationError
from aiaddons.core.execution.external.models import ExternalExecutionResult, ExternalRuntime
from aiaddons.core.models.agent import AgentCapability, AgentDetectionResult, Scope
from aiaddons.core.models.stack import AddonStack, StackAddonItem, parse_stack_file
from aiaddons.core.verification.models import VerificationCheck, VerificationResult, VerificationStatus
from aiaddons.state.lockfile import LockfileManager
from aiaddons.state.store import InstalledStateStore
from aiaddons.state.transaction import TransactionWALManager

runner = CliRunner()


def _mock_claude_agent(tmp_path: Path) -> AgentDetectionResult:
    return AgentDetectionResult(
        agent_id="claude-code",
        name="Claude Code",
        installed=True,
        supported_scopes=[Scope.GLOBAL, Scope.WORKSPACE],
        capabilities=[AgentCapability.MCP, AgentCapability.SKILL],
        workspace_config_path=str(tmp_path / ".claude.json"),
    )


def _create_sample_registry(tmp_path: Path) -> Path:
    reg_dir = tmp_path / "registry"
    reg_dir.mkdir(parents=True, exist_ok=True)
    dummy_checksum = "sha256:" + "a" * 64

    # 1. MCP server 1
    (reg_dir / "mcp-server-1.json").write_text(
        f"""{{
            "id": "mcp-server-1",
            "name": "MCP Server One",
            "version": "1.0.0",
            "description": "First MCP integration",
            "license": "MIT",
            "category": "developer-tools",
            "integration_type": "mcp",
            "target_agents": ["claude-code"],
            "supported_scopes": ["workspace"],
            "source": {{
                "source_type": "package",
                "package_name": "@modelcontextprotocol/server-one",
                "checksum": "{dummy_checksum}"
            }},
            "trust": {{
                "verification_status": "verified",
                "publisher": {{"name": "MCP Team"}}
            }},
            "handler_spec": {{
                "mcp": {{
                    "transport": "stdio",
                    "runtime": "npx",
                    "package_name": "@modelcontextprotocol/server-one"
                }}
            }}
        }}""",
        encoding="utf-8",
    )

    # 2. MCP server 2
    (reg_dir / "mcp-server-2.json").write_text(
        f"""{{
            "id": "mcp-server-2",
            "name": "MCP Server Two",
            "version": "2.0.0",
            "description": "Second MCP integration",
            "license": "MIT",
            "category": "developer-tools",
            "integration_type": "mcp",
            "target_agents": ["claude-code"],
            "supported_scopes": ["workspace"],
            "source": {{
                "source_type": "package",
                "package_name": "@modelcontextprotocol/server-two",
                "checksum": "{dummy_checksum}"
            }},
            "trust": {{
                "verification_status": "verified",
                "publisher": {{"name": "MCP Team"}}
            }},
            "handler_spec": {{
                "mcp": {{
                    "transport": "stdio",
                    "runtime": "npx",
                    "package_name": "@modelcontextprotocol/server-two"
                }}
            }}
        }}""",
        encoding="utf-8",
    )

    # 3. Skill add-on
    (reg_dir / "sample-skill.json").write_text(
        """{
            "id": "sample-skill",
            "name": "Sample Skill",
            "version": "1.5.0",
            "description": "Sample agent skill",
            "license": "MIT",
            "category": "workflow",
            "integration_type": "skill",
            "target_agents": ["claude-code"],
            "supported_scopes": ["workspace"],
            "source": {
                "source_type": "local",
                "path": "."
            },
            "trust": {
                "verification_status": "verified",
                "publisher": {"name": "Skill Team"}
            },
            "handler_spec": {
                "skill": {
                    "skill_file": "SKILL.md"
                }
            }
        }""",
        encoding="utf-8",
    )

    return reg_dir


def test_stack_model_validation() -> None:
    """Verify AddonStack model validation rules."""
    # Plain strings
    stack1 = AddonStack.model_validate({"addons": ["ponytail", "some-mcp"]})
    assert len(stack1.addons) == 2
    assert stack1.addons[0] == "ponytail"

    # Object items with pinned versions
    stack2 = AddonStack.model_validate(
        {"version": "1.0", "addons": [{"id": "ponytail", "version": "4.9.0"}, {"id": "some-mcp"}]}
    )
    assert len(stack2.addons) == 2
    assert isinstance(stack2.addons[0], StackAddonItem)
    assert stack2.addons[0].id == "ponytail"
    assert stack2.addons[0].version == "4.9.0"

    # Mixed items
    stack3 = AddonStack.model_validate(
        {"addons": ["ponytail", {"id": "some-mcp", "version": "2.0.0"}]}
    )
    assert len(stack3.addons) == 2
    assert stack3.addons[0] == "ponytail"
    assert isinstance(stack3.addons[1], StackAddonItem)


def test_batch_install_multiple_positional_ids(tmp_path: Path, monkeypatch: Any) -> None:
    """Verify batch installation of 2+ valid add-ons succeeds using positional arguments."""
    reg_dir = _create_sample_registry(tmp_path)
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(workspace_dir)

    (workspace_dir / "SKILL.md").write_text("# Sample Skill", encoding="utf-8")
    agent = _mock_claude_agent(workspace_dir)

    mock_mgr = patch(
        "aiaddons.cli.commands.install.AgentDetectionManager.detect_agents",
        return_value={"claude-code": agent},
    )
    mock_runner = patch("aiaddons.core.execution.external.runner.ExternalRunner.execute")

    with mock_mgr, mock_runner as mock_ext:
        mock_ext.return_value = ExternalExecutionResult(
            success=True,
            runtime=ExternalRuntime.NPX,
            executable_path="/usr/bin/npx",
            command_vector=["npx", "-y", "@modelcontextprotocol/server-one"],
            return_code=0,
            stdout="OK",
            stderr="",
            duration=0.1,
        )

        args = [
            "install",
            "mcp-server-1",
            "mcp-server-2",
            "sample-skill",
            "--yes",
            "--registry",
            str(reg_dir),
        ]
        result = runner.invoke(app, args)
        assert result.exit_code == ExitCode.SUCCESS
        assert "Successfully installed 3 add-on(s)" in result.stdout
        assert "mcp-server-1" in result.stdout
        assert "mcp-server-2" in result.stdout


def test_batch_install_invalid_addon_aborts_all(tmp_path: Path, monkeypatch: Any) -> None:
    """Verify batch install with invalid add-on ID aborts the whole batch upfront without touching state."""
    reg_dir = _create_sample_registry(tmp_path)
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(workspace_dir)

    agent = _mock_claude_agent(workspace_dir)

    mock_mgr = patch(
        "aiaddons.cli.commands.install.AgentDetectionManager.detect_agents",
        return_value={"claude-code": agent},
    )

    with mock_mgr:
        # Request valid addon and two invalid addons
        args = [
            "install",
            "mcp-server-1",
            "nonexistent-addon-1",
            "nonexistent-addon-2",
            "--yes",
            "--registry",
            str(reg_dir),
        ]
        result = runner.invoke(app, args)
        assert result.exit_code == ExitCode.INVALID_INPUT
        assert "nonexistent-addon-1" in result.stdout
        assert "nonexistent-addon-2" in result.stdout

        # Verify no files or configs were created
        assert not (workspace_dir / ".claude.json").exists()
        assert not (workspace_dir / "aiaddons.lock").exists()


def test_batch_install_dry_run_combined_plan(tmp_path: Path, monkeypatch: Any) -> None:
    """Verify batch --dry-run produces a combined plan without writing anything to disk."""
    reg_dir = _create_sample_registry(tmp_path)
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(workspace_dir)

    agent = _mock_claude_agent(workspace_dir)

    mock_mgr = patch(
        "aiaddons.cli.commands.install.AgentDetectionManager.detect_agents",
        return_value={"claude-code": agent},
    )

    with mock_mgr:
        args = [
            "install",
            "mcp-server-1",
            "mcp-server-2",
            "--dry-run",
            "--registry",
            str(reg_dir),
        ]
        result = runner.invoke(app, args)
        assert result.exit_code == ExitCode.SUCCESS
        assert "Installation Plan (2 add-ons)" in result.stdout
        assert "MCP Server One" in result.stdout
        assert "MCP Server Two" in result.stdout
        assert "No changes were made." in result.stdout

        # Verify zero mutations
        assert not (workspace_dir / ".claude.json").exists()
        assert not (workspace_dir / "aiaddons.lock").exists()


def test_batch_install_stack_yaml_file(tmp_path: Path, monkeypatch: Any) -> None:
    """Verify installing from a YAML stack file containing mixed entries and pinned versions."""
    reg_dir = _create_sample_registry(tmp_path)
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(workspace_dir)

    (workspace_dir / "SKILL.md").write_text("# Sample Skill", encoding="utf-8")
    stack_file = workspace_dir / "my-stack.yaml"
    stack_file.write_text(
        """version: "1.0"
addons:
  - id: mcp-server-1
    version: "1.0.0"
  - mcp-server-2
  - sample-skill
""",
        encoding="utf-8",
    )

    agent = _mock_claude_agent(workspace_dir)
    mock_mgr = patch(
        "aiaddons.cli.commands.install.AgentDetectionManager.detect_agents",
        return_value={"claude-code": agent},
    )
    mock_runner = patch("aiaddons.core.execution.external.runner.ExternalRunner.execute")

    with mock_mgr, mock_runner as mock_ext:
        mock_ext.return_value = ExternalExecutionResult(
            success=True,
            runtime=ExternalRuntime.NPX,
            executable_path="/usr/bin/npx",
            command_vector=["npx", "-y", "@modelcontextprotocol/server-one"],
            return_code=0,
            stdout="OK",
            stderr="",
            duration=0.1,
        )

        args = [
            "install",
            "--file",
            str(stack_file),
            "--yes",
            "--registry",
            str(reg_dir),
        ]
        result = runner.invoke(app, args)
        assert result.exit_code == ExitCode.SUCCESS
        assert "Successfully installed 3 add-on(s)" in result.stdout


def test_batch_install_stack_version_mismatch(tmp_path: Path, monkeypatch: Any) -> None:
    """Verify stack file with mismatched pinned version fails upfront."""
    reg_dir = _create_sample_registry(tmp_path)
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(workspace_dir)

    stack_file = workspace_dir / "mismatch-stack.yaml"
    stack_file.write_text(
        """version: "1.0"
addons:
  - id: mcp-server-1
    version: "9.9.9"
""",
        encoding="utf-8",
    )

    agent = _mock_claude_agent(workspace_dir)
    mock_mgr = patch(
        "aiaddons.cli.commands.install.AgentDetectionManager.detect_agents",
        return_value={"claude-code": agent},
    )

    with mock_mgr:
        args = ["install", "--file", str(stack_file), "--registry", str(reg_dir)]
        result = runner.invoke(app, args)
        assert result.exit_code == ExitCode.INVALID_INPUT
        assert "version mismatch" in result.stdout.lower()


def test_batch_install_malformed_stack_file(tmp_path: Path, monkeypatch: Any) -> None:
    """Verify malformed stack file (invalid YAML or schema violation) is rejected upfront."""
    reg_dir = _create_sample_registry(tmp_path)
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(workspace_dir)

    bad_stack = workspace_dir / "bad.yaml"
    bad_stack.write_text("invalid_key: true\naddons: []\n", encoding="utf-8")

    agent = _mock_claude_agent(workspace_dir)
    mock_mgr = patch(
        "aiaddons.cli.commands.install.AgentDetectionManager.detect_agents",
        return_value={"claude-code": agent},
    )

    with mock_mgr:
        args = ["install", "--file", str(bad_stack), "--registry", str(reg_dir)]
        result = runner.invoke(app, args)
        assert result.exit_code == ExitCode.INVALID_INPUT
        assert "validation failed" in result.stdout.lower() or "invalid stack file" in result.stdout.lower()


def test_batch_install_already_installed_skipping(tmp_path: Path, monkeypatch: Any) -> None:
    """Verify that an already installed add-on in a batch is skipped with a clear message."""
    reg_dir = _create_sample_registry(tmp_path)
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(workspace_dir)

    (workspace_dir / "SKILL.md").write_text("# Sample Skill", encoding="utf-8")
    agent = _mock_claude_agent(workspace_dir)

    mock_mgr = patch(
        "aiaddons.cli.commands.install.AgentDetectionManager.detect_agents",
        return_value={"claude-code": agent},
    )
    mock_runner = patch("aiaddons.core.execution.external.runner.ExternalRunner.execute")

    with mock_mgr, mock_runner as mock_ext:
        mock_ext.return_value = ExternalExecutionResult(
            success=True,
            runtime=ExternalRuntime.NPX,
            executable_path="/usr/bin/npx",
            command_vector=["npx", "-y", "@modelcontextprotocol/server-one"],
            return_code=0,
            stdout="OK",
            stderr="",
            duration=0.1,
        )

        # 1. Install mcp-server-1 first
        res1 = runner.invoke(
            app, ["install", "mcp-server-1", "--yes", "--registry", str(reg_dir)]
        )
        assert res1.exit_code == ExitCode.SUCCESS

        # 2. Now run a batch with mcp-server-1 AND mcp-server-2
        res2 = runner.invoke(
            app,
            [
                "install",
                "mcp-server-1",
                "mcp-server-2",
                "--yes",
                "--registry",
                str(reg_dir),
            ],
        )
        assert res2.exit_code == ExitCode.SUCCESS
        assert "already installed" in res2.stdout.lower()
        assert "Skipping" in res2.stdout
        assert "Successfully installed" in res2.stdout
        assert "MCP Server Two" in res2.stdout


def test_batch_install_inter_addon_conflict(tmp_path: Path, monkeypatch: Any) -> None:
    """Verify that compatibility conflict between two requested add-ons in a batch is caught upfront."""
    reg_dir = tmp_path / "registry"
    reg_dir.mkdir(parents=True, exist_ok=True)
    dummy_checksum = "sha256:" + "c" * 64

    # Addon A requires python <3.10
    (reg_dir / "addon-a.json").write_text(
        f"""{{
            "id": "addon-a",
            "name": "Addon A",
            "version": "1.0.0",
            "description": "Requires python <3.10",
            "license": "MIT",
            "category": "developer-tools",
            "integration_type": "mcp",
            "target_agents": ["claude-code"],
            "supported_scopes": ["workspace"],
            "dependencies": [
                {{"name": "python", "type": "cli", "version_constraint": "<3.10", "required": true}}
            ],
            "source": {{"source_type": "package", "package_name": "pkg-a", "checksum": "{dummy_checksum}"}},
            "trust": {{"verification_status": "verified", "publisher": {{"name": "Team"}}}},
            "handler_spec": {{"mcp": {{"runtime": "npx", "package_name": "pkg-a"}}}}
        }}""",
        encoding="utf-8",
    )

    # Addon B requires python >=3.11
    (reg_dir / "addon-b.json").write_text(
        f"""{{
            "id": "addon-b",
            "name": "Addon B",
            "version": "1.0.0",
            "description": "Requires python >=3.11",
            "license": "MIT",
            "category": "developer-tools",
            "integration_type": "mcp",
            "target_agents": ["claude-code"],
            "supported_scopes": ["workspace"],
            "dependencies": [
                {{"name": "python", "type": "cli", "version_constraint": ">=3.11", "required": true}}
            ],
            "source": {{"source_type": "package", "package_name": "pkg-b", "checksum": "{dummy_checksum}"}},
            "trust": {{"verification_status": "verified", "publisher": {{"name": "Team"}}}},
            "handler_spec": {{"mcp": {{"runtime": "npx", "package_name": "pkg-b"}}}}
        }}""",
        encoding="utf-8",
    )

    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(workspace_dir)

    agent = _mock_claude_agent(workspace_dir)
    mock_mgr = patch(
        "aiaddons.cli.commands.install.AgentDetectionManager.detect_agents",
        return_value={"claude-code": agent},
    )

    with mock_mgr:
        args = ["install", "addon-a", "addon-b", "--yes", "--registry", str(reg_dir)]
        result = runner.invoke(app, args)
        assert result.exit_code == ExitCode.COMPATIBILITY_FAILURE
        assert "conflict" in result.stdout.lower()


def test_batch_install_json_output(tmp_path: Path, monkeypatch: Any) -> None:
    """Verify batch --json output contains per-addon status and operations."""
    reg_dir = _create_sample_registry(tmp_path)
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(workspace_dir)

    agent = _mock_claude_agent(workspace_dir)
    mock_mgr = patch(
        "aiaddons.cli.commands.install.AgentDetectionManager.detect_agents",
        return_value={"claude-code": agent},
    )

    with mock_mgr:
        args = [
            "install",
            "mcp-server-1",
            "mcp-server-2",
            "--dry-run",
            "--json",
            "--registry",
            str(reg_dir),
        ]
        result = runner.invoke(app, args)
        assert result.exit_code == ExitCode.SUCCESS
        data = json.loads(result.stdout)
        assert data["agent"] == "claude-code"
        assert data["scope"] == "workspace"
        assert data["success"] is True
        assert data["transaction_status"] == "PLANNED"
        assert isinstance(data["addons"], list)
        assert len(data["addons"]) == 2
        addon_ids = [a["addon_id"] for a in data["addons"]]
        assert "mcp-server-1" in addon_ids
        assert "mcp-server-2" in addon_ids
        for add_res in data["addons"]:
            assert add_res["compatible"] is True
            assert add_res["success"] is True
            assert len(add_res["planned_operations"]) > 0


def test_batch_install_rollback_on_execution_failure(tmp_path: Path, monkeypatch: Any) -> None:
    """Verify that if one add-on in a batch fails execution, the entire batch rolls back."""
    reg_dir = _create_sample_registry(tmp_path)
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(workspace_dir)

    agent = _mock_claude_agent(workspace_dir)
    mock_mgr = patch(
        "aiaddons.cli.commands.install.AgentDetectionManager.detect_agents",
        return_value={"claude-code": agent},
    )
    mock_runner = patch("aiaddons.core.execution.external.runner.ExternalRunner.execute")

    with mock_mgr, mock_runner as mock_ext:
        # First call succeeds, second fails
        mock_ext.side_effect = [
            ExternalExecutionResult(
                success=True,
                runtime=ExternalRuntime.NPX,
                executable_path="/usr/bin/npx",
                command_vector=["npx", "-y", "@modelcontextprotocol/server-one"],
                return_code=0,
                stdout="OK",
                stderr="",
                duration=0.1,
            ),
            ExternalExecutionResult(
                success=False,
                runtime=ExternalRuntime.NPX,
                executable_path="/usr/bin/npx",
                command_vector=["npx", "-y", "@modelcontextprotocol/server-two"],
                return_code=1,
                stdout="",
                stderr="Package not found error",
                error_message="Package not found error",
                duration=0.1,
            ),
        ]

        args = [
            "install",
            "mcp-server-1",
            "mcp-server-2",
            "--yes",
            "--registry",
            str(reg_dir),
        ]
        result = runner.invoke(app, args)
        assert result.exit_code == ExitCode.EXECUTION_FAILURE
        assert "Rolling back installation" in result.stdout
        assert "Rollback completed" in result.stdout

        # Verify nothing remains installed in config or lockfile
        config_file = workspace_dir / ".claude.json"
        if config_file.exists():
            cfg_data = json.loads(config_file.read_text(encoding="utf-8"))
            mcp_servers = cfg_data.get("mcpServers", {})
            assert "mcp-server-1" not in mcp_servers
            assert "mcp-server-2" not in mcp_servers
