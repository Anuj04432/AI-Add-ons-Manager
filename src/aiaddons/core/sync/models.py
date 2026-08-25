"""Domain models for workspace synchronization."""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from aiaddons.core.models.agent import Scope
from aiaddons.core.models.manifest import IntegrationType


class SyncStatus(str, Enum):
    """Synchronization status of an add-on or workspace."""

    IN_SYNC = "in_sync"
    MISSING = "missing"
    EXTRA = "extra"
    MISMATCHED = "mismatched"


class SyncTargetSpec(BaseModel):
    """Normalized specification of an expected add-on from a lockfile or stack file."""

    model_config = ConfigDict(extra="forbid")

    addon_id: str
    target_agent: str
    expected_version: str | None = None
    name: str | None = None
    integration_type: IntegrationType | None = None
    checksum: str | None = None


class SyncDiffItem(BaseModel):
    """An individual difference item between expected state and installed local state."""

    model_config = ConfigDict(extra="forbid")

    addon_id: str
    name: str
    target_agent: str
    status: SyncStatus
    installed_version: str | None = None
    expected_version: str | None = None
    integration_type: IntegrationType | None = None


class SyncDiff(BaseModel):
    """Full diff comparing lockfile/stack specifications with locally installed state."""

    model_config = ConfigDict(extra="forbid")

    missing: list[SyncDiffItem] = Field(default_factory=list)
    extra: list[SyncDiffItem] = Field(default_factory=list)
    mismatched: list[SyncDiffItem] = Field(default_factory=list)
    synced: list[SyncDiffItem] = Field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        """Return True if local state matches expected state with zero drift or missing items."""
        return len(self.missing) == 0 and len(self.extra) == 0 and len(self.mismatched) == 0

    def total_reconcile_actions(self, prune: bool = False, update: bool = False) -> int:
        """Calculate total number of mutating actions to be taken given sync flags."""
        count = len(self.missing)
        if prune:
            count += len(self.extra)
        if update:
            count += len(self.mismatched)
        return count


class SyncPlan(BaseModel):
    """Dry-run execution plan for reconciling local workspace state with lockfile."""

    model_config = ConfigDict(extra="forbid")

    target_agent: str
    target_agent_name: str
    scope: Scope = Scope.WORKSPACE
    lockfile_path: Path | None = None
    diff: SyncDiff
    to_install: list[SyncDiffItem] = Field(default_factory=list)
    to_prune: list[SyncDiffItem] = Field(default_factory=list)
    to_update: list[SyncDiffItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SyncResult(BaseModel):
    """Outcome of a workspace synchronization execution."""

    model_config = ConfigDict(extra="forbid")

    target_agent: str
    target_agent_name: str
    scope: Scope = Scope.WORKSPACE
    in_sync: bool
    diff: SyncDiff
    installed_addons: list[str] = Field(default_factory=list)
    pruned_addons: list[str] = Field(default_factory=list)
    updated_addons: list[str] = Field(default_factory=list)
    success: bool = True
    error_message: str | None = None
    is_dry_run: bool = False
