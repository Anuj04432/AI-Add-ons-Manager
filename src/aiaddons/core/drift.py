"""Drift detection module for inspecting live filesystem and configuration divergence."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from aiaddons.core.models.agent import AgentDetectionResult, Scope
from aiaddons.core.models.manifest import IntegrationManifest, IntegrationType
from aiaddons.integrations.mcp import make_relative_config_path
from aiaddons.integrations.skill import get_skill_target_directory
from aiaddons.state.store import InstalledAddonRecord


def detect_installation_drift(
    record: InstalledAddonRecord | None,
    manifest: IntegrationManifest,
    agent: AgentDetectionResult,
    scope: Scope,
    workspace_dir: Path,
) -> list[str]:
    """Detect if the live host filesystem or configuration has drifted from recorded state."""
    drifts: list[str] = []
    target_root = Path.home() if scope == Scope.GLOBAL else workspace_dir.expanduser().resolve()

    if manifest.integration_type == IntegrationType.MCP:
        raw_config_path = agent.get_config_path_for_scope(scope)
        if not raw_config_path:
            raw_config_path = f".{agent.agent_id}.json"
        config_path = make_relative_config_path(raw_config_path, scope)
        config_file = (target_root / config_path).resolve()

        if not config_file.exists() or not config_file.is_file():
            drifts.append(f"Configuration file '{config_file}' does not exist on disk.")
        else:
            try:
                content = config_file.read_text(encoding="utf-8").strip()
                if not content:
                    drifts.append(f"Configuration file '{config_file}' is empty.")
                else:
                    is_yaml = config_file.suffix.lower() in (".yaml", ".yml") or agent.agent_id.lower() in ("hermes", "hermes-agent")
                    if is_yaml:
                        data = yaml.safe_load(content)
                        servers_key = "mcp_servers"
                    else:
                        data = json.loads(content)
                        servers_key = "mcpServers"
                    mcp_servers = data.get(servers_key, {}) if isinstance(data, dict) else {}
                    if not isinstance(mcp_servers, dict) or manifest.id not in mcp_servers:
                        drifts.append(
                            f"MCP server entry '{manifest.id}' is missing from '{servers_key}' "
                            f"in '{config_file}'."
                        )

            except Exception as exc:
                drifts.append(
                    f"Configuration file '{config_file}' is malformed or unreadable: {exc}"
                )

    elif manifest.integration_type == IntegrationType.SKILL:
        base_dir = get_skill_target_directory(agent.agent_id, scope)
        dest_dir = (target_root / base_dir / manifest.id).resolve()
        if not dest_dir.exists() or not dest_dir.is_dir():
            drifts.append(f"Skill directory '{dest_dir}' does not exist on disk.")
        else:
            skill_file = dest_dir / "SKILL.md"
            if not skill_file.exists():
                drifts.append(f"Skill file 'SKILL.md' is missing from '{dest_dir}'.")

    elif manifest.integration_type == IntegrationType.PLUGIN:
        dest_dir_rel = (
            f".aiaddons/plugins/{manifest.id}"
            if scope == Scope.GLOBAL
            else f".agents/plugins/{manifest.id}"
        )
        dest_dir = (target_root / dest_dir_rel).resolve()
        if not dest_dir.exists() or not dest_dir.is_dir():
            drifts.append(f"Plugin directory '{dest_dir}' does not exist on disk.")

    return drifts
