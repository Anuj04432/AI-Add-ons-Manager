"""Unit tests for state.json and aiaddons.lock file-level concurrency locking."""

import pytest
from pathlib import Path
from filelock import FileLock

from aiaddons.core.exceptions import StateLockTimeoutError
from aiaddons.core.models.agent import Scope
from aiaddons.core.models.manifest import IntegrationType
from aiaddons.state.locking import (
    DEFAULT_LOCK_TIMEOUT,
    get_lock_file_path,
    state_file_lock,
)
from aiaddons.state.lockfile import LockfileAddonEntry, LockfileManager
from aiaddons.state.store import InstalledAddonRecord, InstalledStateStore


def _sample_record(addon_id: str = "test-addon") -> InstalledAddonRecord:
    return InstalledAddonRecord(
        addon_id=addon_id,
        name="Test Addon",
        version="1.0.0",
        target_agent="claude-code",
        scope=Scope.WORKSPACE,
        integration_type=IntegrationType.MCP,
        installed_at="2026-08-24T12:00:00Z",
    )


def _sample_lock_entry(addon_id: str = "test-addon") -> LockfileAddonEntry:
    return LockfileAddonEntry(
        addon_id=addon_id,
        name="Test Addon",
        version="1.0.0",
        integration_type=IntegrationType.MCP,
        target_agent="claude-code",
        checksum="sha256:" + "a" * 64,
        installed_at="2026-08-24T12:00:00Z",
    )


def test_lock_file_path_generation(tmp_path: Path) -> None:
    """Verify lock file path helper produces companion .lock filename."""
    state_file = tmp_path / "state.json"
    lock_path = get_lock_file_path(state_file)
    assert lock_path == tmp_path / "state.json.lock"

    lockfile_path = tmp_path / "aiaddons.lock"
    lock_path2 = get_lock_file_path(lockfile_path)
    assert lock_path2 == tmp_path / "aiaddons.lock.lock"


def test_state_file_lock_acquired_and_released(tmp_path: Path) -> None:
    """Verify lock is cleanly acquired and released in normal context."""
    target_file = tmp_path / "state.json"
    lock_path = get_lock_file_path(target_file)

    assert not lock_path.exists()

    with state_file_lock(target_file, timeout=1.0) as lock:
        assert lock.is_locked
        assert lock_path.exists()

    # After exiting block, lock must not be held
    assert not lock.is_locked


def test_state_file_lock_released_on_exception(tmp_path: Path) -> None:
    """Verify lock is released even if exception is raised inside the context block."""
    target_file = tmp_path / "state.json"
    lock_ref: FileLock | None = None

    with pytest.raises(RuntimeError, match="Simulated failure"):
        with state_file_lock(target_file, timeout=1.0) as lock:
            lock_ref = lock
            assert lock.is_locked
            raise RuntimeError("Simulated failure")

    assert lock_ref is not None
    assert not lock_ref.is_locked


def test_state_file_lock_timeout_error(tmp_path: Path) -> None:
    """Verify attempting to acquire a lock held by another context times out with clear error."""
    target_file = tmp_path / "state.json"
    lock_path = get_lock_file_path(target_file)

    # Hold the lock with a non-singleton FileLock to simulate external process
    holding_lock = FileLock(str(lock_path), is_singleton=False)
    holding_lock.acquire()

    try:
        with pytest.raises(StateLockTimeoutError) as exc_info:
            with state_file_lock(target_file, timeout=0.1):
                pass

        err_msg = str(exc_info.value)
        assert "Timed out after 0.1 seconds" in err_msg
        assert "Another aiaddons process appears to be running" in err_msg
        assert str(lock_path) in err_msg
        assert exc_info.value.timeout == 0.1
        assert exc_info.value.lock_path == lock_path
    finally:
        holding_lock.release()


def test_installed_state_store_locking_success(tmp_path: Path) -> None:
    """Verify InstalledStateStore records and removes entries under lock protection."""
    store = InstalledStateStore(store_dir=tmp_path)
    record = _sample_record("addon-1")

    # Record installation
    store.record_installation(record)
    installed = store.get_installed()
    assert len(installed) == 1
    assert installed[0].addon_id == "addon-1"

    # Companion lock file path is properly configured
    assert store.get_lock_path() == tmp_path / "state.json.lock"

    # Remove installation
    removed = store.remove_installation(record.target_agent, record.scope, record.addon_id)
    assert removed is True
    assert len(store.get_installed()) == 0


def test_installed_state_store_timeout_when_locked(tmp_path: Path) -> None:
    """Verify InstalledStateStore write operations fail with StateLockTimeoutError when lock is held."""
    store = InstalledStateStore(store_dir=tmp_path, lock_timeout=0.1)
    lock_path = store.get_lock_path()

    holding_lock = FileLock(str(lock_path), is_singleton=False)
    holding_lock.acquire()

    try:
        record = _sample_record("addon-blocked")
        with pytest.raises(StateLockTimeoutError) as exc_info:
            store.record_installation(record)
        assert "Another aiaddons process appears to be running" in str(exc_info.value)

        with pytest.raises(StateLockTimeoutError):
            store.remove_installation("claude-code", Scope.WORKSPACE, "addon-blocked")
    finally:
        holding_lock.release()


def test_lockfile_manager_locking_success(tmp_path: Path) -> None:
    """Verify LockfileManager updates and removes entries under lock protection."""
    mgr = LockfileManager()
    entry = _sample_lock_entry("addon-lock-1")

    # Update lockfile
    mgr.update_lockfile(tmp_path, entry)
    entries = mgr.get_entries(tmp_path)
    assert len(entries) == 1
    assert entries[0].addon_id == "addon-lock-1"

    # Companion lock file path is properly configured
    assert mgr.get_lock_path(tmp_path) == tmp_path / "aiaddons.lock.lock"

    # Remove from lockfile
    removed = mgr.remove_from_lockfile(tmp_path, entry.target_agent, entry.addon_id)
    assert removed is True
    assert len(mgr.get_entries(tmp_path)) == 0


def test_lockfile_manager_timeout_when_locked(tmp_path: Path) -> None:
    """Verify LockfileManager operations fail with StateLockTimeoutError when lock is held."""
    mgr = LockfileManager(lock_timeout=0.1)
    lock_path = mgr.get_lock_path(tmp_path)

    holding_lock = FileLock(str(lock_path), is_singleton=False)
    holding_lock.acquire()

    try:
        entry = _sample_lock_entry("addon-blocked")
        with pytest.raises(StateLockTimeoutError) as exc_info:
            mgr.update_lockfile(tmp_path, entry)
        assert "Another aiaddons process appears to be running" in str(exc_info.value)

        with pytest.raises(StateLockTimeoutError):
            mgr.remove_from_lockfile(tmp_path, "claude-code", "addon-blocked")
    finally:
        holding_lock.release()


def test_store_and_lockfile_reentrant_lock_context(tmp_path: Path) -> None:
    """Verify callers can wrap multi-step transactions in store.lock() or mgr.lock()."""
    store = InstalledStateStore(store_dir=tmp_path)
    mgr = LockfileManager()

    with store.lock():
        store.record_installation(_sample_record("addon-a"))
        store.record_installation(_sample_record("addon-b"))

    assert len(store.get_installed()) == 2

    with mgr.lock(tmp_path):
        mgr.update_lockfile(tmp_path, _sample_lock_entry("addon-a"))
        mgr.update_lockfile(tmp_path, _sample_lock_entry("addon-b"))

    assert len(mgr.get_entries(tmp_path)) == 2
