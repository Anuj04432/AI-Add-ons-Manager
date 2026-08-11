"""Unit tests for Phase 5B AgentAdapter integration and scope handling."""

from pathlib import Path
from unittest.mock import patch

import pytest

from aiaddons.agents.claude_code import ClaudeCodeAdapter
from aiaddons.agents.codex import CodexAdapter
from aiaddons.agents.manager import AgentDetectionManager
from aiaddons.core.execution.engine import ExecutionEngine
from aiaddons.core.execution.models import ExecutionStatus
from aiaddons.core.installer.engine import InstallationEngine
from aiaddons.core.models.agent import Scope
from aiaddons.core.models.manifest import IntegrationManifest


@pytest.fixture
def sample_mcp_manifest() -> IntegrationManifest:
    return IntegrationManifest.model_validate(
        {
            "id": "github-mcp",
            "name": "GitHub MCP Server",
            "version": "1.0.0",
            "description": "GitHub integration",
            "license": "MIT",
            "category": "developer-tools",
            "integration_type": "mcp",
            "target_agents": ["claude-code", "codex"],
            "supported_scopes": ["global", "workspace"],
            "source": {
                "source_type": "package",
                "package_name": "@modelcontextprotocol/server-github",
                "checksum": "sha256:" + "a" * 64,
            },
            "trust": {
                "verification_status": "verified",
                "publisher": {"name": "MCP Team"},
            },
            "handler_spec": {
                "mcp": {
                    "transport": "stdio",
                    "runtime": "npx",
                    "package_name": "@modelcontextprotocol/server-github",
                    "env_vars": [
                        {"name": "GITHUB_PAT", "required": True, "secret": True}
                    ],
                }
            },
        }
    )


def test_agent_detection_manager_adapter_lookup() -> None:
    """Verify AgentDetectionManager adapter registration and lookup."""
    manager = AgentDetectionManager()
    claude = manager.get_adapter("claude-code")
    codex = manager.get_adapter("codex")

    assert claude is not None
    assert claude.agent_id == "claude-code"
    assert codex is not None
    assert codex.agent_id == "codex"
    assert manager.get_adapter("nonexistent") is None


def test_claude_code_global_and_workspace_paths(tmp_path: Path) -> None:
    """Verify Claude Code adapter returns correct config and skill paths."""
    adapter = ClaudeCodeAdapter()

    global_cfg = adapter.get_config_path(Scope.GLOBAL)
    assert global_cfg is not None
    assert ".claude" in str(global_cfg)

    global_skill = adapter.get_skill_directory(Scope.GLOBAL)
    assert str(global_skill).replace("\\", "/").endswith(".claude/skills")

    ws_cfg = adapter.get_config_path(Scope.WORKSPACE, project_path=tmp_path)
    assert ws_cfg is not None
    assert str(ws_cfg) == str(tmp_path / ".claude.json")

    ws_skill = adapter.get_skill_directory(Scope.WORKSPACE, project_path=tmp_path)
    assert str(ws_skill) == str(tmp_path / ".claude" / "skills")


def test_codex_global_and_workspace_paths(tmp_path: Path) -> None:
    """Verify OpenAI Codex adapter returns correct config and skill paths."""

    adapter = CodexAdapter()

    global_cfg = adapter.get_config_path(Scope.GLOBAL)
    assert global_cfg is not None
    assert ".codex" in str(global_cfg)

    global_skill = adapter.get_skill_directory(Scope.GLOBAL)
    assert str(global_skill).replace("\\", "/").endswith(".codex/skills")

    ws_cfg = adapter.get_config_path(Scope.WORKSPACE, project_path=tmp_path)
    assert ws_cfg is not None
    assert str(ws_cfg) == str(tmp_path / ".codex")

    ws_skill = adapter.get_skill_directory(Scope.WORKSPACE, project_path=tmp_path)
    assert str(ws_skill) == str(tmp_path / ".agents" / "skills")


def test_claude_code_global_installation_transaction(
    tmp_path: Path, sample_mcp_manifest: IntegrationManifest
) -> None:
    """Verify Claude Code global installation flow using transaction and execution engine."""
    adapter = ClaudeCodeAdapter()
    detection = adapter.detect(project_path=tmp_path)
    detection.installed = True

    engine = InstallationEngine()
    plan = engine.generate_plan(sample_mcp_manifest, detection, Scope.GLOBAL)
    assert plan.target_scope == Scope.GLOBAL

    for op in plan.planned_operations:
        op.target_root = str(tmp_path)

    exec_engine = ExecutionEngine()
    with patch.object(exec_engine.external_runner, "execute") as mock_exec:
        mock_exec.return_value.success = True
        mock_exec.return_value.return_code = 0
        mock_exec.return_value.stdout = "Installed"
        mock_exec.return_value.stderr = ""

        res = exec_engine.execute_plan(plan, dry_run=False)
        assert res.status == ExecutionStatus.SUCCESS


def test_codex_workspace_installation_transaction(
    tmp_path: Path, sample_mcp_manifest: IntegrationManifest
) -> None:
    """Verify OpenAI Codex workspace installation flow using transaction and execution engine."""
    adapter = CodexAdapter()
    detection = adapter.detect(project_path=tmp_path)
    detection.installed = True

    engine = InstallationEngine()
    plan = engine.generate_plan(sample_mcp_manifest, detection, Scope.WORKSPACE)
    assert plan.target_scope == Scope.WORKSPACE

    for op in plan.planned_operations:
        op.target_root = str(tmp_path)

    exec_engine = ExecutionEngine()
    with patch.object(exec_engine.external_runner, "execute") as mock_exec:
        mock_exec.return_value.success = True
        mock_exec.return_value.return_code = 0
        mock_exec.return_value.stdout = "Installed"
        mock_exec.return_value.stderr = ""

        res = exec_engine.execute_plan(plan, dry_run=False)
        assert res.status == ExecutionStatus.SUCCESS
