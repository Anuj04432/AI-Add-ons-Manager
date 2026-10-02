"""Cursor agent detection and configuration adapter."""

import os
from pathlib import Path
import sys

from aiaddons.agents.utils import find_executable, run_version_command
from aiaddons.core.exceptions import UnsupportedIntegrationTypeError
from aiaddons.core.models.agent import AgentCapability, AgentDetectionResult, Scope


class CursorAdapter:
    """Adapter for detecting and configuring Cursor IDE."""

    agent_id: str = "cursor"
    name: str = "Cursor"
    capabilities: list[AgentCapability] = [AgentCapability.MCP]

    def supports_capability(self, capability: AgentCapability) -> bool:
        """Check if Cursor supports the given capability."""
        return capability in self.capabilities

    def get_config_path(self, scope: Scope, project_path: Path | None = None) -> Path | None:
        """Get the configuration path for Cursor based on scope."""
        if scope == Scope.GLOBAL:
            return Path.home() / ".cursor" / "mcp.json"

        base_dir = project_path if project_path is not None else Path.cwd()
        return base_dir / ".cursor" / "mcp.json"

    def get_skill_directory(self, scope: Scope, project_path: Path | None = None) -> Path:
        """Get the skill installation directory for Cursor based on scope."""
        raise UnsupportedIntegrationTypeError(
            f"Agent '{self.name}' does not support skills. Cursor only supports MCP via .cursor/mcp.json."
        )

    def detect(self, project_path: Path | None = None) -> AgentDetectionResult:
        """Detect Cursor installation status, binary path, version, and config location."""
        exec_path = find_executable("cursor")

        # Platform-specific executable fallbacks (e.g. Windows user-level install, macOS .app)
        if exec_path is None:
            if sys.platform == "win32":
                local_appdata = os.environ.get("LOCALAPPDATA", "")
                if local_appdata:
                    user_cmd = Path(local_appdata) / "Programs" / "cursor" / "resources" / "app" / "bin" / "cursor.cmd"
                    user_exe = Path(local_appdata) / "Programs" / "cursor" / "Cursor.exe"
                    if user_cmd.exists():
                        exec_path = user_cmd
                    elif user_exe.exists():
                        exec_path = user_exe
                if exec_path is None:
                    prog_files = os.environ.get("ProgramFiles", "")
                    if prog_files:
                        sys_exe = Path(prog_files) / "cursor" / "Cursor.exe"
                        if sys_exe.exists():
                            exec_path = sys_exe
            elif sys.platform == "darwin":
                mac_bin = Path("/Applications/Cursor.app/Contents/Resources/app/bin/cursor")
                if mac_bin.exists():
                    exec_path = mac_bin
            elif sys.platform.startswith("linux"):
                linux_bin = Path.home() / ".local" / "share" / "cursor" / "bin" / "cursor"
                if linux_bin.exists():
                    exec_path = linux_bin

        global_config = self.get_config_path(Scope.GLOBAL)
        workspace_config = self.get_config_path(Scope.WORKSPACE, project_path=project_path)

        base_dir = project_path if project_path is not None else Path.cwd()
        has_global = (
            (global_config is not None and global_config.exists())
            or (Path.home() / ".cursor").exists()
        )
        has_workspace = (
            (workspace_config is not None and workspace_config.exists())
            or (base_dir / ".cursor").exists()
        )
        installed = (exec_path is not None) or has_global or has_workspace

        version: str | None = None
        detection_error: str | None = None

        if exec_path is not None:
            try:
                cmd_name = exec_path.stem.lower()
                version = (
                    run_version_command(cmd_name)
                    or run_version_command("cursor")
                    or run_version_command(str(exec_path))
                )
            except Exception as exc:
                detection_error = f"Error running version command: {exc}"

        active_config = (
            workspace_config
            if (has_workspace and workspace_config and workspace_config.exists())
            else (
                global_config
                if (has_global and global_config and global_config.exists())
                else (workspace_config or global_config)
            )
        )

        return AgentDetectionResult(
            agent_id=self.agent_id,
            name=self.name,
            installed=installed,
            version=version,
            executable_path=str(exec_path) if exec_path else None,
            global_config_path=str(global_config) if global_config else None,
            workspace_config_path=str(workspace_config) if workspace_config else None,
            config_path=str(active_config) if active_config else None,
            supported_scopes=[Scope.GLOBAL, Scope.WORKSPACE],
            capabilities=list(self.capabilities),
            detection_error=detection_error,
        )
