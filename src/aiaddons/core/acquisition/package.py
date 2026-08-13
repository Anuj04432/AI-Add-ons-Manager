"""Package source acquisition fetcher for package specs and archives."""

from pathlib import Path

from aiaddons.core.acquisition.base import BaseSourceFetcher
from aiaddons.core.acquisition.models import AcquiredSourceResult
from aiaddons.core.acquisition.url import UrlSourceFetcher
from aiaddons.core.exceptions import SourceAcquisitionError
from aiaddons.core.models.manifest import (
    SourceSpec,
    SourceType,
    validate_mcp_package_name,
)


class PackageSourceFetcher(BaseSourceFetcher):
    """Fetcher for package sources supporting verified tarballs and package specs."""

    def __init__(self, url_fetcher: UrlSourceFetcher | None = None) -> None:
        self.url_fetcher = url_fetcher or UrlSourceFetcher()

    def supported_source_type(self) -> SourceType:
        return SourceType.PACKAGE

    def fetch(
        self,
        manifest_id: str,
        source: SourceSpec,
        staging_dir: Path,
        dry_run: bool = False,
    ) -> AcquiredSourceResult:
        if source.source_type != SourceType.PACKAGE:
            raise SourceAcquisitionError(
                f"Expected source type 'package', got '{source.source_type.value}'."
            )

        # 1. If remote URL archive is provided, use UrlSourceFetcher with SHA-256 verification
        if source.url and source.url.strip():
            url_source = SourceSpec(
                source_type=SourceType.URL,
                url=source.url,
                checksum=source.checksum,
            )
            return self.url_fetcher.fetch(manifest_id, url_source, staging_dir, dry_run=dry_run)

        # 2. Validate package_name if specified
        pkg_name = source.package_name
        if not pkg_name or not pkg_name.strip():
            raise SourceAcquisitionError(
                f"Package source for '{manifest_id}' requires either a package_name or a url."
            )

        try:
            clean_pkg = validate_mcp_package_name(pkg_name)
        except ValueError as err:
            raise SourceAcquisitionError(
                f"Security violation in package name '{pkg_name}' for '{manifest_id}': {err}"
            ) from err

        # Verify checksum requirement if provided
        if source.checksum and not source.checksum.startswith("sha256:"):
            raise SourceAcquisitionError(
                f"Security violation: Package checksum for '{manifest_id}' must start with sha256:"
            )

        if dry_run:
            return AcquiredSourceResult(
                manifest_id=manifest_id,
                source_type=SourceType.PACKAGE,
                staging_path=staging_dir,
                checksum=source.checksum,
                is_staged=False,
                is_dry_run=True,
                acquired_files=[f"[DRY-RUN] package spec '{clean_pkg}' verified"],
            )

        # For runtime-managed package specs (e.g. npx @modelcontextprotocol/server-github),
        # create a verified staging metadata descriptor file inside staging_dir
        staging_dir.mkdir(parents=True, exist_ok=True)
        spec_file = staging_dir / "package_spec.json"
        spec_content = (
            f'{{\n  "package_name": "{clean_pkg}",\n  "checksum": "{source.checksum or ""}"\n}}\n'
        )
        spec_file.write_text(spec_content, encoding="utf-8")

        return AcquiredSourceResult(
            manifest_id=manifest_id,
            source_type=SourceType.PACKAGE,
            staging_path=staging_dir,
            checksum=source.checksum,
            is_staged=True,
            is_dry_run=False,
            acquired_files=["package_spec.json"],
        )
