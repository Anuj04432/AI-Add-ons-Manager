"""Hermes Agent (NousResearch) detection and configuration adapter."""

import os
from pathlib import Path
import sys

from aiaddons.agents.utils import find_executable, run_version_command
from aiaddons.core.models.agent import AgentCapability, AgentDetectionResult, Scope


def _get_hermes_home() -> Path:
    """Resolve the Hermes home directory (env override -> platform default)."""
    env_home = os.environ.get("HERMES_HOME", "").strip()
    if env_home:
        return Path(env_home)

    home = Path.home()
    if sys.platform == "win32":
        local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
        if local_appdata:
            local_hermes = Path(local_appdata) / "hermes"
            home_hermes = home / ".hermes"
            if (home_hermes / "config.yaml").exists() or home_hermes.exists():
                return home_hermes
            if (local_hermes / "config.yaml").exists() or local_hermes.exists():
                return local_hermes
            return local_hermes
    return home / ".hermes"


class HermesAdapter:
    """Adapter for detecting and configuring Hermes Agent (by NousResearch)."""

    agent_id: str = "hermes"
    name: str = "Hermes Agent"
    capabilities: list[AgentCapability] = [AgentCapability.MCP, AgentCapability.SKILL]

    def supports_capability(self, capability: AgentCapability) -> bool:
        """Check if Hermes Agent supports the given capability."""
        return capability in self.capabilities

    def get_config_path(self, scope: Scope, project_path: Path | None = None) -> Path | None:
        """Get the configuration path for Hermes Agent based on scope."""
        if scope == Scope.GLOBAL:
            return _get_hermes_home() / "config.yaml"
        # Hermes Agent operates on global scope; workspace scope is not supported
        return None

    def get_skill_directory(self, scope: Scope, project_path: Path | None = None) -> Path:
        """Get the skill installation directory for Hermes Agent based on scope."""
        if scope == Scope.GLOBAL:
            return _get_hermes_home() / "skills"
        base_dir = project_path if project_path is not None else Path.cwd()
        return base_dir / ".hermes" / "skills"

    def detect(self, project_path: Path | None = None) -> AgentDetectionResult:
        """Detect Hermes Agent installation status, binary path, version, and config location."""
        exec_path = find_executable("hermes")

        # Check venv executable location if not on PATH
        hermes_home = _get_hermes_home()
        if exec_path is None:
            if sys.platform == "win32":
                venv_exec = hermes_home / "hermes-agent" / "venv" / "Scripts" / "hermes.exe"
                if venv_exec.exists():
                    exec_path = venv_exec
            else:
                venv_exec = hermes_home / "hermes-agent" / "venv" / "bin" / "hermes"
                if venv_exec.exists():
                    exec_path = venv_exec

        global_config = self.get_config_path(Scope.GLOBAL)

        has_global = (
            (global_config is not None and global_config.exists())
            or hermes_home.exists()
            or (Path.home() / ".hermes").exists()
        )
        installed = (exec_path is not None) or has_global

        version: str | None = None
        detection_error: str | None = None

        if exec_path is not None:
            try:
                cmd_name = exec_path.stem.lower()
                version = (
                    run_version_command(cmd_name)
                    or run_version_command("hermes")
                    or run_version_command(str(exec_path))
                )
            except Exception as exc:
                detection_error = f"Error running version command: {exc}"

        active_config = (
            global_config
            if (has_global and global_config and global_config.exists())
            else global_config
        )

        return AgentDetectionResult(
            agent_id=self.agent_id,
            name=self.name,
            installed=installed,
            version=version,
            executable_path=str(exec_path) if exec_path else None,
            global_config_path=str(global_config) if global_config else None,
            workspace_config_path=None,
            config_path=str(active_config) if active_config else None,
            supported_scopes=[Scope.GLOBAL],
            capabilities=list(self.capabilities),
            detection_error=detection_error,
        )
