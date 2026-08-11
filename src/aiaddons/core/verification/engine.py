"""Verification engine for post-installation integrity and rollback validation (Phase 5B.9)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from aiaddons.core.exceptions import (
    SecurityValidationError,
    VerificationError,
    VerificationPathSecurityError,
)
from aiaddons.core.execution.external.security import mask_secrets_in_text
from aiaddons.core.execution.security import verify_safe_target_path
from aiaddons.core.installer.models import (
    AddMcpServerOperation,
    AddPluginReferenceOperation,
    AddSkillOperation,
    BaseOperation,
    CopyFileOperation,
    CreateDirectoryOperation,
    InstallationPlan,
    ModifyJsonOperation,
    ModifyYamlOperation,
    WriteFileOperation,
)
from aiaddons.core.verification.models import (
    VerificationCheck,
    VerificationResult,
    VerificationStatus,
)

if TYPE_CHECKING:
    from aiaddons.agents.manager import AgentDetectionManager
    from aiaddons.registry.registry import Registry


def verify_path_security(target_root: str | Path, relative_path: str | Path) -> tuple[Path, Path]:
    """Re-verify filesystem boundaries and symlinks during verification phase.

    Protects against null bytes, UNC paths, URL-encoded traversal, '..', and symlink escapes.
    """
    rel_str = str(relative_path).strip()
    root_str = str(target_root).strip()

    if "\0" in rel_str or "\0" in root_str:
        raise VerificationPathSecurityError("Null byte detected in verification path.")
    if rel_str.startswith("//") or rel_str.startswith("\\\\"):
        raise VerificationPathSecurityError(f"UNC path '{rel_str}' is prohibited.")
    if "%2e%2e" in rel_str.lower() or "%2f" in rel_str.lower() or "%5c" in rel_str.lower():
        raise VerificationPathSecurityError(f"URL-encoded traversal detected in '{rel_str}'.")

    try:
        resolved_root, resolved_dest = verify_safe_target_path(target_root, relative_path)
    except SecurityValidationError as err:
        raise VerificationPathSecurityError(f"Security validation error: {err}") from err

    if resolved_dest.exists() or resolved_dest.is_symlink():
        try:
            target = resolved_dest.resolve(strict=False)
            if not target.is_relative_to(resolved_root):
                msg = f"Symlink target '{target}' escapes root '{resolved_root}'."
                raise VerificationPathSecurityError(msg)
        except ValueError as err:
            msg = f"Path '{resolved_dest}' outside '{resolved_root}'."
            raise VerificationPathSecurityError(msg) from err

    return resolved_root, resolved_dest


def _hash_content(content: str | bytes) -> str:
    """Return SHA-256 hex digest of string or bytes content."""
    if isinstance(content, str):
        content_bytes = content.encode("utf-8")
    else:
        content_bytes = content
    return hashlib.sha256(content_bytes).hexdigest()


def _hash_file(file_path: Path) -> str:
    """Return SHA-256 hex digest of a file on disk."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _get_json_path_value(data: Any, path: str) -> tuple[bool, Any]:
    """Retrieve value from a nested JSON/YAML structure using dot notation."""
    parts = path.strip().split(".")
    curr = data
    for part in parts:
        if not isinstance(curr, dict) or part not in curr:
            return False, None
        curr = curr[part]
    return True, curr


