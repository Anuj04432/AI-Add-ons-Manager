"""HTTPS streaming URL source acquisition fetcher with streaming SHA-256 checksum verification."""

import hashlib
import tempfile
from pathlib import Path

import httpx

from aiaddons.core.acquisition.base import BaseSourceFetcher
from aiaddons.core.acquisition.extractor import SafeArchiveExtractor
from aiaddons.core.acquisition.models import AcquiredSourceResult
from aiaddons.core.exceptions import ChecksumMismatchError, SourceAcquisitionError
from aiaddons.core.models.manifest import SourceSpec, SourceType

ALLOWED_HTTP_HOSTS = {"localhost", "127.0.0.1"}
DEFAULT_MAX_DOWNLOAD_SIZE = 100 * 1024 * 1024  # 100 MB


class UrlSourceFetcher(BaseSourceFetcher):
    """HTTPS streaming archive fetcher with SHA-256 verification and ZipSlip/TarSlip protection."""

    def __init__(
        self,
        extractor: SafeArchiveExtractor | None = None,
        timeout: float = 30.0,
        max_download_size: int = DEFAULT_MAX_DOWNLOAD_SIZE,
        httpx_client: httpx.Client | None = None,
    ) -> None:
        self.extractor = extractor or SafeArchiveExtractor()
        self.timeout = timeout
        self.max_download_size = max_download_size
        self._httpx_client = httpx_client

    def supported_source_type(self) -> SourceType:
        return SourceType.URL

    def _validate_url_scheme(self, url: str) -> None:
        """Enforce HTTPS for remote URLs, allowing http strictly for localhost/127.0.0.1."""
        lowered = url.lower()
        if lowered.startswith("http://"):
            stripped = lowered[7:]
            host = stripped.split("/")[0].split(":")[0]
            if host not in ALLOWED_HTTP_HOSTS:
                raise SourceAcquisitionError(
                    f"Security violation: Insecure HTTP URL '{url}' is prohibited. "
                    "Remote sources must use HTTPS."
                )
        elif not lowered.startswith("https://"):
            raise SourceAcquisitionError(
                f"Invalid URL scheme in '{url}'. Only HTTPS endpoints are supported."
            )

    def fetch(
        self,
        manifest_id: str,
        source: SourceSpec,
        staging_dir: Path,
        dry_run: bool = False,
    ) -> AcquiredSourceResult:
        if source.source_type != SourceType.URL:
            raise SourceAcquisitionError(
                f"Expected source type 'url', got '{source.source_type.value}'."
            )

        url = source.url
        if not url or not url.strip():
            raise SourceAcquisitionError(f"URL source for '{manifest_id}' requires a valid URL.")

        self._validate_url_scheme(url)

        # Require expected SHA-256 checksum
        if not source.checksum or not source.checksum.startswith("sha256:"):
            raise SourceAcquisitionError(
                f"Security violation: URL source for '{manifest_id}' requires a SHA-256 checksum."
            )
        expected_hash = source.checksum[7:].strip().lower()

        if dry_run:
            return AcquiredSourceResult(
                manifest_id=manifest_id,
                source_type=SourceType.URL,
                staging_path=staging_dir,
                checksum=source.checksum,
                is_staged=False,
                is_dry_run=True,
                acquired_files=[f"[DRY-RUN] GET {url} (SHA-256 verified)"],
            )

        # Real streaming download and checksum calculation
        temp_archive_path: Path | None = None
        hasher = hashlib.sha256()
        total_downloaded = 0

        try:
            filename = url.split("?")[0].rstrip("/").split("/")[-1]
            if not filename or "." not in filename:
                filename = f"{manifest_id}.zip"

            # Download into a temporary file inside staging_dir
            staging_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=staging_dir, suffix=f"_{filename}", delete=False
            ) as temp_file:
                temp_archive_path = Path(temp_file.name)

                if self._httpx_client is not None:
                    response = self._httpx_client.get(url, timeout=self.timeout)
                    if response.status_code != 200:
                        raise SourceAcquisitionError(
                            f"HTTP download failed for '{url}': Status {response.status_code}"
                        )
                    for chunk in response.iter_bytes(chunk_size=65536):
                        total_downloaded += len(chunk)
                        if total_downloaded > self.max_download_size:
                            raise SourceAcquisitionError(
                                f"Security violation: Download size for '{url}' exceeded limit."
                            )
                        hasher.update(chunk)
                        temp_file.write(chunk)
                else:
                    with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                        with client.stream("GET", url) as response:
                            if response.status_code != 200:
                                status_code = response.status_code
                                raise SourceAcquisitionError(
                                    f"HTTP download failed: Status {status_code}"
                                )
                            for chunk in response.iter_raw(chunk_size=65536):
                                total_downloaded += len(chunk)
                                if total_downloaded > self.max_download_size:
                                    raise SourceAcquisitionError("Download size limit exceeded.")
                                hasher.update(chunk)
                                temp_file.write(chunk)

            # Validate checksum
            actual_hash = hasher.hexdigest().lower()
            if actual_hash != expected_hash:
                raise ChecksumMismatchError(
                    f"Checksum mismatch for '{manifest_id}': expected sha256:{expected_hash}, "
                    f"got sha256:{actual_hash}."
                )

            # Safely extract archive into staging_dir
            acquired_files = self.extractor.extract(temp_archive_path, staging_dir)

            return AcquiredSourceResult(
                manifest_id=manifest_id,
                source_type=SourceType.URL,
                staging_path=staging_dir,
                checksum=f"sha256:{actual_hash}",
                is_staged=True,
                is_dry_run=False,
                acquired_files=acquired_files,
            )
        except Exception:
            raise
        finally:
            if temp_archive_path and temp_archive_path.exists():
                try:
                    temp_archive_path.unlink()
                except OSError:
                    pass
