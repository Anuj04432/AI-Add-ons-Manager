"""Unit tests verifying Phase 6A security guardrails and injection prevention."""

import pytest
from pydantic import ValidationError

from aiaddons.core.exceptions import ManifestValidationError, SecurityValidationError
from aiaddons.core.models.manifest import (
    validate_env_var_name,
    validate_mcp_package_name,
    validate_safe_relative_path,
)
from aiaddons.registry.validator import validate_registry_data


def test_reject_shell_metacharacters_in_manifest_string() -> None:
    """Verify that shell metacharacters in manifest fields raise validation error."""
    malicious_manifest = {
        "id": "malicious-addon; rm -rf /",
        "name": "Malicious Addon",
        "version": "1.0.0",
        "description": "Shell injection attempt",
        "license": "MIT",
        "category": "developer-tools",
        "integration_type": "mcp",
        "source": {
            "source_type": "package",
            "package_name": "@scope/valid-pkg",
            "checksum": "sha256:1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
        },
        "trust": {"publisher": {"name": "Test"}},
        "handler_spec": {"mcp": {"runtime": "npx", "package_name": "@scope/valid-pkg"}},
    }
    raw_data = {
        "schema_version": "1.0",
        "generated_at": "2026-08-13T12:00:00Z",
        "manifests": [malicious_manifest],
    }
    with pytest.raises((ValidationError, ManifestValidationError, SecurityValidationError)):
        validate_registry_data(raw_data)


def test_reject_forbidden_keyword_fields() -> None:
    """Verify that forbidden fields like install_command or script are rejected."""
    forbidden_field_manifest = {
        "id": "forbidden-field-addon",
        "name": "Forbidden Field Addon",
        "version": "1.0.0",
        "description": "Contains forbidden keyword field",
        "license": "MIT",
        "category": "developer-tools",
        "integration_type": "mcp",
        "install_command": "echo hacked",
        "source": {
            "source_type": "package",
            "package_name": "@scope/valid-pkg",
            "checksum": "sha256:1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
        },
        "trust": {"publisher": {"name": "Test"}},
        "handler_spec": {"mcp": {"runtime": "npx", "package_name": "@scope/valid-pkg"}},
    }
    raw_data = {
        "schema_version": "1.0",
        "generated_at": "2026-08-13T12:00:00Z",
        "manifests": [forbidden_field_manifest],
    }
    with pytest.raises((ValidationError, ManifestValidationError)):
        validate_registry_data(raw_data)


def test_reject_dangerous_env_vars() -> None:
    """Verify that dangerous environment variables (LD_PRELOAD, PYTHONPATH, PATH) are rejected."""
    for dangerous_var in ["LD_PRELOAD", "PYTHONPATH", "PATH", "NODE_OPTIONS"]:
        with pytest.raises(ValueError) as exc_info:
            validate_env_var_name(dangerous_var)
        assert "restricted for security reasons" in str(exc_info.value)


def test_reject_package_name_argument_injection() -> None:
    """Verify that package names starting with '-' or containing flags are rejected."""
    for bad_pkg in ["--eval", "-e", "pkg_name; echo hacked", "pkg name", "../pkg"]:
        with pytest.raises(ValueError):
            validate_mcp_package_name(bad_pkg)


def test_reject_path_traversal_variations() -> None:
    """Verify path traversal rejection across relative, absolute, UNC, and drive paths."""
    bad_paths = [
        "../secret.txt",
        "foo/../../bar",
        "/etc/passwd",
        "C:\\Windows\\System32",
        "\\\\server\\share",
        "foo/\0bar",
    ]
    for bad_path in bad_paths:
        with pytest.raises(ValueError):
            validate_safe_relative_path(bad_path)
