"""Cross-platform file locking utility for state.json and aiaddons.lock concurrency protection."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from filelock import BaseFileLock, FileLock, Timeout

from aiaddons.core.exceptions import StateLockTimeoutError

DEFAULT_LOCK_TIMEOUT: float = 10.0


def get_lock_file_path(target_file_path: Path | str) -> Path:
    """Return the companion lock file path for a given target file path."""
    target = Path(target_file_path).expanduser().resolve()
    return target.with_name(f"{target.name}.lock")


@contextmanager
def state_file_lock(
    target_path: Path | str,
    timeout: float = DEFAULT_LOCK_TIMEOUT,
) -> Iterator[BaseFileLock]:
    """Acquire an exclusive cross-platform file lock on a target state/lockfile path.

    Args:
        target_path: Path to the file being protected (e.g. state.json or aiaddons.lock).
                     The actual lock file will be created alongside it as <target_path>.lock.
        timeout: Maximum seconds to wait for acquiring the lock before failing.

    Yields:
        The acquired FileLock instance.

    Raises:
        StateLockTimeoutError: If the lock cannot be acquired within the timeout period.
    """
    lock_path = get_lock_file_path(target_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(lock_path), is_singleton=True)

    try:
        lock.acquire(timeout=timeout)
    except Timeout as err:
        raise StateLockTimeoutError(
            lock_path=lock_path,
            timeout=timeout,
        ) from err

    try:
        yield lock
    finally:
        lock.release()
