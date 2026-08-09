"""Agent detection manager aggregating all registered AI agent adapters."""

from collections.abc import Sequence
from pathlib import Path

from aiaddons.agents.base import BaseAgentAdapter
from aiaddons.agents.claude_code import ClaudeCodeAdapter
from aiaddons.agents.codex import CodexAdapter
from aiaddons.core.models.agent import AgentDetectionResult


class AgentDetectionManager:
    """Manager component for detecting installed AI coding agents."""

    def __init__(self, adapters: Sequence[BaseAgentAdapter] | None = None) -> None:
        if adapters is None:
            self._adapters: list[BaseAgentAdapter] = [
                ClaudeCodeAdapter(),
                CodexAdapter(),
            ]
        else:
            self._adapters = list(adapters)

    def register_adapter(self, adapter: BaseAgentAdapter) -> None:
        """Register a new agent adapter."""
        self._adapters.append(adapter)

    def detect_agents(self, project_path: Path | None = None) -> dict[str, AgentDetectionResult]:
        """Detect all registered agents and return results mapped by agent ID."""
        results: dict[str, AgentDetectionResult] = {}
        for adapter in self._adapters:
            try:
                results[adapter.agent_id] = adapter.detect(project_path=project_path)
            except Exception as exc:
                results[adapter.agent_id] = AgentDetectionResult(
                    agent_id=adapter.agent_id,
                    name=adapter.name,
                    installed=False,
                    capabilities=list(adapter.capabilities),
                    detection_error=f"Unexpected detection error: {exc}",
                )
        return results


def detect_agents(project_path: Path | None = None) -> dict[str, AgentDetectionResult]:
    """Convenience function to run agent detection with default adapters."""
    manager = AgentDetectionManager()
    return manager.detect_agents(project_path=project_path)
