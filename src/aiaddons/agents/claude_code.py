"""Claude Code agent detection adapter."""

from pathlib import Path

from aiaddons.agents.utils import find_executable, run_version_command
from aiaddons.core.models.agent import AgentCapability, AgentDetectionResult, Scope


class ClaudeCodeAdapter:
    """Adapter for detecting and configuring Claude Code."""

    agent_id: str = "claude-code"
    name: str = "Claude Code"
    capabilities: list[AgentCapability] = [AgentCapability.MCP, AgentCapability.SKILL]

    def supports_capability(self, capability: AgentCapability) -> bool:
        """Check if Claude Code supports the given capability."""
        return capability in self.capabilities

    def get_config_path(self, scope: Scope, project_path: Path | None = None) -> Path | None:
        """Get the configuration path for Claude Code based on scope."""
        if scope == Scope.GLOBAL:
            global_json = Path.home() / ".claude.json"
            if global_json.exists():
                return global_json
            global_dir = Path.home() / ".claude"
            if global_dir.exists():
                return global_dir
            return global_json

        base_dir = project_path if project_path is not None else Path.cwd()
        ws_json = base_dir / ".claude.json"
        if ws_json.exists():
            return ws_json
        ws_dir = base_dir / ".claude"
        if ws_dir.exists():
            return ws_dir
        return ws_json

    def get_skill_directory(self, scope: Scope, project_path: Path | None = None) -> Path:
        """Get the skill installation directory for Claude Code based on scope."""
        if scope == Scope.GLOBAL:
            return Path.home() / ".claude" / "skills"
        base_dir = project_path if project_path is not None else Path.cwd()
        return base_dir / ".claude" / "skills"


    def detect(self, project_path: Path | None = None) -> AgentDetectionResult:
        """Detect Claude Code installation status, binary path, version, and config location."""
        exec_path = find_executable("claude")
        global_config = self.get_config_path(Scope.GLOBAL)
        workspace_config = self.get_config_path(Scope.WORKSPACE, project_path=project_path)

        # Check if config exists on disk
        has_global = global_config is not None and global_config.exists()
        has_workspace = workspace_config is not None and workspace_config.exists()
        installed = (exec_path is not None) or has_global or has_workspace

        version: str | None = None
        detection_error: str | None = None

        if exec_path is not None:
            try:
                version = run_version_command("claude")
            except Exception as exc:
                detection_error = f"Error running version command: {exc}"

        active_config = (
            workspace_config
            if (has_workspace and workspace_config)
            else (global_config if (has_global and global_config) else None)
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
