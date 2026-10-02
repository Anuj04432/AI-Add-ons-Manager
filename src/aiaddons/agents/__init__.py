"""Agent detection and adapters subpackage."""

from aiaddons.agents.antigravity import AntigravityAdapter
from aiaddons.agents.base import BaseAgentAdapter
from aiaddons.agents.claude_code import ClaudeCodeAdapter
from aiaddons.agents.codex import CodexAdapter
from aiaddons.agents.cursor import CursorAdapter
from aiaddons.agents.hermes import HermesAdapter
from aiaddons.agents.manager import AgentDetectionManager, detect_agents

__all__ = [
    "AntigravityAdapter",
    "BaseAgentAdapter",
    "ClaudeCodeAdapter",
    "CodexAdapter",
    "CursorAdapter",
    "HermesAdapter",
    "AgentDetectionManager",
    "detect_agents",
]
