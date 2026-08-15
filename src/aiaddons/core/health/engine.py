"""Health check and diagnostic engine for Phase 6 (aiaddons doctor).

Provides read-only system inspection across Registry, Installed State, Lockfile,
Transactions/WAL, Staging, Runtimes, AI Coding Agents, Consistency, and Security.
"""

from __future__ import annotations

import json
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml
from packaging.version import InvalidVersion, Version

from aiaddons.agents.manager import AgentDetectionManager
from aiaddons.agents.utils import find_executable, run_version_command
from aiaddons.core.execution.external.security import (
    ALLOWED_EXECUTABLES,
    ALLOWED_RUNTIMES,
)
from aiaddons.core.health.models import (
    HealthCategory,
    HealthCheckItem,
    HealthReport,
    HealthStatus,
)
from aiaddons.core.models.agent import Scope
from aiaddons.core.models.manifest import (
    DANGEROUS_ENV_VARS,
    FORBIDDEN_SHELL_PATTERNS,
    IntegrationType,
)
from aiaddons.registry.cache import RegistryCacheManager
from aiaddons.registry.client import DEFAULT_REGISTRY_URL
from aiaddons.state.lockfile import LockfileManager, WorkspaceLockfile
from aiaddons.state.store import InstalledStateDatabase, InstalledStateStore
from aiaddons.state.transaction import TransactionWALManager


