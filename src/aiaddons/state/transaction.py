"""Write-Ahead Log (WAL) transaction manager for crash-safe state persistence."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from aiaddons.core.exceptions import InstallationError, SecurityValidationError
from aiaddons.core.execution.models import ExecutionStatus
from aiaddons.core.installer.models import (
    InstallationTransaction,
    TransactionPhase,
)

if TYPE_CHECKING:
    from aiaddons.core.execution.engine import ExecutionEngine


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


class TransactionWALManager:
    """Manager for durable Write-Ahead Logging of installation transactions."""

    def __init__(self, transactions_dir: Path | None = None) -> None:
        if transactions_dir is None:
            self.transactions_dir = Path.home() / ".aiaddons" / "transactions"
        else:
            self.transactions_dir = transactions_dir.expanduser()

    def get_log_path(self, transaction_id: str) -> Path:
        """Get path to the transaction log file."""
        safe_id = transaction_id.strip()
        if not safe_id or "/" in safe_id or "\\" in safe_id or ".." in safe_id:
            raise SecurityValidationError(f"Invalid transaction ID '{transaction_id}'.")
        return self.transactions_dir / f"{safe_id}.json"

    def write_transaction(self, transaction: InstallationTransaction) -> Path:
        """Atomically persist or update transaction state to disk."""
        content = transaction.model_dump_json(indent=2) + "\n"
        file_path = f"{transaction.transaction_id}.json"
        return _atomic_write_file(self.transactions_dir, file_path, content)

    def read_transaction(self, transaction_id: str) -> InstallationTransaction | None:
        """Read and validate a persisted transaction from disk."""
        log_path = self.get_log_path(transaction_id)
        if not log_path.exists() or not log_path.is_file():
            return None
        try:
            raw_text = log_path.read_text(encoding="utf-8")
            return InstallationTransaction.model_validate_json(raw_text)
        except Exception as err:
            msg = f"Failed to read transaction log '{transaction_id}': {err}"
            raise InstallationError(msg) from err

    def list_transactions(self) -> list[InstallationTransaction]:
        """List all persisted transactions sorted by log file timestamp."""
        if not self.transactions_dir.exists():
            return []
        transactions: list[InstallationTransaction] = []
        for file_path in sorted(self.transactions_dir.glob("*.json")):
            try:
                raw_text = file_path.read_text(encoding="utf-8")
                tx = InstallationTransaction.model_validate_json(raw_text)
                transactions.append(tx)
            except Exception:
                continue
        return transactions

    def list_interrupted_transactions(self) -> list[InstallationTransaction]:
        """Filter transactions left in EXECUTING/FAILED without COMMITTED or ROLLED_BACK."""
        interrupted: list[InstallationTransaction] = []
        for tx in self.list_transactions():
            if tx.phase in (TransactionPhase.EXECUTING, TransactionPhase.FAILED):
                interrupted.append(tx)
        return interrupted

    def recover_interrupted_transaction(
        self,
        transaction_id: str,
        execution_engine: ExecutionEngine | None = None,
    ) -> InstallationTransaction:
        """Recover an interrupted transaction, validating safety before rolling back."""
        tx = self.read_transaction(transaction_id)
        if not tx:
            raise InstallationError(f"Transaction log '{transaction_id}' not found for recovery.")

        if tx.phase not in (TransactionPhase.EXECUTING, TransactionPhase.FAILED):
            return tx

        if tx.plan:
            # Re-validate safety of plan before recovery
            tx.plan.validate_safety()

        if execution_engine is None:
            from aiaddons.core.execution.engine import ExecutionEngine
            engine = ExecutionEngine()
        else:
            engine = execution_engine

        if tx.plan:
            # Execute rollback for safety
            res = engine.execute_plan(tx.plan, transaction=tx, dry_run=False)
            if res.status in (ExecutionStatus.ROLLED_BACK, ExecutionStatus.FAILED):
                tx.phase = TransactionPhase.ROLLED_BACK
                msg = res.error_message or "Recovered and rolled back interrupted transaction."
                tx.error_message = msg
        else:
            tx.phase = TransactionPhase.FAILED

        self.write_transaction(tx)
        return tx

