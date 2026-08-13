"""Registry domain models for index metadata, schema versions, and manifest collections."""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from aiaddons.core.models.manifest import IntegrationManifest

SUPPORTED_SCHEMA_VERSIONS: set[str] = {"1.0", "1.0.0"}


class RegistryIndex(BaseModel):
    """Domain model representing a complete declarative add-on registry index."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    generated_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    manifests: list[IntegrationManifest] = Field(default_factory=list)

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, v: str) -> str:
        """Validate that the registry schema version is supported."""
        ver = v.strip()
        if ver not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(
                f"Unsupported registry schema version '{v}'. "
                f"Supported versions: {sorted(SUPPORTED_SCHEMA_VERSIONS)}."
            )
        return ver

    @field_validator("manifests")
    @classmethod
    def validate_unique_manifest_ids(
        cls, v: list[IntegrationManifest]
    ) -> list[IntegrationManifest]:
        """Validate that all add-on manifests in the index have unique IDs."""
        seen_ids: set[str] = set()
        for manifest in v:
            clean_id = manifest.id.strip().lower()
            if clean_id in seen_ids:
                raise ValueError(
                    f"Duplicate add-on ID '{manifest.id}' detected in registry index."
                )
            seen_ids.add(clean_id)
        return v
