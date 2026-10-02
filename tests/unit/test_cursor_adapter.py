"""Unit tests for Cursor IDE adapter and detection integration."""

from pathlib import Path
import sys
from unittest.mock import patch

import pytest

from aiaddons.agents.cursor import CursorAdapter
from aiaddons.agents.manager import AgentDetectionManager, detect_agents
from aiaddons.core.execution.engine import ExecutionEngine
from aiaddons.core.execution.models import ExecutionStatus
from aiaddons.core.health.engine import HealthCheckEngine
from aiaddons.core.health.models import HealthStatus
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
            "target_agents": ["cursor", "claude-code", "codex"],
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


@pytest.fixture
def sample_skill_manifest() -> IntegrationManifest:
    return IntegrationManifest.model_validate(
        {
            "id": "code-review",
            "name": "Code Review Skill",
            "version": "1.0.0",
            "description": "Reviews code",
            "license": "MIT",
            "category": "developer-tools",
            "integration_type": "skill",
            "target_agents": ["cursor"],
            "supported_scopes": ["global", "workspace"],
            "source": {
                "source_type": "local",
                "path": "skill_source",
            },
            "trust": {
                "verification_status": "verified",
                "publisher": {"name": "Test Team"},
            },
            "handler_spec": {
                "skill": {
                    "skill_file": "SKILL.md",
                    "supporting_files": [],
                }
            },
        }
    )


def test_cursor_detected_with_binary_and_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Test Cursor detection when 'cursor' binary and config exist."""
    fake_binary = tmp_path / "bin" / "cursor"
    fake_binary.parent.mkdir(parents=True, exist_ok=True)
    fake_binary.touch()

    monkeypatch.setattr(
        "shutil.which", lambda cmd: str(fake_binary) if cmd == "cursor" else None
    )
    monkeypatch.setattr(
        "aiaddons.agents.cursor.run_version_command", lambda cmd: "0.45.6"
    )
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")

    adapter = CursorAdapter()
    result = adapter.detect(project_path=tmp_path / "workspace")

    assert result.installed is True
    assert result.agent_id == "cursor"
    assert result.name == "Cursor"
    assert result.version == "0.45.6"
    assert result.executable_path == str(fake_binary)
    assert AgentCapability.MCP in result.capabilities
    assert AgentCapability.SKILL not in result.capabilities
    assert adapter.supports_capability(AgentCapability.MCP) is True
    assert adapter.supports_capability(AgentCapability.SKILL) is False


def test_cursor_detected_with_windows_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Test Cursor detection using Windows LOCALAPPDATA fallback."""
    local_appdata = tmp_path / "LocalAppData"
    cursor_exe = local_appdata / "Programs" / "cursor" / "Cursor.exe"
    cursor_exe.parent.mkdir(parents=True, exist_ok=True)
    cursor_exe.touch()

    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    monkeypatch.setattr(
        "aiaddons.agents.cursor.run_version_command", lambda cmd: "0.46.0"
    )
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")

    adapter = CursorAdapter()
    result = adapter.detect(project_path=tmp_path / "workspace")

    assert result.installed is True
    assert result.agent_id == "cursor"
    assert result.version == "0.46.0"
    assert result.executable_path == str(cursor_exe)


def test_cursor_detected_with_macos_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Test Cursor detection using macOS Application bundle fallback."""
    fake_mac_app = tmp_path / "Applications" / "Cursor.app" / "Contents" / "Resources" / "app" / "bin" / "cursor"
    fake_mac_app.parent.mkdir(parents=True, exist_ok=True)
    fake_mac_app.touch()

    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    orig_exists = Path.exists

    def mock_exists(self: Path) -> bool:
        if "/Applications/Cursor.app" in str(self).replace("\\", "/"):
            return True
        return orig_exists(self)

    monkeypatch.setattr(Path, "exists", mock_exists)
    monkeypatch.setattr(
        "aiaddons.agents.cursor.run_version_command", lambda cmd: "0.44.0"
    )
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")

    adapter = CursorAdapter()
    result = adapter.detect(project_path=tmp_path / "workspace")

    assert result.installed is True
    assert result.version == "0.44.0"


def test_cursor_detected_via_global_config_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Test Cursor detection when binary is absent but global ~/.cursor exists."""
    home_dir = tmp_path / "home"
    cursor_dir = home_dir / ".cursor"
    cursor_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("shutil.which", lambda cmd: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "empty_local"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "empty_prog"))
    monkeypatch.setattr("pathlib.Path.home", lambda: home_dir)

    adapter = CursorAdapter()
    result = adapter.detect(project_path=tmp_path / "workspace")

    assert result.installed is True
    assert result.executable_path is None
    assert result.version is None


def test_cursor_detected_via_workspace_config_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Test Cursor detection when binary is absent but workspace .cursor directory exists."""
    workspace_dir = tmp_path / "workspace"
    cursor_dir = workspace_dir / ".cursor"
    cursor_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("shutil.which", lambda cmd: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "empty_local"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "empty_prog"))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")

    adapter = CursorAdapter()
    result = adapter.detect(project_path=workspace_dir)

    assert result.installed is True
    assert result.executable_path is None
    assert result.version is None


def test_cursor_not_detected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test Cursor when neither binary nor config exists."""
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "empty_local"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "empty_prog"))

    adapter = CursorAdapter()
    result = adapter.detect(project_path=tmp_path / "workspace")

    assert result.installed is False
    assert result.version is None
    assert result.executable_path is None


