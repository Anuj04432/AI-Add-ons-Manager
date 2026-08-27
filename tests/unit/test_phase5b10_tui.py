"""Unit tests for Phase 5B.10 Textual TUI interface."""

import asyncio
from pathlib import Path
from unittest.mock import patch

from aiaddons.core.models.agent import AgentCapability, AgentDetectionResult, Scope
from aiaddons.tui.app import AIAddonsTUIApp


def _create_sample_registry(tmp_path: Path) -> Path:
    reg_dir = tmp_path / "registry"
    reg_dir.mkdir(parents=True, exist_ok=True)
    manifest_file = reg_dir / "github-mcp.json"
    dummy_checksum = "sha256:" + "a" * 64
    manifest_file.write_text(
        f"""{{
            "id": "github-mcp",
            "name": "GitHub MCP Server",
            "version": "1.0.0",
            "description": "GitHub MCP integration",
            "license": "MIT",
            "category": "developer-tools",
            "integration_type": "mcp",
            "target_agents": ["claude-code"],
            "supported_scopes": ["global", "workspace"],
            "source": {{
                "source_type": "package",
                "package_name": "@modelcontextprotocol/server-github",
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
                    "package_name": "@modelcontextprotocol/server-github"
                }}
            }}
        }}""",
        encoding="utf-8",
    )
    return reg_dir


def _mock_claude_agent(tmp_path: Path) -> AgentDetectionResult:
    return AgentDetectionResult(
        agent_id="claude-code",
        name="Claude Code",
        installed=True,
        supported_scopes=[Scope.GLOBAL, Scope.WORKSPACE],
        capabilities=[AgentCapability.MCP],
        workspace_config_path=str(tmp_path / ".claude.json"),
    )


def test_tui_app_mount_and_selection(tmp_path: Path) -> None:
    """Verify TUI mounts, populates add-on list, and delegates to core engines."""

    async def _run_test() -> None:
        reg_dir = _create_sample_registry(tmp_path)
        agent = _mock_claude_agent(tmp_path)

        mock_mgr = patch(
            "aiaddons.tui.app.AgentDetectionManager.detect_agents",
            return_value={"claude-code": agent},
        )
        with mock_mgr:
            tui_app = AIAddonsTUIApp(
                registry_dir=reg_dir,
                workspace_dir=tmp_path,
                store_dir=tmp_path / ".aiaddons",
            )
            async with tui_app.run_test() as pilot:
                assert tui_app.is_running
                assert len(tui_app.manifests) == 1
                assert tui_app.manifests[0].id == "github-mcp"

                # Select item
                tui_app.selected_manifest = tui_app.manifests[0]
                tui_app._update_details_view()
                await pilot.pause()

                # Press compatibility check button
                await pilot.click("#btn-compat")
                await pilot.pause()

                # Press preview plan button
                tui_app.action_preview_plan()
                await pilot.pause()

                assert tui_app.current_plan is not None
                assert tui_app.current_plan.addon_id == "github-mcp"

    asyncio.run(_run_test())
