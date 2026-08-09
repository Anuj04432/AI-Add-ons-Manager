"""Pydantic domain models for AI coding agents and capabilities."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class AgentCapability(StrEnum):
    """Capabilities supported by AI coding agents."""

    MCP = "mcp"
    SKILL = "skill"
    PLUGIN = "plugin"
    HOOK = "hook"


class Scope(StrEnum):
    """Configuration scope for agent settings and add-ons."""

    GLOBAL = "global"
    WORKSPACE = "workspace"

    @classmethod
    def from_str(cls, value: str) -> "Scope":
        """Parse scope string, accepting aliases ('user' -> 'global', 'project' -> 'workspace')."""
        normalized = value.lower().strip()
        if normalized in ("global", "user"):
            return cls.GLOBAL
        if normalized in ("workspace", "project", "local"):
            return cls.WORKSPACE
        raise ValueError(
            f"Invalid scope '{value}'. Must be 'global'/'user' or 'workspace'/'project'."
        )


class AgentDetectionResult(BaseModel):
    """Result object representing detection state and metadata for an agent."""

    agent_id: str
    name: str
    installed: bool
    version: str | None = None
    executable_path: str | None = None
    global_config_path: str | None = None
    workspace_config_path: str | None = None
    config_path: str | None = None
    supported_scopes: list[Scope] = Field(
        default_factory=lambda: [Scope.GLOBAL, Scope.WORKSPACE]
    )
    capabilities: list[AgentCapability] = Field(default_factory=list)
    detection_error: str | None = None

    def get_config_path_for_scope(self, scope: Scope | str) -> str | None:
        """Return the configuration path corresponding to the given scope."""
        resolved_scope = Scope.from_str(scope) if isinstance(scope, str) else scope
        if resolved_scope == Scope.GLOBAL:
            return self.global_config_path
        if resolved_scope == Scope.WORKSPACE:
            return self.workspace_config_path
        return None

    def model_dump_dict(self) -> dict[str, Any]:
        """Return a dictionary representation of the detection result."""
        return self.model_dump()
