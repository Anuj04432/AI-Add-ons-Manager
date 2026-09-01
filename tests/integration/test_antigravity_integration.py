"""Integration tests for Antigravity CLI MCP and Skill installation lifecycle."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aiaddons.agents.antigravity import AntigravityAdapter
from aiaddons.agents.manager import AgentDetectionManager
from aiaddons.cli.main import app
from aiaddons.core.models.agent import AgentCapability, AgentDetectionResult, Scope
from aiaddons.state.lockfile import LockfileManager
from aiaddons.state.store import InstalledStateStore
from aiaddons.state.transaction import TransactionWALManager

runner = CliRunner()


def test_antigravity_mcp_install_and_remove_cycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end integration test: install MCP server for Antigravity -> verify config -> remove -> verify."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    agents_dir = workspace_dir / ".agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    mcp_config_file = agents_dir / "mcp_config.json"
    mcp_config_file.write_text("{}", encoding="utf-8")

    state_dir = tmp_path / ".aiaddons"
    state_dir.mkdir(parents=True, exist_ok=True)
    wal_dir = state_dir / "transactions"
    wal_dir.mkdir(parents=True, exist_ok=True)

    reg_dir = tmp_path / "registry"
    reg_dir.mkdir(parents=True, exist_ok=True)
    (reg_dir / "test-db-mcp.json").write_text(
        """{
            "id": "test-db-mcp",
            "name": "Test Database MCP",
            "version": "1.0.0",
            "description": "Database MCP for testing",
            "license": "MIT",
            "category": "developer-tools",
            "integration_type": "mcp",
            "target_agents": ["antigravity"],
            "supported_scopes": ["workspace"],
            "source": {
                "source_type": "package",
                "package_name": "@test/db-mcp",
                "checksum": "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
            },
            "trust": {"verification_status": "verified", "publisher": {"name": "Test Team"}},
            "handler_spec": {
                "mcp": {
                    "transport": "stdio",
                    "runtime": "npx",
                    "package_name": "@test/db-mcp",
                    "env_vars": [
                        {"name": "DB_CONNECTION_STRING", "required": false, "secret": false}
                    ]
                }
            }
        }""",
        encoding="utf-8",
    )

    state_store = InstalledStateStore(store_dir=state_dir)
    lockfile_mgr = LockfileManager()
    wal_mgr = TransactionWALManager(transactions_dir=wal_dir)

    monkeypatch.setattr(
        "aiaddons.cli.commands.install.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )
    monkeypatch.setattr(
        "aiaddons.cli.commands.remove.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )
    monkeypatch.setattr(
        "aiaddons.cli.commands.install.TransactionWALManager",
        lambda *args, **kwargs: wal_mgr,
    )
    monkeypatch.setattr(
        "aiaddons.cli.commands.remove.TransactionWALManager",
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
        agent_id="antigravity",
        name="Antigravity CLI",
        installed=True,
        version="1.0.0",
        workspace_config_path=str(mcp_config_file),
        config_path=str(mcp_config_file),
        capabilities=[AgentCapability.MCP, AgentCapability.SKILL],
    )

    class MockDetectionManager:
        def detect_agents(self, project_path: Path | None = None) -> dict[str, AgentDetectionResult]:
            return {"antigravity": fake_detection}

        def get_adapter(self, agent_id: str):
            if agent_id == "antigravity":
                return AntigravityAdapter()
            return None

    monkeypatch.setattr(
        "aiaddons.cli.commands.install.AgentDetectionManager",
        MockDetectionManager,
    )
    monkeypatch.setattr(
        "aiaddons.cli.commands.remove.AgentDetectionManager",
        MockDetectionManager,
    )

    monkeypatch.chdir(workspace_dir)

    # 1. Install MCP server for antigravity without mocking ExternalRunner.execute
    install_res = runner.invoke(
        app,
        [
            "install",
            "test-db-mcp",
            "--agent",
            "antigravity",
            "--scope",
            "workspace",
            "--registry",
            str(reg_dir),
            "--yes",
            "--json",
        ],
    )

    assert install_res.exit_code == 0, f"Install failed: {install_res.stdout}"
    install_data = json.loads(install_res.stdout)
    assert install_data["success"] is True
    assert install_data["agent"] == "antigravity"

    # Verify config file written
    assert mcp_config_file.exists()
    config_content = json.loads(mcp_config_file.read_text(encoding="utf-8"))
    assert "mcpServers" in config_content
    assert "test-db-mcp" in config_content["mcpServers"]
    assert config_content["mcpServers"]["test-db-mcp"]["command"] == "npx"
    assert config_content["mcpServers"]["test-db-mcp"]["args"] == ["@test/db-mcp"]

    # 2. Remove MCP server
    remove_res = runner.invoke(
        app,
        [
            "remove",
            "test-db-mcp",
            "--agent",
            "antigravity",
            "--scope",
            "workspace",
            "--registry",
            str(reg_dir),
            "--yes",
            "--json",
        ],
    )

    assert remove_res.exit_code == 0, f"Remove failed: {remove_res.stdout}"
    remove_data = json.loads(remove_res.stdout)
    assert remove_data["success"] is True

    # Verify MCP server was cleanly removed from JSON
    final_config = json.loads(mcp_config_file.read_text(encoding="utf-8"))
    assert "test-db-mcp" not in final_config.get("mcpServers", {})


def test_antigravity_skill_install_and_remove_cycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end integration test: install Skill for Antigravity -> verify -> remove -> verify."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    agents_dir = workspace_dir / ".agents"
    agents_dir.mkdir(parents=True, exist_ok=True)

    (workspace_dir / "SKILL.md").write_text("# Test Guidelines", encoding="utf-8")

    state_dir = tmp_path / ".aiaddons"
    state_dir.mkdir(parents=True, exist_ok=True)
    wal_dir = state_dir / "transactions"
    wal_dir.mkdir(parents=True, exist_ok=True)

    reg_dir = tmp_path / "registry"
    reg_dir.mkdir(parents=True, exist_ok=True)
    (reg_dir / "test-guidelines-skill.json").write_text(
        """{
            "id": "test-guidelines-skill",
            "name": "Test Guidelines Skill",
            "version": "1.0.0",
            "description": "Guidelines skill for testing",
            "license": "MIT",
            "category": "workflow",
            "integration_type": "skill",
            "target_agents": ["antigravity"],
            "supported_scopes": ["workspace"],
            "source": {"source_type": "local", "path": "."},
            "trust": {"verification_status": "verified", "publisher": {"name": "Test Team"}},
            "handler_spec": {
                "skill": {
                    "skill_file": "SKILL.md"
                }
            }
        }""",
        encoding="utf-8",
    )

    state_store = InstalledStateStore(store_dir=state_dir)
    lockfile_mgr = LockfileManager()
    wal_mgr = TransactionWALManager(transactions_dir=wal_dir)

    monkeypatch.setattr(
        "aiaddons.cli.commands.install.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )
    monkeypatch.setattr(
        "aiaddons.cli.commands.remove.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )
    monkeypatch.setattr(
        "aiaddons.cli.commands.install.TransactionWALManager",
        lambda *args, **kwargs: wal_mgr,
    )
    monkeypatch.setattr(
        "aiaddons.cli.commands.remove.TransactionWALManager",
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
        agent_id="antigravity",
        name="Antigravity CLI",
        installed=True,
        version="1.0.0",
        workspace_config_path=str(agents_dir / "mcp_config.json"),
        config_path=str(agents_dir / "mcp_config.json"),
        capabilities=[AgentCapability.MCP, AgentCapability.SKILL],
    )

    class MockDetectionManager:
        def detect_agents(self, project_path: Path | None = None) -> dict[str, AgentDetectionResult]:
            return {"antigravity": fake_detection}

        def get_adapter(self, agent_id: str):
            if agent_id == "antigravity":
                return AntigravityAdapter()
            return None

    monkeypatch.setattr(
        "aiaddons.cli.commands.install.AgentDetectionManager",
        MockDetectionManager,
    )
    monkeypatch.setattr(
        "aiaddons.cli.commands.remove.AgentDetectionManager",
        MockDetectionManager,
    )

    monkeypatch.chdir(workspace_dir)

    # 1. Install Skill for Antigravity
    install_res = runner.invoke(
        app,
        [
            "install",
            "test-guidelines-skill",
            "--agent",
            "antigravity",
            "--scope",
            "workspace",
            "--registry",
            str(reg_dir),
            "--yes",
            "--json",
        ],
    )

    assert install_res.exit_code == 0, f"Install failed: {install_res.stdout}"
    install_data = json.loads(install_res.stdout)
    assert install_data["success"] is True

    # Verify skill installed to .agents/skills/test-guidelines-skill/SKILL.md
    installed_skill_file = agents_dir / "skills" / "test-guidelines-skill" / "SKILL.md"
    assert installed_skill_file.exists()
    assert "# Test Guidelines" in installed_skill_file.read_text(encoding="utf-8")

    # 2. Remove Skill
    remove_res = runner.invoke(
        app,
        [
            "remove",
            "test-guidelines-skill",
            "--agent",
            "antigravity",
            "--scope",
            "workspace",
            "--registry",
            str(reg_dir),
            "--yes",
            "--json",
        ],
    )

    assert remove_res.exit_code == 0, f"Remove failed: {remove_res.stdout}"
    remove_data = json.loads(remove_res.stdout)
    assert remove_data["success"] is True

    # Verify skill directory was removed
    assert not installed_skill_file.exists()
    assert not (agents_dir / "skills" / "test-guidelines-skill").exists()


def test_real_stdio_mcp_server_install_does_not_launch_daemon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test: installing a real stdio MCP server manifest does not attempt to launch the server binary and completes in milliseconds."""
    import time
    from unittest.mock import patch

    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    agents_dir = workspace_dir / ".agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    mcp_config_file = agents_dir / "mcp_config.json"
    mcp_config_file.write_text("{}", encoding="utf-8")

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
        agent_id="antigravity",
        name="Antigravity CLI",
        installed=True,
        version="1.0.0",
        workspace_config_path=str(mcp_config_file),
        config_path=str(mcp_config_file),
        capabilities=[AgentCapability.MCP, AgentCapability.SKILL],
    )

    class MockDetectionManager:
        def detect_agents(self, project_path: Path | None = None) -> dict[str, AgentDetectionResult]:
            return {"antigravity": fake_detection}

        def get_adapter(self, agent_id: str):
            if agent_id == "antigravity":
                return AntigravityAdapter()
            return None

    monkeypatch.setattr(
        "aiaddons.cli.commands.install.AgentDetectionManager",
        MockDetectionManager,
    )
    monkeypatch.chdir(workspace_dir)

    real_registry_path = Path(__file__).parent.parent.parent / "registry"

    with patch("aiaddons.core.execution.external.runner.ExternalRunner.execute") as mock_exec:
        t0 = time.perf_counter()
        install_res = runner.invoke(
            app,
            [
                "install",
                "filesystem-mcp",
                "--agent",
                "antigravity",
                "--scope",
                "workspace",
                "--registry",
                str(real_registry_path),
                "--yes",
                "--json",
            ],
        )
        elapsed = time.perf_counter() - t0

        # Subprocess execute must NEVER have been called
        assert mock_exec.call_count == 0, "ExternalRunner.execute must not be called during MCP install!"
        # Execution must complete quickly in well under 5 seconds (not 120s timeout)
        assert elapsed < 5.0, f"MCP install took {elapsed:.2f}s, expected < 5s"
        assert install_res.exit_code == 0, f"Install failed: {install_res.stdout}"
        install_data = json.loads(install_res.stdout)
        assert install_data["success"] is True

    # Verify configuration written accurately
    assert mcp_config_file.exists()
    config_content = json.loads(mcp_config_file.read_text(encoding="utf-8"))
    assert "mcpServers" in config_content
    assert "filesystem-mcp" in config_content["mcpServers"]
    assert config_content["mcpServers"]["filesystem-mcp"]["command"] == "npx"
    assert config_content["mcpServers"]["filesystem-mcp"]["args"] == [
        "@modelcontextprotocol/server-filesystem",
    ]

