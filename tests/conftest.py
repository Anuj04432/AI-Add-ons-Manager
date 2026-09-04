"""Pytest configuration and shared fixtures for aiaddons test suite.

Provides automated test isolation across all filesystem and state stores to
guarantee tests never interact with or pollute the host user's ~/.aiaddons/
directory or the repository root aiaddons.lock.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Generator

import pytest

pytest_plugins = ["tests.fixtures.ponytail_manifests"]

import aiaddons.core.installer.engine  # noqa: F401
import aiaddons.state.transaction  # noqa: F401

# Real host paths captured once at import time before any mocking
REAL_USER_HOME = Path.home().resolve()
REAL_AIADDONS_DIR = (REAL_USER_HOME / ".aiaddons").resolve()
REAL_REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_REPO_LOCKFILE = (REAL_REPO_ROOT / "aiaddons.lock").resolve()


def _is_subpath(target: Path, parent: Path) -> bool:
    """Check if target path is equal to or located within parent directory."""
    try:
        target.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, Exception):
        return False


def _verify_safe_write_path(dest_path: Path) -> None:
    """Raise AssertionError loudly if dest_path targets real user state or repo lockfile."""
    resolved = dest_path.resolve()
    if _is_subpath(resolved, REAL_AIADDONS_DIR):
        raise AssertionError(
            f"CRITICAL TEST ISOLATION FAILURE: Test attempted to write to real user state directory: '{resolved}'"
        )
    if resolved == REAL_REPO_LOCKFILE:
        raise AssertionError(
            f"CRITICAL TEST ISOLATION FAILURE: Test attempted to write to repository root lockfile: '{resolved}'"
        )


@pytest.fixture(autouse=True)
def isolate_aiaddons_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[None, None, None]:
    """Autouse fixture providing complete filesystem isolation for every test.

    1. Redirects Path.home() and HOME/USERPROFILE env vars to an isolated temp directory.
    2. Initializes isolated .aiaddons/ structure inside the temporary home.
    3. Changes current working directory to an isolated temporary workspace.
    4. Guards atomic write operations with runtime assertions against real paths.
    """
    # 1. Isolated fake home directory
    fake_home = tmp_path / "test_home"
    fake_aiaddons = fake_home / ".aiaddons"
    fake_aiaddons.mkdir(parents=True, exist_ok=True)
    (fake_aiaddons / "transactions").mkdir(parents=True, exist_ok=True)
    (fake_aiaddons / "registry").mkdir(parents=True, exist_ok=True)
    (fake_aiaddons / "staging").mkdir(parents=True, exist_ok=True)
    (fake_home / ".claude").mkdir(parents=True, exist_ok=True)
    (fake_home / ".codex").mkdir(parents=True, exist_ok=True)

    # 2. Isolated fake workspace directory
    fake_workspace = tmp_path / "test_workspace"
    fake_workspace.mkdir(parents=True, exist_ok=True)

    # 3. Patch home resolution and environment
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))

    # 4. Patch cwd to isolated workspace so LockfileManager / CLI defaults are safe
    monkeypatch.chdir(fake_workspace)

    # 5. Guard atomic file writers in state modules against real paths
    import aiaddons.state.transaction as tx_mod
    import aiaddons.state.lockfile as lockfile_mod
    import aiaddons.state.store as store_mod

    orig_store_atomic = store_mod._atomic_write_file
    orig_lock_atomic = lockfile_mod._atomic_write_file
    orig_tx_atomic = tx_mod._atomic_write_file

    def guarded_store_atomic(dir_path: Path, filename: str, content: str) -> Path:
        target = Path(dir_path) / filename
        _verify_safe_write_path(target)
        return orig_store_atomic(dir_path, filename, content)

    def guarded_lock_atomic(dir_path: Path, filename: str, content: str) -> Path:
        target = Path(dir_path) / filename
        _verify_safe_write_path(target)
        return orig_lock_atomic(dir_path, filename, content)

    def guarded_tx_atomic(dir_path: Path, filename: str, content: str) -> Path:
        target = Path(dir_path) / filename
        _verify_safe_write_path(target)
        return orig_tx_atomic(dir_path, filename, content)

    monkeypatch.setattr(store_mod, "_atomic_write_file", guarded_store_atomic)
    monkeypatch.setattr(lockfile_mod, "_atomic_write_file", guarded_lock_atomic)
    monkeypatch.setattr(tx_mod, "_atomic_write_file", guarded_tx_atomic)

    # Track real state modification timestamps before test
    real_state_file = REAL_AIADDONS_DIR / "state.json"
    mtime_before = real_state_file.stat().st_mtime if real_state_file.exists() else None
    lock_mtime_before = REAL_REPO_LOCKFILE.stat().st_mtime if REAL_REPO_LOCKFILE.exists() else None

    yield

    # Verify real state was not touched after test completion
    if real_state_file.exists():
        mtime_after = real_state_file.stat().st_mtime
        if mtime_before is not None and mtime_after != mtime_before:
            raise AssertionError(
                f"SAFETY GUARDRAIL FAILURE: Real state file was modified during test: '{real_state_file}'"
            )

    if REAL_REPO_LOCKFILE.exists():
        lock_mtime_after = REAL_REPO_LOCKFILE.stat().st_mtime
        if lock_mtime_before is not None and lock_mtime_after != lock_mtime_before:
            raise AssertionError(
                f"SAFETY GUARDRAIL FAILURE: Real repo lockfile was modified during test: '{REAL_REPO_LOCKFILE}'"
            )
