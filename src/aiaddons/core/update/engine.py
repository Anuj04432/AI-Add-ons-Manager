"""UpdateEngine for managing add-on version updates and version-swap transactions."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from packaging.version import InvalidVersion, Version

from aiaddons.core.compatibility.engine import CompatibilityEngine
from aiaddons.core.exceptions import (
    IncompatibleAgentError,
    InstallationError,
    InstallationPlanningError,
    ManifestValidationError,
    UnsupportedIntegrationTypeError,
    UnsupportedScopeError,
)
from aiaddons.core.execution.engine import ExecutionEngine
from aiaddons.core.execution.models import ExecutionStatus
from aiaddons.core.installer.engine import InstallationEngine
from aiaddons.core.installer.models import TransactionPhase
from aiaddons.core.models.agent import AgentDetectionResult, Scope
from aiaddons.core.models.manifest import (
    HandlerSpecContainer,
    IntegrationManifest,
    IntegrationType,
    MCPHandlerSpec,
    MCPRuntime,
    PluginHandlerSpec,
    PublisherClaimSpec,
    SkillHandlerSpec,
    SourceSpec,
    SourceType,
    TrustMetadata,
)
from aiaddons.core.models.manifest import (
    VerificationStatus as TrustVerificationStatus,
)
from aiaddons.core.update.models import UpdatePlan, UpdatePlanItem, UpdateResult
from aiaddons.state.lockfile import LockfileManager
from aiaddons.state.store import InstalledAddonRecord, InstalledStateStore
from aiaddons.state.transaction import TransactionWALManager

if TYPE_CHECKING:
    from aiaddons.registry.registry import Registry


def is_newer_version(current_ver: str, candidate_ver: str) -> bool:
    """Compare two semantic version strings to determine if candidate is newer."""
    try:
        return Version(candidate_ver) > Version(current_ver)
    except (InvalidVersion, Exception):
        return candidate_ver != current_ver


def _synthesize_manifest_from_record(record: InstalledAddonRecord) -> IntegrationManifest:
    """Synthesize a fallback manifest from an installed state record."""
    handler_spec = HandlerSpecContainer()
    if record.integration_type == IntegrationType.MCP:
        handler_spec.mcp = MCPHandlerSpec(
            runtime=MCPRuntime.NPX,
            package_name=record.addon_id,
        )
    elif record.integration_type == IntegrationType.SKILL:
        handler_spec.skill = SkillHandlerSpec(
            skill_file="SKILL.md",
        )
    elif record.integration_type == IntegrationType.PLUGIN:
        handler_spec.plugin = PluginHandlerSpec(
            components=[],
        )

    return IntegrationManifest(
        id=record.addon_id,
        name=record.name or record.addon_id,
        version=record.version or "1.0.0",
        description=f"Installed {record.integration_type.value} add-on",
        license="MIT",
        category="general",
        integration_type=record.integration_type,
        target_agents=[record.target_agent],
        supported_scopes=[record.scope],
        source=SourceSpec(source_type=SourceType.LOCAL, path="."),
        trust=TrustMetadata(
            verification_status=TrustVerificationStatus.UNVERIFIED,
            publisher=PublisherClaimSpec(name="Installed State"),
        ),
        handler_spec=handler_spec,
    )


class UpdateEngine:
    """Core domain engine for managing version swaps and atomic update plans."""

    def __init__(
        self,
        state_store: InstalledStateStore | None = None,
        lockfile_manager: LockfileManager | None = None,
        installer_engine: InstallationEngine | None = None,
        execution_engine: ExecutionEngine | None = None,
        compatibility_engine: CompatibilityEngine | None = None,
        registry: Registry | None = None,
        wal_manager: TransactionWALManager | None = None,
        workspace_dir: Path | None = None,
    ) -> None:
        self.state_store = state_store or InstalledStateStore()
        self.lockfile_manager = lockfile_manager or LockfileManager()
        self.wal_manager = wal_manager or TransactionWALManager()
        self.registry = registry
        self.compatibility_engine = compatibility_engine or CompatibilityEngine(registry=registry)
        self.installer_engine = installer_engine or InstallationEngine(
            registry=registry,
            compatibility_engine=self.compatibility_engine,
            wal_manager=self.wal_manager,
        )
        self.workspace_dir = (workspace_dir or Path.cwd()).resolve()
        self.execution_engine = execution_engine or ExecutionEngine(
            wal_manager=self.wal_manager,
            state_store=self.state_store,
            lockfile_manager=self.lockfile_manager,
            registry=registry,
            workspace_dir=self.workspace_dir,
        )

    def plan_update(
        self,
        addon_id: str,
        target_agent: AgentDetectionResult,
        scope: Scope = Scope.WORKSPACE,
        target_version: str | None = None,
        registry: Registry | None = None,
        workspace_dir: Path | None = None,
    ) -> UpdatePlan:
        """Plan a single add-on update/version-swap, combining removal and installation operations."""
        active_reg = registry or self.registry
        ws_dir = (workspace_dir or self.workspace_dir).resolve()
        clean_aid = addon_id.strip().lower()

        # 1. Lookup current installed record and version
        target_rec = self.state_store.get_record(
            target_agent=target_agent.agent_id,
            scope=scope,
            addon_id=clean_aid,
        )

        lock_entry = None
        if scope == Scope.WORKSPACE:
            lock_entries = self.lockfile_manager.get_entries(ws_dir)
            for entry in lock_entries:
                if (
                    entry.addon_id.strip().lower() == clean_aid
                    and entry.target_agent.strip().lower() == target_agent.agent_id.strip().lower()
                ):
                    lock_entry = entry
                    break

        if not target_rec and not lock_entry:
            raise InstallationPlanningError(
                f"Add-on '{addon_id}' is not installed for agent '{target_agent.name}' ({scope.value})."
            )

        current_version = target_rec.version if target_rec else (lock_entry.version if lock_entry else "1.0.0")

        # 2. Lookup new target manifest from registry
        if not active_reg or not active_reg.get(clean_aid):
            raise InstallationPlanningError(f"Add-on '{addon_id}' not found in registry.")

        new_manifest = active_reg.get(clean_aid)
        assert new_manifest is not None

        # 3. Check requested version constraint or pinning
        if target_version:
            if new_manifest.version != target_version:
                raise InstallationPlanningError(
                    f"Requested version '{target_version}' for '{addon_id}', "
                    f"but registry provides '{new_manifest.version}'."
                )

        # 4. Check if already at target version
        if target_version:
            if current_version == target_version:
                return UpdatePlan(
                    target_agent=target_agent.agent_id,
                    target_agent_name=target_agent.name,
                    target_scope=scope,
                    items=[],
                    skipped_items=[
                        {
                            "addon_id": clean_aid,
                            "name": new_manifest.name,
                            "old_version": current_version,
                            "new_version": target_version,
                            "reason": "Already at target version",
                        }
                    ],
                )
        else:
            if not is_newer_version(current_version, new_manifest.version):
                return UpdatePlan(
                    target_agent=target_agent.agent_id,
                    target_agent_name=target_agent.name,
                    target_scope=scope,
                    items=[],
                    skipped_items=[
                        {
                            "addon_id": clean_aid,
                            "name": new_manifest.name,
                            "old_version": current_version,
                            "new_version": new_manifest.version,
                            "reason": "Already up to date",
                        }
                    ],
                )

        # 5. Evaluate compatibility of new version
        compat = self.compatibility_engine.evaluate(
            manifest=new_manifest,
            agent=target_agent,
            requested_scope=scope,
            registry=active_reg,
        )
        if not compat.compatible:
            reasons_str = "; ".join(compat.reasons)
            raise IncompatibleAgentError(
                f"Updated version '{new_manifest.version}' of '{new_manifest.name}' "
                f"is incompatible with agent '{target_agent.name}': {reasons_str}"
            )

        # 6. Resolve old manifest for removal
        old_manifest: IntegrationManifest
        if target_rec:
            old_manifest = _synthesize_manifest_from_record(target_rec)
        elif lock_entry:
            old_manifest = IntegrationManifest(
                id=lock_entry.addon_id,
                name=lock_entry.name,
                version=lock_entry.version,
                description="Installed add-on",
                license="MIT",
                category="general",
                integration_type=lock_entry.integration_type,
                target_agents=[lock_entry.target_agent],
                supported_scopes=[scope],
                source=SourceSpec(source_type=SourceType.LOCAL, path="."),
                trust=TrustMetadata(
                    verification_status=TrustVerificationStatus.UNVERIFIED,
                    publisher=PublisherClaimSpec(name="Installed State"),
                ),
                handler_spec=HandlerSpecContainer(),
            )
        else:
            old_manifest = new_manifest

        # 7. Generate removal plan for old version and install plan for new version
        removal_plan = self.installer_engine.generate_removal_plan(
            manifest=old_manifest,
            agent=target_agent,
            scope=scope,
            registry=active_reg,
            force=True,
        )

        install_plan = self.installer_engine.generate_plan(
            manifest=new_manifest,
            agent=target_agent,
            scope=scope,
            registry=active_reg,
        )

        combined_warnings = removal_plan.warnings + install_plan.warnings

        item = UpdatePlanItem(
            addon_id=clean_aid,
            addon_name=new_manifest.name,
            current_version=current_version,
            target_version=new_manifest.version,
            integration_type=new_manifest.integration_type,
            old_manifest=old_manifest,
            new_manifest=new_manifest,
            removal_plan=removal_plan,
            install_plan=install_plan,
            warnings=combined_warnings,
        )

        plan = UpdatePlan(
            target_agent=target_agent.agent_id,
            target_agent_name=target_agent.name,
            target_scope=scope,
            items=[item],
            warnings=combined_warnings,
        )
        plan.validate_safety()
        return plan

    def plan_all_updates(
        self,
        target_agent: AgentDetectionResult,
        scope: Scope = Scope.WORKSPACE,
        registry: Registry | None = None,
        workspace_dir: Path | None = None,
    ) -> UpdatePlan:
        """Plan updates for all installed add-ons that have newer versions in the registry."""
        active_reg = registry or self.registry
        ws_dir = (workspace_dir or self.workspace_dir).resolve()

        installed_records = self.state_store.get_installed(
            target_agent=target_agent.agent_id, scope=scope
        )
        lock_entries = (
            self.lockfile_manager.get_entries(ws_dir) if scope == Scope.WORKSPACE else []
        )

        # Build map of installed items: addon_id -> (name, version, integration_type)
        installed_map: dict[str, tuple[str, str, IntegrationType]] = {}
        for r in installed_records:
            installed_map[r.addon_id.strip().lower()] = (r.name, r.version, r.integration_type)
        for e in lock_entries:
            if e.target_agent.strip().lower() == target_agent.agent_id.strip().lower():
                aid = e.addon_id.strip().lower()
                if aid not in installed_map:
                    installed_map[aid] = (e.name, e.version, e.integration_type)

        update_items: list[UpdatePlanItem] = []
        skipped_items: list[dict[str, str]] = []
        all_warnings: list[str] = []

        if not active_reg or active_reg.count() == 0:
            return UpdatePlan(
                target_agent=target_agent.agent_id,
                target_agent_name=target_agent.name,
                target_scope=scope,
                items=[],
                skipped_items=skipped_items,
                warnings=["Registry is empty or unavailable."],
            )

        for aid, (name, current_ver, itype) in installed_map.items():
            candidate_m = active_reg.get(aid)
            if not candidate_m:
                skipped_items.append(
                    {
                        "addon_id": aid,
                        "name": name,
                        "old_version": current_ver,
                        "new_version": current_ver,
                        "reason": "Not in registry",
                    }
                )
                continue

            if not is_newer_version(current_ver, candidate_m.version):
                skipped_items.append(
                    {
                        "addon_id": aid,
                        "name": candidate_m.name,
                        "old_version": current_ver,
                        "new_version": candidate_m.version,
                        "reason": "Already up to date",
                    }
                )
                continue

            # Evaluate compatibility for the candidate version
            compat = self.compatibility_engine.evaluate(
                manifest=candidate_m,
                agent=target_agent,
                requested_scope=scope,
                registry=active_reg,
            )
            if not compat.compatible:
                all_warnings.append(
                    f"Skipping update for '{candidate_m.name}' ({aid}) to v{candidate_m.version}: "
                    f"{'; '.join(compat.reasons)}"
                )
                skipped_items.append(
                    {
                        "addon_id": aid,
                        "name": candidate_m.name,
                        "old_version": current_ver,
                        "new_version": candidate_m.version,
                        "reason": f"Incompatible: {'; '.join(compat.reasons)}",
                    }
                )
                continue

            # Generate individual update item
            try:
                item_plan = self.plan_update(
                    addon_id=aid,
                    target_agent=target_agent,
                    scope=scope,
                    target_version=candidate_m.version,
                    registry=active_reg,
                    workspace_dir=ws_dir,
                )
                if item_plan.items:
                    update_items.extend(item_plan.items)
                    all_warnings.extend(item_plan.warnings)
            except Exception as exc:
                all_warnings.append(f"Failed to plan update for '{aid}': {exc}")

        # Check batch compatibility across all candidate update manifests
        if update_items:
            candidate_manifests = [it.new_manifest for it in update_items]
            batch_compat = self.compatibility_engine.evaluate_batch(
                manifests=candidate_manifests,
                agent=target_agent,
                requested_scope=scope,
                registry=active_reg,
            )
            incompatible_batch = [cr for cr in batch_compat if not cr.compatible]
            if incompatible_batch:
                for cr in incompatible_batch:
                    all_warnings.append(
                        f"Batch conflict for '{cr.addon_name}': {'; '.join(cr.reasons)}"
                    )

        plan = UpdatePlan(
            target_agent=target_agent.agent_id,
            target_agent_name=target_agent.name,
            target_scope=scope,
            items=update_items,
            skipped_items=skipped_items,
            warnings=all_warnings,
        )
        plan.validate_safety()
        return plan

    def execute_update(
        self,
        plan: UpdatePlan,
        target_agent: AgentDetectionResult,
        workspace_dir: Path | None = None,
        registry: Registry | None = None,
        dry_run: bool = False,
        secret_values: dict[str, str] | None = None,
    ) -> UpdateResult:
        """Execute an update plan within ONE atomic WAL transaction and full rollback protection."""
        active_reg = registry or self.registry
        ws_dir = (workspace_dir or self.workspace_dir).resolve()

        if dry_run:
            return UpdateResult(
                target_agent=target_agent.agent_id,
                target_agent_name=target_agent.name,
                scope=plan.target_scope,
                updated_addons=[it.addon_id for it in plan.items],
                skipped_addons=[s.get("addon_id", "") for s in plan.skipped_items],
                items=plan.items,
                success=True,
                is_dry_run=True,
                already_up_to_date=plan.is_empty,
            )

        if plan.is_empty:
            return UpdateResult(
                target_agent=target_agent.agent_id,
                target_agent_name=target_agent.name,
                scope=plan.target_scope,
                updated_addons=[],
                skipped_addons=[s.get("addon_id", "") for s in plan.skipped_items],
                items=[],
                success=True,
                is_dry_run=False,
                already_up_to_date=True,
            )

        # Wrap execution in state and lockfile locks
        with self.state_store.lock(), self.lockfile_manager.lock(ws_dir):
            exec_res = self.execution_engine.execute_update_plan(
                update_plan=plan,
                dry_run=False,
                secret_values=secret_values,
                registry=active_reg,
                workspace_dir=ws_dir,
            )

            if exec_res.status != ExecutionStatus.SUCCESS:
                return UpdateResult(
                    target_agent=target_agent.agent_id,
                    target_agent_name=target_agent.name,
                    scope=plan.target_scope,
                    updated_addons=[],
                    skipped_addons=[s.get("addon_id", "") for s in plan.skipped_items],
                    items=plan.items,
                    success=False,
                    is_dry_run=False,
                    error_message=exec_res.error_message or "Update execution failed.",
                )

            return UpdateResult(
                target_agent=target_agent.agent_id,
                target_agent_name=target_agent.name,
                scope=plan.target_scope,
                updated_addons=[it.addon_id for it in plan.items],
                skipped_addons=[s.get("addon_id", "") for s in plan.skipped_items],
                items=plan.items,
                success=True,
                is_dry_run=False,
            )
