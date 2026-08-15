"""Domain models for Phase 6 Health Check & Doctor subsystem."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class HealthStatus(StrEnum):
    """Health check status levels."""

    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"


class HealthCategory(StrEnum):
    """Health check diagnostic categories."""

    REGISTRY = "registry"
    INSTALLED_STATE = "installed_state"
    LOCKFILE = "lockfile"
    TRANSACTIONS = "transactions"
    STAGING = "staging"
    RUNTIMES = "runtimes"
    AGENTS = "agents"
    CONSISTENCY = "consistency"
    SECURITY = "security"

    @property
    def display_name(self) -> str:
        """Return human-readable display name for the category."""
        titles = {
            HealthCategory.REGISTRY: "Registry",
            HealthCategory.INSTALLED_STATE: "Installed State",
            HealthCategory.LOCKFILE: "Lockfile",
            HealthCategory.TRANSACTIONS: "Transactions",
            HealthCategory.STAGING: "Staging",
            HealthCategory.RUNTIMES: "Runtimes",
            HealthCategory.AGENTS: "AI Coding Agents",
            HealthCategory.CONSISTENCY: "Add-on Consistency",
            HealthCategory.SECURITY: "Security",
        }
        return titles.get(self, self.value.replace("_", " ").title())


class HealthCheckItem(BaseModel):
    """Single health check observation result."""

    model_config = ConfigDict(extra="forbid")

    check_id: str
    category: HealthCategory
    status: HealthStatus
    message: str
    remediation: str | None = None
    diagnostic_details: dict[str, Any] | None = None


class HealthReport(BaseModel):
    """Aggregated health check report produced by HealthCheckEngine."""

    model_config = ConfigDict(extra="forbid")

    overall_status: HealthStatus
    summary: dict[str, int] = Field(default_factory=dict)
    items: list[HealthCheckItem] = Field(default_factory=list)
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    workspace_dir: str | None = None

    def get_items_by_category(self, category: HealthCategory) -> list[HealthCheckItem]:
        """Return all check items belonging to a specific category."""
        return [item for item in self.items if item.category == category]
