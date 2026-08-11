"""Unit tests for Claude Code adapter, Codex adapter, and AgentDetectionManager."""

from pathlib import Path

import pytest

from aiaddons.agents.claude_code import ClaudeCodeAdapter
from aiaddons.agents.codex import CodexAdapter
from aiaddons.agents.manager import AgentDetectionManager, detect_agents
from aiaddons.core.models.agent import AgentCapability, AgentDetectionResult, Scope


def test_claude_detected_with_binary_and_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Test Claude Code detection when binary and config exist."""
    fake_binary = tmp_path / "bin" / "claude"
    fake_binary.parent.mkdir(parents=True, exist_ok=True)
    fake_binary.touch()

    monkeypatch.setattr("shutil.which", lambda cmd: str(fake_binary) if cmd == "claude" else None)
    monkeypatch.setattr("aiaddons.agents.claude_code.run_version_command", lambda cmd: "0.2.1")
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    adapter = ClaudeCodeAdapter()
    result = adapter.detect(project_path=tmp_path)

    assert result.installed is True
    assert result.agent_id == "claude-code"
    assert result.name == "Claude Code"
    assert result.version == "0.2.1"
    assert result.executable_path == str(fake_binary)
    assert AgentCapability.MCP in result.capabilities
    assert AgentCapability.SKILL in result.capabilities
    assert adapter.supports_capability(AgentCapability.MCP) is True


def test_claude_detected_via_config_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Test Claude Code detection when binary is absent but config file exists."""
    config_file = tmp_path / ".claude.json"
    config_file.touch()

    monkeypatch.setattr("shutil.which", lambda cmd: None)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    adapter = ClaudeCodeAdapter()
    result = adapter.detect(project_path=tmp_path)

    assert result.installed is True
    assert result.executable_path is None
    assert result.version is None
    assert result.config_path == str(config_file)


def test_claude_not_detected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test Claude Code when neither binary nor config exists."""
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")

    adapter = ClaudeCodeAdapter()
    result = adapter.detect(project_path=tmp_path / "project")

    assert result.installed is False
    assert result.version is None
    assert result.executable_path is None


def test_claude_malformed_version_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Test Claude Code detection when binary returns malformed version output."""
    fake_binary = tmp_path / "bin" / "claude"
    fake_binary.parent.mkdir(parents=True, exist_ok=True)
    fake_binary.touch()

    monkeypatch.setattr("shutil.which", lambda cmd: str(fake_binary) if cmd == "claude" else None)
    monkeypatch.setattr("aiaddons.agents.claude_code.run_version_command", lambda cmd: None)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    adapter = ClaudeCodeAdapter()
    result = adapter.detect(project_path=tmp_path)

    assert result.installed is True
    assert result.version is None
    assert result.executable_path == str(fake_binary)


def test_codex_detected_with_binary_and_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Test OpenAI Codex detection when binary and config exist."""
    fake_binary = tmp_path / "bin" / "codex"
    fake_binary.parent.mkdir(parents=True, exist_ok=True)
    fake_binary.touch()

    monkeypatch.setattr("shutil.which", lambda cmd: str(fake_binary) if cmd == "codex" else None)
    monkeypatch.setattr("aiaddons.agents.codex.run_version_command", lambda cmd: "1.0.4")
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    adapter = CodexAdapter()
    result = adapter.detect(project_path=tmp_path)

    assert result.installed is True
    assert result.agent_id == "codex"
    assert result.name == "OpenAI Codex"
    assert result.version == "1.0.4"
    assert result.executable_path == str(fake_binary)
    assert adapter.supports_capability(AgentCapability.SKILL) is True


def test_codex_detected_via_config_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Test OpenAI Codex detection when config directory exists."""
    config_dir = tmp_path / ".codex"
    config_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("shutil.which", lambda cmd: None)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    adapter = CodexAdapter()
    result = adapter.detect(project_path=tmp_path)

    assert result.installed is True
    assert result.config_path == str(config_dir)


def test_codex_not_detected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test OpenAI Codex when neither binary nor config exists."""
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")

    adapter = CodexAdapter()
    result = adapter.detect(project_path=tmp_path / "project")

    assert result.installed is False
    assert result.version is None


def test_detection_manager_all_installed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Test AgentDetectionManager when all agents are detected."""
    monkeypatch.setattr("shutil.which", lambda cmd: f"/usr/bin/{cmd}")
    monkeypatch.setattr("aiaddons.agents.claude_code.run_version_command", lambda cmd: "0.2.1")
    monkeypatch.setattr("aiaddons.agents.codex.run_version_command", lambda cmd: "1.0.0")
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    results = detect_agents(project_path=tmp_path)

    assert "claude-code" in results
    assert "codex" in results
    assert results["claude-code"].installed is True
    assert results["codex"].installed is True


def test_detection_manager_handles_exception_in_adapter() -> None:
    """Test AgentDetectionManager handles unexpected exceptions without crashing."""

    class FaultyAdapter:
        agent_id = "faulty-agent"
        name = "Faulty Agent"
        capabilities = [AgentCapability.MCP]

        def detect(self, project_path: Path | None = None) -> AgentDetectionResult:
            raise RuntimeError("Unexpected adapter failure")

        def get_config_path(self, scope: Scope, project_path: Path | None = None) -> Path | None:
            return None

        def supports_capability(self, capability: AgentCapability) -> bool:
            return False

        def get_skill_directory(self, scope: Scope, project_path: Path | None = None) -> Path:
            return Path("/tmp/faulty/skills")


    manager = AgentDetectionManager(adapters=[FaultyAdapter()])
    results = manager.detect_agents()

    assert "faulty-agent" in results
    res = results["faulty-agent"]
    assert res.installed is False
    assert res.detection_error is not None
    assert "Unexpected adapter failure" in res.detection_error


def test_scope_enum_from_str() -> None:
    """Test Scope.from_str method with standard values and aliases."""
    assert Scope.from_str("global") == Scope.GLOBAL
    assert Scope.from_str("user") == Scope.GLOBAL
    assert Scope.from_str("GLOBAL") == Scope.GLOBAL
    assert Scope.from_str("workspace") == Scope.WORKSPACE
    assert Scope.from_str("project") == Scope.WORKSPACE
    assert Scope.from_str("local") == Scope.WORKSPACE

    with pytest.raises(ValueError, match="Invalid scope"):
        Scope.from_str("unknown_scope")


def test_agent_detection_result_scope_paths() -> None:
    """Test AgentDetectionResult tracking global/workspace paths and scope lookup."""
    res = AgentDetectionResult(
        agent_id="test-agent",
        name="Test Agent",
        installed=True,
        global_config_path="/home/user/.test.json",
        workspace_config_path="/project/.test.json",
        supported_scopes=[Scope.GLOBAL, Scope.WORKSPACE],
    )

    assert res.global_config_path == "/home/user/.test.json"
    assert res.workspace_config_path == "/project/.test.json"
    assert res.get_config_path_for_scope(Scope.GLOBAL) == "/home/user/.test.json"
    assert res.get_config_path_for_scope("user") == "/home/user/.test.json"
    assert res.get_config_path_for_scope(Scope.WORKSPACE) == "/project/.test.json"
    assert res.get_config_path_for_scope("project") == "/project/.test.json"
    assert Scope.GLOBAL in res.supported_scopes
    assert Scope.WORKSPACE in res.supported_scopes

