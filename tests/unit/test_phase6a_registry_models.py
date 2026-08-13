"""Unit tests for Phase 6A Registry domain models and validation layer."""

import pytest
from pydantic import ValidationError

from aiaddons.core.exceptions import ManifestValidationError, SecurityValidationError
from aiaddons.core.models.agent import Scope
from aiaddons.core.models.manifest import (
    HandlerSpecContainer,
    IntegrationManifest,
    IntegrationType,
    MCPHandlerSpec,
    MCPRuntime,
    PublisherClaimSpec,
    SourceSpec,
    SourceType,
    TrustMetadata,
)
from aiaddons.registry.models import RegistryIndex
from aiaddons.registry.validator import validate_registry_data


def _make_valid_manifest(
    addon_id: str = "valid-mcp",
    version: str = "1.0.0",
    package_name: str = "@scope/valid-mcp",
    checksum: str = "sha256:1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
) -> IntegrationManifest:
    """Helper to create a valid IntegrationManifest."""
    return IntegrationManifest(
        id=addon_id,
        name="Valid MCP",
        version=version,
        description="A valid test MCP server.",
        license="MIT",
        category="developer-tools",
        integration_type=IntegrationType.MCP,
        target_agents=["claude-code"],
        supported_scopes=[Scope.GLOBAL, Scope.WORKSPACE],
        source=SourceSpec(
            source_type=SourceType.PACKAGE,
            package_name=package_name,
            checksum=checksum,
        ),
        trust=TrustMetadata(
            publisher=PublisherClaimSpec(name="Valid Publisher"),
        ),
        handler_spec=HandlerSpecContainer(
            mcp=MCPHandlerSpec(
                runtime=MCPRuntime.NPX,
                package_name=package_name,
            )
        ),
    )


def test_valid_registry_index() -> None:
    """Test instantiating a valid RegistryIndex."""
    manifest = _make_valid_manifest("addon-1")
    index = RegistryIndex(
        schema_version="1.0",
        generated_at="2026-08-13T12:00:00Z",
        manifests=[manifest],
    )
    assert index.schema_version == "1.0"
    assert len(index.manifests) == 1
    assert index.manifests[0].id == "addon-1"


def test_invalid_schema_version() -> None:
    """Test that unsupported schema version raises ValidationError."""
    manifest = _make_valid_manifest("addon-1")
    with pytest.raises(ValidationError) as exc_info:
        RegistryIndex(
            schema_version="9.9",
            generated_at="2026-08-13T12:00:00Z",
            manifests=[manifest],
        )
    assert "Unsupported registry schema version" in str(exc_info.value)


def test_duplicate_addon_ids_rejected() -> None:
    """Test that duplicate add-on IDs within an index raise ValidationError."""
    m1 = _make_valid_manifest("dup-addon")
    m2 = _make_valid_manifest("dup-addon")
    with pytest.raises(ValidationError) as exc_info:
        RegistryIndex(
            schema_version="1.0",
            generated_at="2026-08-13T12:00:00Z",
            manifests=[m1, m2],
        )
    assert "Duplicate add-on ID 'dup-addon'" in str(exc_info.value)


def test_extra_fields_forbidden() -> None:
    """Test that extra unrecognised fields in RegistryIndex raise ValidationError."""
    with pytest.raises(ValidationError):
        RegistryIndex.model_validate(
            {
                "schema_version": "1.0",
                "generated_at": "2026-08-13T12:00:00Z",
                "manifests": [],
                "unwanted_extra_field": "forbidden",
            }
        )


def test_validator_rejects_invalid_checksum_format() -> None:
    """Test that RegistryValidator rejects invalid SHA-256 checksum format."""
    raw_data = {
        "schema_version": "1.0",
        "generated_at": "2026-08-13T12:00:00Z",
        "manifests": [
            _make_valid_manifest(checksum="sha256:short_invalid_hash").model_dump()
        ],
    }
    with pytest.raises(ManifestValidationError) as exc_info:
        validate_registry_data(raw_data)
    assert "checksum" in str(exc_info.value) or "valid SHA-256" in str(exc_info.value)


def test_validator_rejects_git_source_missing_or_invalid_commit_sha() -> None:
    """Test that Git source with invalid commit SHA is rejected by validator."""
    git_manifest_data = {
        "id": "git-addon",
        "name": "Git Addon",
        "version": "1.0.0",
        "description": "Git source add-on",
        "license": "MIT",
        "category": "workflow",
        "integration_type": "skill",
        "source": {
            "source_type": "git",
            "repository": "https://github.com/test/repo",
            "commit_sha": "not_a_40_char_hex_sha",
        },
        "trust": {"publisher": {"name": "Test"}},
        "handler_spec": {"skill": {"skill_file": "SKILL.md"}},
    }
    raw_data = {
        "schema_version": "1.0",
        "generated_at": "2026-08-13T12:00:00Z",
        "manifests": [git_manifest_data],
    }
    with pytest.raises(ManifestValidationError) as exc_info:
        validate_registry_data(raw_data)
    assert "commit_sha" in str(exc_info.value)


def test_validator_rejects_unsafe_mcp_package_name() -> None:
    """Test that unsafe MCP package name raises SecurityValidationError."""
    unsafe_manifest_data = {
        "id": "unsafe-mcp",
        "name": "Unsafe MCP",
        "version": "1.0.0",
        "description": "Unsafe package name",
        "license": "MIT",
        "category": "developer-tools",
        "integration_type": "mcp",
        "source": {
            "source_type": "package",
            "package_name": "@scope/valid-pkg",
            "checksum": "sha256:1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
        },
        "trust": {"publisher": {"name": "Test"}},
        "handler_spec": {
            "mcp": {
                "runtime": "npx",
                "package_name": "--inject-flag-pkg",
            }
        },
    }
    raw_data = {
        "schema_version": "1.0",
        "generated_at": "2026-08-13T12:00:00Z",
        "manifests": [unsafe_manifest_data],
    }
    with pytest.raises((SecurityValidationError, ManifestValidationError)):
        validate_registry_data(raw_data)


def test_validator_rejects_unsafe_path_traversal() -> None:
    """Test that path traversal in skill_file is rejected."""
    traversal_manifest_data = {
        "id": "traversal-skill",
        "name": "Traversal Skill",
        "version": "1.0.0",
        "description": "Path traversal attempt",
        "license": "MIT",
        "category": "workflow",
        "integration_type": "skill",
        "source": {
            "source_type": "git",
            "repository": "https://github.com/test/repo",
            "commit_sha": "1234567890abcdef1234567890abcdef12345678",
        },
        "trust": {"publisher": {"name": "Test"}},
        "handler_spec": {
            "skill": {
                "skill_file": "../../../etc/passwd",
            }
        },
    }
    raw_data = {
        "schema_version": "1.0",
        "generated_at": "2026-08-13T12:00:00Z",
        "manifests": [traversal_manifest_data],
    }
    with pytest.raises((SecurityValidationError, ManifestValidationError)):
        validate_registry_data(raw_data)