def test_cursor_malformed_version_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Test Cursor detection when binary returns unparseable version output."""
    fake_binary = tmp_path / "bin" / "cursor"
    fake_binary.parent.mkdir(parents=True, exist_ok=True)
    fake_binary.touch()

    monkeypatch.setattr("shutil.which", lambda cmd: str(fake_binary) if cmd == "cursor" else None)
    monkeypatch.setattr("aiaddons.agents.cursor.run_version_command", lambda cmd: None)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")

    adapter = CursorAdapter()
    result = adapter.detect(project_path=tmp_path / "workspace")

    assert result.installed is True
    assert result.version is None
    assert result.executable_path == str(fake_binary)


def test_cursor_global_and_workspace_paths(tmp_path: Path) -> None:
    """Verify Cursor adapter returns canonical config and rejects skill paths."""
    from aiaddons.core.exceptions import UnsupportedIntegrationTypeError

    adapter = CursorAdapter()

    global_cfg = adapter.get_config_path(Scope.GLOBAL)
    assert global_cfg is not None
    assert ".cursor" in str(global_cfg)
    assert "mcp.json" in str(global_cfg)

    with pytest.raises(UnsupportedIntegrationTypeError):
        adapter.get_skill_directory(Scope.GLOBAL)

    ws_cfg = adapter.get_config_path(Scope.WORKSPACE, project_path=tmp_path)
    assert ws_cfg is not None
    assert str(ws_cfg) == str(tmp_path / ".cursor" / "mcp.json")

    with pytest.raises(UnsupportedIntegrationTypeError):
        adapter.get_skill_directory(Scope.WORKSPACE, project_path=tmp_path)


def test_cursor_registered_in_manager() -> None:
    """Verify AgentDetectionManager registers Cursor."""
    manager = AgentDetectionManager()
    cursor = manager.get_adapter("cursor")

    assert cursor is not None
    assert cursor.agent_id == "cursor"
    assert cursor.name == "Cursor"


def test_cursor_detection_manager_detect_all(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verify detect_agents includes cursor."""
    monkeypatch.setattr("shutil.which", lambda cmd: f"/usr/bin/{cmd}")
    monkeypatch.setattr(
        "aiaddons.agents.cursor.run_version_command", lambda cmd: "0.45.0"
    )
    monkeypatch.setattr(
        "aiaddons.agents.claude_code.run_version_command", lambda cmd: "0.2.1"
    )
    monkeypatch.setattr(
        "aiaddons.agents.codex.run_version_command", lambda cmd: "1.0.0"
    )
    monkeypatch.setattr(
        "aiaddons.agents.antigravity.run_version_command", lambda cmd: "1.0.0"
    )
    monkeypatch.setattr(
        "aiaddons.agents.hermes.run_version_command", lambda cmd: "0.1.0"
    )
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    results = detect_agents(project_path=tmp_path)

    assert "cursor" in results
    assert results["cursor"].installed is True
    assert results["cursor"].version == "0.45.0"


def test_cursor_doctor_health_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verify aiaddons doctor correctly inspects Cursor detection status and config."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    cursor_dir = workspace_dir / ".cursor"
    cursor_dir.mkdir(parents=True, exist_ok=True)
    (cursor_dir / "mcp.json").write_text(
        '{"mcpServers": {"test-mcp": {"command": "npx", "args": ["test"]}}}',
        encoding="utf-8",
    )

    fake_binary = tmp_path / "bin" / "cursor"
    monkeypatch.setattr("shutil.which", lambda cmd: str(fake_binary) if cmd == "cursor" else None)
    monkeypatch.setattr("aiaddons.agents.cursor.run_version_command", lambda cmd: "0.45.0")
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")

    engine = HealthCheckEngine(workspace_dir=workspace_dir, store_dir=tmp_path / ".aiaddons")
    agent_items = engine.check_agents()

    cursor_items = [item for item in agent_items if "cursor" in item.check_id]
    assert len(cursor_items) >= 1
    assert cursor_items[0].status == HealthStatus.PASS
    assert "Cursor detected" in cursor_items[0].message
    assert "v0.45.0" in cursor_items[0].message


def test_cursor_workspace_installation_transaction(
    tmp_path: Path, sample_mcp_manifest: IntegrationManifest
) -> None:
    """Verify Cursor workspace MCP installation flow using execution engine."""
    adapter = CursorAdapter()
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

    config_file = tmp_path / ".cursor" / "mcp.json"
    assert config_file.exists()
    content = config_file.read_text(encoding="utf-8")
    assert "github-mcp" in content


def test_cursor_skill_installation_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sample_skill_manifest: IntegrationManifest
) -> None:
    """Verify Cursor rejects skill installation due to lack of skill capability."""
    from aiaddons.core.exceptions import IncompatibleAgentError

    monkeypatch.chdir(tmp_path)
    adapter = CursorAdapter()
    detection = adapter.detect(project_path=tmp_path)
    detection.installed = True

    engine = InstallationEngine()
    with pytest.raises(IncompatibleAgentError) as exc_info:
        engine.generate_plan(sample_skill_manifest, detection, Scope.WORKSPACE)
    assert "skill" in str(exc_info.value).lower()
