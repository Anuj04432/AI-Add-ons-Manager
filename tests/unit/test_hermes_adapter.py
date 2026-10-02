"""Unit tests for Hermes Agent adapter and detection integration."""

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from aiaddons.agents.hermes import HermesAdapter
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
            "target_agents": ["hermes", "claude-code", "codex"],
            "supported_scopes": ["global"],
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
            "target_agents": ["hermes"],
            "supported_scopes": ["global"],
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


def test_hermes_detected_with_binary_and_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Test Hermes Agent detection when 'hermes' binary and config exist."""
    fake_binary = tmp_path / "bin" / "hermes"
    fake_binary.parent.mkdir(parents=True, exist_ok=True)
    fake_binary.touch()

    monkeypatch.setattr(
        "shutil.which", lambda cmd: str(fake_binary) if cmd == "hermes" else None
    )
    monkeypatch.setattr(
        "aiaddons.agents.hermes.run_version_command", lambda cmd: "0.2.0"
    )
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")

    adapter = HermesAdapter()
    result = adapter.detect(project_path=tmp_path / "workspace")

    assert result.installed is True
    assert result.agent_id == "hermes"
    assert result.name == "Hermes Agent"
    assert result.version == "0.2.0"
    assert result.executable_path == str(fake_binary)
    assert AgentCapability.MCP in result.capabilities
    assert AgentCapability.SKILL in result.capabilities
    assert adapter.supports_capability(AgentCapability.MCP) is True
    assert adapter.supports_capability(AgentCapability.SKILL) is True


def test_hermes_detected_with_venv_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Test Hermes Agent detection using hermes-agent venv fallback."""
    hermes_home = tmp_path / "custom_hermes"
    venv_exec = hermes_home / "hermes-agent" / "venv" / "Scripts" / "hermes.exe"
    venv_exec.parent.mkdir(parents=True, exist_ok=True)
    venv_exec.touch()

    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    monkeypatch.setattr(
        "aiaddons.agents.hermes.run_version_command", lambda cmd: "0.2.1"
    )
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")

    adapter = HermesAdapter()
    result = adapter.detect(project_path=tmp_path / "workspace")

    assert result.installed is True
    assert result.agent_id == "hermes"
    assert result.version == "0.2.1"
    assert result.executable_path == str(venv_exec)


def test_hermes_detected_via_global_config_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Test Hermes Agent detection when binary is absent but global ~/.hermes/config.yaml exists."""
    home_dir = tmp_path / "home"
    hermes_dir = home_dir / ".hermes"
    hermes_dir.mkdir(parents=True, exist_ok=True)
    (hermes_dir / "config.yaml").write_text("model: hermes-3\n", encoding="utf-8")

    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "empty_local"))
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    monkeypatch.setattr("pathlib.Path.home", lambda: home_dir)

    adapter = HermesAdapter()
    result = adapter.detect(project_path=tmp_path / "workspace")

    assert result.installed is True
    assert result.executable_path is None
    assert result.version is None
    assert result.config_path is not None
    assert "config.yaml" in result.config_path


def test_hermes_detected_via_hermes_home_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Test Hermes Agent detection when HERMES_HOME environment variable is set."""
    custom_home = tmp_path / "my_hermes"
    custom_home.mkdir(parents=True, exist_ok=True)
    (custom_home / "config.yaml").write_text("model: hermes-3\n", encoding="utf-8")

    monkeypatch.setenv("HERMES_HOME", str(custom_home))
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")

    adapter = HermesAdapter()
    result = adapter.detect(project_path=tmp_path / "workspace")

    assert result.installed is True
    assert result.config_path == str(custom_home / "config.yaml")


def test_hermes_not_detected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test Hermes Agent when neither binary nor config exists."""
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "empty_local"))
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")

    adapter = HermesAdapter()
    result = adapter.detect(project_path=tmp_path / "workspace")

    assert result.installed is False
    assert result.version is None
    assert result.executable_path is None


def test_hermes_malformed_version_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Test Hermes Agent detection when binary returns unparseable version output."""
    fake_binary = tmp_path / "bin" / "hermes"
    fake_binary.parent.mkdir(parents=True, exist_ok=True)
    fake_binary.touch()

    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "empty_local"))
    monkeypatch.setattr("shutil.which", lambda cmd: str(fake_binary) if cmd == "hermes" else None)
    monkeypatch.setattr("aiaddons.agents.hermes.run_version_command", lambda cmd: None)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")

    adapter = HermesAdapter()
    result = adapter.detect(project_path=tmp_path / "workspace")

    assert result.installed is True
    assert result.version is None
    assert result.executable_path == str(fake_binary)


