"""Workspace lockfile manager for recording reproducible add-on installations."""

import os
import tempfile
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from aiaddons.core.exceptions import InstallationError
from aiaddons.core.models.manifest import IntegrationType


def _atomic_write_file(dir_path: Path, filename: str, content: str) -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    dest_path = dir_path / filename
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=dir_path, encoding="utf-8", delete=False
        ) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(content)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, dest_path)
        return dest_path
    except Exception as err:
        if temp_path and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        raise InstallationError(f"Atomic write failed for '{dest_path}': {err}") from err



class LockfileAddonEntry(BaseModel):
    """Lockfile entry representing an installed workspace add-on."""

    model_config = ConfigDict(extra="forbid")

    addon_id: str
    name: str
    version: str
    integration_type: IntegrationType
    target_agent: str
    checksum: str | None = None
    installed_at: str | None = None


class WorkspaceLockfile(BaseModel):
    """Root structure of aiaddons.lock file."""

    model_config = ConfigDict(extra="forbid")

    version: str = "1.0"
    addons: dict[str, LockfileAddonEntry] = Field(default_factory=dict)


class LockfileManager:
    """Manager for managing workspace aiaddons.lock files atomically."""

    LOCKFILE_NAME: str = "aiaddons.lock"

    def get_lockfile_path(self, workspace_dir: Path) -> Path:
        """Get absolute path to aiaddons.lock in workspace."""
        return workspace_dir.expanduser().resolve() / self.LOCKFILE_NAME

    def _load_lockfile(self, workspace_dir: Path) -> WorkspaceLockfile:
        lock_path = self.get_lockfile_path(workspace_dir)
        if not lock_path.exists() or not lock_path.is_file():
            return WorkspaceLockfile()
        try:
            content = lock_path.read_text(encoding="utf-8").strip()
            if not content:
                return WorkspaceLockfile()
            data = yaml.safe_load(content)
            if not isinstance(data, dict):
                return WorkspaceLockfile()
            return WorkspaceLockfile.model_validate(data)
        except Exception as err:
            raise InstallationError(f"Failed to read lockfile '{lock_path}': {err}") from err

    def update_lockfile(self, workspace_dir: Path, entry: LockfileAddonEntry) -> Path:
        """Atomically update workspace lockfile with entry."""
        lock = self._load_lockfile(workspace_dir)
        key = f"{entry.target_agent.strip().lower()}:{entry.addon_id.strip().lower()}"
        lock.addons[key] = entry

        dumped_dict = lock.model_dump(mode="json")
        yaml_content = yaml.safe_dump(dumped_dict, sort_keys=False)
        resolved_ws = workspace_dir.expanduser().resolve()
        return _atomic_write_file(resolved_ws, self.LOCKFILE_NAME, yaml_content)

    def remove_from_lockfile(self, workspace_dir: Path, target_agent: str, addon_id: str) -> bool:
        """Atomically remove an add-on entry from workspace lockfile."""
        lock = self._load_lockfile(workspace_dir)
        key = f"{target_agent.strip().lower()}:{addon_id.strip().lower()}"
        if key in lock.addons:
            del lock.addons[key]
            dumped_dict = lock.model_dump(mode="json")
            yaml_content = yaml.safe_dump(dumped_dict, sort_keys=False)
            resolved_ws = workspace_dir.expanduser().resolve()
            _atomic_write_file(resolved_ws, self.LOCKFILE_NAME, yaml_content)
            return True
        return False



    def get_entries(self, workspace_dir: Path) -> list[LockfileAddonEntry]:
        """Get all add-on entries from workspace lockfile."""
        lock = self._load_lockfile(workspace_dir)
        return list(lock.addons.values())
