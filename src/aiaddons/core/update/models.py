"""Data models for version swap planning and update execution."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from aiaddons.core.installer.models import BaseOperation, InstallationPlan
from aiaddons.core.models.agent import Scope
from aiaddons.core.models.manifest import IntegrationManifest, IntegrationType


class UpdatePlanItem(BaseModel):
    """Plan for updating a single add-on from its current version to a target version."""

    addon_id: str
    addon_name: str
    current_version: str
    target_version: str
    integration_type: IntegrationType
    old_manifest: IntegrationManifest
    new_manifest: IntegrationManifest
    removal_plan: InstallationPlan
    install_plan: InstallationPlan
    warnings: list[str] = Field(default_factory=list)

    @property
    def planned_operations(self) -> list[BaseOperation]:
        """All planned operations for this item (removal followed by installation)."""
        return list(self.removal_plan.planned_operations) + list(self.install_plan.planned_operations)


class UpdatePlan(BaseModel):
    """Complete update plan containing one or more add-on update items."""

    target_agent: str
    target_agent_name: str
    target_scope: Scope
    items: list[UpdatePlanItem] = Field(default_factory=list)
    skipped_items: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    is_dry_run: bool = False

    @property
    def is_empty(self) -> bool:
        """Return True if there are no mutating update actions to perform."""
        return len(self.items) == 0

    @property
    def all_operations(self) -> list[BaseOperation]:
        """All planned operations across all update items in this plan."""
        ops: list[BaseOperation] = []
        for item in self.items:
            ops.extend(item.removal_plan.planned_operations)
            ops.extend(item.install_plan.planned_operations)
        return ops

    def validate_safety(self) -> None:
        """Validate safety of all sub-plans in this update plan."""
        for item in self.items:
            item.removal_plan.validate_safety()
            item.install_plan.validate_safety()


class UpdateResult(BaseModel):
    """Result of an update plan execution."""

    target_agent: str
    target_agent_name: str
    scope: Scope
    updated_addons: list[str] = Field(default_factory=list)
    skipped_addons: list[str] = Field(default_factory=list)
    items: list[UpdatePlanItem] = Field(default_factory=list)
    success: bool
    is_dry_run: bool = False
    error_message: str | None = None
    already_up_to_date: bool = False
