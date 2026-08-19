"""Staging manager for creating, scoping, and cleaning isolated transaction staging directories."""

import os
import shutil
import stat
from pathlib import Path
from typing import Any

from aiaddons.core.exceptions import SecurityValidationError
from aiaddons.core.execution.security import verify_safe_target_path


class SourceStagingManager:
    """Manager for isolated staging directories at ~/.aiaddons/staging/<transaction_id>/."""

    def __init__(self, base_staging_dir: Path | None = None) -> None:
        if base_staging_dir is None:
            self.base_staging_dir = Path.home() / ".aiaddons" / "staging"
        else:
            self.base_staging_dir = base_staging_dir.expanduser()

    def get_staging_dir(self, transaction_id: str, create: bool = True) -> Path:
        """Get and optionally create an isolated staging directory for a transaction ID.

        Enforces restrictive permissions (0700) where supported by host OS.
        """
        safe_id = transaction_id.strip()
        if not safe_id or "/" in safe_id or "\\" in safe_id or ".." in safe_id or "\0" in safe_id:
            raise SecurityValidationError(f"Invalid transaction ID '{transaction_id}' for staging.")

        # Ensure base staging dir exists securely
        self.base_staging_dir.mkdir(parents=True, exist_ok=True)

        _, staging_path = verify_safe_target_path(self.base_staging_dir, safe_id)

        if create:
            staging_path.mkdir(parents=True, exist_ok=True)

            # Apply restrictive permissions (0700) on POSIX platforms
            if os.name != "nt":
                try:
                    os.chmod(staging_path, stat.S_IRWXU)
                except OSError:
                    pass

        return staging_path

    def cleanup_staging(self, transaction_id: str) -> None:
        """Safely remove a transaction's staging directory and all contained files.

        Handles Windows file locking robustly.
        """
        safe_id = transaction_id.strip()
        if not safe_id or "/" in safe_id or "\\" in safe_id or ".." in safe_id or "\0" in safe_id:
            return

        staging_path = self.base_staging_dir / safe_id
        if not staging_path.exists():
            return

        # Enforce containment check before deletion
        try:
            _, resolved_staging = verify_safe_target_path(self.base_staging_dir, safe_id)
        except Exception:
            return

        if not resolved_staging.exists():
            return

        # Robust directory cleanup with Windows read-only flag removal handler
        for p in resolved_staging.rglob("*"):
            try:
                os.chmod(p, stat.S_IWRITE)
            except Exception:
                pass
        try:
            os.chmod(resolved_staging, stat.S_IWRITE)
        except Exception:
            pass

        def _handle_remove_readonly(func: Any, path: str, exc_info: Any) -> None:
            try:
                os.chmod(path, stat.S_IWRITE)
                func(path)
            except Exception:
                pass

        try:
            shutil.rmtree(resolved_staging, onerror=_handle_remove_readonly)
        except Exception:
            pass

        if resolved_staging.exists():
            try:
                shutil.rmtree(resolved_staging, ignore_errors=True)
            except Exception:
                pass
