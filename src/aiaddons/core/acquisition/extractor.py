"""Security-critical archive extraction engine protecting against ZipSlip and zip bombs."""

import tarfile
import zipfile
from pathlib import Path

from aiaddons.core.acquisition.models import ArchiveCheckResult
from aiaddons.core.exceptions import ArchiveSecurityError
from aiaddons.core.execution.security import verify_safe_target_path
from aiaddons.core.models.manifest import validate_safe_relative_path

DEFAULT_MAX_FILE_COUNT: int = 10_000
DEFAULT_MAX_TOTAL_SIZE: int = 100 * 1024 * 1024  # 100 MB
DEFAULT_MAX_RATIO: float = 100.0


class SafeArchiveExtractor:
    """Secure extractor for zip and tar archives enforcing pre-extraction validation."""

    def __init__(
        self,
        max_file_count: int = DEFAULT_MAX_FILE_COUNT,
        max_total_size: int = DEFAULT_MAX_TOTAL_SIZE,
        max_ratio: float = DEFAULT_MAX_RATIO,
    ) -> None:
        self.max_file_count = max_file_count
        self.max_total_size = max_total_size
        self.max_ratio = max_ratio

    def extract_zip(self, archive_path: Path, staging_root: Path) -> list[str]:
        """Validate and extract a ZIP archive into staging_root."""
        if not archive_path.exists() or not archive_path.is_file():
            raise ArchiveSecurityError(f"Archive file '{archive_path}' does not exist.")

        try:
            with zipfile.ZipFile(archive_path, "r") as zf:
                infolist = zf.infolist()

                # 1. Inspect resource limits (Zip Bomb defense)
                self._check_zip_resource_limits(infolist, archive_path)

                # 2. Pre-validate all entry paths before extracting any file
                extracted_files: list[str] = []
                for member in infolist:
                    self._validate_zip_member(member, staging_root)

                # 3. Perform actual extraction safely
                for member in infolist:
                    # Skip directory entries for file writing, but create directories
                    clean_name = member.filename.replace("\\", "/")
                    if clean_name.endswith("/"):
                        _, dest_dir = verify_safe_target_path(staging_root, clean_name)
                        dest_dir.mkdir(parents=True, exist_ok=True)
                        continue

                    _, dest_file = verify_safe_target_path(staging_root, clean_name)
                    dest_file.parent.mkdir(parents=True, exist_ok=True)

                    # Extract file content directly to dest_file
                    with zf.open(member, "r") as src, open(dest_file, "wb") as dst:
                        while chunk := src.read(65536):
                            dst.write(chunk)
                    extracted_files.append(clean_name)

                return extracted_files
        except ArchiveSecurityError:
            raise
        except Exception as err:
            raise ArchiveSecurityError(
                f"Failed to extract ZIP archive '{archive_path}': {err}"
            ) from err

    def extract_tar(self, archive_path: Path, staging_root: Path) -> list[str]:
        """Validate and extract a TAR (.tar, .tar.gz, .tgz, .tar.xz) archive into staging_root."""
        if not archive_path.exists() or not archive_path.is_file():
            raise ArchiveSecurityError(f"Archive file '{archive_path}' does not exist.")

        try:
            with tarfile.open(name=archive_path, mode="r:*") as tf:
                members = tf.getmembers()

                # 1. Inspect resource limits
                self._check_tar_resource_limits(members, archive_path)

                # 2. Pre-validate all members
                extracted_files: list[str] = []
                for member in members:
                    self._validate_tar_member(member, staging_root)

                # 3. Perform safe extraction
                for member in members:
                    clean_name = member.name.replace("\\", "/")
                    if member.isdir():
                        _, dest_dir = verify_safe_target_path(staging_root, clean_name)
                        dest_dir.mkdir(parents=True, exist_ok=True)
                        continue

                    if member.isfile():
                        _, dest_file = verify_safe_target_path(staging_root, clean_name)
                        dest_file.parent.mkdir(parents=True, exist_ok=True)
                        src = tf.extractfile(member)
                        if src is not None:
                            with src, open(dest_file, "wb") as dst:
                                while chunk := src.read(65536):
                                    dst.write(chunk)
                            extracted_files.append(clean_name)

                return extracted_files
        except ArchiveSecurityError:
            raise
        except Exception as err:
            raise ArchiveSecurityError(
                f"Failed to extract TAR archive '{archive_path}': {err}"
            ) from err

    def extract(self, archive_path: Path, staging_root: Path) -> list[str]:
        """Identify archive type safely and extract into staging_root."""
        filename = archive_path.name.lower()
        if filename.endswith(".zip"):
            return self.extract_zip(archive_path, staging_root)
        elif (
            filename.endswith(".tar")
            or filename.endswith(".tar.gz")
            or filename.endswith(".tgz")
            or filename.endswith(".tar.xz")
        ):
            return self.extract_tar(archive_path, staging_root)

        # Fail closed on unknown extension
        raise ArchiveSecurityError(
            f"Unsupported or unsafe archive format for file '{archive_path.name}'. "
            "Only .zip, .tar, .tar.gz, .tgz, and .tar.xz are supported."
        )

    def _check_zip_resource_limits(
        self, infolist: list[zipfile.ZipInfo], archive_path: Path
    ) -> ArchiveCheckResult:
        """Check total file count, uncompressed size, and compression ratio for ZIP."""
        total_files = len(infolist)
        if total_files > self.max_file_count:
            raise ArchiveSecurityError(
                f"Security violation: Archive '{archive_path.name}' contains {total_files} files, "
                f"exceeding maximum limit of {self.max_file_count}."
            )

        total_uncompressed = 0
        total_compressed = 0
        compressed_archive_size = archive_path.stat().st_size

        for info in infolist:
            total_uncompressed += info.file_size
            total_compressed += info.compress_size

        if total_uncompressed > self.max_total_size:
            raise ArchiveSecurityError(
                f"Security violation: Archive '{archive_path.name}' total uncompressed size "
                f"({total_uncompressed} bytes) exceeds limit of {self.max_total_size} bytes."
            )

        denom = max(total_compressed, compressed_archive_size, 1)
        ratio = total_uncompressed / denom
        if ratio > self.max_ratio:
            raise ArchiveSecurityError(
                f"Security violation: Archive '{archive_path.name}' compression ratio "
                f"({ratio:.1f}:1) exceeds maximum ratio of {self.max_ratio}:1."
            )

        return ArchiveCheckResult(
            total_files=total_files,
            total_uncompressed_size=total_uncompressed,
            max_compression_ratio=ratio,
        )

    def _check_tar_resource_limits(
        self, members: list[tarfile.TarInfo], archive_path: Path
    ) -> ArchiveCheckResult:
        """Check total file count, uncompressed size, and compression ratio for TAR."""
        total_files = len(members)
        if total_files > self.max_file_count:
            raise ArchiveSecurityError(
                f"Security violation: Archive '{archive_path.name}' contains {total_files} files, "
                f"exceeding maximum limit of {self.max_file_count}."
            )

        total_uncompressed = 0
        archive_size = max(archive_path.stat().st_size, 1)

        for member in members:
            total_uncompressed += member.size

        if total_uncompressed > self.max_total_size:
            raise ArchiveSecurityError(
                f"Security violation: Archive '{archive_path.name}' total uncompressed size "
                f"({total_uncompressed} bytes) exceeds limit of {self.max_total_size} bytes."
            )

        ratio = total_uncompressed / archive_size
        if ratio > self.max_ratio:
            raise ArchiveSecurityError(
                f"Security violation: Archive '{archive_path.name}' compression ratio "
                f"({ratio:.1f}:1) exceeds maximum ratio of {self.max_ratio}:1."
            )

        return ArchiveCheckResult(
            total_files=total_files,
            total_uncompressed_size=total_uncompressed,
            max_compression_ratio=ratio,
        )

    def _validate_zip_member(self, member: zipfile.ZipInfo, staging_root: Path) -> None:
        """Validate a single ZIP entry for path traversal, symlinks, and device files."""
        name = member.filename.replace("\\", "/")

        # 1. Path traversal & metacharacter check
        try:
            validate_safe_relative_path(name)
        except ValueError as err:
            msg = f"ZipSlip traversal violation in entry '{name}': {err}"
            raise ArchiveSecurityError(msg) from err

        # 2. Boundary containment check using verify_safe_target_path
        try:
            _, resolved_dest = verify_safe_target_path(staging_root, name)
        except Exception as err:
            raise ArchiveSecurityError(f"ZipSlip entry escape for '{name}': {err}") from err

        # 3. Check for symlinks in ZIP external_attr (unix mode)
        # In ZIP files, symlinks have external_attr high bits 0120000 (S_IFLNK)
        unix_mode = member.external_attr >> 16
        if (unix_mode & 0o170000) == 0o120000:
            raise ArchiveSecurityError(
                f"Security violation: Entry '{name}' is a symbolic link, which is prohibited."
            )

    def _validate_tar_member(self, member: tarfile.TarInfo, staging_root: Path) -> None:
        """Validate TAR entry for TarSlip, symlinks, device nodes, setuid/setgid."""
        name = member.name.replace("\\", "/")

        # 1. Path traversal check
        try:
            validate_safe_relative_path(name)
        except ValueError as err:
            raise ArchiveSecurityError(
                f"TarSlip traversal violation in entry '{name}': {err}"
            ) from err

        # 2. Boundary containment check
        try:
            _, resolved_dest = verify_safe_target_path(staging_root, name)
        except Exception as err:
            raise ArchiveSecurityError(f"TarSlip entry escape for '{name}': {err}") from err

        # 3. Reject symlinks, hardlinks, FIFOs, sockets, character/block devices
        if member.issym() or member.islnk():
            raise ArchiveSecurityError(
                f"Security violation: Entry '{name}' is a symlink/hardlink, which is prohibited."
            )

        if member.ischr() or member.isblk() or member.isfifo():
            raise ArchiveSecurityError(
                f"Security violation: Entry '{name}' is a special device node/FIFO file."
            )

        # 4. Reject setuid / setgid bits
        if member.mode & (0o4000 | 0o2000):
            raise ArchiveSecurityError(
                f"Security violation: Entry '{name}' has setuid or setgid permissions."
            )
