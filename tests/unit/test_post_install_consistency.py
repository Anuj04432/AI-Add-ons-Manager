"""Regression and integration tests for post-install consistency and state tracking."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from aiaddons.agents.manager import AgentDetectionManager
from aiaddons.cli.main import app
from aiaddons.core.acquisition.engine import AcquisitionEngine
from aiaddons.core.acquisition.git import GitSourceFetcher
from aiaddons.core.acquisition.models import AcquiredSourceResult
from aiaddons.core.acquisition.staging import SourceStagingManager
from aiaddons.core.execution.engine import ExecutionEngine
from aiaddons.core.execution.models import ExecutionStatus
from aiaddons.core.health.engine import HealthCheckEngine
from aiaddons.core.health.models import HealthCategory, HealthStatus
from aiaddons.core.installer.engine import InstallationEngine
from aiaddons.core.installer.models import (
    InstallationPlan,
    InstallationTransaction,
    RiskLevel,
    RollbackMetadata,
    TransactionPhase,
    WriteFileOperation,
)
from aiaddons.core.models.agent import AgentCapability, AgentDetectionResult, Scope
from aiaddons.core.models.manifest import (
    IntegrationManifest,
    IntegrationType,
    SourceSpec,
    SourceType,
)
from aiaddons.registry.loader import RegistryLoader
from aiaddons.registry.registry import Registry
from aiaddons.state.lockfile import LockfileManager
from aiaddons.state.store import InstalledStateStore
from aiaddons.state.transaction import TransactionWALManager
from tests.fixtures.ponytail_manifests import (
    PONYTAIL_COMMIT_SHA,
)

EnvTuple = tuple[
    Path,
    Path,
    InstalledStateStore,
    LockfileManager,
    TransactionWALManager,
    SourceStagingManager,
]


@pytest.fixture
def isolated_env(tmp_path: Path) -> EnvTuple:
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    home_dir = tmp_path / "home"
    home_dir.mkdir(parents=True, exist_ok=True)
    store_dir = home_dir / ".aiaddons"
    store_dir.mkdir(parents=True, exist_ok=True)

    state_store = InstalledStateStore(store_dir=store_dir)
    lock_mgr = LockfileManager()
    wal_mgr = TransactionWALManager(transactions_dir=store_dir / "transactions")
    staging_mgr = SourceStagingManager(base_staging_dir=store_dir / "staging")

    return workspace_dir, store_dir, state_store, lock_mgr, wal_mgr, staging_mgr


@pytest.fixture
def registry_with_ponytail() -> Registry:
    loader = RegistryLoader()
    addons_dir = Path("registry/addons")
    manifests: dict[str, IntegrationManifest] = {}
    for yml in addons_dir.glob("*.yaml"):
        try:
            m = loader.load_file(yml)
            manifests[m.id] = m
        except Exception:
            continue
    return Registry(manifests=list(manifests.values()))


@pytest.fixture
def mock_claude_agent(tmp_path: Path) -> AgentDetectionResult:
    claude_cfg = tmp_path / "home" / ".claude.json"
    claude_cfg.write_text('{\n  "mcpServers": {}\n}\n', encoding="utf-8")
    return AgentDetectionResult(
        agent_id="claude-code",
        name="Claude Code",
        installed=True,
        version="1.0.0",
        global_config_path=str(claude_cfg),
        workspace_config_path=str(tmp_path / "workspace" / ".claude.json"),
        supported_scopes=[Scope.GLOBAL, Scope.WORKSPACE],
        capabilities=[AgentCapability.MCP, AgentCapability.SKILL],
    )


def test_successful_ponytail_installation_creates_installed_state_and_lockfile(
    isolated_env: EnvTuple,
    registry_with_ponytail: Registry,
    mock_claude_agent: AgentDetectionResult,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that installing Ponytail persists records to state.json and aiaddons.lock."""
    workspace_dir, store_dir, state_store, lock_mgr, wal_mgr, staging_mgr = isolated_env
    manifest = registry_with_ponytail.get("ponytail")
    assert manifest is not None

    inst_engine = InstallationEngine(registry=registry_with_ponytail, wal_manager=wal_mgr)
    tx = inst_engine.create_transaction(
        manifest, mock_claude_agent, Scope.WORKSPACE, registry=registry_with_ponytail
    )
    assert tx.plan is not None

    # Adjust target_root of operations to use workspace_dir
    for op in tx.plan.planned_operations:
        op.target_root = str(workspace_dir)

    # Mock git fetcher to populate staging dir with required skill files
    class MockGitFetcher(GitSourceFetcher):
        def fetch(
            self,
            manifest_id: str,
            source: SourceSpec,
            staging_dir: Path,
            dry_run: bool = False,
        ) -> AcquiredSourceResult:
            if not dry_run:
                skills_base = staging_dir / "skills"
                skills = [
                    "ponytail",
                    "ponytail-review",
                    "ponytail-audit",
                    "ponytail-debt",
                    "ponytail-gain",
                    "ponytail-help",
                ]
                for s in skills:
                    s_dir = skills_base / s
                    s_dir.mkdir(parents=True, exist_ok=True)
                    (s_dir / "SKILL.md").write_text(
                        f"---\nname: {s}\n---\n# {s}\n", encoding="utf-8"
                    )
            return AcquiredSourceResult(
                manifest_id=manifest_id,
                source_type=SourceType.GIT,
                staging_path=staging_dir,
                commit_sha=PONYTAIL_COMMIT_SHA,
                is_staged=not dry_run,
                is_dry_run=dry_run,
                acquired_files=["skills/ponytail/SKILL.md"],
            )

    acq_engine = AcquisitionEngine(
        fetchers=[MockGitFetcher()],
        staging_manager=staging_mgr,
    )

    exec_engine = ExecutionEngine(
        wal_manager=wal_mgr,
        state_store=state_store,
        lockfile_manager=lock_mgr,
        acquisition_engine=acq_engine,
        registry=registry_with_ponytail,
        workspace_dir=workspace_dir,
    )

    tx.is_dry_run = False
    result = exec_engine.execute_plan(tx.plan, transaction=tx, dry_run=False)

    assert result.status == ExecutionStatus.SUCCESS
    assert tx.phase == TransactionPhase.COMMITTED

    # 1. Verify InstalledStateStore has ponytail record
    records = state_store.get_installed()
    assert len(records) == 1
    rec = records[0]
    assert rec.addon_id == "ponytail"
    assert rec.name == manifest.name
    assert rec.version == manifest.version
    assert rec.integration_type == IntegrationType.PLUGIN
    assert rec.target_agent == "claude-code"
    assert rec.scope == Scope.WORKSPACE
    assert len(rec.installed_files) > 0

    # 2. Verify Lockfile has ponytail entry
    lock_entries = lock_mgr.get_entries(workspace_dir)
    assert len(lock_entries) == 1
    entry = lock_entries[0]
    assert entry.addon_id == "ponytail"
    assert entry.integration_type == IntegrationType.PLUGIN
    assert entry.target_agent == "claude-code"

    # 3. Verify staging directory was cleaned up upon commit
    assert not (staging_mgr.base_staging_dir / tx.transaction_id).exists()

    # 4. Verify Doctor recognizes Ponytail as installed and consistent
    agent_mgr = AgentDetectionManager()
    monkeypatch.setattr(
        agent_mgr,
        "detect_agents",
        lambda project_path=None: {"claude-code": mock_claude_agent},
    )

    doctor = HealthCheckEngine(
        store_dir=store_dir,
        workspace_dir=workspace_dir,
        agent_manager=agent_mgr,
    )
    report = doctor.run_all_checks()

    # Check lockfile category
    lockfile_items = report.get_items_by_category(HealthCategory.LOCKFILE)
    consistency_item = next(
        (i for i in lockfile_items if i.check_id == "lockfile_state_consistency"),
        None,
    )
    assert consistency_item is not None
    assert consistency_item.status == HealthStatus.PASS
    assert "consistent with installed state" in consistency_item.message

    # Check consistency category
    consistency_items = report.get_items_by_category(HealthCategory.CONSISTENCY)
    overall_item = next(
        (i for i in consistency_items if i.check_id == "consistency_overall"),
        None,
    )
    assert overall_item is not None
    assert overall_item.status == HealthStatus.PASS
    assert "All 1 installed add-on(s) are consistent" in overall_item.message


