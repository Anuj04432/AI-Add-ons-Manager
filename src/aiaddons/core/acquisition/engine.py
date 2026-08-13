"""Central AcquisitionEngine orchestrating secure source acquisition, verification, and staging."""

from aiaddons.core.acquisition.base import BaseSourceFetcher
from aiaddons.core.acquisition.git import GitSourceFetcher
from aiaddons.core.acquisition.local import LocalSourceFetcher
from aiaddons.core.acquisition.models import AcquiredSourceResult
from aiaddons.core.acquisition.package import PackageSourceFetcher
from aiaddons.core.acquisition.staging import SourceStagingManager
from aiaddons.core.acquisition.url import UrlSourceFetcher
from aiaddons.core.exceptions import SourceAcquisitionError
from aiaddons.core.execution.external.runner import ExternalRunner
from aiaddons.core.models.manifest import SourceSpec, SourceType


class AcquisitionEngine:
    """Central engine managing pluggable source fetchers and staging lifecycle."""

    def __init__(
        self,
        fetchers: list[BaseSourceFetcher] | None = None,
        staging_manager: SourceStagingManager | None = None,
        runner: ExternalRunner | None = None,
    ) -> None:
        self.staging_manager = staging_manager or SourceStagingManager()
        self._runner = runner or ExternalRunner()
        self._fetchers: dict[SourceType, BaseSourceFetcher] = {}

        if fetchers is None:
            default_fetchers: list[BaseSourceFetcher] = [
                GitSourceFetcher(runner=self._runner),
                UrlSourceFetcher(),
                PackageSourceFetcher(),
                LocalSourceFetcher(),
            ]
            for f in default_fetchers:
                self.register_fetcher(f)
        else:
            for f in fetchers:
                self.register_fetcher(f)

    def register_fetcher(self, fetcher: BaseSourceFetcher) -> None:
        """Register a source-type-specific fetcher."""
        stype = fetcher.supported_source_type()
        self._fetchers[stype] = fetcher

    def get_fetcher(self, source_type: SourceType) -> BaseSourceFetcher:
        """Retrieve registered fetcher for the given source type."""
        if source_type not in self._fetchers:
            raise SourceAcquisitionError(
                f"No acquisition fetcher registered for source type '{source_type.value}'."
            )
        return self._fetchers[source_type]

    def acquire_source(
        self,
        manifest_id: str,
        source: SourceSpec,
        transaction_id: str,
        dry_run: bool = False,
    ) -> AcquiredSourceResult:
        """Acquire and stage add-on source into isolated transaction staging.

        Args:
            manifest_id: Unique add-on identifier.
            source: Source specification metadata.
            transaction_id: Active installation transaction ID.
            dry_run: If True, preview acquisition without disk/network mutations.

        Returns:
            AcquiredSourceResult: Result containing staged path and metadata.
        """
        # Check source installability rules
        installable, reason = source.check_installable()
        if not installable:
            raise SourceAcquisitionError(
                f"Source acquisition pre-check failed for '{manifest_id}': {reason}"
            )

        fetcher = self.get_fetcher(source.source_type)

        if dry_run:
            # Under dry-run, get preview staging path without creating directory on disk
            staging_path = self.staging_manager.get_staging_dir(transaction_id, create=False)
            return fetcher.fetch(manifest_id, source, staging_path, dry_run=True)

        staging_path = self.staging_manager.get_staging_dir(transaction_id, create=True)

        try:
            return fetcher.fetch(manifest_id, source, staging_path, dry_run=False)
        except Exception:
            # Clean up staging directory on acquisition failure
            self.staging_manager.cleanup_staging(transaction_id)
            raise

    def cleanup_staging(self, transaction_id: str) -> None:
        """Clean up staging directory for a completed or rolled-back transaction."""
        self.staging_manager.cleanup_staging(transaction_id)
