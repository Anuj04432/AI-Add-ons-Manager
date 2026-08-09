"""Agent detection and adapters subpackage."""

from aiaddons.agents.base import BaseAgentAdapter
from aiaddons.agents.claude_code import ClaudeCodeAdapter
from aiaddons.agents.codex import CodexAdapter
from aiaddons.agents.manager import AgentDetectionManager, detect_agents

__all__ = [
    "BaseAgentAdapter",
    "ClaudeCodeAdapter",
    "CodexAdapter",
    "AgentDetectionManager",
    "detect_agents",
]