def test_committed_transaction_removes_staging_directory(
    isolated_env: EnvTuple,
    mock_claude_agent: AgentDetectionResult,
) -> None:
    """Verify that a COMMITTED transaction removes its staging directory."""
    workspace_dir, store_dir, state_store, lock_mgr, wal_mgr, staging_mgr = isolated_env

    source = SourceSpec(
        source_type=SourceType.PACKAGE,
        package_name="@modelcontextprotocol/server-github",
        checksum="sha256:" + "a" * 64,
    )
    op = WriteFileOperation(
        description="Write test file",
        target_root=str(workspace_dir),
        file_path="test_file.txt",
        content="hello",
        content_summary="hello",
        overwrite=True,
    )
    plan = InstallationPlan(
        addon_id="github-mcp",
        addon_name="GitHub MCP",
        addon_version="1.0.0",
        target_agent="claude-code",
        target_agent_name="Claude Code",
        target_scope=Scope.WORKSPACE,
        integration_type=IntegrationType.MCP,
        source=source,
        planned_operations=[op],
        risk_level=RiskLevel.LOW,
        reversible=True,
        rollback_info=RollbackMetadata(reversible=True),
    )
    tx = InstallationTransaction(
        transaction_id="tx_commit_test",
        phase=TransactionPhase.PLANNED,
        manifest=IntegrationManifest.model_validate({
            "id": "github-mcp",
            "name": "GitHub MCP",
            "version": "1.0.0",
            "description": "Test",
            "license": "MIT",
            "category": "developer-tools",
            "integration_type": "mcp",
            "target_agents": ["claude-code"],
            "source": source.model_dump(),
            "trust": {"verification_status": "verified", "publisher": {"name": "Test"}},
            "handler_spec": {"mcp": {"runtime": "npx", "package_name": "@test/pkg"}},
        }),
        agent=mock_claude_agent,
        requested_scope=Scope.WORKSPACE,
        plan=plan,
        is_dry_run=False,
    )

    acq_engine = AcquisitionEngine(staging_manager=staging_mgr)
    exec_engine = ExecutionEngine(
        wal_manager=wal_mgr,
        state_store=state_store,
        lockfile_manager=lock_mgr,
        acquisition_engine=acq_engine,
        workspace_dir=workspace_dir,
    )

    result = exec_engine.execute_plan(plan, transaction=tx, dry_run=False)
    assert result.status == ExecutionStatus.SUCCESS
    assert tx.phase == TransactionPhase.COMMITTED

    # Staging directory must be cleaned up
    assert not (staging_mgr.base_staging_dir / "tx_commit_test").exists()


