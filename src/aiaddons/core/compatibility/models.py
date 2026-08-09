"""Domain models for Phase 4 Compatibility Engine results and dependency checks."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from aiaddons.core.models.agent import Scope
from aiaddons.core.models.manifest import DependencyType


class DependencyStatus(StrEnum):
    """Status of a dependency check."""

    SATISFIED = "satisfied"
    MISSING = "missing"
    UNKNOWN = "unknown"


class DependencyCheckResult(BaseModel):
    """Result of evaluating a single add-on dependency requirement."""

    model_config = ConfigDict(extra="forbid")

    name: str
    type: DependencyType
    required: bool
    status: DependencyStatus
    details: str | None = None


class CompatibilityResult(BaseModel):
    """Detailed compatibility decision result for an add-on against an AI agent and scope."""

    model_config = ConfigDict(extra="forbid")

    compatible: bool
    agent_id: str
    agent_name: str
    addon_id: str
    addon_name: str
    requested_scope: Scope
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    missing_requirements: list[str] = Field(default_factory=list)
    unsupported_requirements: list[str] = Field(default_factory=list)
    dependencies: list[DependencyCheckResult] = Field(default_factory=list)
    is_installed_agent: bool = True
    source_installable: bool = True
    source_install_warning: str | None = None
