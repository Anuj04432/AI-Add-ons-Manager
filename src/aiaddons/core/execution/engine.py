"""Execution engine for Phase 5B safe structural and external package execution operations."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aiaddons.core.acquisition.models import AcquiredSourceResult
from aiaddons.core.exceptions import InstallationError, SecurityValidationError
from aiaddons.core.execution.external.models import ExternalExecutionRequest, ExternalRuntime
from aiaddons.core.execution.external.runner import ExternalRunner
from aiaddons.core.execution.external.security import mask_secrets_in_text
from aiaddons.core.execution.models import (
    ExecutionResult,
    ExecutionStatus,
    OperationExecutionResult,
)
from aiaddons.core.execution.primitives import (
    atomic_write_file_primitive,
    copy_file_primitive,
    create_directory_primitive,
    modify_json_primitive,
    modify_yaml_primitive,
    remove_directory_primitive,
    remove_file_primitive,
    remove_json_key_primitive,
    remove_yaml_key_primitive,
)
from aiaddons.core.execution.security import verify_safe_target_path
from aiaddons.core.installer.models import (
    AddMcpServerOperation,
    AddPluginReferenceOperation,
    AddSkillOperation,
    BaseOperation,
    BatchInstallationPlan,
    CopyFileOperation,
    CreateDirectoryOperation,
    InstallationPlan,
    InstallationTransaction,
    ModifyJsonOperation,
    ModifyYamlOperation,
    OperationType,
    RemoveDirectoryOperation,
    RemoveFileOperation,
    RemoveMcpServerOperation,
    RemovePluginReferenceOperation,
    RemoveSkillOperation,
    TransactionPhase,
    WriteFileOperation,
)
from aiaddons.core.models.agent import Scope
from aiaddons.core.models.manifest import (
    MCPRuntime,
    SourceType,
    validate_safe_relative_path,
)
from aiaddons.core.verification.engine import VerificationEngine
from aiaddons.core.verification.models import VerificationStatus
from aiaddons.state.lockfile import LockfileAddonEntry, LockfileManager
from aiaddons.state.store import InstalledAddonRecord, InstalledStateStore
from aiaddons.state.transaction import TransactionWALManager

if TYPE_CHECKING:
    from aiaddons.core.acquisition.engine import AcquisitionEngine
    from aiaddons.core.update.models import UpdatePlan
    from aiaddons.registry.registry import Registry



class RollbackAction:
    """Internal helper representing a reversible state modification."""

    def __init__(
        self,
        op_type: str,
        target_root: str,
        target_path: str,
        backup_content: str | None = None,
        existed_before: bool = False,
        json_path: str | None = None,
        backup_files: dict[str, str] | None = None,
    ) -> None:
        self.op_type = op_type
        self.target_root = target_root
        self.target_path = target_path
        self.backup_content = backup_content
        self.existed_before = existed_before
        self.json_path = json_path
        self.backup_files = backup_files or {}

    def rollback(self) -> None:
        """Execute the reversal step enforcing strict target-root path safety."""
        verify_safe_target_path(self.target_root, self.target_path)
        if self.backup_files:
            _, dest_dir = verify_safe_target_path(self.target_root, self.target_path)
            dest_dir.mkdir(parents=True, exist_ok=True)
            for rel_file, file_content in self.backup_files.items():
                atomic_write_file_primitive(
                    target_root=self.target_root,
                    file_path=rel_file,
                    content=file_content,
                    overwrite=True,
                )
        elif self.existed_before and self.backup_content is not None:
            atomic_write_file_primitive(
                target_root=self.target_root,
                file_path=self.target_path,
                content=self.backup_content,
                overwrite=True,
            )
        elif not self.existed_before:
            if self.op_type in ("directory", "directory_creation"):
                remove_directory_primitive(self.target_root, self.target_path)
            else:
                remove_file_primitive(self.target_root, self.target_path)
        elif self.json_path:
            if self.op_type == "yaml":
                remove_yaml_key_primitive(self.target_root, self.target_path, self.json_path)
            else:
                remove_json_key_primitive(self.target_root, self.target_path, self.json_path)


class ExecutionEngine:
    """Foundational execution engine for structural and external operations."""

    SUPPORTED_OPERATIONS: set[OperationType] = {
        OperationType.CREATE_DIRECTORY,
        OperationType.COPY_FILE,
        OperationType.WRITE_FILE,
        OperationType.MODIFY_JSON,
        OperationType.MODIFY_YAML,
        OperationType.ADD_MCP_SERVER,
        OperationType.ADD_SKILL,
        OperationType.ADD_PLUGIN_REFERENCE,
        OperationType.REMOVE_DIRECTORY,
        OperationType.REMOVE_FILE,
        OperationType.REMOVE_MCP_SERVER,
        OperationType.REMOVE_SKILL,
        OperationType.REMOVE_PLUGIN_REFERENCE,
    }

    def __init__(
        self,
        external_runner: ExternalRunner | None = None,
        wal_manager: TransactionWALManager | None = None,
        state_store: InstalledStateStore | None = None,
        lockfile_manager: LockfileManager | None = None,
        verification_engine: VerificationEngine | None = None,
        acquisition_engine: AcquisitionEngine | None = None,
        registry: Registry | None = None,
        workspace_dir: Path | None = None,
    ) -> None:
        self.external_runner = external_runner or ExternalRunner()
        self.wal_manager = wal_manager or TransactionWALManager()
        self.state_store = state_store or InstalledStateStore()
        self.lockfile_manager = lockfile_manager or LockfileManager()
        self.workspace_dir = (workspace_dir or Path.cwd()).resolve()
        self.verification_engine = verification_engine or VerificationEngine(
            workspace_dir=self.workspace_dir
        )
        self.registry = registry
        from aiaddons.core.acquisition.engine import AcquisitionEngine

        self.acquisition_engine = acquisition_engine or AcquisitionEngine(
            runner=self.external_runner
        )

    def _resolve_target_root(self, target_root: str | Path, workspace_dir: Path | None = None) -> str:
        """Resolve workspace root indicator ('.') to canonical workspace directory."""
        ws_dir = (workspace_dir or self.workspace_dir).resolve()
        s = str(target_root)
        if s in (".", "./", ""):
            return str(ws_dir)
        return s

    def execute_plan(
        self,
        plan: InstallationPlan,
        transaction: InstallationTransaction | None = None,
        dry_run: bool = False,
        secret_values: dict[str, str] | None = None,
        registry: Registry | None = None,
        is_removal: bool = False,
        workspace_dir: Path | None = None,
    ) -> ExecutionResult:
        """Execute operations in an installation plan with safety validation and rollback."""
        ws_dir = (workspace_dir or self.workspace_dir).resolve()
        plan.validate_safety()
        if transaction is not None:
            transaction.is_dry_run = dry_run
        is_dry = dry_run
        secrets_list = list(secret_values.values()) if secret_values else []

        unsupported_ops = [
            op for op in plan.planned_operations if op.op_type not in self.SUPPORTED_OPERATIONS
        ]
        if unsupported_ops:
            unsupported_names = ", ".join(sorted({op.op_type.value for op in unsupported_ops}))
            err_msg = f"Unsupported operations detected ({unsupported_names})."
            if transaction:
                transaction.phase = TransactionPhase.FAILED
                transaction.error_message = mask_secrets_in_text(err_msg, secrets_list)
                if self.wal_manager and not is_dry:
                    self.wal_manager.write_transaction(transaction)

            return ExecutionResult(
                addon_id=plan.addon_id,
                target_agent=plan.target_agent,
                scope=plan.target_scope,
                status=ExecutionStatus.UNSUPPORTED,
                error_message=mask_secrets_in_text(err_msg, secrets_list),
            )

        is_removal_plan = is_removal or any(
            op.op_type
            in {
                OperationType.REMOVE_DIRECTORY,
                OperationType.REMOVE_FILE,
                OperationType.REMOVE_MCP_SERVER,
                OperationType.REMOVE_SKILL,
                OperationType.REMOVE_PLUGIN_REFERENCE,
            }
            for op in plan.planned_operations
        )

        acquired_result: AcquiredSourceResult | None = None
        tx_id = transaction.transaction_id if transaction else f"tx_anon_{plan.addon_id}"

        if not is_removal_plan:
            # Execute Source Acquisition Phase for installations
            if transaction:
                transaction.phase = TransactionPhase.SOURCE_ACQUISITION
                if self.wal_manager and not is_dry:
                    self.wal_manager.write_transaction(transaction)

            try:
                acquired_result = self.acquisition_engine.acquire_source(
                    manifest_id=plan.addon_id,
                    source=plan.source,
                    transaction_id=tx_id,
                    dry_run=is_dry,
                )
            except Exception as exc:
                err_msg = mask_secrets_in_text(f"Source acquisition failed: {exc}", secrets_list)
                if transaction:
                    transaction.phase = TransactionPhase.FAILED
                    transaction.error_message = err_msg
                    if self.wal_manager and not is_dry:
                        self.wal_manager.write_transaction(transaction)
                    transaction.phase = TransactionPhase.ROLLED_BACK
                    if self.wal_manager and not is_dry:
                        self.wal_manager.write_transaction(transaction)

                if not is_dry:
                    self.acquisition_engine.cleanup_staging(tx_id)

                return ExecutionResult(
                    addon_id=plan.addon_id,
                    target_agent=plan.target_agent,
                    scope=plan.target_scope,
                    status=ExecutionStatus.FAILED,
                    error_message=err_msg,
                )

            # Bind staged source directory to plan operations
            staged_addon_dir = acquired_result.staging_path
            if plan.source and plan.source.path and plan.source.path not in (".", ""):
                try:
                    clean_rel = validate_safe_relative_path(plan.source.path)
                    if clean_rel:
                        candidate_sub = acquired_result.staging_path / clean_rel
                        if candidate_sub.exists():
                            staged_addon_dir = candidate_sub
                except ValueError as err:
                    err_msg = mask_secrets_in_text(
                        f"Security violation in source path '{plan.source.path}': {err}",
                        secrets_list,
                    )
                    if transaction:
                        transaction.phase = TransactionPhase.FAILED
                        transaction.error_message = err_msg
                        if self.wal_manager and not is_dry:
                            self.wal_manager.write_transaction(transaction)
                        transaction.phase = TransactionPhase.ROLLED_BACK
                        if self.wal_manager and not is_dry:
                            self.wal_manager.write_transaction(transaction)
                    if not is_dry:
                        self.acquisition_engine.cleanup_staging(tx_id)

                    return ExecutionResult(
                        addon_id=plan.addon_id,
                        target_agent=plan.target_agent,
                        scope=plan.target_scope,
                        status=ExecutionStatus.FAILED,
                        error_message=err_msg,
                    )

            for op in plan.planned_operations:
                if isinstance(op, AddSkillOperation):
                    try:
                        if not op.source_dir or not Path(op.source_dir).is_relative_to(
                            staged_addon_dir
                        ):
                            op.source_dir = str(staged_addon_dir)
                    except ValueError:
                        op.source_dir = str(staged_addon_dir)
                elif isinstance(op, CopyFileOperation):
                    try:
                        if not Path(op.source_path).is_relative_to(staged_addon_dir):
                            op.source_path = str(staged_addon_dir / op.source_path)
                    except ValueError:
                        op.source_path = str(staged_addon_dir / op.source_path)

        if transaction:
            transaction.phase = TransactionPhase.EXECUTING
            if self.wal_manager and not is_dry:
                self.wal_manager.write_transaction(transaction)

        executed_results: list[OperationExecutionResult] = []
        rolled_back_results: list[OperationExecutionResult] = []
        rollback_stack: list[RollbackAction] = []

        for op in plan.planned_operations:
            try:
                rollback_action = self._dispatch_operation(
                    op,
                    dry_run=is_dry,
                    secret_values=secret_values,
                    acquired_result=acquired_result,
                )
                rollback_stack.append(rollback_action)
                executed_results.append(
                    OperationExecutionResult(
                        op_type=op.op_type.value,
                        description=op.description,
                        status=ExecutionStatus.SUCCESS,
                        target_root=op.target_root,
                        target_path=op.target_path or "",
                    )
                )
            except Exception as err:
                err_msg = mask_secrets_in_text(
                    f"Failed executing '{op.description}': {err}", secrets_list
                )
                executed_results.append(
                    OperationExecutionResult(
                        op_type=op.op_type.value,
                        description=op.description,
                        status=ExecutionStatus.FAILED,
                        target_root=op.target_root,
                        target_path=op.target_path or "",
                        error_message=err_msg,
                    )
                )

                if transaction:
                    transaction.phase = TransactionPhase.FAILED
                    transaction.error_message = err_msg
                    if self.wal_manager and not is_dry:
                        self.wal_manager.write_transaction(transaction)

                rolled_back_results = self._rollback_executed_stack(rollback_stack)

                if transaction:
                    transaction.phase = TransactionPhase.ROLLED_BACK
                    if self.wal_manager and not is_dry:
                        self.wal_manager.write_transaction(transaction)

                if not is_dry:
                    self.acquisition_engine.cleanup_staging(tx_id)

                return ExecutionResult(
                    addon_id=plan.addon_id,
                    target_agent=plan.target_agent,
                    scope=plan.target_scope,
                    status=ExecutionStatus.ROLLED_BACK,
                    executed_operations=executed_results,
                    rolled_back_operations=rolled_back_results,
                    error_message=err_msg,
                )

        verification_res = self.verification_engine.verify_plan(
            plan=plan,
            dry_run=is_dry,
            secret_values=secret_values,
        )

        if not verification_res.verified or verification_res.status == VerificationStatus.FAILED:
            err_msg = (
                "; ".join(verification_res.errors)
                if verification_res.errors
                else "Post-execution verification failed: created assets missing or invalid."
            )
            err_msg = mask_secrets_in_text(err_msg, secrets_list)
            if transaction:
                transaction.phase = TransactionPhase.FAILED
                transaction.error_message = err_msg
                if self.wal_manager and not is_dry:
                    self.wal_manager.write_transaction(transaction)

            rolled_back_results = self._rollback_executed_stack(rollback_stack)
            self.verification_engine.verify_rollback(plan)

            if transaction:
                transaction.phase = TransactionPhase.ROLLED_BACK
                if self.wal_manager and not is_dry:
                    self.wal_manager.write_transaction(transaction)

            if not is_dry and not is_removal_plan:
                self.acquisition_engine.cleanup_staging(tx_id)

            return ExecutionResult(
                addon_id=plan.addon_id,
                target_agent=plan.target_agent,
                scope=plan.target_scope,
                status=ExecutionStatus.ROLLED_BACK,
                executed_operations=executed_results,
                rolled_back_operations=rolled_back_results,
                error_message=err_msg,
            )

        if transaction:
            transaction.phase = TransactionPhase.VERIFIED
            if self.wal_manager and not is_dry:
                self.wal_manager.write_transaction(transaction)

        # Atomic installed state and lockfile persistence upon commit
        if not is_dry:
            try:
                if is_removal_plan:
                    if self.state_store:
                        self.state_store.remove_installation(
                            target_agent=plan.target_agent,
                            scope=plan.target_scope,
                            addon_id=plan.addon_id,
                        )
                    if self.lockfile_manager and plan.target_scope == Scope.WORKSPACE:
                        self.lockfile_manager.remove_from_lockfile(
                            ws_dir,
                            target_agent=plan.target_agent,
                            addon_id=plan.addon_id,
                        )
                else:
                    now_str = datetime.now(UTC).isoformat()
                    installed_files = [r.target_path for r in executed_results if r.target_path]
                    if self.state_store:
                        record = InstalledAddonRecord(
                            addon_id=plan.addon_id,
                            name=plan.addon_name,
                            version=plan.addon_version,
                            target_agent=plan.target_agent,
                            scope=plan.target_scope,
                            integration_type=plan.integration_type,
                            installed_at=now_str,
                            installed_files=installed_files,
                        )
                        self.state_store.record_installation(record)

                    if self.lockfile_manager and plan.target_scope == Scope.WORKSPACE:
                        entry = LockfileAddonEntry(
                            addon_id=plan.addon_id,
                            name=plan.addon_name,
                            version=plan.addon_version,
                            integration_type=plan.integration_type,
                            target_agent=plan.target_agent,
                            checksum=plan.source.checksum if plan.source else None,
                            installed_at=now_str,
                        )
                        self.lockfile_manager.update_lockfile(ws_dir, entry)
            except Exception as exc:
                err_msg = f"State persistence error: {exc}"
                if transaction:
                    transaction.phase = TransactionPhase.FAILED
                    transaction.error_message = err_msg
                    if self.wal_manager and not is_dry:
                        self.wal_manager.write_transaction(transaction)

                rolled_back_results = self._rollback_executed_stack(rollback_stack)

                if transaction:
                    transaction.phase = TransactionPhase.ROLLED_BACK
                    if self.wal_manager and not is_dry:
                        self.wal_manager.write_transaction(transaction)

                if not is_dry and not is_removal_plan:
                    self.acquisition_engine.cleanup_staging(tx_id)

                return ExecutionResult(
                    addon_id=plan.addon_id,
                    target_agent=plan.target_agent,
                    scope=plan.target_scope,
                    status=ExecutionStatus.ROLLED_BACK,
                    executed_operations=executed_results,
                    rolled_back_operations=rolled_back_results,
                    error_message=err_msg,
                )

        if transaction:
            if not is_dry:
                transaction.phase = TransactionPhase.COMMITTED
                if self.wal_manager:
                    self.wal_manager.write_transaction(transaction)
                if not is_removal_plan:
                    self.acquisition_engine.cleanup_staging(transaction.transaction_id)
        elif not is_dry and not is_removal_plan:
            self.acquisition_engine.cleanup_staging(tx_id)

        return ExecutionResult(
            addon_id=plan.addon_id,
            target_agent=plan.target_agent,
            scope=plan.target_scope,
            status=ExecutionStatus.SUCCESS,
            executed_operations=executed_results,
        )

    def execute_batch_plan(
        self,
        batch_plan: BatchInstallationPlan,
        transaction: InstallationTransaction | None = None,
        dry_run: bool = False,
        secret_values: dict[str, str] | None = None,
        registry: Registry | None = None,
        workspace_dir: Path | None = None,
    ) -> ExecutionResult:
        """Execute a batch of installation plans within ONE single WAL transaction and rollback stack."""
        ws_dir = (workspace_dir or self.workspace_dir).resolve()
        batch_plan.validate_safety()
        if transaction is not None:
            transaction.is_dry_run = dry_run
        is_dry = dry_run
        secrets_list = list(secret_values.values()) if secret_values else []

        tx_id = transaction.transaction_id if transaction else f"tx_batch_{uuid.uuid4().hex[:12]}"

        # 1. Source acquisition phase for all plans in batch
        if transaction:
            transaction.phase = TransactionPhase.SOURCE_ACQUISITION
            if self.wal_manager and not is_dry:
                self.wal_manager.write_transaction(transaction)

        acquired_results: dict[str, AcquiredSourceResult] = {}
        for plan in batch_plan.plans:
            plan_tx_id = f"{tx_id}_{plan.addon_id}"
            try:
                acq_res = self.acquisition_engine.acquire_source(
                    manifest_id=plan.addon_id,
                    source=plan.source,
                    transaction_id=plan_tx_id,
                    dry_run=is_dry,
                )
                acquired_results[plan.addon_id] = acq_res
            except Exception as exc:
                err_msg = mask_secrets_in_text(f"Source acquisition failed for '{plan.addon_id}': {exc}", secrets_list)
                if transaction:
                    transaction.phase = TransactionPhase.FAILED
                    transaction.error_message = err_msg
                    if self.wal_manager and not is_dry:
                        self.wal_manager.write_transaction(transaction)
                    transaction.phase = TransactionPhase.ROLLED_BACK
                    if self.wal_manager and not is_dry:
                        self.wal_manager.write_transaction(transaction)

                if not is_dry:
                    for p in batch_plan.plans:
                        self.acquisition_engine.cleanup_staging(f"{tx_id}_{p.addon_id}")

                return ExecutionResult(
                    addon_id=",".join(p.addon_id for p in batch_plan.plans),
                    target_agent=batch_plan.target_agent,
                    scope=batch_plan.target_scope,
                    status=ExecutionStatus.FAILED,
                    error_message=err_msg,
                )

            # Bind staged paths to operations for this plan
            staged_addon_dir = acq_res.staging_path
            if plan.source and plan.source.path and plan.source.path not in (".", ""):
                try:
                    clean_rel = validate_safe_relative_path(plan.source.path)
                    if clean_rel:
                        candidate_sub = acq_res.staging_path / clean_rel
                        if candidate_sub.exists():
                            staged_addon_dir = candidate_sub
                except ValueError as err:
                    err_msg = mask_secrets_in_text(
                        f"Security violation in source path '{plan.source.path}': {err}",
                        secrets_list,
                    )
                    if transaction:
                        transaction.phase = TransactionPhase.FAILED
                        transaction.error_message = err_msg
                        if self.wal_manager and not is_dry:
                            self.wal_manager.write_transaction(transaction)
                        transaction.phase = TransactionPhase.ROLLED_BACK
                        if self.wal_manager and not is_dry:
                            self.wal_manager.write_transaction(transaction)
                    if not is_dry:
                        for p in batch_plan.plans:
                            self.acquisition_engine.cleanup_staging(f"{tx_id}_{p.addon_id}")

                    return ExecutionResult(
                        addon_id=",".join(p.addon_id for p in batch_plan.plans),
                        target_agent=batch_plan.target_agent,
                        scope=batch_plan.target_scope,
                        status=ExecutionStatus.FAILED,
                        error_message=err_msg,
                    )

            for op in plan.planned_operations:
                if isinstance(op, AddSkillOperation):
                    try:
                        if not op.source_dir or not Path(op.source_dir).is_relative_to(staged_addon_dir):
                            op.source_dir = str(staged_addon_dir)
                    except ValueError:
                        op.source_dir = str(staged_addon_dir)
                elif isinstance(op, CopyFileOperation):
                    try:
                        if not Path(op.source_path).is_relative_to(staged_addon_dir):
                            op.source_path = str(staged_addon_dir / op.source_path)
                    except ValueError:
                        op.source_path = str(staged_addon_dir / op.source_path)

        # 2. Executing Phase
        if transaction:
            transaction.phase = TransactionPhase.EXECUTING
            if self.wal_manager and not is_dry:
                self.wal_manager.write_transaction(transaction)

        executed_results: list[OperationExecutionResult] = []
        rollback_stack: list[RollbackAction] = []

        for plan in batch_plan.plans:
            plan_acq_res = acquired_results.get(plan.addon_id)
            for op in plan.planned_operations:
                try:
                    rollback_action = self._dispatch_operation(
                        op,
                        dry_run=is_dry,
                        secret_values=secret_values,
                        acquired_result=plan_acq_res,
                    )
                    rollback_stack.append(rollback_action)
                    executed_results.append(
                        OperationExecutionResult(
                            op_type=op.op_type.value,
                            description=op.description,
                            status=ExecutionStatus.SUCCESS,
                            target_root=op.target_root,
                            target_path=op.target_path or "",
                        )
                    )
                except Exception as err:
                    err_msg = mask_secrets_in_text(
                        f"Failed executing '{op.description}' for '{plan.addon_id}': {err}", secrets_list
                    )
                    executed_results.append(
                        OperationExecutionResult(
                            op_type=op.op_type.value,
                            description=op.description,
                            status=ExecutionStatus.FAILED,
                            target_root=op.target_root,
                            target_path=op.target_path or "",
                            error_message=err_msg,
                        )
                    )

                    if transaction:
                        transaction.phase = TransactionPhase.FAILED
                        transaction.error_message = err_msg
                        if self.wal_manager and not is_dry:
                            self.wal_manager.write_transaction(transaction)

                    rolled_back_results = self._rollback_executed_stack(rollback_stack)

                    if transaction:
                        transaction.phase = TransactionPhase.ROLLED_BACK
                        if self.wal_manager and not is_dry:
                            self.wal_manager.write_transaction(transaction)

                    if not is_dry:
                        for p in batch_plan.plans:
                            self.acquisition_engine.cleanup_staging(f"{tx_id}_{p.addon_id}")

                    return ExecutionResult(
                        addon_id=",".join(p.addon_id for p in batch_plan.plans),
                        target_agent=batch_plan.target_agent,
                        scope=batch_plan.target_scope,
                        status=ExecutionStatus.ROLLED_BACK,
                        executed_operations=executed_results,
                        rolled_back_operations=rolled_back_results,
                        error_message=err_msg,
                    )

        # 3. Post-execution Verification Phase
        for plan in batch_plan.plans:
            verification_res = self.verification_engine.verify_plan(
                plan=plan,
                dry_run=is_dry,
                secret_values=secret_values,
            )

            if not verification_res.verified or verification_res.status == VerificationStatus.FAILED:
                err_msg = (
                    "; ".join(verification_res.errors)
                    if verification_res.errors
                    else f"Post-execution verification failed for add-on '{plan.addon_id}'."
                )
                err_msg = mask_secrets_in_text(err_msg, secrets_list)
                if transaction:
                    transaction.phase = TransactionPhase.FAILED
                    transaction.error_message = err_msg
                    if self.wal_manager and not is_dry:
                        self.wal_manager.write_transaction(transaction)

                rolled_back_results = self._rollback_executed_stack(rollback_stack)
                for p in batch_plan.plans:
                    self.verification_engine.verify_rollback(p)

                if transaction:
                    transaction.phase = TransactionPhase.ROLLED_BACK
                    if self.wal_manager and not is_dry:
                        self.wal_manager.write_transaction(transaction)

                if not is_dry:
                    for p in batch_plan.plans:
                        self.acquisition_engine.cleanup_staging(f"{tx_id}_{p.addon_id}")

                return ExecutionResult(
                    addon_id=",".join(p.addon_id for p in batch_plan.plans),
                    target_agent=batch_plan.target_agent,
                    scope=batch_plan.target_scope,
                    status=ExecutionStatus.ROLLED_BACK,
                    executed_operations=executed_results,
                    rolled_back_operations=rolled_back_results,
                    error_message=err_msg,
                )

        if transaction:
            transaction.phase = TransactionPhase.VERIFIED
            if self.wal_manager and not is_dry:
                self.wal_manager.write_transaction(transaction)

        # 4. Atomic Persistence upon commit for all add-ons in batch
        if not is_dry:
            try:
                now_str = datetime.now(UTC).isoformat()
                for plan in batch_plan.plans:
                    installed_files = [
                        r.target_path for r in executed_results if r.target_path
                    ]
                    if self.state_store:
                        record = InstalledAddonRecord(
                            addon_id=plan.addon_id,
                            name=plan.addon_name,
                            version=plan.addon_version,
                            target_agent=plan.target_agent,
                            scope=plan.target_scope,
                            integration_type=plan.integration_type,
                            installed_at=now_str,
                            installed_files=installed_files,
                        )
                        self.state_store.record_installation(record)

                    if self.lockfile_manager and plan.target_scope == Scope.WORKSPACE:
                        entry = LockfileAddonEntry(
                            addon_id=plan.addon_id,
                            name=plan.addon_name,
                            version=plan.addon_version,
                            integration_type=plan.integration_type,
                            target_agent=plan.target_agent,
                            checksum=plan.source.checksum if plan.source else None,
                            installed_at=now_str,
                        )
                        self.lockfile_manager.update_lockfile(ws_dir, entry)
            except Exception as exc:
                err_msg = f"State persistence error: {exc}"
                if transaction:
                    transaction.phase = TransactionPhase.FAILED
                    transaction.error_message = err_msg
                    if self.wal_manager and not is_dry:
                        self.wal_manager.write_transaction(transaction)

                rolled_back_results = self._rollback_executed_stack(rollback_stack)

                if transaction:
                    transaction.phase = TransactionPhase.ROLLED_BACK
                    if self.wal_manager and not is_dry:
                        self.wal_manager.write_transaction(transaction)

                if not is_dry:
                    for p in batch_plan.plans:
                        self.acquisition_engine.cleanup_staging(f"{tx_id}_{p.addon_id}")

                return ExecutionResult(
                    addon_id=",".join(p.addon_id for p in batch_plan.plans),
                    target_agent=batch_plan.target_agent,
                    scope=batch_plan.target_scope,
                    status=ExecutionStatus.ROLLED_BACK,
                    executed_operations=executed_results,
                    rolled_back_operations=rolled_back_results,
                    error_message=err_msg,
                )

        if transaction:
            if not is_dry:
                transaction.phase = TransactionPhase.COMMITTED
                if self.wal_manager:
                    self.wal_manager.write_transaction(transaction)
                for p in batch_plan.plans:
                    self.acquisition_engine.cleanup_staging(f"{tx_id}_{p.addon_id}")
        elif not is_dry:
            for p in batch_plan.plans:
                self.acquisition_engine.cleanup_staging(f"{tx_id}_{p.addon_id}")

        return ExecutionResult(
            addon_id=",".join(p.addon_id for p in batch_plan.plans),
            target_agent=batch_plan.target_agent,
            scope=batch_plan.target_scope,
            status=ExecutionStatus.SUCCESS,
            executed_operations=executed_results,
        )

    def execute_update_plan(
        self,
        update_plan: UpdatePlan,
        transaction: InstallationTransaction | None = None,
        dry_run: bool = False,
        secret_values: dict[str, str] | None = None,
        registry: Registry | None = None,
        workspace_dir: Path | None = None,
    ) -> ExecutionResult:
        """Execute all removal and installation operations in an update plan within ONE atomic transaction."""
        ws_dir = (workspace_dir or self.workspace_dir).resolve()
        update_plan.validate_safety()
        if transaction is not None:
            transaction.is_dry_run = dry_run
        is_dry = dry_run
        secrets_list = list(secret_values.values()) if secret_values else []

        if update_plan.is_empty:
            return ExecutionResult(
                addon_id="",
                target_agent=update_plan.target_agent,
                scope=update_plan.target_scope,
                status=ExecutionStatus.SUCCESS,
            )

        tx_id = transaction.transaction_id if transaction else f"tx_update_{uuid.uuid4().hex[:12]}"
        new_manifests = [item.new_manifest for item in update_plan.items]

        # 1. Source acquisition phase for all new manifests
        if transaction:
            transaction.phase = TransactionPhase.SOURCE_ACQUISITION
            if self.wal_manager and not is_dry:
                self.wal_manager.write_transaction(transaction)

        acquired_results: dict[str, AcquiredSourceResult] = {}
        for item in update_plan.items:
            plan = item.install_plan
            plan_tx_id = f"{tx_id}_{plan.addon_id}"
            try:
                acq_res = self.acquisition_engine.acquire_source(
                    manifest_id=plan.addon_id,
                    source=plan.source,
                    transaction_id=plan_tx_id,
                    dry_run=is_dry,
                )
                acquired_results[plan.addon_id] = acq_res
            except Exception as exc:
                err_msg = mask_secrets_in_text(
                    f"Source acquisition failed for '{plan.addon_id}': {exc}", secrets_list
                )
                if transaction:
                    transaction.phase = TransactionPhase.FAILED
                    transaction.error_message = err_msg
                    if self.wal_manager and not is_dry:
                        self.wal_manager.write_transaction(transaction)
                    transaction.phase = TransactionPhase.ROLLED_BACK
                    if self.wal_manager and not is_dry:
                        self.wal_manager.write_transaction(transaction)

                if not is_dry:
                    for it in update_plan.items:
                        self.acquisition_engine.cleanup_staging(f"{tx_id}_{it.addon_id}")

                return ExecutionResult(
                    addon_id=",".join(it.addon_id for it in update_plan.items),
                    target_agent=update_plan.target_agent,
                    scope=update_plan.target_scope,
                    status=ExecutionStatus.FAILED,
                    error_message=err_msg,
                )

            # Bind staged paths to operations for this plan
            staged_addon_dir = acq_res.staging_path
            if plan.source and plan.source.path and plan.source.path not in (".", ""):
                try:
                    clean_rel = validate_safe_relative_path(plan.source.path)
                    if clean_rel:
                        candidate_sub = acq_res.staging_path / clean_rel
                        if candidate_sub.exists():
                            staged_addon_dir = candidate_sub
                except ValueError as err:
                    err_msg = mask_secrets_in_text(
                        f"Security violation in source path '{plan.source.path}': {err}",
                        secrets_list,
                    )
                    if transaction:
                        transaction.phase = TransactionPhase.FAILED
                        transaction.error_message = err_msg
                        if self.wal_manager and not is_dry:
                            self.wal_manager.write_transaction(transaction)
                        transaction.phase = TransactionPhase.ROLLED_BACK
                        if self.wal_manager and not is_dry:
                            self.wal_manager.write_transaction(transaction)
                    if not is_dry:
                        for it in update_plan.items:
                            self.acquisition_engine.cleanup_staging(f"{tx_id}_{it.addon_id}")

                    return ExecutionResult(
                        addon_id=",".join(it.addon_id for it in update_plan.items),
                        target_agent=update_plan.target_agent,
                        scope=update_plan.target_scope,
                        status=ExecutionStatus.FAILED,
                        error_message=err_msg,
                    )

            for op in plan.planned_operations:
                if isinstance(op, AddSkillOperation):
                    try:
                        if not op.source_dir or not Path(op.source_dir).is_relative_to(
                            staged_addon_dir
                        ):
                            op.source_dir = str(staged_addon_dir)
                    except ValueError:
                        op.source_dir = str(staged_addon_dir)
                elif isinstance(op, CopyFileOperation):
                    try:
                        if not Path(op.source_path).is_relative_to(staged_addon_dir):
                            op.source_path = str(staged_addon_dir / op.source_path)
                    except ValueError:
                        op.source_path = str(staged_addon_dir / op.source_path)

        # 2. Executing Phase
        if transaction:
            transaction.phase = TransactionPhase.EXECUTING
            if self.wal_manager and not is_dry:
                self.wal_manager.write_transaction(transaction)

        executed_results: list[OperationExecutionResult] = []
        rollback_stack: list[RollbackAction] = []

        for item in update_plan.items:
            # 2A. Execute removal operations for old version
            for op in item.removal_plan.planned_operations:
                try:
                    rollback_action = self._dispatch_operation(
                        op,
                        dry_run=is_dry,
                        secret_values=secret_values,
                    )
                    rollback_stack.append(rollback_action)
                    executed_results.append(
                        OperationExecutionResult(
                            op_type=op.op_type.value,
                            description=op.description,
                            status=ExecutionStatus.SUCCESS,
                            target_root=op.target_root,
                            target_path=op.target_path or "",
                        )
                    )
                except Exception as err:
                    err_msg = mask_secrets_in_text(
                        f"Failed executing removal '{op.description}' for '{item.addon_id}': {err}",
                        secrets_list,
                    )
                    executed_results.append(
                        OperationExecutionResult(
                            op_type=op.op_type.value,
                            description=op.description,
                            status=ExecutionStatus.FAILED,
                            target_root=op.target_root,
                            target_path=op.target_path or "",
                            error_message=err_msg,
                        )
                    )

                    if transaction:
                        transaction.phase = TransactionPhase.FAILED
                        transaction.error_message = err_msg
                        if self.wal_manager and not is_dry:
                            self.wal_manager.write_transaction(transaction)

                    rolled_back_results = self._rollback_executed_stack(rollback_stack)

                    if transaction:
                        transaction.phase = TransactionPhase.ROLLED_BACK
                        if self.wal_manager and not is_dry:
                            self.wal_manager.write_transaction(transaction)

                    if not is_dry:
                        for it in update_plan.items:
                            self.acquisition_engine.cleanup_staging(f"{tx_id}_{it.addon_id}")

                    return ExecutionResult(
                        addon_id=",".join(it.addon_id for it in update_plan.items),
                        target_agent=update_plan.target_agent,
                        scope=update_plan.target_scope,
                        status=ExecutionStatus.ROLLED_BACK,
                        executed_operations=executed_results,
                        rolled_back_operations=rolled_back_results,
                        error_message=err_msg,
                    )

            # 2B. Execute install operations for new version
            plan_acq_res = acquired_results.get(item.addon_id)
            for op in item.install_plan.planned_operations:
                try:
                    rollback_action = self._dispatch_operation(
                        op,
                        dry_run=is_dry,
                        secret_values=secret_values,
                        acquired_result=plan_acq_res,
                    )
                    rollback_stack.append(rollback_action)
                    executed_results.append(
                        OperationExecutionResult(
                            op_type=op.op_type.value,
                            description=op.description,
                            status=ExecutionStatus.SUCCESS,
                            target_root=op.target_root,
                            target_path=op.target_path or "",
                        )
                    )
                except Exception as err:
                    err_msg = mask_secrets_in_text(
                        f"Failed executing install '{op.description}' for '{item.addon_id}': {err}",
                        secrets_list,
                    )
                    executed_results.append(
                        OperationExecutionResult(
                            op_type=op.op_type.value,
                            description=op.description,
                            status=ExecutionStatus.FAILED,
                            target_root=op.target_root,
                            target_path=op.target_path or "",
                            error_message=err_msg,
                        )
                    )

                    if transaction:
                        transaction.phase = TransactionPhase.FAILED
                        transaction.error_message = err_msg
                        if self.wal_manager and not is_dry:
                            self.wal_manager.write_transaction(transaction)

                    rolled_back_results = self._rollback_executed_stack(rollback_stack)

                    if transaction:
                        transaction.phase = TransactionPhase.ROLLED_BACK
                        if self.wal_manager and not is_dry:
                            self.wal_manager.write_transaction(transaction)

                    if not is_dry:
                        for it in update_plan.items:
                            self.acquisition_engine.cleanup_staging(f"{tx_id}_{it.addon_id}")

                    return ExecutionResult(
                        addon_id=",".join(it.addon_id for it in update_plan.items),
                        target_agent=update_plan.target_agent,
                        scope=update_plan.target_scope,
                        status=ExecutionStatus.ROLLED_BACK,
                        executed_operations=executed_results,
                        rolled_back_operations=rolled_back_results,
                        error_message=err_msg,
                    )

        # 3. Post-execution Verification Phase
        for item in update_plan.items:
            verification_res = self.verification_engine.verify_plan(
                plan=item.install_plan,
                dry_run=is_dry,
                secret_values=secret_values,
            )

            if not verification_res.verified or verification_res.status == VerificationStatus.FAILED:
                err_msg = (
                    "; ".join(verification_res.errors)
                    if verification_res.errors
                    else f"Post-execution verification failed for updated add-on '{item.addon_id}'."
                )
                err_msg = mask_secrets_in_text(err_msg, secrets_list)
                if transaction:
                    transaction.phase = TransactionPhase.FAILED
                    transaction.error_message = err_msg
                    if self.wal_manager and not is_dry:
                        self.wal_manager.write_transaction(transaction)

                rolled_back_results = self._rollback_executed_stack(rollback_stack)
                for it in update_plan.items:
                    self.verification_engine.verify_rollback(it.install_plan)

                if transaction:
                    transaction.phase = TransactionPhase.ROLLED_BACK
                    if self.wal_manager and not is_dry:
                        self.wal_manager.write_transaction(transaction)

                if not is_dry:
                    for it in update_plan.items:
                        self.acquisition_engine.cleanup_staging(f"{tx_id}_{it.addon_id}")

                return ExecutionResult(
                    addon_id=",".join(it.addon_id for it in update_plan.items),
                    target_agent=update_plan.target_agent,
                    scope=update_plan.target_scope,
                    status=ExecutionStatus.ROLLED_BACK,
                    executed_operations=executed_results,
                    rolled_back_operations=rolled_back_results,
                    error_message=err_msg,
                )

        if transaction:
            transaction.phase = TransactionPhase.VERIFIED
            if self.wal_manager and not is_dry:
                self.wal_manager.write_transaction(transaction)

        # 4. Atomic Persistence upon commit for all updated add-ons
        if not is_dry:
            try:
                now_str = datetime.now(UTC).isoformat()
                for item in update_plan.items:
                    plan = item.install_plan
                    installed_files = [
                        r.target_path for r in executed_results if r.target_path
                    ]
                    if self.state_store:
                        record = InstalledAddonRecord(
                            addon_id=plan.addon_id,
                            name=plan.addon_name,
                            version=plan.addon_version,
                            target_agent=plan.target_agent,
                            scope=plan.target_scope,
                            integration_type=plan.integration_type,
                            installed_at=now_str,
                            installed_files=installed_files,
                        )
                        self.state_store.record_installation(record)

                    if self.lockfile_manager and plan.target_scope == Scope.WORKSPACE:
                        entry = LockfileAddonEntry(
                            addon_id=plan.addon_id,
                            name=plan.addon_name,
                            version=plan.addon_version,
                            integration_type=plan.integration_type,
                            target_agent=plan.target_agent,
                            checksum=plan.source.checksum if plan.source else None,
                            installed_at=now_str,
                        )
                        self.lockfile_manager.update_lockfile(ws_dir, entry)
            except Exception as exc:
                err_msg = f"State persistence error: {exc}"
                if transaction:
                    transaction.phase = TransactionPhase.FAILED
                    transaction.error_message = err_msg
                    if self.wal_manager and not is_dry:
                        self.wal_manager.write_transaction(transaction)

                rolled_back_results = self._rollback_executed_stack(rollback_stack)

                if transaction:
                    transaction.phase = TransactionPhase.ROLLED_BACK
                    if self.wal_manager and not is_dry:
                        self.wal_manager.write_transaction(transaction)

                if not is_dry:
                    for it in update_plan.items:
                        self.acquisition_engine.cleanup_staging(f"{tx_id}_{it.addon_id}")

                return ExecutionResult(
                    addon_id=",".join(it.addon_id for it in update_plan.items),
                    target_agent=update_plan.target_agent,
                    scope=update_plan.target_scope,
                    status=ExecutionStatus.ROLLED_BACK,
                    executed_operations=executed_results,
                    rolled_back_operations=rolled_back_results,
                    error_message=err_msg,
                )

        if transaction:
            if not is_dry:
                transaction.phase = TransactionPhase.COMMITTED
                if self.wal_manager:
                    self.wal_manager.write_transaction(transaction)
                for it in update_plan.items:
                    self.acquisition_engine.cleanup_staging(f"{tx_id}_{it.addon_id}")
        elif not is_dry:
            for it in update_plan.items:
                self.acquisition_engine.cleanup_staging(f"{tx_id}_{it.addon_id}")

        return ExecutionResult(
            addon_id=",".join(it.addon_id for it in update_plan.items),
            target_agent=update_plan.target_agent,
            scope=update_plan.target_scope,
            status=ExecutionStatus.SUCCESS,
            executed_operations=executed_results,
        )

    def _dispatch_operation(
        self,
        op: BaseOperation,
        dry_run: bool = False,
        secret_values: dict[str, str] | None = None,
        acquired_result: AcquiredSourceResult | None = None,
    ) -> RollbackAction:
        """Dispatch a single operation to its execution primitive."""
        effective_root = self._resolve_target_root(op.target_root)
        if dry_run:
            if op.target_path:
                verify_safe_target_path(effective_root, op.target_path)
            return RollbackAction(
                op_type=op.op_type.value,
                target_root=effective_root,
                target_path=op.target_path or "",
            )

        if isinstance(op, CreateDirectoryOperation):
            _, dest = verify_safe_target_path(effective_root, op.directory_path)
            existed = dest.exists()
            op_mkdir = op if effective_root == op.target_root else op.model_copy(update={"target_root": effective_root})
            create_directory_primitive(op_mkdir)
            return RollbackAction(
                op_type="directory",
                target_root=effective_root,
                target_path=op.directory_path,
                existed_before=existed,
            )

        elif isinstance(op, WriteFileOperation):
            _, dest = verify_safe_target_path(effective_root, op.file_path)
            existed = dest.exists()
            backup = dest.read_text(encoding="utf-8") if (existed and dest.is_file()) else None
            atomic_write_file_primitive(
                target_root=effective_root,
                file_path=op.file_path,
                content=op.content,
                overwrite=op.overwrite,
            )
            return RollbackAction(
                op_type="file",
                target_root=effective_root,
                target_path=op.file_path,
                backup_content=backup,
                existed_before=existed,
            )

        elif isinstance(op, CopyFileOperation):
            if op.source_path and acquired_result and not dry_run and acquired_result.is_staged:
                src_path = Path(op.source_path).expanduser().resolve()
                staging_root = acquired_result.staging_path.expanduser().resolve()
                if src_path != staging_root:
                    try:
                        src_path.relative_to(staging_root)
                    except ValueError as err:
                        raise SecurityValidationError(
                            f"Security violation: Copy source path '{op.source_path}' escapes "
                            f"verified staging boundary '{staging_root}'."
                        ) from err

            _, dest = verify_safe_target_path(effective_root, op.destination_path)
            existed = dest.exists()
            backup = dest.read_text(encoding="utf-8") if (existed and dest.is_file()) else None
            op_copy = op if effective_root == op.target_root else op.model_copy(update={"target_root": effective_root})
            copy_file_primitive(op_copy)
            return RollbackAction(
                op_type="file",
                target_root=effective_root,
                target_path=op.destination_path,
                backup_content=backup,
                existed_before=existed,
            )

        elif isinstance(op, ModifyJsonOperation):
            _, dest = verify_safe_target_path(effective_root, op.file_path)
            existed = dest.exists()
            backup = dest.read_text(encoding="utf-8") if (existed and dest.is_file()) else None

            resolved_value = op.value
            if secret_values and isinstance(op.value, dict):
                import copy
                resolved_value = copy.deepcopy(op.value)
                if "env" in resolved_value and isinstance(resolved_value["env"], dict):
                    for k, v in list(resolved_value["env"].items()):
                        if k in secret_values and secret_values[k] is not None:
                            resolved_value["env"][k] = secret_values[k]
                        elif isinstance(v, str) and v in secret_values and secret_values[v] is not None:
                            resolved_value["env"][k] = secret_values[v]
                        elif isinstance(v, str) and v.startswith("${") and v.endswith("}") and v[2:-1] in secret_values:
                            var_key = v[2:-1]
                            if secret_values[var_key] is not None:
                                resolved_value["env"][k] = secret_values[var_key]
            op_json = op.model_copy(update={"target_root": effective_root, "value": resolved_value})
            modify_json_primitive(op_json)
            return RollbackAction(
                op_type="json",
                target_root=effective_root,
                target_path=op.file_path,
                backup_content=backup,
                existed_before=existed,
                json_path=op.json_path,
            )

        elif isinstance(op, ModifyYamlOperation):
            _, dest = verify_safe_target_path(effective_root, op.file_path)
            existed = dest.exists()
            backup = dest.read_text(encoding="utf-8") if (existed and dest.is_file()) else None
            op_yaml = op if effective_root == op.target_root else op.model_copy(update={"target_root": effective_root})
            modify_yaml_primitive(op_yaml)
            return RollbackAction(
                op_type="yaml",
                target_root=effective_root,
                target_path=op.file_path,
                backup_content=backup,
                existed_before=existed,
            )

        elif isinstance(op, AddMcpServerOperation):
            _, dest = verify_safe_target_path(effective_root, op.config_path)
            existed = dest.exists()
            backup = dest.read_text(encoding="utf-8") if (existed and dest.is_file()) else None

            is_yaml = op.config_path.endswith((".yaml", ".yml"))
            config_key = f"mcp_servers.{op.server_name}" if is_yaml else f"mcpServers.{op.server_name}"
            is_npx = op.runtime == MCPRuntime.NPX
            args_list = ["-y", op.package_name] if is_npx else [op.package_name]
            mcp_val: dict[str, Any] = {
                "command": op.runtime.value,
                "args": args_list,
            }
            if op.env_var_names:
                env_map: dict[str, str] = {}
                for env_name in op.env_var_names:
                    if secret_values and env_name in secret_values and secret_values[env_name] is not None:
                        env_map[env_name] = secret_values[env_name]
                    elif env_name in os.environ:
                        env_map[env_name] = os.environ[env_name]
                    else:
                        env_map[env_name] = ""
                mcp_val["env"] = env_map

            rollback_action = RollbackAction(
                op_type="yaml" if is_yaml else "json",
                target_root=effective_root,
                target_path=op.config_path,
                json_path=config_key,
                backup_content=backup,
                existed_before=existed,
            )

            if is_yaml:
                modify_op_yaml = ModifyYamlOperation(
                    description=f"Inject MCP server '{op.server_name}' configuration",
                    target_root=effective_root,
                    file_path=op.config_path,
                    yaml_path=config_key,
                    value=mcp_val,
                    value_summary=f"mcp_servers.{op.server_name}",
                )
                try:
                    modify_yaml_primitive(modify_op_yaml)
                    runtime_map = {
                        MCPRuntime.NPX: ExternalRuntime.NPX,
                        MCPRuntime.UVX: ExternalRuntime.UVX,
                        MCPRuntime.NODE: ExternalRuntime.NODE,
                        MCPRuntime.PYTHON: ExternalRuntime.PYTHON,
                    }
                    ext_runtime = runtime_map.get(op.runtime, ExternalRuntime.NPX)
                    self.external_runner.resolve_executable(ext_runtime)
                except Exception:
                    rollback_action.rollback()
                    raise
            else:
                modify_op = ModifyJsonOperation(
                    description=f"Inject MCP server '{op.server_name}' configuration",
                    target_root=effective_root,
                    file_path=op.config_path,
                    json_path=config_key,
                    value=mcp_val,
                    value_summary=f"mcpServers.{op.server_name}",
                )
                try:
                    modify_json_primitive(modify_op)
                    runtime_map = {
                        MCPRuntime.NPX: ExternalRuntime.NPX,
                        MCPRuntime.UVX: ExternalRuntime.UVX,
                        MCPRuntime.NODE: ExternalRuntime.NODE,
                        MCPRuntime.PYTHON: ExternalRuntime.PYTHON,
                    }
                    ext_runtime = runtime_map.get(op.runtime, ExternalRuntime.NPX)
                    self.external_runner.resolve_executable(ext_runtime)
                except Exception:
                    rollback_action.rollback()
                    raise

            return rollback_action


        elif isinstance(op, AddSkillOperation):
            if op.source_dir and acquired_result and not dry_run and acquired_result.is_staged:
                src_path = Path(op.source_dir).expanduser().resolve()
                staging_root = acquired_result.staging_path.expanduser().resolve()
                if src_path != staging_root:
                    try:
                        src_path.relative_to(staging_root)
                    except ValueError as err:
                        raise SecurityValidationError(
                            f"Security violation: Skill source directory '{op.source_dir}' escapes "
                            f"verified staging boundary '{staging_root}'."
                        ) from err

            skill_dest_dir = op.destination_dir
            skill_file_path = f"{op.destination_dir}/{op.skill_file}"
            _, dest_dir_path = verify_safe_target_path(effective_root, skill_dest_dir)
            _, dest_file_path = verify_safe_target_path(effective_root, skill_file_path)

            existed = dest_dir_path.exists()

            create_directory_primitive(
                CreateDirectoryOperation(
                    description=f"Create skill directory '{skill_dest_dir}'",
                    target_root=effective_root,
                    directory_path=skill_dest_dir,
                )
            )

            # Resolve skill file content
            resolved_skill_content: str
            if op.skill_content is not None:
                resolved_skill_content = op.skill_content
            elif op.source_dir:
                src_dir_path = Path(op.source_dir).expanduser().resolve()
                if not src_dir_path.exists() or not src_dir_path.is_dir():
                    msg = (
                        f"Skill source directory '{op.source_dir}' does not exist"
                        " or is not a directory."
                    )
                    raise InstallationError(msg)

                src_skill_file = (src_dir_path / op.skill_file).resolve()
                if not src_skill_file.is_relative_to(src_dir_path):
                    msg = (
                        f"Security violation: Skill file '{op.skill_file}' escapes"
                        f" source directory '{src_dir_path}'."
                    )
                    raise SecurityValidationError(msg)

                if src_skill_file.exists() and src_skill_file.is_file():
                    if src_skill_file.is_symlink():
                        sym_target = src_skill_file.resolve()
                        if not sym_target.is_relative_to(src_dir_path):
                            msg = (
                                f"Security violation: Symlink skill file '{op.skill_file}'"
                                " escapes source directory."
                            )
                            raise SecurityValidationError(msg)
                    resolved_skill_content = src_skill_file.read_text(encoding="utf-8")
                elif acquired_result and acquired_result.source_type in (
                    SourceType.LOCAL,
                    SourceType.GIT,
                    SourceType.URL,
                ):
                    msg = (
                        f"Required skill file '{op.skill_file}' missing from source"
                        f" directory '{src_dir_path}'."
                    )
                    raise InstallationError(msg)
                else:
                    resolved_skill_content = (
                        f"# Skill: {op.skill_name}\n\nInstructions for {op.skill_name}."
                    )
            else:
                resolved_skill_content = (
                    f"# Skill: {op.skill_name}\n\nInstructions for {op.skill_name}."
                )

            if "\0" in resolved_skill_content:
                raise SecurityValidationError("Null byte detected in skill file content.")

            atomic_write_file_primitive(
                target_root=effective_root,
                file_path=skill_file_path,
                content=resolved_skill_content,
                overwrite=True,
            )

            # Handle supporting files
            for supp in op.supporting_files:
                supp_dest_path = f"{op.destination_dir}/{supp}"
                verify_safe_target_path(effective_root, supp_dest_path)

                supp_content: str
                if supp in op.supporting_contents:
                    supp_content = op.supporting_contents[supp]
                elif op.source_dir:
                    src_dir_path = Path(op.source_dir).expanduser().resolve()
                    src_supp_file = (src_dir_path / supp).resolve()
                    if not src_supp_file.is_relative_to(src_dir_path):
                        msg = (
                            f"Security violation: Supporting file '{supp}' escapes"
                            " source directory."
                        )
                        raise SecurityValidationError(msg)

                    if src_supp_file.exists() and src_supp_file.is_file():
                        if src_supp_file.is_symlink():
                            sym_target = src_supp_file.resolve()
                            if not sym_target.is_relative_to(src_dir_path):
                                msg = (
                                    "Security violation: Supporting file symlink"
                                    f" '{supp}' escapes source directory."
                                    )
                                raise SecurityValidationError(msg)
                        supp_content = src_supp_file.read_text(encoding="utf-8")
                    elif acquired_result and acquired_result.source_type in (
                        SourceType.LOCAL,
                        SourceType.GIT,
                        SourceType.URL,
                    ):
                        msg = (
                            f"Supporting file '{supp}' missing from source"
                            f" directory '{src_dir_path}'."
                        )
                        raise InstallationError(msg)
                    else:
                        supp_content = f"# Supporting file '{supp}' for skill '{op.skill_name}'\n"
                else:
                    supp_content = f"# Supporting file '{supp}' for skill '{op.skill_name}'\n"

                if "\0" in supp_content:
                    msg = f"Null byte detected in supporting file '{supp}'."
                    raise SecurityValidationError(msg)

                atomic_write_file_primitive(
                    target_root=effective_root,
                    file_path=supp_dest_path,
                    content=supp_content,
                    overwrite=True,
                )

            return RollbackAction(
                op_type="directory",
                target_root=effective_root,
                target_path=skill_dest_dir,
                existed_before=existed,
            )

        elif isinstance(op, AddPluginReferenceOperation):
            _, dest = verify_safe_target_path(effective_root, op.config_path)
            existed = dest.exists()
            backup = dest.read_text(encoding="utf-8") if (existed and dest.is_file()) else None

            json_key = f"plugins.{op.plugin_id}"
            plugin_val = {
                "id": op.plugin_id,
                "components": op.component_ids,
            }
            modify_op = ModifyJsonOperation(
                description=f"Register plugin reference '{op.plugin_id}'",
                target_root=effective_root,
                file_path=op.config_path,
                json_path=json_key,
                value=plugin_val,
                value_summary=f"plugins.{op.plugin_id}",
            )
            modify_json_primitive(modify_op)

            return RollbackAction(
                op_type="json",
                target_root=effective_root,
                target_path=op.config_path,
                json_path=json_key,
                backup_content=backup,
                existed_before=existed,
            )

        elif isinstance(op, RemoveMcpServerOperation):
            _, dest = verify_safe_target_path(effective_root, op.config_path)
            existed = dest.exists()
            backup = dest.read_text(encoding="utf-8") if (existed and dest.is_file()) else None

            is_yaml = op.config_path.endswith((".yaml", ".yml"))
            config_key = f"mcp_servers.{op.server_name}" if is_yaml else f"mcpServers.{op.server_name}"
            rollback_action = RollbackAction(
                op_type="yaml" if is_yaml else "json",
                target_root=effective_root,
                target_path=op.config_path,
                json_path=config_key,
                backup_content=backup,
                existed_before=existed,
            )

            if existed:
                if is_yaml:
                    remove_yaml_key_primitive(effective_root, op.config_path, config_key)
                else:
                    remove_json_key_primitive(effective_root, op.config_path, config_key)

            return rollback_action


        elif isinstance(op, RemoveSkillOperation):
            _, dest_dir = verify_safe_target_path(effective_root, op.destination_dir)
            existed = dest_dir.exists() and dest_dir.is_dir()
            backup_files: dict[str, str] = {}
            if existed:
                target_root_p = Path(effective_root).expanduser().resolve()
                for file_p in dest_dir.rglob("*"):
                    if file_p.is_file():
                        try:
                            rel_f = str(file_p.relative_to(target_root_p)).replace("\\", "/")
                        except Exception:
                            rel_f = f"{op.destination_dir}/{file_p.name}"
                        try:
                            backup_files[rel_f] = file_p.read_text(encoding="utf-8")
                        except Exception:
                            pass

            rollback_action = RollbackAction(
                op_type="directory",
                target_root=effective_root,
                target_path=op.destination_dir,
                existed_before=existed,
                backup_files=backup_files,
            )

            if existed:
                remove_directory_primitive(effective_root, op.destination_dir)

            return rollback_action

        elif isinstance(op, RemovePluginReferenceOperation):
            _, dest = verify_safe_target_path(effective_root, op.config_path)
            existed = dest.exists()
            backup = dest.read_text(encoding="utf-8") if (existed and dest.is_file()) else None

            json_key = f"plugins.{op.plugin_id}"
            rollback_action = RollbackAction(
                op_type="json",
                target_root=effective_root,
                target_path=op.config_path,
                backup_content=backup,
                existed_before=existed,
            )

            if existed:
                remove_json_key_primitive(effective_root, op.config_path, json_key)

            return rollback_action

        elif isinstance(op, RemoveDirectoryOperation):
            _, dest_dir = verify_safe_target_path(effective_root, op.directory_path)
            existed = dest_dir.exists() and dest_dir.is_dir()
            backup_files = {}
            if existed:
                target_root_p = Path(effective_root).expanduser().resolve()
                for file_p in dest_dir.rglob("*"):
                    if file_p.is_file():
                        try:
                            rel_f = str(file_p.relative_to(target_root_p)).replace("\\", "/")
                        except Exception:
                            rel_f = f"{op.directory_path}/{file_p.name}"
                        try:
                            backup_files[rel_f] = file_p.read_text(encoding="utf-8")
                        except Exception:
                            pass

            rollback_action = RollbackAction(
                op_type="directory",
                target_root=effective_root,
                target_path=op.directory_path,
                existed_before=existed,
                backup_files=backup_files,
            )

            if existed:
                remove_directory_primitive(effective_root, op.directory_path)

            return rollback_action

        elif isinstance(op, RemoveFileOperation):
            _, dest_file = verify_safe_target_path(effective_root, op.file_path)
            existed = dest_file.exists() and dest_file.is_file()
            backup = dest_file.read_text(encoding="utf-8") if existed else None

            rollback_action = RollbackAction(
                op_type="file",
                target_root=effective_root,
                target_path=op.file_path,
                backup_content=backup,
                existed_before=existed,
            )

            if existed:
                remove_file_primitive(effective_root, op.file_path)

            return rollback_action

        else:
            raise SecurityValidationError(f"Unsupported operation model '{op.op_type}'.")

    def _rollback_executed_stack(
        self, stack: list[RollbackAction]
    ) -> list[OperationExecutionResult]:
        """Rollback all executed operations in reverse sequence."""
        results: list[OperationExecutionResult] = []
        for action in reversed(stack):
            try:
                action.rollback()
                results.append(
                    OperationExecutionResult(
                        op_type=f"rollback_{action.op_type}",
                        description=f"Rolled back {action.target_path}",
                        status=ExecutionStatus.ROLLED_BACK,
                        target_root=action.target_root,
                        target_path=action.target_path,
                    )
                )
            except Exception as err:
                results.append(
                    OperationExecutionResult(
                        op_type=f"rollback_{action.op_type}",
                        description=f"Rolled back {action.target_path}",
                        status=ExecutionStatus.FAILED,
                        target_root=action.target_root,
                        target_path=action.target_path,
                        error_message=str(err),
                    )
                )
        return results

    def _verify_executed_operations(
        self, executed_results: list[OperationExecutionResult], dry_run: bool = False
    ) -> bool:
        """Verify that all target assets exist and remain within target_root."""
        if dry_run:
            return True
        for result in executed_results:
            if result.status != ExecutionStatus.SUCCESS:
                continue
            if not result.target_path:
                continue
            if result.op_type.startswith("remove_"):
                continue
            try:
                _, resolved_dest = verify_safe_target_path(result.target_root, result.target_path)
                if not resolved_dest.exists():
                    return False
            except Exception:
                return False
        return True
