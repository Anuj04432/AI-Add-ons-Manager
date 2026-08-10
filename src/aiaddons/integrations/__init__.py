"""Integrations subpackage containing handler and installer implementations."""

from aiaddons.integrations.base import BaseIntegrationInstaller
from aiaddons.integrations.cli_tool import CLIToolInstaller
from aiaddons.integrations.mcp import MCPInstaller
from aiaddons.integrations.plugin import PluginInstaller
from aiaddons.integrations.skill import SkillInstaller

__all__ = [
    "BaseIntegrationInstaller",
    "CLIToolInstaller",
    "MCPInstaller",
    "PluginInstaller",
    "SkillInstaller",
]
