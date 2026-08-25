"""Installed state database store for tracking active add-on installations."""

import os
import tempfile
from contextlib import AbstractContextManager
from pathlib import Path

from filelock import BaseFileLock
from pydantic import BaseModel, ConfigDict, Field

from aiaddons.core.exceptions import InstallationError
from aiaddons.core.models.agent import Scope
from aiaddons.core.models.manifest import IntegrationType
from aiaddons.state.locking import (
    DEFAULT_LOCK_TIMEOUT,
    get_lock_file_path,
    state_file_lock,
)


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


class InstalledAddonRecord(BaseModel):
    """Metadata record representing an installed add-on."""

    model_config = ConfigDict(extra="forbid")

    addon_id: str
    name: str
    version: str
    target_agent: str
    scope: Scope
    integration_type: IntegrationType
    installed_at: str
    installed_files: list[str] = Field(default_factory=list)


class InstalledStateDatabase(BaseModel):
    """Root model for installed state JSON database."""

    model_config = ConfigDict(extra="forbid")

    version: str = "1.0"
    records: dict[str, InstalledAddonRecord] = Field(default_factory=dict)


class InstalledStateStore:
    """Store for managing installed add-on records in ~/.aiaddons/state.json with file locking."""

    def __init__(
        self,
        store_dir: Path | None = None,
        lock_timeout: float = DEFAULT_LOCK_TIMEOUT,
    ) -> None:
        if store_dir is None:
            self.store_dir = Path.home() / ".aiaddons"
        else:
            self.store_dir = store_dir.expanduser()
        self.state_file_name = "state.json"
        self.lock_timeout = lock_timeout

    def get_state_file_path(self) -> Path:
        """Get absolute path to state.json."""
        return self.store_dir / self.state_file_name

    def get_lock_path(self) -> Path:
        """Get absolute path to companion state.json.lock file."""
        return get_lock_file_path(self.get_state_file_path())

    def lock(self, timeout: float | None = None) -> AbstractContextManager[BaseFileLock]:
        """Acquire exclusive file lock for state.json."""
        eff_timeout = self.lock_timeout if timeout is None else timeout
        return state_file_lock(self.get_state_file_path(), timeout=eff_timeout)

    def _get_key(self, target_agent: str, scope: Scope, addon_id: str) -> str:
        return f"{target_agent.strip().lower()}:{scope.value}:{addon_id.strip().lower()}"

    def _load_db(self) -> InstalledStateDatabase:
        state_file = self.get_state_file_path()
        if not state_file.exists() or not state_file.is_file():
            return InstalledStateDatabase()
        try:
            content = state_file.read_text(encoding="utf-8").strip()
            if not content:
                return InstalledStateDatabase()
            return InstalledStateDatabase.model_validate_json(content)
        except Exception as err:
            raise InstallationError(f"Failed to load state database '{state_file}': {err}") from err

    def record_installation(self, record: InstalledAddonRecord) -> None:
        """Atomically record or update an installed add-on entry with exclusive locking."""
        with self.lock():
            db = self._load_db()
            key = self._get_key(record.target_agent, record.scope, record.addon_id)
            db.records[key] = record

            content = db.model_dump_json(indent=2) + "\n"
            _atomic_write_file(self.store_dir, self.state_file_name, content)

    def remove_installation(self, target_agent: str, scope: Scope, addon_id: str) -> bool:
        """Atomically remove an installed add-on entry with exclusive locking."""
        with self.lock():
            db = self._load_db()
            key = self._get_key(target_agent, scope, addon_id)
            if key in db.records:
                del db.records[key]
                content = db.model_dump_json(indent=2) + "\n"
                _atomic_write_file(self.store_dir, self.state_file_name, content)
                return True
            return False

    def get_installed(
        self,
        target_agent: str | None = None,
        scope: Scope | None = None,
    ) -> list[InstalledAddonRecord]:
        """List installed add-on records, optionally filtered by agent or scope."""
        db = self._load_db()
        records: list[InstalledAddonRecord] = []
        for record in db.records.values():
            if target_agent and record.target_agent.lower() != target_agent.lower():
                continue
            if scope and record.scope != scope:
                continue
            records.append(record)
        return records

    def get_record(
        self,
        target_agent: str,
        scope: Scope,
        addon_id: str,
    ) -> InstalledAddonRecord | None:
        """Retrieve record for a specific installed add-on."""
        db = self._load_db()
        key = self._get_key(target_agent, scope, addon_id)
        return db.records.get(key)
