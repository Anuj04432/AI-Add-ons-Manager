"""Comprehensive validation and integration test suite for Ponytail repository add-on."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from aiaddons.core.acquisition.git import GitSourceFetcher
from aiaddons.core.acquisition.models import AcquiredSourceResult
from aiaddons.core.compatibility.engine import CompatibilityEngine
from aiaddons.core.exceptions import (
    GitAcquisitionError,
    InstallationPlanningError,
)
from aiaddons.core.execution.engine import ExecutionEngine
from aiaddons.core.execution.external.models import ExternalExecutionResult, ExternalRuntime
from aiaddons.core.execution.external.runner import ExternalRunner
from aiaddons.core.execution.models import ExecutionStatus
from aiaddons.core.installer.engine import InstallationEngine
from aiaddons.core.installer.models import (
    AddPluginReferenceOperation,
    AddSkillOperation,
    CreateDirectoryOperation,
    InstallationPlan,
    ModifyJsonOperation,
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
from aiaddons.core.verification.engine import VerificationEngine, verify_path_security
from aiaddons.core.verification.models import VerificationStatus
from aiaddons.integrations.plugin import resolve_plugin_components
from aiaddons.registry.loader import RegistryLoader
from aiaddons.registry.registry import Registry
from tests.fixtures.ponytail_manifests import (
    PONYTAIL_COMMIT_SHA,
    PONYTAIL_REPO_URL,
    PONYTAIL_VERSION,
)

# ============================================================================
# 1. Manifest Validation Tests
# ============================================================================


def test_ponytail_real_registry_files_load_and_validate() -> None:
    """Verify that actual ponytail YAML files in registry/addons/ pass strict validation."""
    addons_dir = Path("registry/addons")
    loader = RegistryLoader()

    ponytail_main = loader.load_file(addons_dir / "ponytail.yaml")
    assert ponytail_main.id == "ponytail"
    assert ponytail_main.version == PONYTAIL_VERSION
    assert ponytail_main.integration_type == IntegrationType.PLUGIN
    assert ponytail_main.source.commit_sha == PONYTAIL_COMMIT_SHA
    assert ponytail_main.source.url == PONYTAIL_REPO_URL
    assert ponytail_main.handler_spec.plugin is not None
    assert len(ponytail_main.handler_spec.plugin.components) == 6

    expected_skills = [
        "ponytail-skill",
        "ponytail-review-skill",
        "ponytail-audit-skill",
        "ponytail-debt-skill",
        "ponytail-gain-skill",
        "ponytail-help-skill",
    ]
    for skill_id in expected_skills:
        skill_file = addons_dir / f"{skill_id}.yaml"
        assert skill_file.exists(), f"Skill manifest {skill_file} must exist"
        loaded = loader.load_file(skill_file)
        assert loaded.id == skill_id
        assert loaded.integration_type == IntegrationType.SKILL
        assert loaded.source.commit_sha == PONYTAIL_COMMIT_SHA
        assert loaded.handler_spec.skill is not None
        assert loaded.handler_spec.skill.skill_file.startswith("skills/")


def test_ponytail_manifest_rejects_dangerous_shell_operators(
    ponytail_composite_manifest: IntegrationManifest,
) -> None:
    """Ensure manifest validation strictly blocks any attempt to insert shell commands."""
    raw_dict = ponytail_composite_manifest.model_dump()
    raw_dict["handler_spec"]["plugin"]["components"] = ["ponytail-skill; rm -rf /"]

    with pytest.raises(ValueError, match="Shell operator ';' detected"):
        IntegrationManifest.model_validate(raw_dict)


def test_ponytail_manifest_rejects_forbidden_keyword_fields(
    ponytail_composite_manifest: IntegrationManifest,
) -> None:
    """Ensure manifest validation blocks forbidden fields like install_command or script."""
    from pydantic import ValidationError

    raw_dict = ponytail_composite_manifest.model_dump()
    raw_dict["install_command"] = "node hooks/ponytail-activate.js"

    with pytest.raises(ValidationError):
        IntegrationManifest.model_validate(raw_dict)


# ============================================================================
# 2. Git Source & Commit SHA Validation Tests
# ============================================================================


def test_ponytail_git_source_validation(ponytail_source: SourceSpec) -> None:
    """Verify that Ponytail git source passes strict cryptographic pinning checks."""
    assert ponytail_source.source_type == SourceType.GIT
    assert ponytail_source.url == "https://github.com/DietrichGebert/ponytail.git"
    assert ponytail_source.commit_sha == "0a4dd63ad4541f4f655c4108a295916f3c1d8fda"
    assert len(ponytail_source.commit_sha) == 40

    ok, warning = ponytail_source.check_installable()
    assert ok is True
    assert warning is None


def test_ponytail_git_source_rejects_short_or_invalid_commit_sha() -> None:
    """Verify that short or non-hexadecimal commit SHAs are rejected as uninstallable."""
    # 7-character short SHA
    short_source = SourceSpec(
        source_type=SourceType.GIT,
        url=PONYTAIL_REPO_URL,
        commit_sha="0a4dd63",
    )
    ok, warning = short_source.check_installable()
    assert ok is False
    assert "immutable 40-character commit_sha" in (warning or "")

    # Non-hexadecimal characters
    invalid_hex = SourceSpec(
        source_type=SourceType.GIT,
        url=PONYTAIL_REPO_URL,
        commit_sha="0a4dd63ad4541f4f655c4108a295916f3c1d8fdz",
    )
    ok, warning = invalid_hex.check_installable()
    assert ok is False


# ============================================================================
# 3. Registry Lookup & Search Tests
# ============================================================================


def test_ponytail_registry_lookup(ponytail_populated_registry: Registry) -> None:
    """Verify retrieving Ponytail manifest by exact ID and case-insensitive ID."""
    manifest = ponytail_populated_registry.get("ponytail")
    assert manifest is not None
    assert manifest.id == "ponytail"
    assert manifest.name == "Ponytail - Lazy Senior Dev Mode"

    # Case insensitivity
    upper_manifest = ponytail_populated_registry.get("PONYTAIL")
    assert upper_manifest is not None
    assert upper_manifest.id == "ponytail"


def test_ponytail_registry_search(ponytail_populated_registry: Registry) -> None:
    """Verify searching for Ponytail by name, tags, description, and keywords."""
    results = ponytail_populated_registry.search("ponytail")
    assert len(results) == 7

    # Search by tag 'yagni'
    yagni_results = ponytail_populated_registry.search("yagni")
    assert any(m.id == "ponytail" for m in yagni_results)
    assert any(m.id == "ponytail-skill" for m in yagni_results)

    # Search by tag 'code-review'
    review_results = ponytail_populated_registry.search("code-review")
    assert len(review_results) == 1
    assert review_results[0].id == "ponytail-review-skill"


# ============================================================================
# 4. Agent Compatibility & Capability Matching Tests
# ============================================================================


def test_ponytail_compatibility_claude_code(
    ponytail_composite_manifest: IntegrationManifest,
    ponytail_populated_registry: Registry,
    claude_code_agent: AgentDetectionResult,
) -> None:
    """Verify Ponytail composite plugin is compatible with Claude Code.

    Claude Code has AgentCapability.SKILL and AgentCapability.MCP, but NOT AgentCapability.PLUGIN.
    aiaddons treats PLUGIN as a composite domain abstraction, resolving to child skills.
    """
    assert AgentCapability.PLUGIN not in claude_code_agent.capabilities
    assert AgentCapability.SKILL in claude_code_agent.capabilities

    engine = CompatibilityEngine(registry=ponytail_populated_registry)

    # Workspace scope evaluation
    compat_ws = engine.evaluate(
        ponytail_composite_manifest,
        claude_code_agent,
        Scope.WORKSPACE,
        registry=ponytail_populated_registry,
    )
    assert compat_ws.compatible is True
    assert compat_ws.agent_id == "claude-code"
    assert len(compat_ws.missing_requirements) == 0

    # Global scope evaluation
    compat_global = engine.evaluate(
        ponytail_composite_manifest,
        claude_code_agent,
        Scope.GLOBAL,
        registry=ponytail_populated_registry,
    )
    assert compat_global.compatible is True


def test_ponytail_compatibility_codex(
    ponytail_composite_manifest: IntegrationManifest,
    ponytail_populated_registry: Registry,
    codex_agent: AgentDetectionResult,
) -> None:
    """Verify Ponytail composite plugin is compatible with OpenAI Codex."""
    engine = CompatibilityEngine(registry=ponytail_populated_registry)

    compat = engine.evaluate(
        ponytail_composite_manifest,
        codex_agent,
        Scope.WORKSPACE,
        registry=ponytail_populated_registry,
    )
    assert compat.compatible is True
    assert compat.agent_id == "codex"


def test_ponytail_compatibility_uninstalled_agent(
    ponytail_composite_manifest: IntegrationManifest,
    ponytail_populated_registry: Registry,
    claude_code_agent: AgentDetectionResult,
) -> None:
    """Verify that uninstalled agents are correctly flagged as incompatible."""
    uninstalled_agent = claude_code_agent.model_copy(update={"installed": False})
    engine = CompatibilityEngine(registry=ponytail_populated_registry)

    compat = engine.evaluate(
        ponytail_composite_manifest,
        uninstalled_agent,
        Scope.WORKSPACE,
        registry=ponytail_populated_registry,
    )
    assert compat.compatible is False
    assert any("not installed" in r for r in compat.reasons)


# ============================================================================
# 5. Composite Plugin Component Resolution Tests
# ============================================================================


def test_ponytail_component_resolution(
    ponytail_composite_manifest: IntegrationManifest,
    ponytail_populated_registry: Registry,
    claude_code_agent: AgentDetectionResult,
) -> None:
    """Verify resolving all 6 child skill components in the correct order."""
    compat_engine = CompatibilityEngine(registry=ponytail_populated_registry)
    components = resolve_plugin_components(
        manifest=ponytail_composite_manifest,
        registry=ponytail_populated_registry,
        agent=claude_code_agent,
        scope=Scope.WORKSPACE,
        compatibility_engine=compat_engine,
    )
    assert len(components) == 6
    component_ids = [c.id for c in components]
    assert component_ids == [
        "ponytail-skill",
        "ponytail-review-skill",
        "ponytail-audit-skill",
        "ponytail-debt-skill",
        "ponytail-gain-skill",
        "ponytail-help-skill",
    ]
    for c in components:
        assert c.integration_type == IntegrationType.SKILL


def test_ponytail_component_resolution_missing_child(
    ponytail_composite_manifest: IntegrationManifest,
    claude_code_agent: AgentDetectionResult,
) -> None:
    """Verify that missing child components raise InstallationPlanningError."""
    empty_registry = Registry(manifests=[ponytail_composite_manifest])
    compat_engine = CompatibilityEngine(registry=empty_registry)

    with pytest.raises(InstallationPlanningError, match="not found in registry"):
        resolve_plugin_components(
            manifest=ponytail_composite_manifest,
            registry=empty_registry,
            agent=claude_code_agent,
            scope=Scope.WORKSPACE,
            compatibility_engine=compat_engine,
        )


# ============================================================================
# 6. Installation Planning & Dry-Run Tests
# ============================================================================


def test_ponytail_dry_run_installation_plan(
    ponytail_composite_manifest: IntegrationManifest,
    ponytail_populated_registry: Registry,
    claude_code_agent: AgentDetectionResult,
) -> None:
    """Verify generating a dry-run InstallationPlan for Claude Code."""
    engine = InstallationEngine(registry=ponytail_populated_registry)
    plan = engine.generate_plan(
        manifest=ponytail_composite_manifest,
        agent=claude_code_agent,
        scope=Scope.WORKSPACE,
        registry=ponytail_populated_registry,
    )

    assert plan.addon_id == "ponytail"
    assert plan.target_agent == "claude-code"
    assert plan.target_scope == Scope.WORKSPACE
    assert plan.risk_level == RiskLevel.MEDIUM
    assert plan.reversible is True

    # Check planned operations: 6 skills * 2 ops + 3 plugin ops = 15 ops
    ops = plan.planned_operations
    assert len(ops) == 15

    skill_ops = [op for op in ops if isinstance(op, AddSkillOperation)]
    assert len(skill_ops) == 6
    assert any(op.skill_name == "Ponytail Core Skill" for op in skill_ops)

    plugin_dir_ops = [
        op
        for op in ops
        if isinstance(op, CreateDirectoryOperation) and "plugins" in op.directory_path
    ]
    assert len(plugin_dir_ops) == 1
    assert plugin_dir_ops[0].directory_path == ".agents/plugins/ponytail"

    plugin_ref_ops = [op for op in ops if isinstance(op, AddPluginReferenceOperation)]
    assert len(plugin_ref_ops) == 1
    assert plugin_ref_ops[0].plugin_id == "ponytail"
    assert len(plugin_ref_ops[0].component_ids) == 6

    json_ops = [op for op in ops if isinstance(op, ModifyJsonOperation)]
    assert len(json_ops) == 1
    assert json_ops[0].file_path == ".agents/plugins/ponytail/plugin.json"


def test_ponytail_transaction_creation(
    ponytail_composite_manifest: IntegrationManifest,
    ponytail_populated_registry: Registry,
    claude_code_agent: AgentDetectionResult,
) -> None:
    """Verify creating a dry-run InstallationTransaction."""
    engine = InstallationEngine(registry=ponytail_populated_registry)
    tx = engine.create_transaction(
        manifest=ponytail_composite_manifest,
        agent=claude_code_agent,
        scope=Scope.WORKSPACE,
        registry=ponytail_populated_registry,
    )

    assert tx.phase == TransactionPhase.PLANNED
    assert tx.is_dry_run is True
    assert tx.plan is not None
    assert tx.error_message is None


# ============================================================================
# 7. Git Source Acquisition & Staging Isolation Tests
# ============================================================================


def test_ponytail_source_acquisition_dry_run(
    ponytail_source: SourceSpec, tmp_path: Path
) -> None:
    """Verify GitSourceFetcher dry-run returns immediately with zero side-effects."""
    fetcher = GitSourceFetcher()
    staging_dir = tmp_path / "staging_ponytail"

    result = fetcher.fetch(
        manifest_id="ponytail",
        source=ponytail_source,
        staging_dir=staging_dir,
        dry_run=True,
    )

    assert isinstance(result, AcquiredSourceResult)
    assert result.is_dry_run is True
    assert result.is_staged is False
    assert not staging_dir.exists()


def test_ponytail_source_acquisition_mocked_git(
    ponytail_source: SourceSpec, tmp_path: Path
) -> None:
    """Verify GitSourceFetcher executes git clone and detached checkout with security flags."""
    mock_runner = MagicMock(spec=ExternalRunner)

    # 1. Clone returns success
    # 2. Checkout returns success
    # 3. Rev-parse returns exact commit SHA
    mock_runner.execute.side_effect = [
        ExternalExecutionResult(
            success=True,
            runtime=ExternalRuntime.GIT,
            executable_path="/usr/bin/git",
            command_vector=["git", "clone"],
            return_code=0,
            stdout="",
            stderr="",
            duration=0.5,
        ),
        ExternalExecutionResult(
            success=True,
            runtime=ExternalRuntime.GIT,
            executable_path="/usr/bin/git",
            command_vector=["git", "checkout"],
            return_code=0,
            stdout="",
            stderr="",
            duration=0.2,
        ),
        ExternalExecutionResult(
            success=True,
            runtime=ExternalRuntime.GIT,
            executable_path="/usr/bin/git",
            command_vector=["git", "rev-parse", "HEAD"],
            return_code=0,
            stdout=PONYTAIL_COMMIT_SHA + "\n",
            stderr="",
            duration=0.1,
        ),
    ]

    staging_dir = tmp_path / "staged_ponytail"
    staging_dir.mkdir(parents=True)
    (staging_dir / "skills").mkdir()
    (staging_dir / "skills" / "SKILL.md").write_text("# Ponytail", encoding="utf-8")

    fetcher = GitSourceFetcher(runner=mock_runner)
    result = fetcher.fetch(
        manifest_id="ponytail",
        source=ponytail_source,
        staging_dir=staging_dir,
        dry_run=False,
    )

    assert result.is_staged is True
    assert result.commit_sha == PONYTAIL_COMMIT_SHA
    assert mock_runner.execute.call_count == 3

    # Check security flags in clone invocation
    clone_call_args = mock_runner.execute.call_args_list[0][0][0]
    assert "core.hooksPath=/dev/null" in clone_call_args.args
    assert "--no-recurse-submodules" in clone_call_args.args
    assert "--no-checkout" in clone_call_args.args


def test_ponytail_source_acquisition_mismatched_sha_error(
    ponytail_source: SourceSpec, tmp_path: Path
) -> None:
    """Verify that if Git HEAD does not match the pinned SHA, acquisition fails securely."""
    mock_runner = MagicMock(spec=ExternalRunner)
    mock_runner.execute.side_effect = [
        ExternalExecutionResult(
            success=True,
            runtime=ExternalRuntime.GIT,
            executable_path="/usr/bin/git",
            command_vector=[],
            return_code=0,
            stdout="",
            stderr="",
            duration=0.1,
        ),
        ExternalExecutionResult(
            success=True,
            runtime=ExternalRuntime.GIT,
            executable_path="/usr/bin/git",
            command_vector=[],
            return_code=0,
            stdout="",
            stderr="",
            duration=0.1,
        ),
        ExternalExecutionResult(
            success=True,
            runtime=ExternalRuntime.GIT,
            executable_path="/usr/bin/git",
            command_vector=[],
            return_code=0,
            stdout="1111111111111111111111111111111111111111\n",
            stderr="",
            duration=0.1,
        ),
    ]

    staging_dir = tmp_path / "staged_ponytail_bad"
    staging_dir.mkdir(parents=True)

    fetcher = GitSourceFetcher(runner=mock_runner)
    with pytest.raises(GitAcquisitionError, match="Security violation: Git HEAD"):
        fetcher.fetch(
            manifest_id="ponytail",
            source=ponytail_source,
            staging_dir=staging_dir,
            dry_run=False,
        )


# ============================================================================
# 8. Verification Engine & Path Security Tests
# ============================================================================


def test_ponytail_verification_engine_path_security(
    ponytail_composite_manifest: IntegrationManifest,
    ponytail_populated_registry: Registry,
    claude_code_agent: AgentDetectionResult,
    tmp_path: Path,
) -> None:
    """Verify VerificationEngine validates operations and enforces strict path boundaries."""
    engine = InstallationEngine(registry=ponytail_populated_registry)
    plan = engine.generate_plan(
        manifest=ponytail_composite_manifest,
        agent=claude_code_agent,
        scope=Scope.WORKSPACE,
        registry=ponytail_populated_registry,
    )

    verifier = VerificationEngine()
    # Dry-run plan verification
    plan_result = verifier.verify_plan(plan, dry_run=True)
    assert plan_result.status == VerificationStatus.PASSED
    assert plan_result.verified is True
    assert len(plan_result.checks) > 0

    # Path security validation on all operations
    for op in plan.planned_operations:
        resolved_root, resolved_dest = verify_path_security(
            str(tmp_path), op.target_path or ""
        )
        assert resolved_root == tmp_path.resolve()
        assert resolved_dest.is_relative_to(resolved_root)


# ============================================================================
# 9. Execution, Transaction Rollback & Zero Residue Tests
# ============================================================================


def test_ponytail_execution_and_rollback_flow(
    tmp_path: Path,
    ponytail_source: SourceSpec,
) -> None:
    """Verify executing operations against a sandbox directory and rolling them back completely."""
    target_root = str(tmp_path)
    op1 = CreateDirectoryOperation(
        description="Create plugin dir",
        target_root=target_root,
        directory_path=".agents/plugins/ponytail",
    )
    # Create a conflict: make a directory at failing_plugin.json so WriteFileOperation fails
    (tmp_path / "failing_plugin.json").mkdir(parents=True)
    op_conflict = WriteFileOperation(
        description="Write failing file",
        target_root=target_root,
        file_path="failing_plugin.json",
        content="test-data",
        content_summary="test-data",
        overwrite=True,
    )
    plan_with_failure = InstallationPlan(
        addon_id="ponytail",
        addon_name="Ponytail",
        addon_version="4.9.0",
        target_agent="claude-code",
        target_agent_name="Claude Code",
        target_scope=Scope.WORKSPACE,
        integration_type=IntegrationType.PLUGIN,
        source=ponytail_source,
        planned_operations=[op1, op_conflict],
        risk_level=RiskLevel.MEDIUM,
        reversible=True,
        rollback_info=RollbackMetadata(reversible=True),
    )

    mock_runner = MagicMock(spec=ExternalRunner)
    mock_runner.execute.side_effect = [
        ExternalExecutionResult(
            success=True,
            runtime=ExternalRuntime.GIT,
            executable_path="/usr/bin/git",
            command_vector=[],
            return_code=0,
            stdout="",
            stderr="",
            duration=0.1,
        ),
        ExternalExecutionResult(
            success=True,
            runtime=ExternalRuntime.GIT,
            executable_path="/usr/bin/git",
            command_vector=[],
            return_code=0,
            stdout="",
            stderr="",
            duration=0.1,
        ),
        ExternalExecutionResult(
            success=True,
            runtime=ExternalRuntime.GIT,
            executable_path="/usr/bin/git",
            command_vector=[],
            return_code=0,
            stdout=PONYTAIL_COMMIT_SHA + "\n",
            stderr="",
            duration=0.1,
        ),
    ]

    exec_engine = ExecutionEngine(external_runner=mock_runner)
    result = exec_engine.execute_plan(plan_with_failure, dry_run=False)

    # Status must be ROLLED_BACK
    assert result.status == ExecutionStatus.ROLLED_BACK
    # op1 should be rolled back and directory cleaned up
    assert not (tmp_path / ".agents/plugins/ponytail").exists()


def test_ponytail_git_fetcher_real_runner_argument_ordering_regression(
    ponytail_source: SourceSpec, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test asserting that GitSourceFetcher with ExternalRunner:
    1. Does NOT fail with 'Invalid git repository URL -c'
    2. Correctly preserves repo URL as https://github.com/DietrichGebert/ponytail.git
    3. Correctly orders '-c core.hooksPath=/dev/null clone ...' in the subprocess argv.
    """
    captured_commands: list[list[str]] = []

    def mock_popen(cmd: list[str], **kwargs: object) -> MagicMock:
        captured_commands.append(cmd)
        proc = MagicMock()
        proc.returncode = 0
        if "rev-parse" in cmd:
            proc.communicate.return_value = (PONYTAIL_COMMIT_SHA + "\n", "")
        else:
            proc.communicate.return_value = ("", "")
        return proc

    monkeypatch.setattr("subprocess.Popen", mock_popen)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/git" if "git" in name else None)

    staging_dir = tmp_path / "staged_ponytail_real_runner"
    staging_dir.mkdir(parents=True)
    (staging_dir / "skills").mkdir()
    (staging_dir / "skills" / "SKILL.md").write_text("# Ponytail", encoding="utf-8")

    real_runner = ExternalRunner()
    fetcher = GitSourceFetcher(runner=real_runner)
    result = fetcher.fetch(
        manifest_id="ponytail",
        source=ponytail_source,
        staging_dir=staging_dir,
        dry_run=False,
    )

    assert result.is_staged is True
    assert result.commit_sha == PONYTAIL_COMMIT_SHA
    assert len(captured_commands) == 3

    # Command 1: Git Clone
    clone_cmd = captured_commands[0]
    assert clone_cmd[0].endswith("git")
    assert clone_cmd[1] == "-c"
    assert clone_cmd[2] == "core.hooksPath=/dev/null"
    assert clone_cmd[3] == "clone"
    assert clone_cmd[4] == "--no-checkout"
    assert clone_cmd[5] == "--no-recurse-submodules"
    assert clone_cmd[6] == "https://github.com/DietrichGebert/ponytail.git"
    assert clone_cmd[7] == str(staging_dir)

    # Command 2: Git Checkout
    checkout_cmd = captured_commands[1]
    assert checkout_cmd[0].endswith("git")
    assert checkout_cmd[1] == "-c"
    assert checkout_cmd[2] == "core.hooksPath=/dev/null"
    assert checkout_cmd[3] == "checkout"
    assert checkout_cmd[4] == "--detach"
    assert checkout_cmd[5] == PONYTAIL_COMMIT_SHA

    # Command 3: Git Rev-Parse
    rev_cmd = captured_commands[2]
    assert rev_cmd[0].endswith("git")
    assert rev_cmd[1] == "rev-parse"
    assert rev_cmd[2] == "HEAD"
