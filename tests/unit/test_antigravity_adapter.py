"""Unit tests for Antigravity CLI adapter and detection integration."""

from pathlib import Path
from unittest.mock import patch

import pytest

from aiaddons.agents.antigravity import AntigravityAdapter
from aiaddons.agents.manager import AgentDetectionManager, detect_agents
from aiaddons.core.execution.engine import ExecutionEngine
from aiaddons.core.execution.models import ExecutionStatus
from aiaddons.core.health.engine import HealthCheckEngine
from aiaddons.core.health.models import HealthCategory, HealthStatus
from aiaddons.core.installer.engine import InstallationEngine
from aiaddons.core.models.agent import AgentCapability, Scope
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
            "target_agents": ["antigravity", "claude-code", "codex"],
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
                    "env_vars": [{"name": "GITHUB_PAT", "required": True, "secret": True}],
                }
            },
        }
    )


def test_antigravity_detected_with_binary_and_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Test Antigravity CLI detection when 'agy' binary and config exist."""
    fake_binary = tmp_path / "bin" / "agy"
    fake_binary.parent.mkdir(parents=True, exist_ok=True)
    fake_binary.touch()

    monkeypatch.setattr(
        "shutil.which", lambda cmd: str(fake_binary) if cmd in ("agy", "antigravity") else None
    )
    monkeypatch.setattr(
        "aiaddons.agents.antigravity.run_version_command", lambda cmd: "1.2.0"
    )
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")

    adapter = AntigravityAdapter()
    result = adapter.detect(project_path=tmp_path / "workspace")

    assert result.installed is True
    assert result.agent_id == "antigravity"
    assert result.name == "Antigravity CLI"
    assert result.version == "1.2.0"
    assert result.executable_path == str(fake_binary)
    assert AgentCapability.MCP in result.capabilities
    assert AgentCapability.SKILL in result.capabilities
    assert adapter.supports_capability(AgentCapability.MCP) is True
    assert adapter.supports_capability(AgentCapability.SKILL) is True


def test_antigravity_detected_with_antigravity_binary_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Test Antigravity CLI detection fallback when binary name is 'antigravity'."""
    fake_binary = tmp_path / "bin" / "antigravity"
    fake_binary.parent.mkdir(parents=True, exist_ok=True)
    fake_binary.touch()

    monkeypatch.setattr(
        "shutil.which", lambda cmd: str(fake_binary) if cmd == "antigravity" else None
    )
    monkeypatch.setattr(
        "aiaddons.agents.antigravity.run_version_command", lambda cmd: "2.0.0"
    )
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")

    adapter = AntigravityAdapter()
    result = adapter.detect(project_path=tmp_path / "workspace")

    assert result.installed is True
    assert result.agent_id == "antigravity"
    assert result.version == "2.0.0"
    assert result.executable_path == str(fake_binary)


def test_antigravity_detected_via_global_config_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Test Antigravity CLI detection when binary is absent but global ~/.gemini exists."""
    home_dir = tmp_path / "home"
    gemini_dir = home_dir / ".gemini"
    gemini_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("shutil.which", lambda cmd: None)
    monkeypatch.setattr("pathlib.Path.home", lambda: home_dir)

    adapter = AntigravityAdapter()
    result = adapter.detect(project_path=tmp_path / "workspace")

    assert result.installed is True
    assert result.executable_path is None
    assert result.version is None


def test_antigravity_detected_via_workspace_config_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Test Antigravity CLI detection when binary is absent but workspace .agents directory exists."""
    workspace_dir = tmp_path / "workspace"
    agents_dir = workspace_dir / ".agents"
    agents_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("shutil.which", lambda cmd: None)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")

    adapter = AntigravityAdapter()
    result = adapter.detect(project_path=workspace_dir)

    assert result.installed is True
    assert result.executable_path is None
    assert result.version is None