def test_hermes_global_and_workspace_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verify Hermes Agent adapter returns canonical global config path and None for workspace config."""
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
    adapter = HermesAdapter()

    global_cfg = adapter.get_config_path(Scope.GLOBAL)
    assert global_cfg is not None
    assert "config.yaml" in str(global_cfg)

    # Workspace scope is not supported for config in Hermes
    ws_cfg = adapter.get_config_path(Scope.WORKSPACE, project_path=tmp_path)
    assert ws_cfg is None

    global_skill = adapter.get_skill_directory(Scope.GLOBAL)
    assert "skills" in str(global_skill)


def test_hermes_registered_in_manager() -> None:
    """Verify AgentDetectionManager registers Hermes."""
    manager = AgentDetectionManager()
    hermes = manager.get_adapter("hermes")

    assert hermes is not None
    assert hermes.agent_id == "hermes"
    assert hermes.name == "Hermes Agent"


def test_hermes_detection_manager_detect_all(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verify detect_agents includes hermes."""
    monkeypatch.setattr("shutil.which", lambda cmd: f"/usr/bin/{cmd}")
    monkeypatch.setattr(
        "aiaddons.agents.hermes.run_version_command", lambda cmd: "0.2.0"
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
        "aiaddons.agents.cursor.run_version_command", lambda cmd: "0.45.0"
    )
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    results = detect_agents(project_path=tmp_path)

    assert "hermes" in results
    assert results["hermes"].installed is True
    assert results["hermes"].version == "0.2.0"


def test_hermes_doctor_health_check_valid_yaml(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verify aiaddons doctor accepts valid YAML config for Hermes Agent."""
    home_dir = tmp_path / "home"
    hermes_dir = home_dir / ".hermes"
    hermes_dir.mkdir(parents=True, exist_ok=True)
    cfg_file = hermes_dir / "config.yaml"
    cfg_file.write_text(
        "model: hermes-3\nmcp_servers:\n  github-mcp:\n    command: npx\n    args: ['test']\n",
        encoding="utf-8",
    )

    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "empty_local"))
    monkeypatch.setattr("pathlib.Path.home", lambda: home_dir)
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/hermes" if cmd == "hermes" else None)
    monkeypatch.setattr("aiaddons.agents.hermes.run_version_command", lambda cmd: "0.2.0")

    engine = HealthCheckEngine(workspace_dir=tmp_path / "ws", store_dir=tmp_path / ".aiaddons")
    agent_items = engine.check_agents()

    hermes_items = [item for item in agent_items if "hermes" in item.check_id]
    assert len(hermes_items) >= 1
    assert hermes_items[0].status == HealthStatus.PASS
    assert "Hermes Agent detected" in hermes_items[0].message


def test_hermes_doctor_health_check_malformed_yaml(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verify aiaddons doctor flags malformed YAML config for Hermes Agent."""
    home_dir = tmp_path / "home"
    hermes_dir = home_dir / ".hermes"
    hermes_dir.mkdir(parents=True, exist_ok=True)
    cfg_file = hermes_dir / "config.yaml"
    cfg_file.write_text("invalid: yaml: [unclosed bracket\n", encoding="utf-8")

    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "empty_local"))
    monkeypatch.setattr("pathlib.Path.home", lambda: home_dir)
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/hermes" if cmd == "hermes" else None)
    monkeypatch.setattr("aiaddons.agents.hermes.run_version_command", lambda cmd: "0.2.0")

    engine = HealthCheckEngine(workspace_dir=tmp_path / "ws", store_dir=tmp_path / ".aiaddons")
    agent_items = engine.check_agents()

    hermes_items = [item for item in agent_items if "hermes" in item.check_id]
    assert len(hermes_items) >= 1
    assert hermes_items[0].status == HealthStatus.FAIL
    assert "malformed" in hermes_items[0].message.lower()


def test_hermes_global_installation_transaction_yaml(
    tmp_path: Path, sample_mcp_manifest: IntegrationManifest
) -> None:
    """Verify Hermes global MCP installation writes to YAML config with mcp_servers key."""
    adapter = HermesAdapter()
    detection = adapter.detect(project_path=tmp_path)
    detection.installed = True
    detection.global_config_path = str(tmp_path / "config.yaml")
    detection.config_path = str(tmp_path / "config.yaml")

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

    config_file = tmp_path / "config.yaml"
    assert config_file.exists()
    data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    assert "mcp_servers" in data
    assert "github-mcp" in data["mcp_servers"]
    assert data["mcp_servers"]["github-mcp"]["command"] == "npx"


def test_hermes_skill_installation_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sample_skill_manifest: IntegrationManifest
) -> None:
    """Verify Hermes skill installation into skills directory."""
    monkeypatch.chdir(tmp_path)
    adapter = HermesAdapter()
    detection = adapter.detect(project_path=tmp_path)
    detection.installed = True

    skill_src = tmp_path / "skill_source"
    skill_src.mkdir(parents=True, exist_ok=True)
    (skill_src / "SKILL.md").write_text("# Review Skill", encoding="utf-8")

    engine = InstallationEngine()
    plan = engine.generate_plan(sample_skill_manifest, detection, Scope.GLOBAL)
    assert plan.target_scope == Scope.GLOBAL

    for op in plan.planned_operations:
        op.target_root = str(tmp_path)
        if hasattr(op, "source_dir"):
            op.source_dir = str(skill_src)

    exec_engine = ExecutionEngine()
    res = exec_engine.execute_plan(plan, dry_run=False)
    assert res.status == ExecutionStatus.SUCCESS
