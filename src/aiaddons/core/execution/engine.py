"""Execution engine for Phase 5B safe structural and external package execution operations."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
)
from aiaddons.core.execution.security import verify_safe_target_path
from aiaddons.core.installer.models import (
    AddMcpServerOperation,
    AddPluginReferenceOperation,
    AddSkillOperation,
    BaseOperation,
    CopyFileOperation,
    CreateDirectoryOperation,
    InstallationPlan,
    InstallationTransaction,
    ModifyJsonOperation,
    ModifyYamlOperation,
    OperationType,
    TransactionPhase,
    WriteFileOperation,
)
from aiaddons.core.models.agent import Scope
from aiaddons.core.models.manifest import MCPRuntime
from aiaddons.state.lockfile import LockfileAddonEntry, LockfileManager
from aiaddons.state.store import InstalledAddonRecord, InstalledStateStore
from aiaddons.state.transaction import TransactionWALManager


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
    ) -> None:
        self.op_type = op_type
        self.target_root = target_root
        self.target_path = target_path
        self.backup_content = backup_content
        self.existed_before = existed_before
        self.json_path = json_path

    def rollback(self) -> None:
        """Execute the reversal step enforcing strict target-root path safety."""
        verify_safe_target_path(self.target_root, self.target_path)
        if self.existed_before and self.backup_content is not None:
            atomic_write_file_primitive(
                target_root=self.target_root,
                file_path=self.target_path,
                content=self.backup_content,
                overwrite=True,
            )
        elif not self.existed_before:
            if self.op_type == "directory":
                remove_directory_primitive(self.target_root, self.target_path)
            else:
                remove_file_primitive(self.target_root, self.target_path)
        elif self.json_path:
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
    }

    def __init__(
        self,
        external_runner: ExternalRunner | None = None,
        wal_manager: TransactionWALManager | None = None,
        state_store: InstalledStateStore | None = None,
        lockfile_manager: LockfileManager | None = None,
    ) -> None:
        self.external_runner = external_runner or ExternalRunner()
        self.wal_manager = wal_manager
        self.state_store = state_store
        self.lockfile_manager = lockfile_manager

    def execute_plan(
        self,
        plan: InstallationPlan,
        transaction: InstallationTransaction | None = None,
        dry_run: bool = False,
        secret_values: dict[str, str] | None = None,
    ) -> ExecutionResult:
        """Execute operations in an installation plan with safety validation and rollback."""
        plan.validate_safety()
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
                if self.wal_manager:
                    self.wal_manager.write_transaction(transaction)

            return ExecutionResult(
                addon_id=plan.addon_id,
                target_agent=plan.target_agent,
                scope=plan.target_scope,
                status=ExecutionStatus.UNSUPPORTED,
                error_message=mask_secrets_in_text(err_msg, secrets_list),
            )

        if transaction:
            transaction.phase = TransactionPhase.EXECUTING
            if self.wal_manager:
                self.wal_manager.write_transaction(transaction)

        executed_results: list[OperationExecutionResult] = []
        rolled_back_results: list[OperationExecutionResult] = []
        rollback_stack: list[RollbackAction] = []

        for op in plan.planned_operations:
            try:
                rollback_action = self._dispatch_operation(
                    op, dry_run=dry_run, secret_values=secret_values
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
                    if self.wal_manager:
                        self.wal_manager.write_transaction(transaction)

                rolled_back_results = self._rollback_executed_stack(rollback_stack)

                if transaction:
                    transaction.phase = TransactionPhase.ROLLED_BACK
                    if self.wal_manager:
                        self.wal_manager.write_transaction(transaction)

                return ExecutionResult(
                    addon_id=plan.addon_id,
                    target_agent=plan.target_agent,
                    scope=plan.target_scope,
                    status=ExecutionStatus.ROLLED_BACK,
                    executed_operations=executed_results,
                    rolled_back_operations=rolled_back_results,
                    error_message=err_msg,
                )

        if not self._verify_executed_operations(executed_results, dry_run=dry_run):
            err_msg = "Post-execution verification failed: created assets missing or invalid."
            if transaction:
                transaction.phase = TransactionPhase.FAILED
                transaction.error_message = err_msg
                if self.wal_manager:
                    self.wal_manager.write_transaction(transaction)

            rolled_back_results = self._rollback_executed_stack(rollback_stack)

            if transaction:
                transaction.phase = TransactionPhase.ROLLED_BACK
                if self.wal_manager:
                    self.wal_manager.write_transaction(transaction)

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
            if self.wal_manager:
                self.wal_manager.write_transaction(transaction)

        # Atomic installed state and lockfile persistence upon commit
        if not dry_run:
            try:
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
                        checksum=plan.source.checksum,
                        installed_at=now_str,
                    )
                    self.lockfile_manager.update_lockfile(Path.cwd(), entry)
            except Exception as exc:
                err_msg = f"State persistence error: {exc}"
                if transaction:
                    transaction.phase = TransactionPhase.FAILED
                    transaction.error_message = err_msg
                    if self.wal_manager:
                        self.wal_manager.write_transaction(transaction)

                rolled_back_results = self._rollback_executed_stack(rollback_stack)

                if transaction:
                    transaction.phase = TransactionPhase.ROLLED_BACK
                    if self.wal_manager:
                        self.wal_manager.write_transaction(transaction)

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
            transaction.phase = TransactionPhase.COMMITTED
            if self.wal_manager:
                self.wal_manager.write_transaction(transaction)

        return ExecutionResult(
            addon_id=plan.addon_id,
            target_agent=plan.target_agent,
            scope=plan.target_scope,
            status=ExecutionStatus.SUCCESS,
            executed_operations=executed_results,
        )

    def _dispatch_operation(
        self,
        op: BaseOperation,
        dry_run: bool = False,
        secret_values: dict[str, str] | None = None,
    ) -> RollbackAction:
        """Dispatch a single operation to its execution primitive."""
        if dry_run:
            if op.target_path:
                verify_safe_target_path(op.target_root, op.target_path)
            return RollbackAction(
                op_type=op.op_type.value,
                target_root=op.target_root,
                target_path=op.target_path or "",
            )

        if isinstance(op, CreateDirectoryOperation):
            _, dest = verify_safe_target_path(op.target_root, op.directory_path)
            existed = dest.exists()
            create_directory_primitive(op)
            return RollbackAction(
                op_type="directory",
                target_root=op.target_root,
                target_path=op.directory_path,
                existed_before=existed,
            )

        elif isinstance(op, WriteFileOperation):
            _, dest = verify_safe_target_path(op.target_root, op.file_path)
            existed = dest.exists()
            backup = dest.read_text(encoding="utf-8") if (existed and dest.is_file()) else None
            atomic_write_file_primitive(
                target_root=op.target_root,
                file_path=op.file_path,
                content=op.content,
                overwrite=op.overwrite,
            )
            return RollbackAction(
                op_type="file",
                target_root=op.target_root,
                target_path=op.file_path,
                backup_content=backup,
                existed_before=existed,
            )

        elif isinstance(op, CopyFileOperation):
            _, dest = verify_safe_target_path(op.target_root, op.destination_path)
            existed = dest.exists()
            backup = dest.read_text(encoding="utf-8") if (existed and dest.is_file()) else None
            copy_file_primitive(op)
            return RollbackAction(
                op_type="file",
                target_root=op.target_root,
                target_path=op.destination_path,
                backup_content=backup,
                existed_before=existed,
            )

        elif isinstance(op, ModifyJsonOperation):
            _, dest = verify_safe_target_path(op.target_root, op.file_path)
            existed = dest.exists()
            backup = dest.read_text(encoding="utf-8") if (existed and dest.is_file()) else None
            modify_json_primitive(op)
            return RollbackAction(
                op_type="json",
                target_root=op.target_root,
                target_path=op.file_path,
                backup_content=backup,
                existed_before=existed,
                json_path=op.json_path,
            )

        elif isinstance(op, ModifyYamlOperation):
            _, dest = verify_safe_target_path(op.target_root, op.file_path)
            existed = dest.exists()
            backup = dest.read_text(encoding="utf-8") if (existed and dest.is_file()) else None
            modify_yaml_primitive(op)
            return RollbackAction(
                op_type="yaml",
                target_root=op.target_root,
                target_path=op.file_path,
                backup_content=backup,
                existed_before=existed,
            )

        elif isinstance(op, AddMcpServerOperation):
            _, dest = verify_safe_target_path(op.target_root, op.config_path)
            existed = dest.exists()
            backup = dest.read_text(encoding="utf-8") if (existed and dest.is_file()) else None

            json_key = f"mcpServers.{op.server_name}"
            is_npx = op.runtime == MCPRuntime.NPX
            args_list = ["-y", op.package_name] if is_npx else [op.package_name]
            mcp_val: dict[str, Any] = {
                "command": op.runtime.value,
                "args": args_list,
            }
            if op.env_var_names:
                mcp_val["env"] = {env_name: f"${{{env_name}}}" for env_name in op.env_var_names}

            rollback_action = RollbackAction(
                op_type="json",
                target_root=op.target_root,
                target_path=op.config_path,
                json_path=json_key,
                backup_content=backup,
                existed_before=existed,
            )

            modify_op = ModifyJsonOperation(
                description=f"Inject MCP server '{op.server_name}' configuration",
                target_root=op.target_root,
                file_path=op.config_path,
                json_path=json_key,
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
                op_env: dict[str, str] = {}
                if secret_values:
                    for env_name in op.env_var_names:
                        if env_name in secret_values:
                            op_env[env_name] = secret_values[env_name]

                req = ExternalExecutionRequest(
                    runtime=ext_runtime,
                    package_name=op.package_name,
                    env_vars=op_env,
                )
                res = self.external_runner.execute(req, dry_run=dry_run)
                if not res.success:
                    msg = f"External package operation failed: {res.error_message}"
                    raise InstallationError(msg)
            except Exception:
                rollback_action.rollback()
                raise

            return rollback_action

        elif isinstance(op, AddSkillOperation):
            skill_dest_dir = op.destination_dir
            skill_file_path = f"{op.destination_dir}/{op.skill_file}"
            _, dest_dir_path = verify_safe_target_path(op.target_root, skill_dest_dir)
            _, dest_file_path = verify_safe_target_path(op.target_root, skill_file_path)

            existed = dest_dir_path.exists()

            create_directory_primitive(
                CreateDirectoryOperation(
                    description=f"Create skill directory '{skill_dest_dir}'",
                    target_root=op.target_root,
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
                if not src_skill_file.exists() or not src_skill_file.is_file():
                    msg = (
                        f"Required skill file '{op.skill_file}' missing from source"
                        f" directory '{src_dir_path}'."
                    )
                    raise InstallationError(msg)
                if src_skill_file.is_symlink():
                    sym_target = src_skill_file.resolve()
                    if not sym_target.is_relative_to(src_dir_path):
                        msg = (
                            f"Security violation: Symlink skill file '{op.skill_file}'"
                            " escapes source directory."
                        )
                        raise SecurityValidationError(msg)

                resolved_skill_content = src_skill_file.read_text(encoding="utf-8")
            else:
                resolved_skill_content = (
                    f"# Skill: {op.skill_name}\n\nInstructions for {op.skill_name}."
                )

            if "\0" in resolved_skill_content:
                raise SecurityValidationError("Null byte detected in skill file content.")

            atomic_write_file_primitive(
                target_root=op.target_root,
                file_path=skill_file_path,
                content=resolved_skill_content,
                overwrite=True,
            )

            # Handle supporting files
            for supp in op.supporting_files:
                supp_dest_path = f"{op.destination_dir}/{supp}"
                verify_safe_target_path(op.target_root, supp_dest_path)

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
                    if not src_supp_file.exists() or not src_supp_file.is_file():
                        msg = (
                            f"Supporting file '{supp}' missing from source"
                            f" directory '{src_dir_path}'."
                        )
                        raise InstallationError(msg)
                    if src_supp_file.is_symlink():
                        sym_target = src_supp_file.resolve()
                        if not sym_target.is_relative_to(src_dir_path):
                            msg = (
                                "Security violation: Supporting file symlink"
                                f" '{supp}' escapes source directory."
                            )
                            raise SecurityValidationError(msg)
                    supp_content = src_supp_file.read_text(encoding="utf-8")
                else:
                    supp_content = f"# Supporting file '{supp}' for skill '{op.skill_name}'\n"

                if "\0" in supp_content:
                    msg = f"Null byte detected in supporting file '{supp}'."
                    raise SecurityValidationError(msg)

                atomic_write_file_primitive(
                    target_root=op.target_root,
                    file_path=supp_dest_path,
                    content=supp_content,
                    overwrite=True,
                )

            return RollbackAction(
                op_type="directory",
                target_root=op.target_root,
                target_path=skill_dest_dir,
                existed_before=existed,
            )

        elif isinstance(op, AddPluginReferenceOperation):
            _, dest = verify_safe_target_path(op.target_root, op.config_path)
            existed = dest.exists()
            backup = dest.read_text(encoding="utf-8") if (existed and dest.is_file()) else None

            json_key = f"plugins.{op.plugin_id}"
            plugin_val = {
                "id": op.plugin_id,
                "components": op.component_ids,
            }
            modify_op = ModifyJsonOperation(
                description=f"Register plugin reference '{op.plugin_id}'",
                target_root=op.target_root,
                file_path=op.config_path,
                json_path=json_key,
                value=plugin_val,
                value_summary=f"plugins.{op.plugin_id}",
            )
            modify_json_primitive(modify_op)

            return RollbackAction(
                op_type="json",
                target_root=op.target_root,
                target_path=op.config_path,
                json_path=json_key,
                backup_content=backup,
                existed_before=existed,
            )

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
            try:
                _, resolved_dest = verify_safe_target_path(result.target_root, result.target_path)
                if not resolved_dest.exists():
                    return False
            except Exception:
                return False
        return True