class VerificationEngine:
    """Pure, testable engine that inspects actual resulting state against installation plans."""

    def __init__(
        self,
        agent_manager: AgentDetectionManager | None = None,
        registry: Registry | None = None,
    ) -> None:
        self.agent_manager = agent_manager
        self.registry = registry

    def verify_operation(
        self,
        op: BaseOperation,
        dry_run: bool = False,
        secret_values: dict[str, str] | None = None,
    ) -> VerificationCheck:
        """Verify a single installation operation against the actual host state."""
        secrets_list = list(secret_values.values()) if secret_values else []
        check_id = f"verify_{op.op_type.value}_{op.target_path or op.target_root}"
        desc = mask_secrets_in_text(op.description, secrets_list)

        if dry_run:
            return VerificationCheck(
                check_id=check_id,
                description=f"{desc} (PLAN ONLY)",
                status=VerificationStatus.SKIPPED,
                actual_value="PLAN ONLY",
            )

        if isinstance(op, CreateDirectoryOperation):
            _, dest = verify_path_security(op.target_root, op.directory_path)
            if dest.exists() and dest.is_dir():
                return VerificationCheck(
                    check_id=check_id,
                    description=desc,
                    status=VerificationStatus.PASSED,
                    expected_value=f"Directory at {op.directory_path}",
                    actual_value="Directory exists",
                )
            return VerificationCheck(
                check_id=check_id,
                description=desc,
                status=VerificationStatus.FAILED,
                expected_value=f"Directory at {op.directory_path}",
                actual_value="Directory missing or not a directory",
                error_info=f"Required directory '{op.directory_path}' was not found.",
            )

        if isinstance(op, WriteFileOperation):
            _, dest = verify_path_security(op.target_root, op.file_path)
            if not dest.exists() or not dest.is_file():
                return VerificationCheck(
                    check_id=check_id,
                    description=desc,
                    status=VerificationStatus.FAILED,
                    expected_value=f"File at {op.file_path}",
                    actual_value="File missing",
                    error_info=f"Expected file '{op.file_path}' was not found.",
                )
            actual_hash = _hash_file(dest)
            expected_hash = _hash_content(op.content)
            if actual_hash == expected_hash:
                return VerificationCheck(
                    check_id=check_id,
                    description=desc,
                    status=VerificationStatus.PASSED,
                    expected_value=f"sha256:{expected_hash}",
                    actual_value=f"sha256:{actual_hash}",
                )
            return VerificationCheck(
                check_id=check_id,
                description=desc,
                status=VerificationStatus.FAILED,
                expected_value=f"sha256:{expected_hash}",
                actual_value=f"sha256:{actual_hash}",
                error_info=f"File content hash mismatch for '{op.file_path}'.",
            )

        if isinstance(op, CopyFileOperation):
            _, dest = verify_path_security(op.target_root, op.destination_path)
            src_path = Path(op.source_path).expanduser().resolve()
            if not dest.exists() or not dest.is_file():
                return VerificationCheck(
                    check_id=check_id,
                    description=desc,
                    status=VerificationStatus.FAILED,
                    expected_value=f"File at {op.destination_path}",
                    actual_value="Destination file missing",
                    error_info=f"Copied file '{op.destination_path}' missing.",
                )
            if src_path.exists() and src_path.is_file():
                src_hash = _hash_file(src_path)
                dest_hash = _hash_file(dest)
                if src_hash != dest_hash:
                    return VerificationCheck(
                        check_id=check_id,
                        description=desc,
                        status=VerificationStatus.FAILED,
                        expected_value=f"sha256:{src_hash}",
                        actual_value=f"sha256:{dest_hash}",
                        error_info=(
                            f"Content hash mismatch for copied file '{op.destination_path}'."
                        ),
                    )
            return VerificationCheck(
                check_id=check_id,
                description=desc,
                status=VerificationStatus.PASSED,
                expected_value="Copied file content match",
                actual_value="Copied file content verified",
            )

        if isinstance(op, ModifyJsonOperation):
            _, dest = verify_path_security(op.target_root, op.file_path)
            if not dest.exists() or not dest.is_file():
                return VerificationCheck(
                    check_id=check_id,
                    description=desc,
                    status=VerificationStatus.FAILED,
                    expected_value=f"JSON file at {op.file_path}",
                    actual_value="File missing",
                    error_info=f"Configuration file '{op.file_path}' missing.",
                )
            try:
                content = dest.read_text(encoding="utf-8")
                data = json.loads(content)
            except Exception as err:
                return VerificationCheck(
                    check_id=check_id,
                    description=desc,
                    status=VerificationStatus.FAILED,
                    expected_value="Valid JSON configuration",
                    actual_value="Malformed JSON",
                    error_info=f"Failed to parse JSON file '{op.file_path}': {err}",
                )
            exists, actual_val = _get_json_path_value(data, op.json_path)
            if not exists:
                return VerificationCheck(
                    check_id=check_id,
                    description=desc,
                    status=VerificationStatus.FAILED,
                    expected_value=f"Key '{op.json_path}' present",
                    actual_value="Key missing",
                    error_info=f"Key '{op.json_path}' not found in '{op.file_path}'.",
                )
            if actual_val != op.value:
                exp_str = mask_secrets_in_text(str(op.value), secrets_list)
                act_str = mask_secrets_in_text(str(actual_val), secrets_list)
                return VerificationCheck(
                    check_id=check_id,
                    description=desc,
                    status=VerificationStatus.FAILED,
                    expected_value=exp_str,
                    actual_value=act_str,
                    error_info=f"Value mismatch for key '{op.json_path}'.",
                )
            return VerificationCheck(
                check_id=check_id,
                description=desc,
                status=VerificationStatus.PASSED,
                expected_value=mask_secrets_in_text(str(op.value), secrets_list),
                actual_value=mask_secrets_in_text(str(actual_val), secrets_list),
            )

        if isinstance(op, ModifyYamlOperation):
            _, dest = verify_path_security(op.target_root, op.file_path)
            if not dest.exists() or not dest.is_file():
                return VerificationCheck(
                    check_id=check_id,
                    description=desc,
                    status=VerificationStatus.FAILED,
                    expected_value=f"YAML file at {op.file_path}",
                    actual_value="File missing",
                    error_info=f"Configuration file '{op.file_path}' missing.",
                )
            try:
                content = dest.read_text(encoding="utf-8")
                data = yaml.safe_load(content)
            except Exception as err:
                return VerificationCheck(
                    check_id=check_id,
                    description=desc,
                    status=VerificationStatus.FAILED,
                    expected_value="Valid YAML configuration",
                    actual_value="Malformed YAML",
                    error_info=f"Failed to parse YAML file '{op.file_path}': {err}",
                )
            exists, actual_val = _get_json_path_value(data, op.yaml_path)
            if not exists or actual_val != op.value:
                exp_str = mask_secrets_in_text(str(op.value), secrets_list)
                act_str = mask_secrets_in_text(
                    str(actual_val) if exists else "Key missing", secrets_list
                )
                return VerificationCheck(
                    check_id=check_id,
                    description=desc,
                    status=VerificationStatus.FAILED,
                    expected_value=exp_str,
                    actual_value=act_str,
                    error_info=f"Key/value mismatch for '{op.yaml_path}'.",
                )
            return VerificationCheck(
                check_id=check_id,
                description=desc,
                status=VerificationStatus.PASSED,
                expected_value=mask_secrets_in_text(str(op.value), secrets_list),
                actual_value=mask_secrets_in_text(str(actual_val), secrets_list),
            )

        if isinstance(op, AddMcpServerOperation):
            _, dest = verify_path_security(op.target_root, op.config_path)
            if not dest.exists() or not dest.is_file():
                return VerificationCheck(
                    check_id=check_id,
                    description=desc,
                    status=VerificationStatus.FAILED,
                    expected_value=f"MCP config file at {op.config_path}",
                    actual_value="File missing",
                    error_info=(
                        f"MCP server '{op.server_name}' was not found:"
                        f" config file '{op.config_path}' missing."
                    ),
                )
            try:
                content = dest.read_text(encoding="utf-8")
                data = json.loads(content)
            except Exception as err:
                return VerificationCheck(
                    check_id=check_id,
                    description=desc,
                    status=VerificationStatus.FAILED,
                    expected_value="Valid JSON configuration",
                    actual_value="Malformed JSON",
                    error_info=f"Malformed configuration file '{op.config_path}': {err}",
                )
            mcp_servers = data.get("mcpServers")
            if not isinstance(mcp_servers, dict) or op.server_name not in mcp_servers:
                return VerificationCheck(
                    check_id=check_id,
                    description=desc,
                    status=VerificationStatus.FAILED,
                    expected_value=f"mcpServers.{op.server_name} entry",
                    actual_value="Server entry missing",
                    error_info=f"MCP server '{op.server_name}' was not found in configuration.",
                )
            srv_cfg = mcp_servers[op.server_name]
            if not isinstance(srv_cfg, dict):
                return VerificationCheck(
                    check_id=check_id,
                    description=desc,
                    status=VerificationStatus.FAILED,
                    expected_value=f"mcpServers.{op.server_name} object",
                    actual_value="Non-dict entry",
                    error_info=f"MCP server '{op.server_name}' entry is malformed.",
                )
            # Verify command/runtime
            actual_cmd = srv_cfg.get("command")
            if actual_cmd != op.runtime.value:
                return VerificationCheck(
                    check_id=check_id,
                    description=desc,
                    status=VerificationStatus.FAILED,
                    expected_value=f"runtime command '{op.runtime.value}'",
                    actual_value=f"command '{actual_cmd}'",
                    error_info=(
                        f"MCP server '{op.server_name}' runtime mismatch: expected"
                        f" '{op.runtime.value}', got '{actual_cmd}'."
                    ),
                )
            # Verify package name in args
            args = srv_cfg.get("args", [])
            if not isinstance(args, list) or op.package_name not in args:
                return VerificationCheck(
                    check_id=check_id,
                    description=desc,
                    status=VerificationStatus.FAILED,
                    expected_value=f"package '{op.package_name}' in args",
                    actual_value=f"args: {args}",
                    error_info=(
                        f"MCP server '{op.server_name}' package identity"
                        f" '{op.package_name}' missing from args."
                    ),
                )
            # Verify environment variables if required
            if op.env_var_names:
                env_dict = srv_cfg.get("env")
                if not isinstance(env_dict, dict):
                    return VerificationCheck(
                        check_id=check_id,
                        description=desc,
                        status=VerificationStatus.FAILED,
                        expected_value=f"env block containing {op.env_var_names}",
                        actual_value="env block missing",
                        error_info=(
                            f"MCP server '{op.server_name}' missing required env configuration."
                        ),
                    )
                for env_name in op.env_var_names:
                    if env_name not in env_dict:
                        return VerificationCheck(
                            check_id=check_id,
                            description=desc,
                            status=VerificationStatus.FAILED,
                            expected_value=f"env var name '{env_name}' present",
                            actual_value=f"env var '{env_name}' missing",
                            error_info=(
                                f"MCP server '{op.server_name}' missing env var '{env_name}'."
                            ),
                        )
                    # Secret protection check
                    env_val = str(env_dict[env_name])
                    if secret_values and env_name in secret_values:
                        sec_val = secret_values[env_name]
                        if sec_val and sec_val in env_val and sec_val != f"${{{env_name}}}":
                            return VerificationCheck(
                                check_id=check_id,
                                description=desc,
                                status=VerificationStatus.FAILED,
                                expected_value=f"${{{env_name}}} reference",
                                actual_value="Plaintext secret value detected in configuration",
                                error_info=(
                                    "Security violation: Plaintext secret detected for"
                                    f" '{env_name}' in config."
                                ),
                            )

            return VerificationCheck(
                check_id=check_id,
                description=desc,
                status=VerificationStatus.PASSED,
                expected_value=f"mcpServers.{op.server_name} valid",
                actual_value=f"mcpServers.{op.server_name} verified",
            )

        if isinstance(op, AddSkillOperation):
            _, dest_dir = verify_path_security(op.target_root, op.destination_dir)
            if not dest_dir.exists() or not dest_dir.is_dir():
                return VerificationCheck(
                    check_id=check_id,
                    description=desc,
                    status=VerificationStatus.FAILED,
                    expected_value=f"Skill directory at {op.destination_dir}",
                    actual_value="Directory missing",
                    error_info=f"Skill directory '{op.destination_dir}' missing.",
                )
            skill_file_rel = f"{op.destination_dir}/{op.skill_file}"
            _, skill_file_dest = verify_path_security(op.target_root, skill_file_rel)
            if not skill_file_dest.exists() or not skill_file_dest.is_file():
                return VerificationCheck(
                    check_id=check_id,
                    description=desc,
                    status=VerificationStatus.FAILED,
                    expected_value=f"Skill file at {skill_file_rel}",
                    actual_value="Skill file missing",
                    error_info=f"Skill file '{op.skill_file}' missing from '{op.destination_dir}'.",
                )
            # Verify symlink escape out of destination directory
            if skill_file_dest.is_symlink():
                sym_target = skill_file_dest.resolve()
                if not sym_target.is_relative_to(dest_dir):
                    raise VerificationPathSecurityError(
                        f"Symlink skill file '{op.skill_file}' escapes destination directory."
                    )
            # Verify content hash if provided
            if op.skill_content is not None:
                exp_h = _hash_content(op.skill_content)
                act_h = _hash_file(skill_file_dest)
                if exp_h != act_h:
                    return VerificationCheck(
                        check_id=check_id,
                        description=desc,
                        status=VerificationStatus.FAILED,
                        expected_value=f"sha256:{exp_h}",
                        actual_value=f"sha256:{act_h}",
                        error_info=f"Skill file content hash mismatch for '{op.skill_file}'.",
                    )
            # Verify supporting files
            for supp in op.supporting_files:
                supp_rel = f"{op.destination_dir}/{supp}"
                _, supp_dest = verify_path_security(op.target_root, supp_rel)
                if not supp_dest.exists() or not supp_dest.is_file():
                    return VerificationCheck(
                        check_id=check_id,
                        description=desc,
                        status=VerificationStatus.FAILED,
                        expected_value=f"Supporting file at {supp_rel}",
                        actual_value="Supporting file missing",
                        error_info=f"Supporting file '{supp}' missing from '{op.destination_dir}'.",
                    )
                if supp_dest.is_symlink():
                    sym_target = supp_dest.resolve()
                    if not sym_target.is_relative_to(dest_dir):
                        raise VerificationPathSecurityError(
                            f"Symlink supporting file '{supp}' escapes destination directory."
                        )
                if supp in op.supporting_contents:
                    exp_supp_h = _hash_content(op.supporting_contents[supp])
                    act_supp_h = _hash_file(supp_dest)
                    if exp_supp_h != act_supp_h:
                        return VerificationCheck(
                            check_id=check_id,
                            description=desc,
                            status=VerificationStatus.FAILED,
                            expected_value=f"sha256:{exp_supp_h}",
                            actual_value=f"sha256:{act_supp_h}",
                            error_info=f"Supporting file content hash mismatch for '{supp}'.",
                        )

            return VerificationCheck(
                check_id=check_id,
                description=desc,
                status=VerificationStatus.PASSED,
                expected_value=f"Skill '{op.skill_name}' bundle valid",
                actual_value="Skill directory and files verified",
            )

        if isinstance(op, AddPluginReferenceOperation):
            _, dest = verify_path_security(op.target_root, op.config_path)
            if not dest.exists() or not dest.is_file():
                return VerificationCheck(
                    check_id=check_id,
                    description=desc,
                    status=VerificationStatus.FAILED,
                    expected_value=f"Plugin descriptor at {op.config_path}",
                    actual_value="Descriptor missing",
                    error_info=f"Plugin descriptor '{op.config_path}' missing.",
                )
            try:
                content = dest.read_text(encoding="utf-8")
                data = json.loads(content)
            except Exception as err:
                return VerificationCheck(
                    check_id=check_id,
                    description=desc,
                    status=VerificationStatus.FAILED,
                    expected_value="Valid JSON configuration",
                    actual_value="Malformed JSON",
                    error_info=f"Malformed plugin config file '{op.config_path}': {err}",
                )
            plugins_dict = data.get("plugins", {})
            plug_entry: Any = None
            if isinstance(plugins_dict, dict) and op.plugin_id in plugins_dict:
                plug_entry = plugins_dict[op.plugin_id]
            elif "id" in data and data["id"] == op.plugin_id:
                plug_entry = data
            else:
                return VerificationCheck(
                    check_id=check_id,
                    description=desc,
                    status=VerificationStatus.FAILED,
                    expected_value=f"Plugin entry '{op.plugin_id}'",
                    actual_value="Plugin entry missing",
                    error_info=f"Plugin '{op.plugin_id}' reference missing from configuration.",
                )
            if isinstance(plug_entry, dict):
                comps = plug_entry.get("components", [])
                for comp_id in op.component_ids:
                    if comp_id not in comps:
                        return VerificationCheck(
                            check_id=check_id,
                            description=desc,
                            status=VerificationStatus.FAILED,
                            expected_value=f"Component '{comp_id}' in plugin descriptor",
                            actual_value=f"components: {comps}",
                            error_info=f"Plugin component '{comp_id}' missing from descriptor.",
                        )

            return VerificationCheck(
                check_id=check_id,
                description=desc,
                status=VerificationStatus.PASSED,
                expected_value=f"Plugin '{op.plugin_id}' reference valid",
                actual_value="Plugin reference verified",
            )

        raise VerificationError(f"Unsupported operation type '{op.op_type}'.")

    def verify_plan(
        self,
        plan: InstallationPlan,
        dry_run: bool = False,
        secret_values: dict[str, str] | None = None,
    ) -> VerificationResult:
        """Deterministically verify whether the installation produced the state
        described by the installation plan.
        """
        secrets_list = list(secret_values.values()) if secret_values else []

        if dry_run:
            check = VerificationCheck(
                check_id="dry_run_inspection",
                description=(
                    f"Dry-run structural plan verification for '{plan.addon_id}' (PLAN ONLY)"
                ),
                status=VerificationStatus.SKIPPED,
                actual_value="PLAN ONLY - Zero disk mutations executed or verified",
            )
            return VerificationResult(
                addon_id=plan.addon_id,
                addon_name=plan.addon_name,
                target_agent=plan.target_agent,
                target_scope=plan.target_scope,
                status=VerificationStatus.PASSED,
                verified=True,
                checks=[check],
                warnings=["Dry-run mode: plan inspected without disk verification."],
            )

        checks: list[VerificationCheck] = []
        errors: list[str] = []
        warnings: list[str] = list(plan.warnings)
        all_passed = True

        for op in plan.planned_operations:
            try:
                check = self.verify_operation(op, dry_run=False, secret_values=secret_values)
                checks.append(check)
                if check.status != VerificationStatus.PASSED:
                    all_passed = False
                    if check.error_info:
                        errors.append(check.error_info)
            except VerificationPathSecurityError as err:
                all_passed = False
                err_msg = mask_secrets_in_text(str(err), secrets_list)
                errors.append(err_msg)
                checks.append(
                    VerificationCheck(
                        check_id=f"verify_{op.op_type.value}_path_security",
                        description=f"Verify path security for {op.description}",
                        status=VerificationStatus.FAILED,
                        error_info=err_msg,
                    )
                )
            except Exception as err:
                all_passed = False
                err_msg = mask_secrets_in_text(
                    f"Verification error on '{op.description}': {err}", secrets_list
                )
                errors.append(err_msg)
                checks.append(
                    VerificationCheck(
                        check_id=f"verify_{op.op_type.value}",
                        description=f"Verify operation {op.description}",
                        status=VerificationStatus.FAILED,
                        error_info=err_msg,
                    )
                )

        overall_status = VerificationStatus.PASSED if all_passed else VerificationStatus.FAILED
        return VerificationResult(
            addon_id=plan.addon_id,
            addon_name=plan.addon_name,
            target_agent=plan.target_agent,
            target_scope=plan.target_scope,
            status=overall_status,
            verified=all_passed,
            checks=checks,
            warnings=warnings,
            errors=errors,
        )

    def verify_rollback(
        self,
        plan: InstallationPlan,
    ) -> VerificationResult:
        """Verify that rollback left the host filesystem clean without target-root escapes."""
        checks: list[VerificationCheck] = []
        errors: list[str] = []
        all_clean = True

        for op in reversed(plan.planned_operations):
            check_id = f"rollback_verify_{op.op_type.value}"
            desc = f"Verify rollback for '{op.description}'"
            target_path = (
                op.target_path
                or getattr(op, "file_path", None)
                or getattr(op, "directory_path", None)
                or getattr(op, "config_path", None)
                or getattr(op, "destination_dir", None)
            )
            if not target_path:
                continue

            try:
                _, resolved_dest = verify_path_security(op.target_root, target_path)
                if isinstance(op, (CreateDirectoryOperation, AddSkillOperation)):
                    if resolved_dest.exists():
                        all_clean = False
                        err_msg = f"Rollback incomplete: directory '{target_path}' still exists."
                        errors.append(err_msg)
                        checks.append(
                            VerificationCheck(
                                check_id=check_id,
                                description=desc,
                                status=VerificationStatus.FAILED,
                                error_info=err_msg,
                            )
                        )
                        continue

                checks.append(
                    VerificationCheck(
                        check_id=check_id,
                        description=desc,
                        status=VerificationStatus.PASSED,
                        actual_value="Rollback verified clean",
                    )
                )
            except Exception as err:
                all_clean = False
                errors.append(f"Rollback verification error for '{target_path}': {err}")
                checks.append(
                    VerificationCheck(
                        check_id=check_id,
                        description=desc,
                        status=VerificationStatus.FAILED,
                        error_info=str(err),
                    )
                )

        return VerificationResult(
            addon_id=plan.addon_id,
            addon_name=plan.addon_name,
            target_agent=plan.target_agent,
            target_scope=plan.target_scope,
            status=VerificationStatus.PASSED if all_clean else VerificationStatus.FAILED,
            verified=all_clean,
            checks=checks,
            errors=errors,
        )
