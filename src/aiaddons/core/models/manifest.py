"""Pydantic domain models for Add-on Manifests, Typed Handlers, and Trust Metadata."""

import re
import urllib.parse
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from aiaddons.core.models.agent import Scope


class IntegrationType(StrEnum):
    """Supported integration types for AI add-ons."""

    MCP = "mcp"
    SKILL = "skill"
    PLUGIN = "plugin"
    CLI_TOOL = "cli_tool"


class SourceType(StrEnum):
    """Types of add-on distribution sources."""

    GIT = "git"
    PACKAGE = "package"
    URL = "url"
    LOCAL = "local"


DANGEROUS_ENV_VARS: set[str] = {
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "LD_AUDIT",
    "DYLD_INSERT_LIBRARIES",
    "DYLD_LIBRARY_PATH",
    "PYTHONPATH",
    "PYTHONHOME",
    "PYTHONSTARTUP",
    "NODE_OPTIONS",
    "NODE_PATH",
    "PATH",
    "SHELL",
    "COMSPEC",
    "PS1",
    "PROMPT_COMMAND",
    "IFS",
    "ENV",
    "BASH_ENV",
    "RUBYLIB",
    "PERL5LIB",
    "PERLLIB",
    "JAVA_TOOL_OPTIONS",
    "_JAVA_OPTIONS",
}

ENV_VAR_REGEX = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def validate_env_var_name(v: str) -> str:
    """Validate that an environment variable name is safe and valid."""
    name = v.strip()
    if not name:
        raise ValueError("Environment variable name cannot be empty.")
    if not ENV_VAR_REGEX.match(name):
        raise ValueError(
            f"Security violation: Invalid environment variable name '{v}'. "
            "Must match pattern [A-Za-z_][A-Za-z0-9_]*."
        )
    if name.upper() in DANGEROUS_ENV_VARS:
        raise ValueError(
            f"Security violation: Environment variable '{v}' is restricted for security reasons."
        )
    return name


def validate_mcp_package_name(v: str) -> str:
    """Validate that an MCP package name is safe and contains no argument injection vectors."""
    cleaned = v.strip()
    if not cleaned:
        raise ValueError("Package name cannot be empty.")
    if cleaned.startswith("-"):
        raise ValueError(f"Security violation: Package name '{v}' cannot start with a hyphen.")
    if any(c.isspace() for c in cleaned):
        raise ValueError(f"Security violation: Package name '{v}' cannot contain whitespace.")
    if any(char in cleaned for char in FORBIDDEN_SHELL_PATTERNS):
        raise ValueError(f"Security violation: Package name '{v}' contains shell metacharacters.")

    # Strict regex pattern for npm/PyPI package names with optional scope/version specifier
    pattern = r"^(?:@[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+|[a-zA-Z0-9_.-]+)(?:[@=><~^]+[a-zA-Z0-9_.-]+)?$"
    if not re.match(pattern, cleaned) or ".." in cleaned:
        raise ValueError(f"Security violation: Invalid or unsafe package name '{v}'.")
    return cleaned


def validate_safe_relative_path(v: str | None) -> str | None:
    """Validate that a path string is strictly relative and contains no path traversal sequences."""
    if v is None:
        return None

    cleaned = v.strip()
    if not cleaned:
        return cleaned

    if "\0" in cleaned:
        raise ValueError("Path traversal violation: Null byte detected in path.")

    unquoted = urllib.parse.unquote(cleaned)

    for path_str in (cleaned, unquoted):
        # Check for absolute path, UNC path, or Windows drive letter (e.g. C:\)
        if (
            path_str.startswith("/")
            or path_str.startswith("\\")
            or re.match(r"^[a-zA-Z]:", path_str)
            or path_str.startswith("//")
            or path_str.startswith("\\\\")
        ):
            raise ValueError(
                f"Path traversal violation: Absolute, drive, or UNC path '{v}' is prohibited."
            )

        # Normalize backslashes to forward slashes for checking
        normalized = path_str.replace("\\", "/")

        # Check for traversal sequences
        parts = Path(normalized).parts
        if ".." in parts or any(part == ".." for part in normalized.split("/")):
            raise ValueError(
                f"Path traversal violation: Traversal sequence '..' detected in '{v}'."
            )

    return cleaned