def test_rolled_back_transaction_removes_staging_directory(
    isolated_env: EnvTuple,
    mock_claude_agent: AgentDetectionResult,
) -> None:
    """Verify that a ROLLED_BACK transaction removes its staging directory."""
    workspace_dir, store_dir, state_store, lock_mgr, wal_mgr, staging_mgr = isolated_env

    source = SourceSpec(
        source_type=SourceType.PACKAGE,
        package_name="@modelcontextprotocol/server-github",
        checksum="sha256:" + "a" * 64,
    )
    # Create destination collision so write fails
    (workspace_dir / "collision_dir").mkdir(parents=True)
    failing_op = WriteFileOperation(
        description="Write colliding file",
        target_root=str(workspace_dir),
        file_path="collision_dir",
        content="fail",
        content_summary="fail",
        overwrite=True,
    )
    plan = InstallationPlan(
        addon_id="github-mcp",
        addon_name="GitHub MCP",
        addon_version="1.0.0",
        target_agent="claude-code",
        target_agent_name="Claude Code",
        target_scope=Scope.WORKSPACE,
        integration_type=IntegrationType.MCP,
        source=source,
        planned_operations=[failing_op],
        risk_level=RiskLevel.LOW,
        reversible=True,
        rollback_info=RollbackMetadata(reversible=True),
    )
    tx = InstallationTransaction(
        transaction_id="tx_rollback_test",
        phase=TransactionPhase.PLANNED,
        manifest=IntegrationManifest.model_validate({
            "id": "github-mcp",
            "name": "GitHub MCP",
            "version": "1.0.0",
            "description": "Test",
            "license": "MIT",
            "category": "developer-tools",
            "integration_type": "mcp",
            "target_agents": ["claude-code"],
            "source": source.model_dump(),
            "trust": {"verification_status": "verified", "publisher": {"name": "Test"}},
            "handler_spec": {"mcp": {"runtime": "npx", "package_name": "@test/pkg"}},
        }),
        agent=mock_claude_agent,
        requested_scope=Scope.WORKSPACE,
        plan=plan,
        is_dry_run=False,
    )

    acq_engine = AcquisitionEngine(staging_manager=staging_mgr)
    exec_engine = ExecutionEngine(
        wal_manager=wal_mgr,
        state_store=state_store,
        lockfile_manager=lock_mgr,
        acquisition_engine=acq_engine,
        workspace_dir=workspace_dir,
    )

    result = exec_engine.execute_plan(plan, transaction=tx, dry_run=False)
    assert result.status == ExecutionStatus.ROLLED_BACK
    assert tx.phase == TransactionPhase.ROLLED_BACK

    # Staging directory must be cleaned up
    assert not (staging_mgr.base_staging_dir / "tx_rollback_test").exists()