def test_antigravity_detected_via_workspace_agents_md(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Test Antigravity CLI detection when AGENTS.md exists in workspace."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    (workspace_dir / "AGENTS.md").write_text("# Engineering Guidelines", encoding="utf-8")

    monkeypatch.setattr("shutil.which", lambda cmd: None)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")

    adapter = AntigravityAdapter()
    result = adapter.detect(project_path=workspace_dir)

    assert result.installed is True


def test_antigravity_not_detected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test Antigravity CLI when neither binary nor config exists."""
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")

    adapter = AntigravityAdapter()
    result = adapter.detect(project_path=tmp_path / "workspace")

    assert result.installed is False
    assert result.version is None
    assert result.executable_path is None


def test_antigravity_malformed_version_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Test Antigravity CLI detection when binary returns unparseable version output."""
    fake_binary = tmp_path / "bin" / "agy"
    fake_binary.parent.mkdir(parents=True, exist_ok=True)
    fake_binary.touch()

    monkeypatch.setattr("shutil.which", lambda cmd: str(fake_binary) if cmd == "agy" else None)
    monkeypatch.setattr("aiaddons.agents.antigravity.run_version_command", lambda cmd: None)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")

    adapter = AntigravityAdapter()
    result = adapter.detect(project_path=tmp_path / "workspace")

    assert result.installed is True
    assert result.version is None
    assert result.executable_path == str(fake_binary)


def test_antigravity_global_and_workspace_paths(tmp_path: Path) -> None:
    """Verify Antigravity adapter returns canonical config and skill paths."""
    adapter = AntigravityAdapter()

    global_cfg = adapter.get_config_path(Scope.GLOBAL)
    assert global_cfg is not None
    assert ".gemini" in str(global_cfg)
    assert "mcp_config.json" in str(global_cfg)

    global_skill = adapter.get_skill_directory(Scope.GLOBAL)
    assert str(global_skill).replace("\\", "/").endswith(".gemini/config/skills")

    ws_cfg = adapter.get_config_path(Scope.WORKSPACE, project_path=tmp_path)
    assert ws_cfg is not None
    assert str(ws_cfg) == str(tmp_path / ".agents" / "mcp_config.json")

    ws_skill = adapter.get_skill_directory(Scope.WORKSPACE, project_path=tmp_path)
    assert str(ws_skill) == str(tmp_path / ".agents" / "skills")


def test_antigravity_registered_in_manager() -> None:
    """Verify AgentDetectionManager registers Antigravity alongside Claude Code and Codex."""
    manager = AgentDetectionManager()
    antigravity = manager.get_adapter("antigravity")

    assert antigravity is not None
    assert antigravity.agent_id == "antigravity"
    assert antigravity.name == "Antigravity CLI"

    claude = manager.get_adapter("claude-code")
    codex = manager.get_adapter("codex")
    assert claude is not None
    assert codex is not None


def test_antigravity_detection_manager_detect_all(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verify detect_agents includes antigravity."""
    monkeypatch.setattr("shutil.which", lambda cmd: f"/usr/bin/{cmd}")
    monkeypatch.setattr(
        "aiaddons.agents.antigravity.run_version_command", lambda cmd: "1.0.0"
    )
    monkeypatch.setattr(
        "aiaddons.agents.claude_code.run_version_command", lambda cmd: "0.2.1"
    )
    monkeypatch.setattr(
        "aiaddons.agents.codex.run_version_command", lambda cmd: "1.0.0"
    )
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    results = detect_agents(project_path=tmp_path)

    assert "antigravity" in results
    assert "claude-code" in results
    assert "codex" in results
    assert results["antigravity"].installed is True
    assert results["antigravity"].version == "1.0.0"


def test_antigravity_doctor_health_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verify aiaddons doctor correctly inspects Antigravity CLI detection status and config."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    agents_dir = workspace_dir / ".agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / "mcp_config.json").write_text(
        '{"mcpServers": {"test-mcp": {"command": "npx", "args": ["test"]}}}',
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "shutil.which", lambda cmd: f"/usr/bin/{cmd}" if cmd == "agy" else None
    )
    monkeypatch.setattr(
        "aiaddons.agents.antigravity.run_version_command", lambda cmd: "1.5.0"
    )
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")

    engine = HealthCheckEngine(workspace_dir=workspace_dir, store_dir=tmp_path / ".aiaddons")
    agent_items = engine.check_agents()

    antigravity_items = [
        item for item in agent_items if "antigravity" in item.check_id
    ]
    assert len(antigravity_items) >= 1
    assert antigravity_items[0].status == HealthStatus.PASS
    assert "Antigravity CLI detected" in antigravity_items[0].message
    assert "v1.5.0" in antigravity_items[0].message