class SourceSpec(BaseModel):
    """Metadata describing where an add-on source is obtained from."""

    model_config = ConfigDict(extra="forbid")

    source_type: SourceType
    url: str | None = None
    repository: str | None = None
    ref: str | None = None
    commit_sha: str | None = None
    path: str | None = None
    package_name: str | None = None
    checksum: str | None = None

    @field_validator("path")
    @classmethod
    def validate_source_path(cls, v: str | None) -> str | None:
        """Ensure local path is a safe relative path."""
        return validate_safe_relative_path(v)

    def check_installable(self) -> tuple[bool, str | None]:
        """Check if source contains necessary cryptographic pins for installation."""
        if self.source_type == SourceType.GIT:
            if not self.commit_sha or not re.match(r"^[a-fA-F0-9]{40}$", self.commit_sha):
                return (
                    False,
                    "Git sources require an immutable 40-character commit_sha for installation.",
                )
        elif self.source_type in (SourceType.PACKAGE, SourceType.URL):
            if not self.checksum or not self.checksum.startswith("sha256:"):
                return (
                    False,
                    f"Remote {self.source_type.value} sources require a SHA-256 checksum.",
                )
        elif self.source_type == SourceType.LOCAL:
            if not self.path:
                return False, "Local sources require a relative path specification."
        return True, None


class DependencyType(StrEnum):
    """Types of dependencies required by an add-on."""

    ADDON = "addon"
    CLI = "cli"
    AGENT_CAPABILITY = "agent_capability"
    RUNTIME = "runtime"


class DependencySpec(BaseModel):
    """Metadata describing an external dependency requirement."""

    model_config = ConfigDict(extra="forbid")

    name: str
    type: DependencyType
    version_constraint: str | None = None
    required: bool = True


class VerificationStatus(StrEnum):
    """Trust and verification status levels for add-ons (application-derived)."""

    VERIFIED = "verified"
    COMMUNITY = "community"
    UNVERIFIED = "unverified"


