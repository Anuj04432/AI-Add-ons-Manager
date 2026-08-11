"""Local state, WAL transaction logging, and lockfile management subpackage."""

from aiaddons.state.lockfile import LockfileAddonEntry, LockfileManager
from aiaddons.state.store import InstalledAddonRecord, InstalledStateStore
from aiaddons.state.transaction import TransactionWALManager

__all__ = [
    "InstalledAddonRecord",
    "InstalledStateStore",
    "LockfileAddonEntry",
    "LockfileManager",
    "TransactionWALManager",
]