class HealthCheckEngine:
    """Read-only diagnostic inspection engine for evaluating system health."""

    def __init__(
        self,
        store_dir: Path | None = None,
        workspace_dir: Path | None = None,
        agent_manager: AgentDetectionManager | None = None,
        registry_url: str = DEFAULT_REGISTRY_URL,
    ) -> None:
        if store_dir is None:
            self.store_dir = Path.home() / ".aiaddons"
        else:
            self.store_dir = store_dir.expanduser().resolve()

        if workspace_dir is None:
            self.workspace_dir = Path.cwd().resolve()
        else:
            self.workspace_dir = workspace_dir.expanduser().resolve()

        if agent_manager is None:
            self.agent_manager = AgentDetectionManager()
        else:
            self.agent_manager = agent_manager

        self.registry_url = registry_url

    def run_all_checks(self) -> HealthReport:
        """Run all diagnostic checks across all categories and return aggregate HealthReport."""
        items: list[HealthCheckItem] = []

        items.extend(self.check_registry())
        items.extend(self.check_installed_state())
        items.extend(self.check_lockfile())
        items.extend(self.check_transactions())
        items.extend(self.check_staging())
        items.extend(self.check_runtimes())
        items.extend(self.check_agents())
        items.extend(self.check_consistency())
        items.extend(self.check_security())

        # Determine overall status
        if any(item.status == HealthStatus.FAIL for item in items):
            overall_status = HealthStatus.FAIL
        elif any(item.status == HealthStatus.WARN for item in items):
            overall_status = HealthStatus.WARN
        else:
            overall_status = HealthStatus.PASS

        # Compute summary counts
        summary = {
            HealthStatus.PASS.value: sum(1 for item in items if item.status == HealthStatus.PASS),
            HealthStatus.WARN.value: sum(1 for item in items if item.status == HealthStatus.WARN),
            HealthStatus.FAIL.value: sum(1 for item in items if item.status == HealthStatus.FAIL),
            HealthStatus.SKIPPED.value: sum(
                1 for item in items if item.status == HealthStatus.SKIPPED
            ),
        }

        return HealthReport(
            overall_status=overall_status,
            summary=summary,
            items=items,
            timestamp=datetime.now(UTC).isoformat(),
            workspace_dir=str(self.workspace_dir),
        )

    # -------------------------------------------------------------------------
    # 1. Registry Health
    # -------------------------------------------------------------------------
    def check_registry(self) -> list[HealthCheckItem]:
        """Inspect local registry cache existence, validity, freshness, and configured URL."""
        items: list[HealthCheckItem] = []
        cache_dir = self.store_dir / "registry"
        cache_mgr = RegistryCacheManager(cache_dir=cache_dir)
        cache_file = cache_mgr.cache_file

        # Registry URL security check
        url_lower = self.registry_url.strip().lower()
        if (
            url_lower.startswith("https://")
            or url_lower.startswith("http://localhost")
            or url_lower.startswith("http://127.0.0.1")
        ):
            items.append(
                HealthCheckItem(
                    check_id="registry_url_secure",
                    category=HealthCategory.REGISTRY,
                    status=HealthStatus.PASS,
                    message=f"Configured registry URL is secure: {self.registry_url}",
                    diagnostic_details={"registry_url": self.registry_url},
                )
            )
        else:
            items.append(
                HealthCheckItem(
                    check_id="registry_url_secure",
                    category=HealthCategory.REGISTRY,
                    status=HealthStatus.FAIL,
                    message=f"Insecure HTTP registry URL configured: {self.registry_url}",
                    remediation="Configure a secure HTTPS registry URL.",
                    diagnostic_details={"registry_url": self.registry_url},
                )
            )

        if not cache_file.exists():
            items.append(
                HealthCheckItem(
                    check_id="registry_cache_exists",
                    category=HealthCategory.REGISTRY,
                    status=HealthStatus.WARN,
                    message="Registry cache file not found.",
                    remediation="Run: aiaddons registry update",
                    diagnostic_details={"cache_path": str(cache_file)},
                )
            )
            return items

        items.append(
            HealthCheckItem(
                check_id="registry_cache_exists",
                category=HealthCategory.REGISTRY,
                status=HealthStatus.PASS,
                message="Registry cache exists.",
                diagnostic_details={"cache_path": str(cache_file)},
            )
        )

        cache_data, err = cache_mgr.load_cache_data()
        if err is not None or cache_data is None:
            items.append(
                HealthCheckItem(
                    check_id="registry_cache_valid",
                    category=HealthCategory.REGISTRY,
                    status=HealthStatus.FAIL,
                    message=f"Registry cache is corrupted or malformed: {err}",
                    remediation="Run: aiaddons registry update",
                    diagnostic_details={"error": err},
                )
            )
            return items

        items.append(
            HealthCheckItem(
                check_id="registry_cache_valid",
                category=HealthCategory.REGISTRY,
                status=HealthStatus.PASS,
                message="Registry cache is valid JSON.",
            )
        )

        # Schema version check
        schema_ver = cache_data.index.schema_version
        if schema_ver == "1.0":
            items.append(
                HealthCheckItem(
                    check_id="registry_schema_version",
                    category=HealthCategory.REGISTRY,
                    status=HealthStatus.PASS,
                    message=f"Registry schema version '{schema_ver}' is supported.",
                    diagnostic_details={"schema_version": schema_ver},
                )
            )
        else:
            items.append(
                HealthCheckItem(
                    check_id="registry_schema_version",
                    category=HealthCategory.REGISTRY,
                    status=HealthStatus.FAIL,
                    message=f"Unsupported registry schema version '{schema_ver}'.",
                    remediation="Update aiaddons or synchronize registry.",
                    diagnostic_details={"schema_version": schema_ver},
                )
            )

        # Manifest count and metadata validation
        manifest_count = len(cache_data.index.manifests)
        items.append(
            HealthCheckItem(
                check_id="registry_manifests_valid",
                category=HealthCategory.REGISTRY,
                status=HealthStatus.PASS,
                message=f"All {manifest_count} cached manifest(s) pass validation.",
                diagnostic_details={"manifest_count": manifest_count},
            )
        )

        # Cache freshness check (warn if older than 7 days)
        try:
            synced_dt = datetime.fromisoformat(cache_data.last_synced_at)
            now = datetime.now(UTC)
            age_seconds = (now - synced_dt).total_seconds()
            if age_seconds > 7 * 86400:
                items.append(
                    HealthCheckItem(
                        check_id="registry_cache_freshness",
                        category=HealthCategory.REGISTRY,
                        status=HealthStatus.WARN,
                        message=f"Registry cache is stale ({cache_data.last_synced_at}).",
                        remediation="Run: aiaddons registry update",
                        diagnostic_details={
                            "last_synced_at": cache_data.last_synced_at,
                            "age_days": round(age_seconds / 86400, 1),
                        },
                    )
                )
            else:
                items.append(
                    HealthCheckItem(
                        check_id="registry_cache_freshness",
                        category=HealthCategory.REGISTRY,
                        status=HealthStatus.PASS,
                        message=f"Registry cache is fresh ({cache_data.last_synced_at}).",
                        diagnostic_details={"last_synced_at": cache_data.last_synced_at},
                    )
                )
        except Exception:
            items.append(
                HealthCheckItem(
                    check_id="registry_cache_freshness",
                    category=HealthCategory.REGISTRY,
                    status=HealthStatus.PASS,
                    message=f"Registry cache synced: {cache_data.last_synced_at}",
                )
            )

        return items

    # -------------------------------------------------------------------------
    # 2. Installed State Health
    # -------------------------------------------------------------------------
    def check_installed_state(self) -> list[HealthCheckItem]:
        """Inspect installed state database (~/.aiaddons/state.json)."""
        items: list[HealthCheckItem] = []
        state_store = InstalledStateStore(store_dir=self.store_dir)
        state_file = state_store.store_dir / state_store.state_file_name

        if not state_file.exists():
            items.append(
                HealthCheckItem(
                    check_id="state_file_exists",
                    category=HealthCategory.INSTALLED_STATE,
                    status=HealthStatus.PASS,
                    message="No state file yet (no add-ons installed).",
                    diagnostic_details={"state_file": str(state_file)},
                )
            )
            return items

        items.append(
            HealthCheckItem(
                check_id="state_file_exists",
                category=HealthCategory.INSTALLED_STATE,
                status=HealthStatus.PASS,
                message="Installed state database file exists.",
                diagnostic_details={"state_file": str(state_file)},
            )
        )

        try:
            content = state_file.read_text(encoding="utf-8").strip()
            if not content:
                db = InstalledStateDatabase()
            else:
                db = InstalledStateDatabase.model_validate_json(content)
        except Exception as err:
            items.append(
                HealthCheckItem(
                    check_id="state_file_valid",
                    category=HealthCategory.INSTALLED_STATE,
                    status=HealthStatus.FAIL,
                    message=f"Installed state database is malformed: {err}",
                    remediation="Inspect ~/.aiaddons/state.json for corruption.",
                    diagnostic_details={"error": str(err)},
                )
            )
            return items

        items.append(
            HealthCheckItem(
                check_id="state_file_valid",
                category=HealthCategory.INSTALLED_STATE,
                status=HealthStatus.PASS,
                message=f"State database structure is valid ({len(db.records)} record(s)).",
            )
        )

        # Validate individual records
        invalid_records: list[str] = []
        for key, record in db.records.items():
            if (
                not record.addon_id
                or any(c in record.addon_id for c in FORBIDDEN_SHELL_PATTERNS)
                or ".." in record.addon_id
            ):
                invalid_records.append(f"{key}: invalid addon_id '{record.addon_id}'")
                continue

            try:
                Version(record.version)
            except (InvalidVersion, Exception):
                if not record.version:
                    invalid_records.append(f"{key}: missing version")

            if not record.target_agent:
                invalid_records.append(f"{key}: missing target_agent")
            if not isinstance(record.scope, Scope):
                invalid_records.append(f"{key}: invalid scope '{record.scope}'")
            if not isinstance(record.integration_type, IntegrationType):
                invalid_records.append(
                    f"{key}: invalid integration_type '{record.integration_type}'"
                )

        if invalid_records:
            items.append(
                HealthCheckItem(
                    check_id="state_records_valid",
                    category=HealthCategory.INSTALLED_STATE,
                    status=HealthStatus.FAIL,
                    message=f"Found {len(invalid_records)} invalid state record(s).",
                    remediation="Review state.json records.",
                    diagnostic_details={"invalid_records": invalid_records},
                )
            )
        else:
            items.append(
                HealthCheckItem(
                    check_id="state_records_valid",
                    category=HealthCategory.INSTALLED_STATE,
                    status=HealthStatus.PASS,
                    message=f"All {len(db.records)} state record(s) are structurally valid.",
                    diagnostic_details={"record_count": len(db.records)},
                )
            )

        return items

    # -------------------------------------------------------------------------
    # 3. Lockfile Health
    # -------------------------------------------------------------------------
    def check_lockfile(self) -> list[HealthCheckItem]:
        """Inspect workspace lockfile (aiaddons.lock) syntax, schema, and state consistency."""
        items: list[HealthCheckItem] = []
        lock_mgr = LockfileManager()
        lock_path = lock_mgr.get_lockfile_path(self.workspace_dir)

        if not lock_path.exists():
            items.append(
                HealthCheckItem(
                    check_id="lockfile_exists",
                    category=HealthCategory.LOCKFILE,
                    status=HealthStatus.PASS,
                    message="No workspace lockfile present in current workspace.",
                    diagnostic_details={"lockfile_path": str(lock_path)},
                )
            )
            return items

        items.append(
            HealthCheckItem(
                check_id="lockfile_exists",
                category=HealthCategory.LOCKFILE,
                status=HealthStatus.PASS,
                message="Workspace lockfile (aiaddons.lock) exists.",
                diagnostic_details={"lockfile_path": str(lock_path)},
            )
        )

        try:
            content = lock_path.read_text(encoding="utf-8").strip()
            if not content:
                lockfile = WorkspaceLockfile()
            else:
                data = yaml.safe_load(content)
                if not isinstance(data, dict):
                    raise ValueError("Lockfile root must be a dictionary.")
                lockfile = WorkspaceLockfile.model_validate(data)
        except Exception as err:
            items.append(
                HealthCheckItem(
                    check_id="lockfile_valid",
                    category=HealthCategory.LOCKFILE,
                    status=HealthStatus.FAIL,
                    message=f"Workspace lockfile is malformed: {err}",
                    remediation="Check aiaddons.lock for YAML syntax errors.",
                    diagnostic_details={"error": str(err)},
                )
            )
            return items

        items.append(
            HealthCheckItem(
                check_id="lockfile_valid",
                category=HealthCategory.LOCKFILE,
                status=HealthStatus.PASS,
                message=f"Workspace lockfile schema is valid ({len(lockfile.addons)} entries).",
            )
        )

        # Cross-check lockfile with workspace-scoped entries in InstalledStateStore
        state_store = InstalledStateStore(store_dir=self.store_dir)
        try:
            installed_ws = state_store.get_installed(scope=Scope.WORKSPACE)
            ws_keys = {f"{r.target_agent.lower()}:{r.addon_id.lower()}" for r in installed_ws}
            lock_keys = set(lockfile.addons.keys())

            missing_in_state = lock_keys - ws_keys
            missing_in_lock = ws_keys - lock_keys

            if missing_in_state or missing_in_lock:
                items.append(
                    HealthCheckItem(
                        check_id="lockfile_state_consistency",
                        category=HealthCategory.LOCKFILE,
                        status=HealthStatus.WARN,
                        message="Inconsistency between workspace lockfile and installed state.",
                        remediation="Run: aiaddons install <addon-id> to synchronize.",
                        diagnostic_details={
                            "missing_in_state": sorted(missing_in_state),
                            "missing_in_lock": sorted(missing_in_lock),
                        },
                    )
                )
            else:
                items.append(
                    HealthCheckItem(
                        check_id="lockfile_state_consistency",
                        category=HealthCategory.LOCKFILE,
                        status=HealthStatus.PASS,
                        message="Workspace lockfile is consistent with installed state.",
                    )
                )
        except Exception as err:
            items.append(
                HealthCheckItem(
                    check_id="lockfile_state_consistency",
                    category=HealthCategory.LOCKFILE,
                    status=HealthStatus.WARN,
                    message=f"Could not cross-check lockfile with state: {err}",
                )
            )

        return items

    # -------------------------------------------------------------------------
    # 4. Transaction / WAL Health
    # -------------------------------------------------------------------------
    def check_transactions(self) -> list[HealthCheckItem]:
        """Inspect ~/.aiaddons/transactions/ for malformed, interrupted, or stale WAL logs."""
        items: list[HealthCheckItem] = []
        wal_dir = self.store_dir / "transactions"
        wal_mgr = TransactionWALManager(transactions_dir=wal_dir)

        if not wal_dir.exists():
            items.append(
                HealthCheckItem(
                    check_id="transactions_directory",
                    category=HealthCategory.TRANSACTIONS,
                    status=HealthStatus.PASS,
                    message="Transaction directory is clean (no transactions logged).",
                )
            )
            return items

        log_files = sorted(wal_dir.glob("*.json"))
        if not log_files:
            items.append(
                HealthCheckItem(
                    check_id="transactions_directory",
                    category=HealthCategory.TRANSACTIONS,
                    status=HealthStatus.PASS,
                    message="Transaction directory contains no log files.",
                )
            )
            return items

        malformed_files: list[str] = []
        valid_transactions = []
        for file_path in log_files:
            try:
                tx = wal_mgr.read_transaction(file_path.stem)
                if tx is None:
                    malformed_files.append(file_path.name)
                else:
                    valid_transactions.append(tx)
            except Exception:
                malformed_files.append(file_path.name)

        if malformed_files:
            items.append(
                HealthCheckItem(
                    check_id="transactions_wal_valid",
                    category=HealthCategory.TRANSACTIONS,
                    status=HealthStatus.FAIL,
                    message=f"Found {len(malformed_files)} malformed transaction WAL file(s).",
                    remediation="Inspect corrupted transaction logs in ~/.aiaddons/transactions/.",
                    diagnostic_details={"malformed_files": malformed_files},
                )
            )
        else:
            items.append(
                HealthCheckItem(
                    check_id="transactions_wal_valid",
                    category=HealthCategory.TRANSACTIONS,
                    status=HealthStatus.PASS,
                    message=f"All {len(log_files)} transaction log file(s) are valid.",
                )
            )

        # Check for interrupted transactions
        interrupted = wal_mgr.list_interrupted_transactions()
        if interrupted:
            interrupted_ids = [t.transaction_id for t in interrupted]
            items.append(
                HealthCheckItem(
                    check_id="transactions_interrupted",
                    category=HealthCategory.TRANSACTIONS,
                    status=HealthStatus.WARN,
                    message=f"{len(interrupted)} interrupted transaction(s) detected.",
                    remediation="Review transaction recovery status or re-run installation.",
                    diagnostic_details={"interrupted_transaction_ids": interrupted_ids},
                )
            )
        else:
            items.append(
                HealthCheckItem(
                    check_id="transactions_interrupted",
                    category=HealthCategory.TRANSACTIONS,
                    status=HealthStatus.PASS,
                    message="No interrupted transactions detected.",
                )
            )

        return items

    # -------------------------------------------------------------------------
    # 5. Staging Health
    # -------------------------------------------------------------------------
    def check_staging(self) -> list[HealthCheckItem]:
        """Inspect ~/.aiaddons/staging/ for orphaned or stale staging directories."""
        items: list[HealthCheckItem] = []
        staging_root = self.store_dir / "staging"
        wal_dir = self.store_dir / "transactions"
        wal_mgr = TransactionWALManager(transactions_dir=wal_dir)

        if not staging_root.exists():
            items.append(
                HealthCheckItem(
                    check_id="staging_clean",
                    category=HealthCategory.STAGING,
                    status=HealthStatus.PASS,
                    message="Staging directory is clean (does not exist yet).",
                )
            )
            return items

        staging_dirs = [d for d in staging_root.iterdir() if d.is_dir()]
        if not staging_dirs:
            items.append(
                HealthCheckItem(
                    check_id="staging_clean",
                    category=HealthCategory.STAGING,
                    status=HealthStatus.PASS,
                    message="No staging directories present.",
                )
            )
            return items

        unsafe_dirs: list[str] = []
        orphaned_dirs: list[str] = []

        for folder in staging_dirs:
            folder_name = folder.name
            if (
                any(c in folder_name for c in FORBIDDEN_SHELL_PATTERNS)
                or ".." in folder_name
                or "\0" in folder_name
            ):
                unsafe_dirs.append(folder_name)
                continue

            tx = wal_mgr.read_transaction(folder_name)
            if tx is None or tx.phase not in (
                "source_acquisition",
                "executing",
            ):
                orphaned_dirs.append(folder_name)

        if unsafe_dirs:
            items.append(
                HealthCheckItem(
                    check_id="staging_path_safety",
                    category=HealthCategory.STAGING,
                    status=HealthStatus.FAIL,
                    message=f"Suspicious path name(s) in staging: {', '.join(unsafe_dirs)}.",
                    remediation="Inspect ~/.aiaddons/staging/ for unauthorized paths.",
                    diagnostic_details={"unsafe_dirs": unsafe_dirs},
                )
            )
        else:
            items.append(
                HealthCheckItem(
                    check_id="staging_path_safety",
                    category=HealthCategory.STAGING,
                    status=HealthStatus.PASS,
                    message="All staging directory names are well-formed.",
                )
            )

        if orphaned_dirs:
            items.append(
                HealthCheckItem(
                    check_id="staging_orphaned",
                    category=HealthCategory.STAGING,
                    status=HealthStatus.WARN,
                    message=f"Found {len(orphaned_dirs)} orphaned staging director(y/ies).",
                    remediation="Staging directories from completed runs can be safely cleaned up.",
                    diagnostic_details={"orphaned_directories": orphaned_dirs},
                )
            )
        else:
            items.append(
                HealthCheckItem(
                    check_id="staging_orphaned",
                    category=HealthCategory.STAGING,
                    status=HealthStatus.PASS,
                    message="No orphaned staging directories.",
                )
            )

        return items

    # -------------------------------------------------------------------------
    # 6. Runtime Health
    # -------------------------------------------------------------------------
    def check_runtimes(self) -> list[HealthCheckItem]:
        """Check availability of allowlisted runtime executables."""
        items: list[HealthCheckItem] = []

        # Python is always required (aiaddons is running in it)
        py_ver = platform.python_version()
        py_exe = sys.executable
        items.append(
            HealthCheckItem(
                check_id="runtime_python",
                category=HealthCategory.RUNTIMES,
                status=HealthStatus.PASS,
                message=f"python {py_ver} available ({py_exe})",
                diagnostic_details={"version": py_ver, "executable": py_exe, "required": True},
            )
        )

        runtimes = [
            ("git", False, "Install git to support git-based add-on sources."),
            ("node", False, "Install Node.js to run npm/npx-based MCP servers."),
            ("npm", False, "Install npm to support Node package installations."),
            ("npx", False, "Install npx to execute npx-based MCP servers."),
            ("pip", False, "Install pip for Python package dependencies."),
            ("uvx", False, "Install uv/uvx for fast Python MCP server execution."),
        ]

        for name, required, remediation in runtimes:
            exec_path = find_executable(name)
            if exec_path is not None:
                ver = run_version_command(name)
                ver_str = f" {ver}" if ver else ""
                items.append(
                    HealthCheckItem(
                        check_id=f"runtime_{name}",
                        category=HealthCategory.RUNTIMES,
                        status=HealthStatus.PASS,
                        message=f"{name}{ver_str} available ({exec_path})",
                        diagnostic_details={
                            "name": name,
                            "version": ver,
                            "executable": str(exec_path),
                            "required": required,
                        },
                    )
                )
            else:
                items.append(
                    HealthCheckItem(
                        check_id=f"runtime_{name}",
                        category=HealthCategory.RUNTIMES,
                        status=HealthStatus.WARN,
                        message=f"{name} is unavailable on PATH.",
                        remediation=remediation,
                        diagnostic_details={"name": name, "required": required},
                    )
                )

        return items

    # -------------------------------------------------------------------------
    # 7. AI Agent Health
    # -------------------------------------------------------------------------
    def check_agents(self) -> list[HealthCheckItem]:
        """Check AI coding agents detection status and configuration parseability."""
        items: list[HealthCheckItem] = []
        results = self.agent_manager.detect_agents(project_path=self.workspace_dir)

        detected_count = 0
        for agent_id, res in results.items():
            if res.installed:
                detected_count += 1
                config_ok = True
                config_err: str | None = None

                for cfg_path_str in (res.global_config_path, res.workspace_config_path):
                    if cfg_path_str:
                        cfg_file = Path(cfg_path_str)
                        if cfg_file.exists() and cfg_file.is_file():
                            try:
                                content = cfg_file.read_text(encoding="utf-8").strip()
                                if content:
                                    json.loads(content)
                            except Exception as exc:
                                config_ok = False
                                config_err = f"Malformed config file '{cfg_file}': {exc}"
                                break

                if config_ok:
                    ver_str = f" (v{res.version})" if res.version else ""
                    items.append(
                        HealthCheckItem(
                            check_id=f"agent_{agent_id.replace('-', '_')}",
                            category=HealthCategory.AGENTS,
                            status=HealthStatus.PASS,
                            message=f"{res.name} detected{ver_str} - Configuration valid.",
                            diagnostic_details={
                                "agent_id": res.agent_id,
                                "version": res.version,
                                "executable": res.executable_path,
                                "config_path": res.config_path,
                            },
                        )
                    )
                else:
                    items.append(
                        HealthCheckItem(
                            check_id=f"agent_{agent_id.replace('-', '_')}_config",
                            category=HealthCategory.AGENTS,
                            status=HealthStatus.FAIL,
                            message=f"{res.name} configuration is malformed: {config_err}",
                            remediation=f"Inspect JSON syntax in {res.name} config file.",
                            diagnostic_details={"error": config_err},
                        )
                    )
            else:
                items.append(
                    HealthCheckItem(
                        check_id=f"agent_{agent_id.replace('-', '_')}",
                        category=HealthCategory.AGENTS,
                        status=HealthStatus.PASS,
                        message=f"{res.name} not detected on system.",
                        diagnostic_details={"agent_id": res.agent_id, "installed": False},
                    )
                )

        if detected_count > 0:
            items.append(
                HealthCheckItem(
                    check_id="agents_summary",
                    category=HealthCategory.AGENTS,
                    status=HealthStatus.PASS,
                    message=f"{detected_count} AI coding agent(s) detected and accessible.",
                    diagnostic_details={"detected_count": detected_count},
                )
            )
        else:
            items.append(
                HealthCheckItem(
                    check_id="agents_summary",
                    category=HealthCategory.AGENTS,
                    status=HealthStatus.WARN,
                    message="No AI coding agents (Claude Code / Codex) detected on host.",
                    remediation="Install Claude Code or OpenAI Codex to use AI Add-ons.",
                    diagnostic_details={"detected_count": 0},
                )
            )

        return items

    # -------------------------------------------------------------------------
    # 8. Add-on Consistency
    # -------------------------------------------------------------------------
    def check_consistency(self) -> list[HealthCheckItem]:
        """Cross-check Registry, Installed State, Lockfile, and Agent Configurations."""
        items: list[HealthCheckItem] = []
        state_store = InstalledStateStore(store_dir=self.store_dir)

        try:
            records = state_store.get_installed()
        except Exception:
            records = []

        if not records:
            items.append(
                HealthCheckItem(
                    check_id="consistency_overall",
                    category=HealthCategory.CONSISTENCY,
                    status=HealthStatus.PASS,
                    message="No active add-ons installed (consistency verified).",
                )
            )
            return items

        consistency_issues: list[str] = []

        for record in records:
            adapter = self.agent_manager.get_adapter(record.target_agent)
            if adapter is None:
                msg = f"Add-on '{record.addon_id}' targets unknown agent '{record.target_agent}'."
                consistency_issues.append(msg)
                items.append(
                    HealthCheckItem(
                        check_id=f"consistency_{record.addon_id}_agent",
                        category=HealthCategory.CONSISTENCY,
                        status=HealthStatus.FAIL,
                        message=msg,
                        remediation=f"Review state.json entry for '{record.addon_id}'.",
                    )
                )
                continue

            # Verify MCP server presence in config
            if record.integration_type == IntegrationType.MCP:
                cfg_path = adapter.get_config_path(record.scope, project_path=self.workspace_dir)
                if cfg_path is None or not cfg_path.exists() or not cfg_path.is_file():
                    msg = f"Installed MCP '{record.addon_id}' config file '{cfg_path}' is missing."
                    consistency_issues.append(msg)
                    items.append(
                        HealthCheckItem(
                            check_id=f"consistency_{record.addon_id}_mcp",
                            category=HealthCategory.CONSISTENCY,
                            status=HealthStatus.FAIL,
                            message=msg,
                            remediation=f"Reinstall: aiaddons install {record.addon_id}",
                        )
                    )
                else:
                    try:
                        content = cfg_path.read_text(encoding="utf-8")
                        cfg_json = json.loads(content)
                        mcp_servers = cfg_json.get("mcpServers", {})
                        if not isinstance(mcp_servers, dict) or record.addon_id not in mcp_servers:
                            msg = (
                                f"Installed MCP server '{record.addon_id}' missing "
                                f"from {record.target_agent} config."
                            )
                            consistency_issues.append(msg)
                            items.append(
                                HealthCheckItem(
                                    check_id=f"consistency_{record.addon_id}_mcp",
                                    category=HealthCategory.CONSISTENCY,
                                    status=HealthStatus.FAIL,
                                    message=msg,
                                    remediation=f"Reinstall: aiaddons install {record.addon_id}",
                                )
                            )
                    except Exception as exc:
                        msg = f"Failed to inspect config for '{record.addon_id}': {exc}"
                        consistency_issues.append(msg)
                        items.append(
                            HealthCheckItem(
                                check_id=f"consistency_{record.addon_id}_mcp",
                                category=HealthCategory.CONSISTENCY,
                                status=HealthStatus.FAIL,
                                message=msg,
                                remediation="Check agent config file syntax.",
                            )
                        )

            # Verify Skill files presence on disk
            elif record.integration_type == IntegrationType.SKILL:
                skill_dir = adapter.get_skill_directory(
                    record.scope, project_path=self.workspace_dir
                ) / record.addon_id
                skill_file = skill_dir / "SKILL.md"
                if not skill_dir.exists() or not skill_file.exists():
                    msg = f"Installed skill '{record.addon_id}' files missing at '{skill_dir}'."
                    consistency_issues.append(msg)
                    items.append(
                        HealthCheckItem(
                            check_id=f"consistency_{record.addon_id}_skill",
                            category=HealthCategory.CONSISTENCY,
                            status=HealthStatus.FAIL,
                            message=msg,
                            remediation=f"Reinstall: aiaddons install {record.addon_id}",
                        )
                    )

            # Verify Plugin component references
            elif record.integration_type == IntegrationType.PLUGIN:
                base_dir = (
                    self.workspace_dir if record.scope == Scope.WORKSPACE else Path.home()
                )
                plugin_file = base_dir / ".agents" / "plugins" / record.addon_id / "plugin.json"
                if not plugin_file.exists():
                    msg = (
                        f"Installed plugin '{record.addon_id}' descriptor missing "
                        f"at '{plugin_file}'."
                    )
                    consistency_issues.append(msg)
                    items.append(
                        HealthCheckItem(
                            check_id=f"consistency_{record.addon_id}_plugin",
                            category=HealthCategory.CONSISTENCY,
                            status=HealthStatus.FAIL,
                            message=msg,
                            remediation=f"Reinstall: aiaddons install {record.addon_id}",
                        )
                    )

        if not consistency_issues:
            items.append(
                HealthCheckItem(
                    check_id="consistency_overall",
                    category=HealthCategory.CONSISTENCY,
                    status=HealthStatus.PASS,
                    message=f"All {len(records)} installed add-on(s) are consistent with configs.",
                )
            )

        return items

    # -------------------------------------------------------------------------
    # 9. Security Health
    # -------------------------------------------------------------------------
    def check_security(self) -> list[HealthCheckItem]:
        """Perform read-only security checks across state, configs, environment, and paths."""
        items: list[HealthCheckItem] = []

        # Check 1: Path Traversal & Path Boundaries in State Database
        state_store = InstalledStateStore(store_dir=self.store_dir)
        state_file = state_store.store_dir / state_store.state_file_name
        suspicious_paths: list[str] = []

        if state_file.exists():
            try:
                content = state_file.read_text(encoding="utf-8")
                db = InstalledStateDatabase.model_validate_json(content)
                for rec in db.records.values():
                    for f in rec.installed_files:
                        if (
                            ".." in f
                            or "\0" in f
                            or f.startswith("//")
                            or f.startswith("\\\\")
                            or "%2e%2e" in f.lower()
                        ):
                            suspicious_paths.append(f)
            except Exception:
                pass

        if suspicious_paths:
            items.append(
                HealthCheckItem(
                    check_id="security_paths",
                    category=HealthCategory.SECURITY,
                    status=HealthStatus.FAIL,
                    message=f"Suspicious path(s) detected: {', '.join(suspicious_paths)}.",
                    remediation="Review state.json and remove invalid path entries.",
                    diagnostic_details={"suspicious_paths": suspicious_paths},
                )
            )
        else:
            items.append(
                HealthCheckItem(
                    check_id="security_paths",
                    category=HealthCategory.SECURITY,
                    status=HealthStatus.PASS,
                    message="All state and file paths pass boundary validation.",
                )
            )

        # Check 2: Dangerous Environment Variables in Agent Configs
        claude_cfg = Path.home() / ".claude.json"
        dangerous_env_found: list[str] = []
        if claude_cfg.exists() and claude_cfg.is_file():
            try:
                cfg = json.loads(claude_cfg.read_text(encoding="utf-8"))
                for srv_name, srv_data in cfg.get("mcpServers", {}).items():
                    if isinstance(srv_data, dict):
                        env_dict = srv_data.get("env", {})
                        if isinstance(env_dict, dict):
                            for var_name in env_dict.keys():
                                if var_name.upper() in DANGEROUS_ENV_VARS:
                                    dangerous_env_found.append(f"{srv_name}:{var_name}")
            except Exception:
                pass

        if dangerous_env_found:
            items.append(
                HealthCheckItem(
                    check_id="security_env_vars",
                    category=HealthCategory.SECURITY,
                    status=HealthStatus.FAIL,
                    message=f"Restricted env var(s) injected: {', '.join(dangerous_env_found)}.",
                    remediation="Remove restricted environment variables from agent configs.",
                    diagnostic_details={"dangerous_env_vars": dangerous_env_found},
                )
            )
        else:
            items.append(
                HealthCheckItem(
                    check_id="security_env_vars",
                    category=HealthCategory.SECURITY,
                    status=HealthStatus.PASS,
                    message="No restricted process manipulation environment variables detected.",
                )
            )

        # Check 3: Executable Allowlist in Agent Configs
        disallowed_execs: list[str] = []
        if claude_cfg.exists() and claude_cfg.is_file():
            try:
                cfg = json.loads(claude_cfg.read_text(encoding="utf-8"))
                for srv_name, srv_data in cfg.get("mcpServers", {}).items():
                    if isinstance(srv_data, dict):
                        cmd = str(srv_data.get("command", "")).strip().lower()
                        if cmd and cmd not in ALLOWED_RUNTIMES and cmd not in ALLOWED_EXECUTABLES:
                            disallowed_execs.append(f"{srv_name}:{cmd}")
            except Exception:
                pass

        if disallowed_execs:
            items.append(
                HealthCheckItem(
                    check_id="security_executables",
                    category=HealthCategory.SECURITY,
                    status=HealthStatus.FAIL,
                    message=(
                        f"Non-allowlisted executable(s) detected: "
                        f"{', '.join(disallowed_execs)}."
                    ),
                    remediation=(
                        "Ensure MCP commands use allowlisted binaries "
                        "(npx, uvx, node, python, pip, git)."
                    ),
                    diagnostic_details={"disallowed_executables": disallowed_execs},
                )
            )
        else:
            items.append(
                HealthCheckItem(
                    check_id="security_executables",
                    category=HealthCategory.SECURITY,
                    status=HealthStatus.PASS,
                    message="All configured executables comply with the runtime allowlist.",
                )
            )

        # Check 4: Secret Leak Detection in Configs
        potential_secrets: list[str] = []
        if claude_cfg.exists() and claude_cfg.is_file():
            try:
                cfg = json.loads(claude_cfg.read_text(encoding="utf-8"))
                for srv_name, srv_data in cfg.get("mcpServers", {}).items():
                    if isinstance(srv_data, dict):
                        env_dict = srv_data.get("env", {})
                        if isinstance(env_dict, dict):
                            for var_name, var_val in env_dict.items():
                                val_str = str(var_val).strip()
                                if (
                                    not val_str.startswith("${")
                                    and not val_str.endswith("}")
                                    and (
                                        val_str.startswith("ghp_")
                                        or val_str.startswith("sk-")
                                        or val_str.startswith("xoxb-")
                                        or (len(val_str) >= 32 and val_str.isalnum())
                                    )
                                ):
                                    potential_secrets.append(f"{srv_name}:{var_name}")
            except Exception:
                pass

        if potential_secrets:
            items.append(
                HealthCheckItem(
                    check_id="security_secrets",
                    category=HealthCategory.SECURITY,
                    status=HealthStatus.WARN,
                    message=f"Potential plaintext secret detected: {', '.join(potential_secrets)}.",
                    remediation="Use environment variable references (e.g. ${API_KEY}).",
                    diagnostic_details={"fields_with_potential_secrets": potential_secrets},
                )
            )
        else:
            items.append(
                HealthCheckItem(
                    check_id="security_secrets",
                    category=HealthCategory.SECURITY,
                    status=HealthStatus.PASS,
                    message="No plaintext secrets detected in configuration files.",
                )
            )

        return items
