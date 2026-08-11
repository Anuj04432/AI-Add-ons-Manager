"""Pydantic domain models for the Transactional Installation Engine."""

from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from aiaddons.core.compatibility.models import CompatibilityResult, DependencyCheckResult
from aiaddons.core.exceptions import SecurityValidationError
from aiaddons.core.models.agent import AgentDetectionResult, Scope
from aiaddons.core.models.manifest import (
    FORBIDDEN_KEYWORD_FIELDS,
    FORBIDDEN_SHELL_PATTERNS,
    IntegrationManifest,
    IntegrationType,
    MCPRuntime,
    MCPTransport,
    SourceSpec,
    validate_env_var_name,
    validate_mcp_package_name,
    validate_safe_relative_path,
)

DISALLOWED_ROOT_CONFIG_KEYS: set[str] = {
    "permissions",
    "allow_all",
    "security",
    "telemetry",
    "auto_approve",
    "auto_approve_commands",
    "trusted_folders",
    "trusted_domains",
    "execution_policy",
    "sudo",
    "hooks",
    "executables",
    "trust",
}


def validate_config_key_path(v: str) -> str:
    """Validate that a configuration path does not target sensitive security settings."""
    cleaned = v.strip()
    if not cleaned:
        raise ValueError("Configuration path key cannot be empty.")
    root_key = cleaned.split(".")[0].split("[")[0].strip().lower()
    if root_key in DISALLOWED_ROOT_CONFIG_KEYS:
        msg = f"Security violation: Modification of key '{root_key}' is disallowed."
        raise ValueError(msg)
    return cleaned


