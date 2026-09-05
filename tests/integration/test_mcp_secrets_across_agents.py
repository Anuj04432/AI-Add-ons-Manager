"""Integration tests verifying resolved secret values in MCP config across all supported agents."""

import json
from pathlib import Path
import pytest
from typer.testing import CliRunner

from aiaddons.agents.antigravity import AntigravityAdapter
from aiaddons.agents.claude_code import ClaudeCodeAdapter
from aiaddons.agents.codex import CodexAdapter
from aiaddons.cli.main import app
from aiaddons.core.models.agent import AgentCapability, AgentDetectionResult, Scope
from aiaddons.state.lockfile import LockfileManager
from aiaddons.state.store import InstalledStateStore
from aiaddons.state.transaction import TransactionWALManager

runner = CliRunner()


@pytest.mark.parametrize(
    "agent_id,adapter_cls,config_rel_path",
    [
        ("antigravity", AntigravityAdapter, ".agents/mcp_config.json"),
        ("claude-code", ClaudeCodeAdapter, ".claude.json"),
        ("codex", CodexAdapter, ".codex/config.json"),
    ],
)
def test_mcp_secret_value_written_correctly_across_agents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    agent_id: str,
    adapter_cls: type,
    config_rel_path: str,
) -> None:
    """Verify that installing an MCP server with a required secret writes the actual resolved secret VALUE,

    not the variable name, into the env dictionary for Claude Code, Codex, and Antigravity.
    """
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    config_file = workspace_dir / config_rel_path
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text("{}", encoding="utf-8")

    state_dir = tmp_path / ".aiaddons"
    state_dir.mkdir(parents=True, exist_ok=True)
    wal_dir = state_dir / "transactions"
    wal_dir.mkdir(parents=True, exist_ok=True)

    state_store = InstalledStateStore(store_dir=state_dir)
    wal_mgr = TransactionWALManager(transactions_dir=wal_dir)

    monkeypatch.setattr(
        "aiaddons.cli.commands.install.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )
    monkeypatch.setattr(
        "aiaddons.cli.commands.install.TransactionWALManager",
        lambda *args, **kwargs: wal_mgr,
    )
    monkeypatch.setattr(
        "aiaddons.core.execution.engine.TransactionWALManager",
        lambda *args, **kwargs: wal_mgr,
    )
    monkeypatch.setattr(
        "aiaddons.core.installer.engine.TransactionWALManager",
        lambda *args, **kwargs: wal_mgr,
    )

    fake_detection = AgentDetectionResult(
        agent_id=agent_id,
        name=agent_id.title(),
        installed=True,
        version="1.0.0",
        workspace_config_path=str(config_file),
        config_path=str(config_file),
        capabilities=[AgentCapability.MCP, AgentCapability.SKILL],
    )

    class MockDetectionManager:
        def detect_agents(self, project_path: Path | None = None) -> dict[str, AgentDetectionResult]:
            return {agent_id: fake_detection}

        def get_adapter(self, target_agent_id: str):
            if target_agent_id == agent_id:
                return adapter_cls()
            return None

    monkeypatch.setattr(
        "aiaddons.cli.commands.install.AgentDetectionManager",
        MockDetectionManager,
    )
    monkeypatch.chdir(workspace_dir)

    test_secret_token = "ghp_secure_actual_token_value_9876543210"
    monkeypatch.setenv("GITHUB_PERSONAL_ACCESS_TOKEN", test_secret_token)

    real_registry_path = Path(__file__).parent.parent.parent / "registry"

    install_res = runner.invoke(
        app,
        [
            "install",
            "github-mcp",
            "--agent",
            agent_id,
            "--scope",
            "workspace",
            "--registry",
            str(real_registry_path),
            "--yes",
            "--json",
        ],
    )

    assert install_res.exit_code == 0, f"Install failed for {agent_id}: {install_res.stdout}"
    install_data = json.loads(install_res.stdout)
    assert install_data["success"] is True

    # Verify config file contents
    assert config_file.exists()
    config_content = json.loads(config_file.read_text(encoding="utf-8"))
    assert "mcpServers" in config_content
    assert "github-mcp" in config_content["mcpServers"]
    env_block = config_content["mcpServers"]["github-mcp"].get("env", {})
    assert "GITHUB_PERSONAL_ACCESS_TOKEN" in env_block

    # CRITICAL ASSERTION: The value must equal the resolved secret token, NOT the variable name
    actual_value = env_block["GITHUB_PERSONAL_ACCESS_TOKEN"]
    assert actual_value == test_secret_token, (
        f"Expected secret value '{test_secret_token}' but got '{actual_value}' for {agent_id}"
    )
    assert actual_value != "GITHUB_PERSONAL_ACCESS_TOKEN"

    # CRITICAL SECURITY ASSERTION: Secret must NOT be persisted in lockfile or WAL logs
    lockfile_path = workspace_dir / "aiaddons.lock"
    if lockfile_path.exists():
        assert test_secret_token not in lockfile_path.read_text(encoding="utf-8")

    for wal_file in wal_dir.glob("*.json"):
        assert test_secret_token not in wal_file.read_text(encoding="utf-8")
