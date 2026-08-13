"""Local filesystem source acquisition fetcher with strict boundary enforcement."""

import shutil
from pathlib import Path

from aiaddons.core.acquisition.base import BaseSourceFetcher
from aiaddons.core.acquisition.models import AcquiredSourceResult
from aiaddons.core.exceptions import SecurityValidationError, SourceAcquisitionError
from aiaddons.core.models.manifest import SourceSpec, SourceType, validate_safe_relative_path


class LocalSourceFetcher(BaseSourceFetcher):
    """Fetcher for local filesystem sources enforcing path boundaries and symlink safety."""

    def supported_source_type(self) -> SourceType:
        return SourceType.LOCAL

    def fetch(
        self,
        manifest_id: str,
        source: SourceSpec,
        staging_dir: Path,
        dry_run: bool = False,
    ) -> AcquiredSourceResult:
        if source.source_type != SourceType.LOCAL:
            raise SourceAcquisitionError(
                f"Expected source type 'local', got '{source.source_type.value}'."
            )

        rel_path = source.path
        if not rel_path or not rel_path.strip():
            raise SourceAcquisitionError(
                f"Local source for '{manifest_id}' requires a path specification."
            )

        # 1. Validate safe relative path
        try:
            clean_rel = validate_safe_relative_path(rel_path)
            if not clean_rel:
                raise SourceAcquisitionError("Local path specification cannot be empty.")
        except ValueError as err:
            raise SourceAcquisitionError(
                f"Security violation in local path '{rel_path}': {err}"
            ) from err

        cand_direct = Path(clean_rel).expanduser()
        cand_cwd = Path.cwd() / clean_rel

        if cand_direct.exists():
            local_source_path = cand_direct
        elif cand_cwd.exists():
            local_source_path = cand_cwd
        else:
            raise SourceAcquisitionError(f"Local source path '{clean_rel}' does not exist.")

        if clean_rel in (".", ""):
            return AcquiredSourceResult(
                manifest_id=manifest_id,
                source_type=SourceType.LOCAL,
                staging_path=local_source_path,
                is_staged=True,
                is_dry_run=False,
                acquired_files=[],
            )

        if dry_run:
            return AcquiredSourceResult(
                manifest_id=manifest_id,
                source_type=SourceType.LOCAL,
                staging_path=staging_dir,
                is_staged=False,
                is_dry_run=True,
                acquired_files=[f"[DRY-RUN] copy local path '{clean_rel}' to staging"],
            )

        # 2. Real copy into staging_dir
        staging_dir.mkdir(parents=True, exist_ok=True)
        acquired_files: list[str] = []

        try:
            if local_source_path.is_file():
                # Verify symlink safety on file
                if local_source_path.is_symlink():
                    sym_target = local_source_path.resolve()
                    if not sym_target.exists():
                        raise SourceAcquisitionError(
                            f"Security violation: Broken symlink in local source '{clean_rel}'."
                        )

                dest_file = staging_dir / local_source_path.name
                shutil.copy2(local_source_path, dest_file)
                acquired_files.append(local_source_path.name)
            elif local_source_path.is_dir():
                # Inspect each file inside directory for symlink escape
                for src_file in local_source_path.rglob("*"):
                    rel_to_src = src_file.relative_to(local_source_path)
                    clean_entry_name = str(rel_to_src).replace("\\", "/")

                    if src_file.is_symlink():
                        try:
                            resolved_sym = src_file.resolve()
                            if not resolved_sym.is_relative_to(local_source_path.resolve()):
                                raise SecurityValidationError(
                                    f"Symlink '{clean_entry_name}' escapes local source root."
                                )
                        except ValueError as err:
                            raise SecurityValidationError(
                                f"Symlink '{clean_entry_name}' escapes local source root."
                            ) from err

                    dest_entry = staging_dir / rel_to_src
                    if src_file.is_dir():
                        dest_entry.mkdir(parents=True, exist_ok=True)
                    elif src_file.is_file():
                        dest_entry.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src_file, dest_entry)
                        acquired_files.append(clean_entry_name)
            else:
                raise SourceAcquisitionError(
                    f"Local source path '{clean_rel}' is neither a file nor a directory."
                )

            return AcquiredSourceResult(
                manifest_id=manifest_id,
                source_type=SourceType.LOCAL,
                staging_path=staging_dir,
                is_staged=True,
                is_dry_run=False,
                acquired_files=acquired_files,
            )
        except (SourceAcquisitionError, SecurityValidationError):
            raise
        except Exception as err:
            raise SourceAcquisitionError(
                f"Failed to stage local source '{clean_rel}': {err}"
            ) from err
