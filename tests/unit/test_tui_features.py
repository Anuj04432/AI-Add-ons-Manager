"""Comprehensive unit tests for Textual TUI screens, actions, modals, and navigation."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

from textual.widgets import Checkbox, ListView, Markdown, Static

from aiaddons.core.execution.external.models import ExternalExecutionResult, ExternalRuntime
from aiaddons.core.models.agent import AgentCapability, AgentDetectionResult, Scope
from aiaddons.core.models.manifest import IntegrationType
from aiaddons.state.lockfile import LockfileManager
from aiaddons.state.store import InstalledAddonRecord, InstalledStateStore
from aiaddons.tui.app import AIAddonsTUIApp
from aiaddons.tui.screens.health import HealthScreen
from aiaddons.tui.screens.modals import DriftConfirmModal, RemoveConfirmModal, UpdatePlanModal
from aiaddons.tui.screens.sync import SyncScreen


def _setup_test_environment(tmp_path: Path) -> tuple[Path, Path, Path, AgentDetectionResult]:
    """Create sample registry, store dir, workspace dir, and mock agent."""
    reg_dir = tmp_path / "registry"
    reg_dir.mkdir(parents=True, exist_ok=True)
    store_dir = tmp_path / ".aiaddons"
    store_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)

    dummy_checksum = "sha256:" + "a" * 64

    # Manifest 1: github-mcp v1.2.0
    (reg_dir / "github-mcp.json").write_text(
        json.dumps(
            {
                "id": "github-mcp",
                "name": "GitHub MCP Server",
                "version": "1.2.0",
                "description": "GitHub MCP integration",
                "license": "MIT",
                "category": "developer-tools",
                "integration_type": "mcp",
                "target_agents": ["claude-code"],
                "supported_scopes": ["global", "workspace"],
                "source": {
                    "source_type": "package",
                    "package_name": "@modelcontextprotocol/server-github",
                    "checksum": dummy_checksum,
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
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    # Manifest 2: postgres-mcp v1.0.0
    (reg_dir / "postgres-mcp.json").write_text(
        json.dumps(
            {
                "id": "postgres-mcp",
                "name": "PostgreSQL MCP Server",
                "version": "1.0.0",
                "description": "PostgreSQL integration",
                "license": "MIT",
                "category": "database",
                "integration_type": "mcp",
                "target_agents": ["claude-code"],
                "supported_scopes": ["global", "workspace"],
                "source": {
                    "source_type": "package",
                    "package_name": "@modelcontextprotocol/server-postgres",
                    "checksum": dummy_checksum,
                },
                "trust": {
                    "verification_status": "verified",
                    "publisher": {"name": "MCP Team"},
                },
                "handler_spec": {
                    "mcp": {
                        "transport": "stdio",
                        "runtime": "npx",
                        "package_name": "@modelcontextprotocol/server-postgres",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    claude_cfg = workspace_dir / ".claude.json"
    claude_cfg.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "github-mcp": {
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-github"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    agent = AgentDetectionResult(
        agent_id="claude-code",
        name="Claude Code",
        installed=True,
        supported_scopes=[Scope.GLOBAL, Scope.WORKSPACE],
        capabilities=[AgentCapability.MCP, AgentCapability.SKILL],
        workspace_config_path=str(claude_cfg),
    )

    return reg_dir, store_dir, workspace_dir, agent


def test_health_screen_renders_summary_and_drilldown(tmp_path: Path) -> None:
    """Verify HealthScreen executes 9 categories, displays overall status badge, and drills down per category."""
    async def _run_test() -> None:
        reg_dir, store_dir, workspace_dir, agent = _setup_test_environment(tmp_path)

        app = AIAddonsTUIApp(registry_dir=reg_dir, workspace_dir=workspace_dir, store_dir=store_dir)
        async with app.run_test() as pilot:
            assert app.is_running

            # Open health screen via action / keybinding
            app.action_doctor()
            await pilot.pause()

            assert isinstance(app.screen, HealthScreen)
            health_screen: HealthScreen = app.screen

            # Verify summary badge and report
            assert health_screen.report is not None
            assert health_screen.report.overall_status is not None
            summary_badge = health_screen.query_one("#health-summary-badge", Static)
            assert summary_badge is not None

            # Verify category list populated with 9 categories
            cat_list = health_screen.query_one("#category-list", ListView)
            assert len(cat_list.children) == 9

            # Test drill-down into Security category
            cat_list.index = 8  # Security is last
            await pilot.pause()

            detail_md = health_screen.query_one("#health-detail-view", Markdown)
            assert "Security Diagnostics" in detail_md.source or "Diagnostics" in detail_md.source

            # Test refresh action
            await pilot.click("#btn-health-refresh")
            await pilot.pause()
            assert health_screen.report is not None

            # Test back to browser
            await pilot.click("#btn-health-back")
            await pilot.pause()
            assert not isinstance(app.screen, HealthScreen)

    asyncio.run(_run_test())


def test_remove_action_with_drift_detection(tmp_path: Path) -> None:
    """Verify Remove action detects drift, shows DriftConfirmModal, and executes forced removal."""
    async def _run_test() -> None:
        reg_dir, store_dir, workspace_dir, agent = _setup_test_environment(tmp_path)

        # Pre-record github-mcp in state store as v1.0.0
        state_store = InstalledStateStore(store_dir=store_dir)
        state_store.record_installation(
            InstalledAddonRecord(
                target_agent="claude-code",
                scope=Scope.WORKSPACE,
                addon_id="github-mcp",
                name="GitHub MCP Server",
                version="1.0.0",
                integration_type=IntegrationType.MCP,
                installed_at="2026-08-27T00:00:00Z",
                installed_files=[],
            )
        )

        # Intentionally corrupt/remove the MCP server entry in .claude.json to introduce drift
        claude_cfg = workspace_dir / ".claude.json"
        claude_cfg.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")

        mock_mgr = patch(
            "aiaddons.tui.app.AgentDetectionManager.detect_agents",
            return_value={"claude-code": agent},
        )

        with mock_mgr:
            app = AIAddonsTUIApp(registry_dir=reg_dir, workspace_dir=workspace_dir, store_dir=store_dir)
            async with app.run_test() as pilot:
                # Select github-mcp
                github_manifest = next(m for m in app.manifests if m.id == "github-mcp")
                app.selected_manifest = github_manifest
                app._update_details_view()
                await pilot.pause()

                remove_btn = app.query_one("#btn-remove")
                assert not remove_btn.disabled

                # Trigger removal -> should show DriftConfirmModal
                app.action_remove()
                await pilot.pause()

                assert isinstance(app.screen, DriftConfirmModal)
                drift_modal: DriftConfirmModal = app.screen
                assert len(drift_modal.drifts) > 0

                # Dismiss modal with confirmation (True)
                drift_modal.dismiss(True)
                await pilot.pause()

                # Verify item was removed from state store
                rec = state_store.get_record("claude-code", Scope.WORKSPACE, "github-mcp")
                assert rec is None

    asyncio.run(_run_test())


def test_update_action_shows_plan_and_executes(tmp_path: Path) -> None:
    """Verify Update action presents dry-run UpdatePlanModal and updates version on confirmation."""
    async def _run_test() -> None:
        reg_dir, store_dir, workspace_dir, agent = _setup_test_environment(tmp_path)

        # Pre-record github-mcp as installed v1.0.0 (registry has v1.2.0)
        state_store = InstalledStateStore(store_dir=store_dir)
        state_store.record_installation(
            InstalledAddonRecord(
                target_agent="claude-code",
                scope=Scope.WORKSPACE,
                addon_id="github-mcp",
                name="GitHub MCP Server",
                version="1.0.0",
                integration_type=IntegrationType.MCP,
                installed_at="2026-08-27T00:00:00Z",
                installed_files=[],
            )
        )

        mock_mgr = patch(
            "aiaddons.tui.app.AgentDetectionManager.detect_agents",
            return_value={"claude-code": agent},
        )

        with mock_mgr:
            app = AIAddonsTUIApp(registry_dir=reg_dir, workspace_dir=workspace_dir, store_dir=store_dir)
            async with app.run_test() as pilot:
                github_manifest = next(m for m in app.manifests if m.id == "github-mcp")
                app.selected_manifest = github_manifest
                app._update_details_view()
                await pilot.pause()

                update_btn = app.query_one("#btn-update")
                assert not update_btn.disabled

                # Trigger update -> opens UpdatePlanModal
                app.action_update()
                await pilot.pause()

                assert isinstance(app.screen, UpdatePlanModal)
                plan_modal: UpdatePlanModal = app.screen
                assert len(plan_modal.plan.items) == 1
                assert plan_modal.plan.items[0].target_version == "1.2.0"

                # Dismiss modal with confirmation (True)
                plan_modal.dismiss(True)
                await pilot.pause()

                # Verify updated version in state store
                updated_rec = state_store.get_record("claude-code", Scope.WORKSPACE, "github-mcp")
                assert updated_rec is not None
                assert updated_rec.version == "1.2.0"

    asyncio.run(_run_test())


def test_sync_screen_diff_and_toggles(tmp_path: Path) -> None:
    """Verify SyncScreen computes diff, respects prune/update checkboxes, and executes sync."""
    async def _run_test() -> None:
        reg_dir, store_dir, workspace_dir, agent = _setup_test_environment(tmp_path)

        # Write a lockfile expecting postgres-mcp
        lockfile_path = workspace_dir / "aiaddons.lock"
        lockfile_path.write_text(
            """version: "1.0"
