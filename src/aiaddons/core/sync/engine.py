"""SyncEngine for reconciling workspace lockfiles with local installed add-on state (Phase 6E)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from aiaddons.core.exceptions import (
    InstallationError,
    LockfileNotFoundError,
    ManifestValidationError,
    SyncError,
    SyncVerificationError,
)
from aiaddons.core.execution.engine import ExecutionEngine
from aiaddons.core.execution.models import ExecutionStatus
from aiaddons.core.installer.engine import InstallationEngine
from aiaddons.core.installer.models import TransactionPhase
from aiaddons.core.models.agent import AgentCapability, AgentDetectionResult, Scope
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
from aiaddons.core.models.stack import AddonStack, parse_stack_file
from aiaddons.core.sync.models import (
    SyncDiff,
    SyncDiffItem,
    SyncPlan,
    SyncResult,
    SyncStatus,
    SyncTargetSpec,
)
from aiaddons.state.lockfile import LockfileManager, WorkspaceLockfile
from aiaddons.state.store import InstalledAddonRecord, InstalledStateStore
from aiaddons.state.transaction import TransactionWALManager

if TYPE_CHECKING:
    from aiaddons.registry.registry import Registry


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


class SyncEngine:
    """Core synchronization engine for detecting drift and reconciling workspace environments."""

    def __init__(
        self,
        state_store: InstalledStateStore | None = None,
        lockfile_manager: LockfileManager | None = None,
        installer_engine: InstallationEngine | None = None,
        execution_engine: ExecutionEngine | None = None,
        registry: Registry | None = None,
        wal_manager: TransactionWALManager | None = None,
    ) -> None:
        self.state_store = state_store or InstalledStateStore()
        self.lockfile_manager = lockfile_manager or LockfileManager()
        self.wal_manager = wal_manager or TransactionWALManager()
        self.registry = registry
        self.installer_engine = installer_engine or InstallationEngine(
            registry=registry, wal_manager=self.wal_manager
        )
        self.execution_engine = execution_engine or ExecutionEngine(
            wal_manager=self.wal_manager,
            state_store=self.state_store,
            lockfile_manager=self.lockfile_manager,
            registry=registry,
        )

    def load_expected_specs(
        self,
        lockfile_path: Path | None,
        workspace_dir: Path,
        target_agent: str | None = None,
        registry: Registry | None = None,
    ) -> list[SyncTargetSpec]:
        """Load expected add-on specifications from a workspace lockfile or an explicit stack/lock file."""
        active_reg = registry or self.registry

        if lockfile_path is not None:
            resolved_path = lockfile_path.expanduser().resolve()
            if not resolved_path.exists() or not resolved_path.is_file():
                raise LockfileNotFoundError(f"Specified sync file '{lockfile_path}' does not exist.")

            content = resolved_path.read_text(encoding="utf-8").strip()
            if not content:
                raise ManifestValidationError(
                    file_path=lockfile_path,
                    message=f"Sync file '{lockfile_path}' is empty.",
                )

            try:
                raw_data = yaml.safe_load(content)
            except Exception as exc:
                raise ManifestValidationError(
                    file_path=lockfile_path,
                    message=f"Failed to parse YAML/JSON in '{lockfile_path}': {exc}",
                ) from exc

            if not isinstance(raw_data, dict):
                raise ManifestValidationError(
                    file_path=lockfile_path,
                    message=f"Invalid sync file structure in '{lockfile_path}': root must be a dictionary.",
                )

            # Check if it is a WorkspaceLockfile format (addons is a dict)
            addons_val = raw_data.get("addons")
            if isinstance(addons_val, dict):
                try:
                    lockfile_obj = WorkspaceLockfile.model_validate(raw_data)
                except Exception as exc:
                    raise ManifestValidationError(
                        file_path=lockfile_path,
                        message=f"Failed to validate lockfile schema in '{lockfile_path}': {exc}",
                    ) from exc

                specs: list[SyncTargetSpec] = []
                for entry in lockfile_obj.addons.values():
                    if target_agent and entry.target_agent.lower() != target_agent.lower():
                        continue
                    specs.append(
                        SyncTargetSpec(
                            addon_id=entry.addon_id,
                            target_agent=entry.target_agent,
                            expected_version=entry.version,
                            name=entry.name,
                            integration_type=entry.integration_type,
                            checksum=entry.checksum,
                        )
                    )
                return specs

            # Check if it is a Stack format (addons is a list)
            elif isinstance(addons_val, list):
                stack_items = parse_stack_file(resolved_path)
                specs = []
                for aid, pinned_ver in stack_items:
                    m = active_reg.get(aid) if active_reg else None
                    specs.append(
                        SyncTargetSpec(
                            addon_id=aid,
                            target_agent=target_agent or "claude-code",
                            expected_version=pinned_ver or (m.version if m else None),
                            name=m.name if m else aid,
                            integration_type=m.integration_type if m else None,
                            checksum=m.source.checksum if (m and m.source) else None,
                        )
                    )
                return specs
            else:
                raise ManifestValidationError(
                    file_path=lockfile_path,
                    message=f"Invalid sync file '{lockfile_path}': 'addons' field must be a dictionary or list.",
                )

        # Default: load from workspace_dir / aiaddons.lock
        resolved_ws = workspace_dir.expanduser().resolve()
        lock_path = resolved_ws / LockfileManager.LOCKFILE_NAME
        if not lock_path.exists() or not lock_path.is_file():
            raise LockfileNotFoundError(
                f"No lockfile found at '{lock_path}'. "
                "Provide an explicit file with --file or run 'aiaddons install' first."
            )

        entries = self.lockfile_manager.get_entries(resolved_ws)
        specs = []
        for entry in entries:
            if target_agent and entry.target_agent.lower() != target_agent.lower():
                continue
            specs.append(
                SyncTargetSpec(
                    addon_id=entry.addon_id,
                    target_agent=entry.target_agent,
                    expected_version=entry.version,
                    name=entry.name,
                    integration_type=entry.integration_type,
                    checksum=entry.checksum,
                )
            )
        return specs

    def compute_diff(
        self,
        expected_specs: list[SyncTargetSpec],
        workspace_dir: Path,
        target_agent: str | None = None,
        scope: Scope = Scope.WORKSPACE,
        registry: Registry | None = None,
    ) -> SyncDiff:
        """Compute differences between expected specifications and locally installed state."""
        active_reg = registry or self.registry
        installed_records = self.state_store.get_installed(target_agent=target_agent, scope=scope)

        installed_map: dict[tuple[str, str], InstalledAddonRecord] = {
            (r.target_agent.strip().lower(), r.addon_id.strip().lower()): r
            for r in installed_records
        }

        expected_map: dict[tuple[str, str], SyncTargetSpec] = {
            (s.target_agent.strip().lower(), s.addon_id.strip().lower()): s
            for s in expected_specs
        }

        missing: list[SyncDiffItem] = []
        mismatched: list[SyncDiffItem] = []
        synced: list[SyncDiffItem] = []
        extra: list[SyncDiffItem] = []

        # Check expected specs against installed records
        for (ag_id, aid), spec in expected_map.items():
            if (ag_id, aid) not in installed_map:
                name = spec.name or aid
                if not spec.name and active_reg:
                    m = active_reg.get(aid)
                    if m:
                        name = m.name
                missing.append(
                    SyncDiffItem(
                        addon_id=spec.addon_id,
                        name=name,
                        target_agent=spec.target_agent,
                        status=SyncStatus.MISSING,
                        installed_version=None,
                        expected_version=spec.expected_version,
                        integration_type=spec.integration_type,
                    )
                )
            else:
                installed_rec = installed_map[(ag_id, aid)]
                if spec.expected_version and installed_rec.version != spec.expected_version:
                    mismatched.append(
                        SyncDiffItem(
                            addon_id=spec.addon_id,
                            name=installed_rec.name or spec.name or aid,
                            target_agent=installed_rec.target_agent,
                            status=SyncStatus.MISMATCHED,
                            installed_version=installed_rec.version,
                            expected_version=spec.expected_version,
                            integration_type=installed_rec.integration_type,
                        )
                    )
                else:
                    synced.append(
                        SyncDiffItem(
                            addon_id=spec.addon_id,
                            name=installed_rec.name,
                            target_agent=installed_rec.target_agent,
                            status=SyncStatus.IN_SYNC,
                            installed_version=installed_rec.version,
                            expected_version=spec.expected_version or installed_rec.version,
                            integration_type=installed_rec.integration_type,
                        )
                    )

        # Check installed records for unmanaged add-ons not in expected specs
        for (ag_id, aid), installed_rec in installed_map.items():
            if (ag_id, aid) not in expected_map:
                extra.append(
                    SyncDiffItem(
                        addon_id=installed_rec.addon_id,
                        name=installed_rec.name,
                        target_agent=installed_rec.target_agent,
                        status=SyncStatus.EXTRA,
                        installed_version=installed_rec.version,
                        expected_version=None,
                        integration_type=installed_rec.integration_type,
                    )
                )

        return SyncDiff(
            missing=missing,
            extra=extra,
            mismatched=mismatched,
            synced=synced,
        )

    def generate_sync_plan(
        self,
        diff: SyncDiff,
        target_agent: AgentDetectionResult,
        scope: Scope = Scope.WORKSPACE,
        lockfile_path: Path | None = None,
        prune: bool = False,
        update: bool = False,
        registry: Registry | None = None,
    ) -> SyncPlan:
        """Generate a dry-run sync plan specifying what will be installed, pruned, or updated."""
        to_install = list(diff.missing)
        to_prune = list(diff.extra) if prune else []
        to_update = list(diff.mismatched) if update else []

        warnings: list[str] = []
        if diff.extra and not prune:
            extra_names = ", ".join(f"'{item.name}' ({item.addon_id})" for item in diff.extra)
            warnings.append(
                f"{len(diff.extra)} unmanaged add-on(s) detected ({extra_names}). "
                "Use --prune to remove unmanaged add-ons."
            )

        if diff.mismatched and not update:
            mismatch_details = ", ".join(
                f"'{item.name}' (installed: {item.installed_version}, expected: {item.expected_version})"
                for item in diff.mismatched
            )
            warnings.append(
                f"{len(diff.mismatched)} version mismatch(es) detected ({mismatch_details}). "
                "Use --update to update mismatched versions."
            )

        return SyncPlan(
            target_agent=target_agent.agent_id,
            target_agent_name=target_agent.name,
            scope=scope,
            lockfile_path=lockfile_path,
            diff=diff,
            to_install=to_install,
            to_prune=to_prune,
            to_update=to_update,
            warnings=warnings,
        )

    def execute_sync(
        self,
        plan: SyncPlan,
        expected_specs: list[SyncTargetSpec],
        target_agent: AgentDetectionResult,
        workspace_dir: Path,
        registry: Registry | None = None,
        dry_run: bool = False,
        secret_values: dict[str, str] | None = None,
    ) -> SyncResult:
        """Execute reconciliation steps inside file locking and WAL protection, then verify state."""
        active_reg = registry or self.registry
        resolved_ws = workspace_dir.expanduser().resolve()

        if dry_run:
            return SyncResult(
                target_agent=target_agent.agent_id,
                target_agent_name=target_agent.name,
                scope=plan.scope,
                in_sync=plan.diff.is_clean,
                diff=plan.diff,
                installed_addons=[],
                pruned_addons=[],
                updated_addons=[],
                success=True,
                is_dry_run=True,
            )

        installed_addons: list[str] = []
        pruned_addons: list[str] = []
        updated_addons: list[str] = []

        # Wrap all synchronization mutating operations with both state store and lockfile locks
        with self.state_store.lock(), self.lockfile_manager.lock(resolved_ws):
            # 1. Prune unmanaged add-ons if requested
            if plan.to_prune:
                for item in plan.to_prune:
                    manifest = active_reg.get(item.addon_id) if active_reg else None
                    if not manifest:
                        target_rec = self.state_store.get_record(
                            target_agent=target_agent.agent_id,
                            scope=plan.scope,
                            addon_id=item.addon_id,
                        )
                        if target_rec:
                            manifest = _synthesize_manifest_from_record(target_rec)
                        else:
                            manifest = IntegrationManifest(
                                id=item.addon_id,
                                name=item.name,
                                version=item.installed_version or "1.0.0",
                                description="Unmanaged add-on",
                                license="MIT",
                                category="general",
                                integration_type=item.integration_type or IntegrationType.MCP,
                                target_agents=[target_agent.agent_id],
                                supported_scopes=[plan.scope],
                                source=SourceSpec(source_type=SourceType.LOCAL, path="."),
                                trust=TrustMetadata(
                                    verification_status=TrustVerificationStatus.UNVERIFIED,
                                    publisher=PublisherClaimSpec(name="Installed State"),
                                ),
                                handler_spec=HandlerSpecContainer(),
                            )

                    removal_plan = self.installer_engine.generate_removal_plan(
                        manifest=manifest,
                        agent=target_agent,
                        scope=plan.scope,
                        registry=active_reg,
                        force=True,
                    )
                    exec_res = self.execution_engine.execute_plan(
                        plan=removal_plan,
                        dry_run=False,
                        is_removal=True,
                    )
                    if exec_res.status != ExecutionStatus.SUCCESS:
                        raise SyncError(
                            f"Failed to prune add-on '{item.addon_id}': {exec_res.error_message}"
                        )
                    pruned_addons.append(item.addon_id)

            # 2. Update version-mismatched add-ons if requested
            # NOTE: This minimal version-swap logic removes the old version and installs the expected version.
            # When a standalone `aiaddons update` command is developed later, this version-swap reconciliation
            # should be factored out into a shared update service module.
            if plan.to_update:
                for item in plan.to_update:
                    # A. Remove old version
                    target_rec = self.state_store.get_record(
                        target_agent=target_agent.agent_id,
                        scope=plan.scope,
                        addon_id=item.addon_id,
                    )
                    old_manifest = active_reg.get(item.addon_id) if active_reg else None
                    if not old_manifest and target_rec:
                        old_manifest = _synthesize_manifest_from_record(target_rec)
                    elif not old_manifest:
                        old_manifest = IntegrationManifest(
                            id=item.addon_id,
                            name=item.name,
                            version=item.installed_version or "1.0.0",
                            description="Installed add-on",
                            license="MIT",
                            category="general",
                            integration_type=item.integration_type or IntegrationType.MCP,
                            target_agents=[target_agent.agent_id],
                            supported_scopes=[plan.scope],
                            source=SourceSpec(source_type=SourceType.LOCAL, path="."),
                            trust=TrustMetadata(
                                verification_status=TrustVerificationStatus.UNVERIFIED,
                                publisher=PublisherClaimSpec(name="Installed State"),
                            ),
                            handler_spec=HandlerSpecContainer(),
                        )

                    removal_plan = self.installer_engine.generate_removal_plan(
                        manifest=old_manifest,
                        agent=target_agent,
                        scope=plan.scope,
                        registry=active_reg,
                        force=True,
                    )
                    rem_res = self.execution_engine.execute_plan(
                        plan=removal_plan,
                        dry_run=False,
                        is_removal=True,
                    )
                    if rem_res.status != ExecutionStatus.SUCCESS:
                        raise SyncError(
                            f"Failed to remove existing version of '{item.addon_id}' during update: "
                            f"{rem_res.error_message}"
                        )

                    # B. Install expected version
                    new_manifest = active_reg.get(item.addon_id) if active_reg else None
                    if not new_manifest:
                        raise SyncError(
                            f"Cannot update '{item.addon_id}': manifest not found in registry."
                        )
                    if item.expected_version and new_manifest.version != item.expected_version:
                        raise SyncError(
                            f"Cannot update '{item.addon_id}': expected version '{item.expected_version}', "
                            f"but registry provides '{new_manifest.version}'."
                        )

                    install_plan = self.installer_engine.generate_plan(
                        manifest=new_manifest,
                        agent=target_agent,
                        scope=plan.scope,
                        registry=active_reg,
                    )
                    inst_res = self.execution_engine.execute_plan(
                        plan=install_plan,
                        dry_run=False,
                        secret_values=secret_values,
                    )
                    if inst_res.status != ExecutionStatus.SUCCESS:
                        raise SyncError(
                            f"Failed to install updated version of '{item.addon_id}': "
                            f"{inst_res.error_message}"
                        )
                    updated_addons.append(item.addon_id)

            # 3. Install missing add-ons using the batch installation engine inside ONE WAL transaction
            if plan.to_install:
                missing_manifests: list[IntegrationManifest] = []
                for item in plan.to_install:
                    m = active_reg.get(item.addon_id) if active_reg else None
                    if not m:
                        raise SyncError(
                            f"Missing add-on '{item.addon_id}' not found in registry."
                        )
                    if item.expected_version and m.version != item.expected_version:
                        raise SyncError(
                            f"Add-on '{item.addon_id}' version mismatch: expected '{item.expected_version}', "
                            f"but registry provides '{m.version}'."
                        )
                    missing_manifests.append(m)

                tx = self.installer_engine.create_batch_transaction(
                    manifests=missing_manifests,
                    agent=target_agent,
                    scope=plan.scope,
                    registry=active_reg,
                )
                if tx.phase == TransactionPhase.FAILED or tx.batch_plan is None:
                    raise SyncError(
                        tx.error_message or "Failed to plan batch installation for missing add-ons."
                    )

                exec_res = self.execution_engine.execute_batch_plan(
                    batch_plan=tx.batch_plan,
                    transaction=tx,
                    dry_run=False,
                    secret_values=secret_values,
                )
                if exec_res.status != ExecutionStatus.SUCCESS:
                    raise SyncError(
                        exec_res.error_message
                        or "Batch installation execution failed for missing add-ons."
                    )

                for m in missing_manifests:
                    installed_addons.append(m.id)

            # 4. Post-sync Verification: re-compute diff and verify convergence
            post_diff = self.compute_diff(
                expected_specs=expected_specs,
                workspace_dir=resolved_ws,
                target_agent=target_agent.agent_id,
                scope=plan.scope,
                registry=active_reg,
            )

            # Check if any missing add-ons remain uninstalled
            if post_diff.missing:
                still_missing = ", ".join(i.addon_id for i in post_diff.missing)
                raise SyncVerificationError(
                    f"Post-sync verification failed: add-on(s) still missing ({still_missing})."
                )

            # If pruning was enabled, verify no extra add-ons remain
            if plan.to_prune and post_diff.extra:
                still_extra = ", ".join(i.addon_id for i in post_diff.extra)
                raise SyncVerificationError(
                    f"Post-sync verification failed: unmanaged add-on(s) still present ({still_extra})."
                )

            # If updating was enabled, verify no mismatched versions remain
            if plan.to_update and post_diff.mismatched:
                still_mismatched = ", ".join(i.addon_id for i in post_diff.mismatched)
                raise SyncVerificationError(
                    f"Post-sync verification failed: version mismatch(es) still present ({still_mismatched})."
                )

            # Determine whether the workspace is completely in-sync
            is_in_sync = post_diff.is_clean or (
                len(post_diff.missing) == 0
                and (not plan.to_update or len(post_diff.mismatched) == 0)
                and (not plan.to_prune or len(post_diff.extra) == 0)
            )

            return SyncResult(
                target_agent=target_agent.agent_id,
                target_agent_name=target_agent.name,
                scope=plan.scope,
                in_sync=is_in_sync,
                diff=post_diff,
                installed_addons=installed_addons,
                pruned_addons=pruned_addons,
                updated_addons=updated_addons,
                success=True,
                is_dry_run=False,
            )
