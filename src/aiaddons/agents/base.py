"""Base agent adapter protocol defining standard interface for agent detection and configuration."""

from pathlib import Path
from typing import Protocol, runtime_checkable

from aiaddons.core.models.agent import AgentCapability, AgentDetectionResult, Scope


@runtime_checkable
class BaseAgentAdapter(Protocol):
    """Protocol for AI coding agent detection and configuration adapters."""

    agent_id: str
    name: str
    capabilities: list[AgentCapability]

    def detect(self, project_path: Path | None = None) -> AgentDetectionResult:
        """Detect agent installation status, binary path, version, and config location."""
        ...

    def get_config_path(self, scope: Scope, project_path: Path | None = None) -> Path | None:
        """Get path to the agent's configuration file for the specified scope."""
        ...

    def supports_capability(self, capability: AgentCapability) -> bool:
        """Check if this agent supports a specific integration capability."""
        ...
