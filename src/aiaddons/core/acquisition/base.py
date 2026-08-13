"""Base protocol definition for pluggable source acquisition fetchers."""

from pathlib import Path
from typing import Protocol

from aiaddons.core.acquisition.models import AcquiredSourceResult
from aiaddons.core.models.manifest import SourceSpec, SourceType


class BaseSourceFetcher(Protocol):
    """Abstract protocol for source-type-specific acquisition fetchers."""

    def supported_source_type(self) -> SourceType:
        """Return the SourceType handled by this fetcher."""
        ...

    def fetch(
        self,
        manifest_id: str,
        source: SourceSpec,
        staging_dir: Path,
        dry_run: bool = False,
    ) -> AcquiredSourceResult:
        """Acquire and stage an add-on source securely.

        Args:
            manifest_id: Unique add-on identifier.
            source: Source specification metadata.
            staging_dir: Staging destination directory for the transaction.
            dry_run: If True, preview acquisition without network/process/disk side effects.

        Returns:
            AcquiredSourceResult: Result containing staged path and metadata.

        Raises:
            SourceAcquisitionError: If acquisition or verification fails.
        """
        ...
