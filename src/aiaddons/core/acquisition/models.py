"""Pydantic domain models for Phase 6B Source Acquisition & Staging Subsystem."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from aiaddons.core.models.manifest import SourceType


class AcquiredSourceResult(BaseModel):
    """Result object representing a safely acquired and staged add-on source."""

    model_config = ConfigDict(extra="forbid")

    manifest_id: str
    source_type: SourceType
    staging_path: Path
    checksum: str | None = None
    commit_sha: str | None = None
    is_staged: bool = True
    is_dry_run: bool = False
    acquired_files: list[str] = Field(default_factory=list)


class StagingContext(BaseModel):
    """Context tracking an active acquisition staging directory for a transaction."""

    model_config = ConfigDict(extra="forbid")

    transaction_id: str
    staging_root: Path
    manifest_id: str


class ArchiveCheckResult(BaseModel):
    """Resource usage metadata for archive verification and zip bomb prevention."""

    model_config = ConfigDict(extra="forbid")

    total_files: int
    total_uncompressed_size: int
    max_compression_ratio: float