class RiskLevel(StrEnum):
    """Risk severity level associated with an installation plan."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class OperationType(StrEnum):
    """Supported declarative, typed installation operation types."""

    CREATE_DIRECTORY = "create_directory"
    COPY_FILE = "copy_file"
    WRITE_FILE = "write_file"
    MODIFY_JSON = "modify_json"
    MODIFY_YAML = "modify_yaml"
    ADD_MCP_SERVER = "add_mcp_server"
    ADD_SKILL = "add_skill"
    ADD_PLUGIN_REFERENCE = "add_plugin_reference"


class RollbackOperation(BaseModel):
    """Representation of an individual reversal/rollback step."""

    model_config = ConfigDict(extra="forbid")

    op_type: str
    description: str
    target_root: str
    target_path: str
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("target_root", "target_path")
    @classmethod
    def validate_rollback_paths(cls, v: str) -> str:
        validate_safe_relative_path(v)
        return v

    @field_validator("params")
    @classmethod
    def validate_rollback_params(cls, v: dict[str, Any]) -> dict[str, Any]:
        for key, val in v.items():
            if "path" in key.lower() or "dir" in key.lower() or "file" in key.lower():
                if isinstance(val, str):
                    validate_safe_relative_path(val)
        return v


class RollbackMetadata(BaseModel):
    """Comprehensive rollback plan metadata for transactional safety."""

    model_config = ConfigDict(extra="forbid")

    reversible: bool = True
    rollback_operations: list[RollbackOperation] = Field(default_factory=list)
    instructions: str | None = None


def validate_target_root_string(v: str) -> str:
    """Validate that target_root is safe and contains no metacharacters, UNC paths, or traversal."""
    cleaned = v.strip()
    if not cleaned:
        raise ValueError("target_root cannot be empty.")
    if "\0" in cleaned:
        raise ValueError("Null byte detected in target_root.")
    if cleaned.startswith("//") or cleaned.startswith("\\\\"):
        raise ValueError(f"UNC path '{v}' is prohibited for target_root.")
    for char in FORBIDDEN_SHELL_PATTERNS:
        if char in cleaned:
            raise ValueError(f"Shell operator '{char}' detected in target_root.")
    normalized = cleaned.replace("\\", "/")
    parts = Path(normalized).parts
    if ".." in parts or any(part == ".." for part in normalized.split("/")):
        raise ValueError(f"Traversal sequence '..' detected in target_root '{v}'.")
    return cleaned


class BaseOperation(BaseModel):
    """Base model for all declarative, typed installation operations.

    Future execution must canonicalize boundaries:
        resolved_root = Path(target_root).resolve()
        resolved_dest = (resolved_root / relative_destination).resolve()
    and reject the operation if resolved_dest is outside resolved_root.
    """

    model_config = ConfigDict(extra="forbid")

    op_type: OperationType
    description: str
    target_root: str
    target_path: str | None = None
    reversible: bool = True
    rollback: RollbackMetadata | None = None

    @field_validator("target_root")
    @classmethod
    def validate_target_root(cls, v: str) -> str:
        return validate_target_root_string(v)

    @field_validator("target_path")
    @classmethod
    def validate_target_path(cls, v: str | None) -> str | None:
        return validate_safe_relative_path(v)


class CreateDirectoryOperation(BaseOperation):
    """Declarative operation to create a required directory."""

    op_type: Literal[OperationType.CREATE_DIRECTORY] = OperationType.CREATE_DIRECTORY
    directory_path: str

    @field_validator("directory_path")
    @classmethod
    def validate_dir_path(cls, v: str) -> str:
        res = validate_safe_relative_path(v)
        if res is None:
            raise ValueError("directory_path cannot be empty.")
        return res


class CopyFileOperation(BaseOperation):
    """Declarative operation to copy a file from source to destination."""

    op_type: Literal[OperationType.COPY_FILE] = OperationType.COPY_FILE
    source_path: str
    destination_path: str

    @field_validator("source_path")
    @classmethod
    def validate_src(cls, v: str) -> str:
        return validate_target_root_string(v)

    @field_validator("destination_path")
    @classmethod
    def validate_dst(cls, v: str) -> str:
        res = validate_safe_relative_path(v)
        if res is None:
            raise ValueError("destination_path cannot be empty.")
        return res


class WriteFileOperation(BaseOperation):
    """Declarative operation to write a new file or asset."""

    op_type: Literal[OperationType.WRITE_FILE] = OperationType.WRITE_FILE
    file_path: str
    content: str
    content_summary: str
    overwrite: bool = False

    @field_validator("file_path")
    @classmethod
    def validate_write_path(cls, v: str) -> str:
        res = validate_safe_relative_path(v)
        if res is None:
            raise ValueError("file_path cannot be empty.")
        return res


class ModifyJsonOperation(BaseOperation):
    """Declarative operation to safely update a JSON configuration file."""

    op_type: Literal[OperationType.MODIFY_JSON] = OperationType.MODIFY_JSON
    file_path: str
    json_path: str
    value: Any
    value_summary: str

    @field_validator("file_path")
    @classmethod
    def validate_json_file_path(cls, v: str) -> str:
        res = validate_safe_relative_path(v)
        if res is None:
            raise ValueError("file_path cannot be empty.")
        return res

    @field_validator("json_path")
    @classmethod
    def validate_json_key(cls, v: str) -> str:
        return validate_config_key_path(v)


class ModifyYamlOperation(BaseOperation):
    """Declarative operation to safely update a YAML configuration file."""

    op_type: Literal[OperationType.MODIFY_YAML] = OperationType.MODIFY_YAML
    file_path: str
    yaml_path: str
    value: Any
    value_summary: str

    @field_validator("file_path")
    @classmethod
    def validate_yaml_file_path(cls, v: str) -> str:
        res = validate_safe_relative_path(v)
        if res is None:
            raise ValueError("file_path cannot be empty.")
        return res

    @field_validator("yaml_path")
    @classmethod
    def validate_yaml_key(cls, v: str) -> str:
        return validate_config_key_path(v)


class AddMcpServerOperation(BaseOperation):
    """Declarative operation to inject an MCP server config into an agent configuration."""

    op_type: Literal[OperationType.ADD_MCP_SERVER] = OperationType.ADD_MCP_SERVER
    server_name: str
    runtime: MCPRuntime
    package_name: str
    transport: MCPTransport = MCPTransport.STDIO
    env_var_names: list[str] = Field(default_factory=list)
    config_path: str

    @field_validator("config_path")
    @classmethod
    def validate_mcp_config_path(cls, v: str) -> str:
        res = validate_safe_relative_path(v)
        if res is None:
            raise ValueError("config_path cannot be empty.")
        return res

    @field_validator("package_name")
    @classmethod
    def validate_pkg_name(cls, v: str) -> str:
        return validate_mcp_package_name(v)

    @field_validator("env_var_names")
    @classmethod
    def validate_env_list(cls, v: list[str]) -> list[str]:
        for name in v:
            validate_env_var_name(name)
        return v


class AddSkillOperation(BaseOperation):
    """Declarative operation to deploy an agent skill bundle."""

    op_type: Literal[OperationType.ADD_SKILL] = OperationType.ADD_SKILL
    skill_name: str
    skill_file: str
    destination_dir: str
    supporting_files: list[str] = Field(default_factory=list)
    source_dir: str | None = None
    skill_content: str | None = None
    supporting_contents: dict[str, str] = Field(default_factory=dict)


    @field_validator("destination_dir", "skill_file")
    @classmethod
    def validate_skill_paths(cls, v: str) -> str:
        res = validate_safe_relative_path(v)
        if res is None:
            raise ValueError("Skill path cannot be empty.")
        return res

    @field_validator("supporting_files")
    @classmethod
    def validate_supporting_list(cls, v: list[str]) -> list[str]:
        for f in v:
            validate_safe_relative_path(f)
        return v


class AddPluginReferenceOperation(BaseOperation):
    """Declarative operation to register a composite plugin reference."""

    op_type: Literal[OperationType.ADD_PLUGIN_REFERENCE] = OperationType.ADD_PLUGIN_REFERENCE
    plugin_id: str
    component_ids: list[str] = Field(default_factory=list)
    config_path: str

    @field_validator("config_path")
    @classmethod
    def validate_plugin_config_path(cls, v: str) -> str:
        res = validate_safe_relative_path(v)
        if res is None:
            raise ValueError("config_path cannot be empty.")
        return res


TypedOperation = (
    CreateDirectoryOperation
    | CopyFileOperation
    | WriteFileOperation
    | ModifyJsonOperation
    | ModifyYamlOperation
    | AddMcpServerOperation
    | AddSkillOperation
    | AddPluginReferenceOperation
)


def _check_string_safety(text: str, context: str) -> None:
    """Check string fields for forbidden shell characters."""
    for char in FORBIDDEN_SHELL_PATTERNS:
        if char in text:
            raise SecurityValidationError(
                f"Security violation in operation ({context}): Shell operator '{char}' detected."
            )


def validate_rollback_operation_safety(rb: RollbackOperation) -> None:
    """Validate safety of a rollback operation."""
    validate_safe_relative_path(rb.target_root)
    validate_safe_relative_path(rb.target_path)
    data = rb.model_dump()

    def _recurse_check(obj: Any, path: str, is_top_level: bool = False) -> None:
        if isinstance(obj, str):
            _check_string_safety(obj, path)
        elif isinstance(obj, dict):
            for k, v in obj.items():
                if is_top_level and str(k).lower() in FORBIDDEN_KEYWORD_FIELDS:
                    msg = f"Security violation in rollback ({path}): Forbidden field '{k}'."
                    raise SecurityValidationError(msg)
                _recurse_check(v, f"{path}.{k}", is_top_level=False)
        elif isinstance(obj, list):
            for idx, item in enumerate(obj):
                _recurse_check(item, f"{path}[{idx}]", is_top_level=False)

    _recurse_check(data, f"rollback.{rb.op_type}", is_top_level=True)


def validate_operation_safety(op: BaseOperation) -> None:
    """Validate that an operation contains no forbidden shell syntax or unapproved command keys."""
    validate_target_root_string(op.target_root)
    if op.target_path:
        validate_safe_relative_path(op.target_path)

    data = op.model_dump()

    def _recurse_check(obj: Any, path: str, is_top_level: bool = False) -> None:
        if isinstance(obj, str):
            _check_string_safety(obj, path)
        elif isinstance(obj, dict):
            for k, v in obj.items():
                if is_top_level and str(k).lower() in FORBIDDEN_KEYWORD_FIELDS:
                    msg = f"Security violation in operation ({path}): Forbidden field '{k}'."
                    raise SecurityValidationError(msg)
                _recurse_check(v, f"{path}.{k}", is_top_level=False)
        elif isinstance(obj, list):
            for idx, item in enumerate(obj):
                _recurse_check(item, f"{path}[{idx}]", is_top_level=False)

    _recurse_check(data, op.op_type.value, is_top_level=True)

    if op.rollback and op.rollback.rollback_operations:
        for rb in op.rollback.rollback_operations:
            validate_rollback_operation_safety(rb)


class InstallationPlan(BaseModel):
    """Structured, declarative plan describing operations to be performed during installation."""

    model_config = ConfigDict(extra="forbid")

    addon_id: str
    addon_name: str
    addon_version: str
    target_agent: str
    target_agent_name: str
    target_scope: Scope
    integration_type: IntegrationType
    source: SourceSpec
    planned_operations: list[BaseOperation]
    dependencies: list[DependencyCheckResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    reversible: bool = True
    rollback_info: RollbackMetadata

    def validate_safety(self) -> None:
        """Validate safety of all planned operations and rollback metadata in the plan."""
        for op in self.planned_operations:
            validate_operation_safety(op)

        if self.rollback_info and self.rollback_info.rollback_operations:
            for rb in self.rollback_info.rollback_operations:
                validate_rollback_operation_safety(rb)


class TransactionPhase(StrEnum):
    """Lifecyle phases of an installation transaction."""

    REQUESTED = "requested"
    COMPATIBILITY_CHECKED = "compatibility_checked"
    PLANNED = "planned"
    REVIEWED = "reviewed"
    EXECUTING = "executing"
    VERIFIED = "verified"
    COMMITTED = "committed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class InstallationTransaction(BaseModel):
    """Model tracking the complete lifecycle and dry-run state of an installation request."""

    model_config = ConfigDict(extra="forbid")

    transaction_id: str
    phase: TransactionPhase = TransactionPhase.REQUESTED
    manifest: IntegrationManifest
    agent: AgentDetectionResult
    requested_scope: Scope
    compatibility_result: CompatibilityResult | None = None
    plan: InstallationPlan | None = None
    is_dry_run: bool = True
    error_message: str | None = None
