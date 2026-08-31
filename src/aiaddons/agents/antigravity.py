"""Antigravity CLI agent detection adapter."""

from pathlib import Path

from aiaddons.agents.utils import find_executable, run_version_command
from aiaddons.core.models.agent import AgentCapability, AgentDetectionResult, Scope


class AntigravityAdapter:
    """Adapter for detecting and configuring Antigravity CLI."""

    agent_id: str = "antigravity"
    name: str = "Antigravity CLI"
    capabilities: list[AgentCapability] = [AgentCapability.MCP, AgentCapability.SKILL]

    def supports_capability(self, capability: AgentCapability) -> bool:
        """Check if Antigravity CLI supports the given capability."""
        return capability in self.capabilities

    def get_config_path(self, scope: Scope, project_path: Path | None = None) -> Path | None:
        """Get the configuration path for Antigravity CLI based on scope."""
        if scope == Scope.GLOBAL:
            global_mcp = Path.home() / ".gemini" / "config" / "mcp_config.json"
            if global_mcp.exists():
                return global_mcp
            global_config_dir = Path.home() / ".gemini" / "config"
            if global_config_dir.exists():
                return global_mcp
            global_gemini_dir = Path.home() / ".gemini"
            if global_gemini_dir.exists():
                return global_mcp
            return global_mcp

        base_dir = project_path if project_path is not None else Path.cwd()
        ws_mcp = base_dir / ".agents" / "mcp_config.json"
        if ws_mcp.exists():
            return ws_mcp
        ws_agents = base_dir / ".agents"
        if ws_agents.exists():
            return ws_mcp
        return ws_mcp

    def get_skill_directory(self, scope: Scope, project_path: Path | None = None) -> Path:
        """Get the skill installation directory for Antigravity CLI based on scope."""
        if scope == Scope.GLOBAL:
            return Path.home() / ".gemini" / "config" / "skills"
        base_dir = project_path if project_path is not None else Path.cwd()
        return base_dir / ".agents" / "skills"

    def detect(self, project_path: Path | None = None) -> AgentDetectionResult:
        """Detect Antigravity CLI installation status, binary path, version, and config location."""
        exec_path = find_executable("agy")
        if exec_path is None:
            exec_path = find_executable("antigravity")

        global_config = self.get_config_path(Scope.GLOBAL)
        workspace_config = self.get_config_path(Scope.WORKSPACE, project_path=project_path)

        base_dir = project_path if project_path is not None else Path.cwd()
        has_global = (
            (global_config is not None and global_config.exists())
            or (Path.home() / ".gemini").exists()
        )
        has_workspace = (
            (workspace_config is not None and workspace_config.exists())
            or (base_dir / ".agents").exists()
            or (base_dir / "AGENTS.md").exists()
            or (base_dir / "GEMINI.md").exists()
        )
        installed = (exec_path is not None) or has_global or has_workspace

        version: str | None = None
        detection_error: str | None = None

        if exec_path is not None:
            try:
                cmd_name = exec_path.name
                if cmd_name.lower().endswith(".exe"):
                    cmd_name = cmd_name[:-4]
                version = run_version_command(cmd_name)
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