def test_failed_acquisition_removes_staging_directory(
    isolated_env: EnvTuple,
    mock_claude_agent: AgentDetectionResult,
) -> None:
    """Verify that an acquisition failure cleans up the staging directory."""
    workspace_dir, store_dir, state_store, lock_mgr, wal_mgr, staging_mgr = isolated_env

    source = SourceSpec(
        source_type=SourceType.PACKAGE,
        package_name="@modelcontextprotocol/server-github",
        checksum="sha256:" + "a" * 64,
    )
    plan = InstallationPlan(
        addon_id="github-mcp",
        addon_name="GitHub MCP",
        addon_version="1.0.0",
        target_agent="claude-code",
        target_agent_name="Claude Code",
        target_scope=Scope.WORKSPACE,
        integration_type=IntegrationType.MCP,
        source=source,
        planned_operations=[],
        risk_level=RiskLevel.LOW,
        reversible=True,
        rollback_info=RollbackMetadata(reversible=True),
    )
    tx = InstallationTransaction(
        transaction_id="tx_acq_fail_test",
        phase=TransactionPhase.PLANNED,
        manifest=IntegrationManifest.model_validate({
            "id": "github-mcp",
            "name": "GitHub MCP",
            "version": "1.0.0",
            "description": "Test",
            "license": "MIT",
            "category": "developer-tools",
            "integration_type": "mcp",
            "target_agents": ["claude-code"],
            "source": source.model_dump(),
            "trust": {"verification_status": "verified", "publisher": {"name": "Test"}},
            "handler_spec": {"mcp": {"runtime": "npx", "package_name": "@test/pkg"}},
        }),
        agent=mock_claude_agent,
        requested_scope=Scope.WORKSPACE,
        plan=plan,
        is_dry_run=False,
    )

    mock_fetcher = MagicMock()
    mock_fetcher.supported_source_type.return_value = SourceType.PACKAGE
    mock_fetcher.fetch.side_effect = RuntimeError("Network error during acquisition")

    acq_engine = AcquisitionEngine(
        fetchers=[mock_fetcher],
        staging_manager=staging_mgr,
    )
    exec_engine = ExecutionEngine(
        wal_manager=wal_mgr,
        state_store=state_store,
        lockfile_manager=lock_mgr,
        acquisition_engine=acq_engine,
        workspace_dir=workspace_dir,
    )

    result = exec_engine.execute_plan(plan, transaction=tx, dry_run=False)
    assert result.status == ExecutionStatus.FAILED
    assert tx.phase == TransactionPhase.ROLLED_BACK

    # Staging directory must be cleaned up
    assert not (staging_mgr.base_staging_dir / "tx_acq_fail_test").exists()


def test_dry_run_creates_no_state_lockfile_wal_or_staging_artifacts(
    isolated_env: EnvTuple,
    mock_claude_agent: AgentDetectionResult,
) -> None:
    """Verify that dry-run creates zero disk artifacts across state, lockfile, WAL, and staging."""
    workspace_dir, store_dir, state_store, lock_mgr, wal_mgr, staging_mgr = isolated_env

    source = SourceSpec(
        source_type=SourceType.PACKAGE,
        package_name="@modelcontextprotocol/server-github",
        checksum="sha256:" + "a" * 64,
    )
    op = WriteFileOperation(
        description="Write dry run file",
        target_root=str(workspace_dir),
        file_path="dry_file.txt",
        content="dry",
        content_summary="dry",
        overwrite=True,
    )
    plan = InstallationPlan(
        addon_id="github-mcp",
        addon_name="GitHub MCP",
        addon_version="1.0.0",
        target_agent="claude-code",
        target_agent_name="Claude Code",
        target_scope=Scope.WORKSPACE,
        integration_type=IntegrationType.MCP,
        source=source,
        planned_operations=[op],
        risk_level=RiskLevel.LOW,
        reversible=True,
        rollback_info=RollbackMetadata(reversible=True),
    )
    tx = InstallationTransaction(
        transaction_id="tx_dry_run_test",
        phase=TransactionPhase.PLANNED,
        manifest=IntegrationManifest.model_validate({
            "id": "github-mcp",
            "name": "GitHub MCP",
            "version": "1.0.0",
            "description": "Test",
            "license": "MIT",
            "category": "developer-tools",
            "integration_type": "mcp",
            "target_agents": ["claude-code"],
            "source": source.model_dump(),
            "trust": {"verification_status": "verified", "publisher": {"name": "Test"}},
            "handler_spec": {"mcp": {"runtime": "npx", "package_name": "@test/pkg"}},
        }),
        agent=mock_claude_agent,
        requested_scope=Scope.WORKSPACE,
        plan=plan,
        is_dry_run=True,
    )

    acq_engine = AcquisitionEngine(staging_manager=staging_mgr)
    exec_engine = ExecutionEngine(
        wal_manager=wal_mgr,
        state_store=state_store,
        lockfile_manager=lock_mgr,
        acquisition_engine=acq_engine,
        workspace_dir=workspace_dir,
    )

    result = exec_engine.execute_plan(plan, transaction=tx, dry_run=True)
    assert result.status == ExecutionStatus.SUCCESS

    # 1. State database must be empty
    assert len(state_store.get_installed()) == 0

    # 2. Lockfile must NOT exist
    assert not (workspace_dir / "aiaddons.lock").exists()

    # 3. WAL logs must NOT exist
    assert len(wal_mgr.list_transactions()) == 0

    # 4. Staging directory must NOT exist
    assert not (staging_mgr.base_staging_dir / "tx_dry_run_test").exists()

    # 5. Target file must NOT exist
    assert not (workspace_dir / "dry_file.txt").exists()