class PublisherClaimSpec(BaseModel):
    """Manifest-declared publisher claims (informational only)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    url: str | None = None
    email: str | None = None
    declared_verified: bool = False


class TrustMetadata(BaseModel):
    """Application-managed trust and security metadata."""

    model_config = ConfigDict(extra="forbid")

    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    publisher: PublisherClaimSpec
    allowed_executables: list[str] = Field(default_factory=list)


class EnvVarSpec(BaseModel):
    """Specification for an environment variable required by an integration."""

    model_config = ConfigDict(extra="forbid")

    name: str
    required: bool = False
    secret: bool = False
    description: str | None = None

    @field_validator("name")
    @classmethod
    def validate_env_name(cls, v: str) -> str:
        return validate_env_var_name(v)


class MCPRuntime(StrEnum):
    """Allowed runtimes for MCP server execution (strict allowlist)."""

    NPX = "npx"
    UVX = "uvx"
    NODE = "node"
    PYTHON = "python"


class MCPTransport(StrEnum):
    """Transport protocol for MCP servers."""

    STDIO = "stdio"
    SSE = "sse"


class MCPHandlerSpec(BaseModel):
    """Strictly typed specification for Model Context Protocol integrations."""

    model_config = ConfigDict(extra="forbid")

    transport: MCPTransport = MCPTransport.STDIO
    runtime: MCPRuntime
    package_name: str
    env_vars: list[EnvVarSpec] = Field(default_factory=list)

    @field_validator("package_name")
    @classmethod
    def validate_package(cls, v: str) -> str:
        return validate_mcp_package_name(v)


class SkillHandlerSpec(BaseModel):
    """Strictly typed specification for Agent Skill integrations."""

    model_config = ConfigDict(extra="forbid")

    skill_file: str = "SKILL.md"
    supporting_files: list[str] = Field(default_factory=list)

    @field_validator("skill_file")
    @classmethod
    def validate_skill_file(cls, v: str) -> str:
        res = validate_safe_relative_path(v)
        if not res:
            raise ValueError("skill_file cannot be empty.")
        return res

    @field_validator("supporting_files")
    @classmethod
    def validate_supporting_files(cls, v: list[str]) -> list[str]:
        for path_str in v:
            validate_safe_relative_path(path_str)
        return v


class PluginHandlerSpec(BaseModel):
    """Strictly typed specification for Composite Plugin integrations."""

    model_config = ConfigDict(extra="forbid")

    components: list[str] = Field(default_factory=list)


class CLIToolHandlerSpec(BaseModel):
    """Strictly typed specification for CLI Tool integrations."""

    model_config = ConfigDict(extra="forbid")

    binary_name: str
    version_constraint: str | None = None


class HandlerSpecContainer(BaseModel):
    """Container holding exactly one typed integration handler specification."""

    model_config = ConfigDict(extra="forbid")

    mcp: MCPHandlerSpec | None = None
    skill: SkillHandlerSpec | None = None
    plugin: PluginHandlerSpec | None = None
    cli_tool: CLIToolHandlerSpec | None = None


FORBIDDEN_SHELL_PATTERNS: list[str] = [
    ";",
    "&&",
    "||",
    "|",
    ">",
    "<",
    "$",
    "`",
]

FORBIDDEN_KEYWORD_FIELDS: set[str] = {
    "install_command",
    "post_install",
    "pre_install",
    "command_string",
    "shell_command",
    "script",
    "powershell",
    "cmd",
    "bash",
    "sh",
    "command",
    "args",
    "executable",
}


def _check_no_forbidden_shell(obj: Any) -> None:
    """Recursively check objects as defense-in-depth against shell execution strings."""
    if isinstance(obj, str):
        for char in FORBIDDEN_SHELL_PATTERNS:
            if char in obj:
                raise ValueError(
                    f"Security violation: Shell operator '{char}' detected in string: '{obj}'"
                )
    elif isinstance(obj, dict):
        for key, val in obj.items():
            if str(key).lower() in FORBIDDEN_KEYWORD_FIELDS:
                raise ValueError(
                    f"Security violation: Forbidden field '{key}' detected in manifest."
                )
            _check_no_forbidden_shell(val)
    elif isinstance(obj, list):
        for item in obj:
            _check_no_forbidden_shell(item)


class IntegrationManifest(BaseModel):
    """Complete domain model representing an AI Add-on Integration Manifest."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    version: str
    description: str
    documentation_url: str | None = None
    license: str
    category: str
    integration_type: IntegrationType
    target_agents: list[str] = Field(default_factory=lambda: ["*"])
    supported_scopes: list[Scope] = Field(
        default_factory=lambda: [Scope.GLOBAL, Scope.WORKSPACE]
    )
    source: SourceSpec
    dependencies: list[DependencySpec] = Field(default_factory=list)
    trust: TrustMetadata
    tags: list[str] = Field(default_factory=list)
    handler_spec: HandlerSpecContainer

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        """Validate manifest ID is non-empty kebab-case or alphanumeric with dashes/underscores."""
        clean_id = v.strip().lower()
        if not clean_id:
            raise ValueError("Add-on ID cannot be empty.")
        if not re.match(r"^[a-z0-9-_]+$", clean_id):
            raise ValueError(
                f"Invalid add-on ID '{v}'. Must be lowercase alphanumeric or dashes."
            )
        return clean_id

    @field_validator("handler_spec")
    @classmethod
    def validate_handler_spec_matches_type(
        cls, v: HandlerSpecContainer, info: Any
    ) -> HandlerSpecContainer:
        """Validate that handler_spec contains the spec corresponding to integration_type."""
        _check_no_forbidden_shell(v.model_dump())
        return v