addons:
  claude-code:postgres-mcp:
    addon_id: postgres-mcp
    name: PostgreSQL MCP Server
    target_agent: claude-code
    version: 1.0.0
    integration_type: mcp
    checksum: null
""",
            encoding="utf-8",
        )

        # Installed state has github-mcp (unmanaged/extra)
        state_store = InstalledStateStore(store_dir=store_dir)
        state_store.record_installation(
            InstalledAddonRecord(
                target_agent="claude-code",
                scope=Scope.WORKSPACE,
                addon_id="github-mcp",
                name="GitHub MCP Server",
                version="1.0.0",
                integration_type=IntegrationType.MCP,
                installed_at="2026-08-27T00:00:00Z",
                installed_files=[],
            )
        )

        mock_mgr = patch(
            "aiaddons.tui.app.AgentDetectionManager.detect_agents",
            return_value={"claude-code": agent},
        )
        mock_runner = patch(
            "aiaddons.core.execution.external.runner.ExternalRunner.execute",
            return_value=ExternalExecutionResult(
                success=True,
                runtime=ExternalRuntime.NPX,
                executable_path="npx",
                command_vector=["npx", "-y", "@modelcontextprotocol/server-postgres"],
                return_code=0,
                stdout="Mock executed",
                stderr="",
                duration=0.01,
            ),
        )

        with mock_mgr, mock_runner:
            app = AIAddonsTUIApp(registry_dir=reg_dir, workspace_dir=workspace_dir, store_dir=store_dir)
            async with app.run_test() as pilot:
                # Open Sync screen
                app.action_sync()
                await pilot.pause()

                assert isinstance(app.screen, SyncScreen)
                sync_screen: SyncScreen = app.screen

                # Verify diff computed
                assert sync_screen.diff is not None
                assert len(sync_screen.diff.missing) == 1  # postgres-mcp missing
                assert len(sync_screen.diff.extra) == 1    # github-mcp extra

                # Toggle prune checkbox
                prune_cb = sync_screen.query_one("#cb-sync-prune", Checkbox)
                prune_cb.value = True
                await pilot.pause()

                assert sync_screen.prune_enabled is True
                assert len(sync_screen.current_plan.to_prune) == 1

                # Execute Sync
                await pilot.click("#btn-sync-execute")
                await pilot.pause()

                # Verify postgres-mcp is now installed and github-mcp was pruned
                pg_rec = state_store.get_record("claude-code", Scope.WORKSPACE, "postgres-mcp")
                gh_rec = state_store.get_record("claude-code", Scope.WORKSPACE, "github-mcp")
                assert pg_rec is not None
                assert gh_rec is None

    asyncio.run(_run_test())