def test_cli_install_and_doctor_ponytail_consistency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that CLI install of ponytail updates state and doctor reports healthy."""
    runner = CliRunner()
    home_dir = tmp_path / "home"
    home_dir.mkdir(parents=True)
    ws_dir = tmp_path / "ws"
    ws_dir.mkdir(parents=True)

    reg_path = Path("registry").resolve()
    monkeypatch.setattr(Path, "home", lambda: home_dir)
    monkeypatch.chdir(ws_dir)

    claude_cfg = home_dir / ".claude.json"
    claude_cfg.write_text('{\n  "mcpServers": {}\n}\n', encoding="utf-8")

    mock_agent = AgentDetectionResult(
        agent_id="claude-code",
        name="Claude Code",
        installed=True,
        version="1.0.0",
        global_config_path=str(claude_cfg),
        workspace_config_path=str(ws_dir / ".claude.json"),
        supported_scopes=[Scope.GLOBAL, Scope.WORKSPACE],
        capabilities=[AgentCapability.MCP, AgentCapability.SKILL],
    )
    monkeypatch.setattr(
        AgentDetectionManager,
        "detect_agents",
        lambda self, project_path=None: {"claude-code": mock_agent},
    )

    # Mock git clone / checkout during ponytail acquisition
    class MockGitFetcher(GitSourceFetcher):
        def fetch(
            self,
            manifest_id: str,
            source: SourceSpec,
            staging_dir: Path,
            dry_run: bool = False,
        ) -> AcquiredSourceResult:
            if not dry_run:
                skills_base = staging_dir / "skills"
                skills = [
                    "ponytail",
                    "ponytail-review",
                    "ponytail-audit",
                    "ponytail-debt",
                    "ponytail-gain",
                    "ponytail-help",
                ]
                for s in skills:
                    s_dir = skills_base / s
                    s_dir.mkdir(parents=True, exist_ok=True)
                    (s_dir / "SKILL.md").write_text(
                        f"---\nname: {s}\n---\n# {s}\n", encoding="utf-8"
                    )
            return AcquiredSourceResult(
                manifest_id=manifest_id,
                source_type=SourceType.GIT,
                staging_path=staging_dir,
                commit_sha=PONYTAIL_COMMIT_SHA,
                is_staged=not dry_run,
                is_dry_run=dry_run,
                acquired_files=["skills/ponytail/SKILL.md"],
            )

    monkeypatch.setattr(
        "aiaddons.core.acquisition.engine.GitSourceFetcher",
        MockGitFetcher,
    )

    # 1. Run CLI install ponytail
    res_install = runner.invoke(
        app, ["install", "ponytail", "--yes", "--json", "-r", str(reg_path)]
    )
    assert res_install.exit_code == 0, f"Install stdout: {res_install.stdout}"
    install_data = json.loads(res_install.stdout)
    assert install_data["success"] is True
    assert install_data["transaction_status"] == "COMMITTED"
    assert install_data["verification_status"] == "passed"

    # 2. Run CLI doctor
    res_doctor = runner.invoke(app, ["doctor", "--json"])
    doc_data = json.loads(res_doctor.stdout)
    assert doc_data["summary"]["FAIL"] == 0, f"Doctor items: {doc_data}"
    assert res_doctor.exit_code in (0, 2)

    # Verify doctor found installed ponytail in consistency check
    consistency_items = [i for i in doc_data["items"] if i["category"] == "consistency"]
    assert any(
        "All 1 installed add-on(s) are consistent" in i["message"] for i in consistency_items
    )
