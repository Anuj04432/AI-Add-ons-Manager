"""Registry validation layer for validating registry metadata documents and manifests."""

import re
from typing import Any

from pydantic import ValidationError

from aiaddons.core.exceptions import ManifestValidationError, SecurityValidationError
from aiaddons.core.models.manifest import (
    SourceType,
    validate_env_var_name,
    validate_mcp_package_name,
    validate_safe_relative_path,
)
from aiaddons.registry.models import RegistryIndex

SHA256_REGEX = re.compile(r"^sha256:[a-fA-F0-9]{64}$")
GIT_SHA_REGEX = re.compile(r"^[a-fA-F0-9]{40}$")


class RegistryValidator:
    """Dedicated validation layer for registry index documents and add-on manifests."""

    @classmethod
    def validate_registry_data(
        cls, raw_data: dict[str, Any], source_label: str = "registry"
    ) -> RegistryIndex:
        """Validate raw dictionary data against RegistryIndex and manifest domain constraints."""
        if not isinstance(raw_data, dict):
            raise ManifestValidationError(
                source_label, "Registry root content must be a JSON/YAML object dictionary."
            )

        try:
            index = RegistryIndex.model_validate(raw_data)
        except ValidationError as val_err:
            error_details = "; ".join(
                f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}"
                for err in val_err.errors()
            )
            raise ManifestValidationError(source_label, error_details) from val_err
        except ValueError as val_err:
            raise ManifestValidationError(source_label, str(val_err)) from val_err

        # Additional detailed validations on each manifest
        for manifest in index.manifests:
            cls.validate_manifest_security_and_integrity(manifest, source_label)

        return index

    @classmethod
    def validate_manifest_security_and_integrity(
        cls, manifest: Any, source_label: str = "manifest"
    ) -> None:
        """Perform granular security and integrity checks on an IntegrationManifest."""
        # 1. Source spec installability & checksum/commit SHA format verification
        source = manifest.source
        is_installable, reason = source.check_installable()
        if not is_installable:
            raise ManifestValidationError(
                source_label,
                f"Add-on '{manifest.id}' has uninstallable source spec: {reason}",
            )

        if source.source_type == SourceType.GIT and source.commit_sha:
            if not GIT_SHA_REGEX.match(source.commit_sha):
                raise ManifestValidationError(
                    source_label,
                    f"Add-on '{manifest.id}' Git commit_sha '{source.commit_sha}' "
                    "is not a valid 40-character hex string.",
                )

        if source.checksum:
            if not SHA256_REGEX.match(source.checksum):
                raise ManifestValidationError(
                    source_label,
                    f"Add-on '{manifest.id}' checksum '{source.checksum}' is not a valid "
                    "SHA-256 string (must start with 'sha256:' followed by 64 hex characters).",
                )

        # 2. Path safety checks on source path
        if source.path:
            try:
                validate_safe_relative_path(source.path)
            except ValueError as err:
                raise SecurityValidationError(
                    f"Add-on '{manifest.id}' source path security violation: {err}"
                ) from err

        # 3. Handler spec validation
        handler = manifest.handler_spec
        if handler.mcp:
            try:
                validate_mcp_package_name(handler.mcp.package_name)
            except ValueError as err:
                raise SecurityValidationError(
                    f"Add-on '{manifest.id}' MCP package security violation: {err}"
                ) from err

            for env_var in handler.mcp.env_vars:
                try:
                    validate_env_var_name(env_var.name)
                except ValueError as err:
                    raise SecurityValidationError(
                        f"Add-on '{manifest.id}' env_var security violation: {err}"
                    ) from err

        if handler.skill:
            try:
                validate_safe_relative_path(handler.skill.skill_file)
                for sup_file in handler.skill.supporting_files:
                    validate_safe_relative_path(sup_file)
            except ValueError as err:
                raise SecurityValidationError(
                    f"Add-on '{manifest.id}' Skill path security violation: {err}"
                ) from err


def validate_registry_data(
    raw_data: dict[str, Any], source_label: str = "registry"
) -> RegistryIndex:
    """Helper function to validate raw dictionary data against RegistryIndex."""
    return RegistryValidator.validate_registry_data(raw_data, source_label)
