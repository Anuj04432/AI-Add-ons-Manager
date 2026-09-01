"""Comprehensive unit tests for Phase 5B.9 Installation Verification & Post-Install Integrity."""

import json
from pathlib import Path

import pytest
import yaml

from aiaddons.core.exceptions import VerificationPathSecurityError
from aiaddons.core.execution.engine import ExecutionEngine
from aiaddons.core.execution.models import ExecutionStatus
from aiaddons.core.installer.models import (
    AddMcpServerOperation,
    AddPluginReferenceOperation,
    AddSkillOperation,
    BaseOperation,
    CopyFileOperation,
    CreateDirectoryOperation,
    InstallationPlan,
    InstallationTransaction,
    ModifyJsonOperation,
    ModifyYamlOperation,
    RiskLevel,
    RollbackMetadata,
    RollbackOperation,
    TransactionPhase,
    WriteFileOperation,
)
from aiaddons.core.models.agent import AgentCapability, AgentDetectionResult, Scope
from aiaddons.core.models.manifest import IntegrationManifest, MCPRuntime, MCPTransport, SourceSpec
from aiaddons.core.verification.engine import VerificationEngine, verify_path_security
from aiaddons.core.verification.models import VerificationResult, VerificationStatus
from aiaddons.state.lockfile import LockfileManager
from aiaddons.state.store import InstalledStateStore
from aiaddons.state.transaction import TransactionWALManager


def _create_sample_plan(target_root: Path, ops: list[BaseOperation]) -> InstallationPlan:
    """Helper to construct an InstallationPlan for testing."""
    return InstallationPlan(
        addon_id="test-addon",
        addon_name="Test Addon",
        addon_version="1.0.0",
        target_agent="claude-code",
        target_agent_name="Claude Code",
        target_scope=Scope.WORKSPACE,
        integration_type="mcp",  # type: ignore[arg-type]
        source=SourceSpec(
            source_type="package",  # type: ignore[arg-type]
            package_name="@test/addon",
            checksum="sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        ),
        planned_operations=ops,
        risk_level=RiskLevel.LOW,
        reversible=True,
        rollback_info=RollbackMetadata(
            reversible=True,
            rollback_operations=[
                RollbackOperation(
                    op_type="remove_file",
                    description="Clean test file",
                    target_root=".",
                    target_path="config.json",
                )
            ],
        ),
    )


# ============================================================================
# 1. Path & Symlink Security Tests
# ============================================================================


def test_verify_path_security_valid(tmp_path: Path) -> None:
    root, dest = verify_path_security(str(tmp_path), "subdir/config.json")
    assert root == tmp_path.resolve()
    assert dest == (tmp_path / "subdir" / "config.json").resolve()


def test_verify_path_security_null_bytes(tmp_path: Path) -> None:
    with pytest.raises(VerificationPathSecurityError, match="Null byte"):
        verify_path_security(str(tmp_path), "file\0name.json")


def test_verify_path_security_unc_path(tmp_path: Path) -> None:
    with pytest.raises(VerificationPathSecurityError, match="UNC path"):
        verify_path_security(str(tmp_path), "//evil/share/file.json")


def test_verify_path_security_url_encoding(tmp_path: Path) -> None:
    with pytest.raises(VerificationPathSecurityError, match="URL-encoded traversal"):
        verify_path_security(str(tmp_path), "%2e%2e/etc/passwd")


def test_verify_path_security_escape_target_root(tmp_path: Path) -> None:
    with pytest.raises(VerificationPathSecurityError, match="Security validation error"):
        verify_path_security(str(tmp_path), "../outside.json")


# ============================================================================
# 2. Structural Operation Verification Tests
# ============================================================================


def test_verification_create_directory_success(tmp_path: Path) -> None:
    dir_path = tmp_path / "created_dir"
    dir_path.mkdir(parents=True)

    engine = VerificationEngine()
    op = CreateDirectoryOperation(
        description="Create directory test",
        target_root=str(tmp_path),
        directory_path="created_dir",
    )
    check = engine.verify_operation(op)
    assert check.status == VerificationStatus.PASSED


def test_verification_create_directory_missing(tmp_path: Path) -> None:
    engine = VerificationEngine()
    op = CreateDirectoryOperation(
        description="Create directory test",
        target_root=str(tmp_path),
        directory_path="nonexistent_dir",
    )
    check = engine.verify_operation(op)
    assert check.status == VerificationStatus.FAILED
    assert "was not found" in (check.error_info or "")


def test_verification_write_file_hash_match(tmp_path: Path) -> None:
    file_path = tmp_path / "test.txt"
    content = "Hello, Verification!"
    file_path.write_text(content, encoding="utf-8")

    engine = VerificationEngine()
    op = WriteFileOperation(
        description="Write file test",
        target_root=str(tmp_path),
        file_path="test.txt",
        content=content,
        content_summary="Hello world text",
    )
    check = engine.verify_operation(op)
    assert check.status == VerificationStatus.PASSED


def test_verification_write_file_hash_mismatch(tmp_path: Path) -> None:
    file_path = tmp_path / "test.txt"
    file_path.write_text("Modified content!", encoding="utf-8")

    engine = VerificationEngine()
    op = WriteFileOperation(
        description="Write file test",
        target_root=str(tmp_path),
        file_path="test.txt",
        content="Original content",
        content_summary="Hello world text",
    )
    check = engine.verify_operation(op)
    assert check.status == VerificationStatus.FAILED
    assert "hash mismatch" in (check.error_info or "")


def test_verification_copy_file_success(tmp_path: Path) -> None:
    src_file = tmp_path / "src.txt"
    dst_file = tmp_path / "dst.txt"
    src_file.write_text("Same content", encoding="utf-8")
    dst_file.write_text("Same content", encoding="utf-8")

    engine = VerificationEngine()
    op = CopyFileOperation(
        description="Copy file test",
        target_root=str(tmp_path),
        source_path=str(src_file),
        destination_path="dst.txt",
    )
    check = engine.verify_operation(op)
    assert check.status == VerificationStatus.PASSED


def test_verification_modify_json_success(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"app": {"name": "aiaddons"}}), encoding="utf-8")

    engine = VerificationEngine()
    op = ModifyJsonOperation(
        description="Modify JSON test",
        target_root=str(tmp_path),
        file_path="config.json",
        json_path="app.name",
        value="aiaddons",
        value_summary="app.name = aiaddons",
    )
    check = engine.verify_operation(op)
    assert check.status == VerificationStatus.PASSED


def test_verification_modify_yaml_success(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.safe_dump({"app": {"version": "1.0"}}), encoding="utf-8")

    engine = VerificationEngine()
    op = ModifyYamlOperation(
        description="Modify YAML test",
        target_root=str(tmp_path),
        file_path="config.yaml",
        yaml_path="app.version",
        value="1.0",
        value_summary="app.version = 1.0",
    )
    check = engine.verify_operation(op)
    assert check.status == VerificationStatus.PASSED


# ============================================================================
# 3. MCP Verification Tests
# ============================================================================


def test_mcp_verification_success(tmp_path: Path) -> None:
    cfg_path = tmp_path / ".claude.json"
    mcp_config = {
        "mcpServers": {
            "github-mcp": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
                "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_PERSONAL_ACCESS_TOKEN}"},
            }
        }
    }
    cfg_path.write_text(json.dumps(mcp_config), encoding="utf-8")

    engine = VerificationEngine()
    op = AddMcpServerOperation(
        description="Inject GitHub MCP Server",
        target_root=str(tmp_path),
        server_name="github-mcp",
        runtime=MCPRuntime.NPX,
        package_name="@modelcontextprotocol/server-github",
        transport=MCPTransport.STDIO,
        env_var_names=["GITHUB_PERSONAL_ACCESS_TOKEN"],
        config_path=".claude.json",
    )
    secrets = {"GITHUB_PERSONAL_ACCESS_TOKEN": "secret_123"}
    check = engine.verify_operation(op, secret_values=secrets)
    assert check.status == VerificationStatus.PASSED
    assert "secret_123" not in (check.expected_value or "")
    assert "secret_123" not in (check.actual_value or "")


def test_mcp_verification_missing_server(tmp_path: Path) -> None:
    cfg_path = tmp_path / ".claude.json"
    cfg_path.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")

    engine = VerificationEngine()
    op = AddMcpServerOperation(
        description="Inject Missing MCP Server",
        target_root=str(tmp_path),
        server_name="github-mcp",
        runtime=MCPRuntime.NPX,
        package_name="@modelcontextprotocol/server-github",
        config_path=".claude.json",
    )
    check = engine.verify_operation(op)
    assert check.status == VerificationStatus.FAILED
    assert "was not found in configuration" in (check.error_info or "")


def test_mcp_verification_incorrect_runtime(tmp_path: Path) -> None:
    cfg_path = tmp_path / ".claude.json"
    mcp_config = {
        "mcpServers": {
            "github-mcp": {
                "command": "node",  # Incorrect, expected npx
                "args": ["@modelcontextprotocol/server-github"],
            }
        }
    }
    cfg_path.write_text(json.dumps(mcp_config), encoding="utf-8")

    engine = VerificationEngine()
    op = AddMcpServerOperation(
        description="Inject GitHub MCP Server",
        target_root=str(tmp_path),
        server_name="github-mcp",
        runtime=MCPRuntime.NPX,
        package_name="@modelcontextprotocol/server-github",
        config_path=".claude.json",
    )
    check = engine.verify_operation(op)
    assert check.status == VerificationStatus.FAILED
    assert "runtime mismatch" in (check.error_info or "")


def test_mcp_verification_malformed_config(tmp_path: Path) -> None:
    cfg_path = tmp_path / ".claude.json"
    cfg_path.write_text("{malformed json", encoding="utf-8")

    engine = VerificationEngine()
    op = AddMcpServerOperation(
        description="Inject MCP Server",
        target_root=str(tmp_path),
        server_name="github-mcp",
        runtime=MCPRuntime.NPX,
        package_name="@modelcontextprotocol/server-github",
        config_path=".claude.json",
    )
    check = engine.verify_operation(op)
    assert check.status == VerificationStatus.FAILED
    assert "Malformed configuration file" in (check.error_info or "")


def test_mcp_verification_secret_matched_success(tmp_path: Path) -> None:
    cfg_path = tmp_path / ".claude.json"
    mcp_config = {
        "mcpServers": {
            "github-mcp": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
                "env": {"GITHUB_TOKEN": "ghp_raw_secret_token_value_123"},
            }
        }
    }
    cfg_path.write_text(json.dumps(mcp_config), encoding="utf-8")

    engine = VerificationEngine()
    op = AddMcpServerOperation(
        description="Inject GitHub MCP Server",
        target_root=str(tmp_path),
        server_name="github-mcp",
        runtime=MCPRuntime.NPX,
        package_name="@modelcontextprotocol/server-github",
        env_var_names=["GITHUB_TOKEN"],
        config_path=".claude.json",
    )
    sec_dict = {"GITHUB_TOKEN": "ghp_raw_secret_token_value_123"}
    check = engine.verify_operation(op, secret_values=sec_dict)
    assert check.status == VerificationStatus.PASSED


def test_mcp_verification_secret_mismatch_detected(tmp_path: Path) -> None:
    cfg_path = tmp_path / ".claude.json"
    mcp_config = {
        "mcpServers": {
            "github-mcp": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
                "env": {"GITHUB_TOKEN": "wrong_mismatched_token"},
            }
        }
    }
    cfg_path.write_text(json.dumps(mcp_config), encoding="utf-8")

    engine = VerificationEngine()
    op = AddMcpServerOperation(
        description="Inject GitHub MCP Server",
        target_root=str(tmp_path),
        server_name="github-mcp",
        runtime=MCPRuntime.NPX,
        package_name="@modelcontextprotocol/server-github",
        env_var_names=["GITHUB_TOKEN"],
        config_path=".claude.json",
    )
    sec_dict = {"GITHUB_TOKEN": "ghp_raw_secret_token_value_123"}
    check = engine.verify_operation(op, secret_values=sec_dict)
    assert check.status == VerificationStatus.FAILED
    assert "does not match resolved secret value" in (check.error_info or "")


# ============================================================================
# 4. Skill Verification Tests
# ============================================================================


def test_skill_verification_success(tmp_path: Path) -> None:
    skill_dir = tmp_path / ".claude" / "skills" / "pdf-reader"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# PDF Skill", encoding="utf-8")
    (skill_dir / "helper.py").write_text("print('hello')", encoding="utf-8")

    engine = VerificationEngine()
    op = AddSkillOperation(
        description="Add PDF Skill",
        target_root=str(tmp_path),
        skill_name="pdf-reader",
        skill_file="SKILL.md",
        destination_dir=".claude/skills/pdf-reader",
        supporting_files=["helper.py"],
        skill_content="# PDF Skill",
        supporting_contents={"helper.py": "print('hello')"},
    )
    check = engine.verify_operation(op)
    assert check.status == VerificationStatus.PASSED


def test_skill_verification_missing_skill_md(tmp_path: Path) -> None:
    skill_dir = tmp_path / ".claude" / "skills" / "pdf-reader"
    skill_dir.mkdir(parents=True)

    engine = VerificationEngine()
    op = AddSkillOperation(
        description="Add PDF Skill",
        target_root=str(tmp_path),
        skill_name="pdf-reader",
        skill_file="SKILL.md",
        destination_dir=".claude/skills/pdf-reader",
    )
    check = engine.verify_operation(op)
    assert check.status == VerificationStatus.FAILED
    assert "missing" in (check.error_info or "")


def test_skill_verification_supporting_file_mismatch(tmp_path: Path) -> None:
    skill_dir = tmp_path / ".claude" / "skills" / "pdf-reader"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# PDF Skill", encoding="utf-8")
    (skill_dir / "helper.py").write_text("modified code", encoding="utf-8")

    engine = VerificationEngine()
    op = AddSkillOperation(
        description="Add PDF Skill",
        target_root=str(tmp_path),
        skill_name="pdf-reader",
        skill_file="SKILL.md",
        destination_dir=".claude/skills/pdf-reader",
        supporting_files=["helper.py"],
        supporting_contents={"helper.py": "expected code"},
    )
    check = engine.verify_operation(op)
    assert check.status == VerificationStatus.FAILED
    assert "hash mismatch" in (check.error_info or "")


# ============================================================================
# 5. Plugin Verification Tests
# ============================================================================


def test_plugin_verification_success(tmp_path: Path) -> None:
    plugin_file = tmp_path / ".agents" / "plugins" / "my-plugin" / "plugin.json"
    plugin_file.parent.mkdir(parents=True)
    plugin_file.write_text(
        json.dumps({"id": "my-plugin", "components": ["mcp-server-1", "skill-1"]}),
        encoding="utf-8",
    )

    engine = VerificationEngine()
    op = AddPluginReferenceOperation(
        description="Register Plugin",
        target_root=str(tmp_path),
        plugin_id="my-plugin",
        component_ids=["mcp-server-1", "skill-1"],
        config_path=".agents/plugins/my-plugin/plugin.json",
    )
    check = engine.verify_operation(op)
    assert check.status == VerificationStatus.PASSED


def test_plugin_verification_missing_component(tmp_path: Path) -> None:
    plugin_file = tmp_path / ".agents" / "plugins" / "my-plugin" / "plugin.json"
    plugin_file.parent.mkdir(parents=True)
    plugin_file.write_text(
        json.dumps({"id": "my-plugin", "components": ["mcp-server-1"]}),
        encoding="utf-8",
    )

    engine = VerificationEngine()
    op = AddPluginReferenceOperation(
        description="Register Plugin",
        target_root=str(tmp_path),
        plugin_id="my-plugin",
        component_ids=["mcp-server-1", "missing-skill"],
        config_path=".agents/plugins/my-plugin/plugin.json",
    )
    check = engine.verify_operation(op)
    assert check.status == VerificationStatus.FAILED
    assert "missing from descriptor" in (check.error_info or "")


# ============================================================================
# 6. Transaction & Lifecycle Integration Tests
# ============================================================================


def _create_sample_agent() -> AgentDetectionResult:
    return AgentDetectionResult(
        agent_id="claude-code",
        name="Claude Code",
        installed=True,
        version="1.0.0",
        global_config_path="~/.claude.json",
        workspace_config_path=".claude.json",
        supported_scopes=[Scope.GLOBAL, Scope.WORKSPACE],
        capabilities=[AgentCapability.MCP, AgentCapability.SKILL],
    )


def _create_sample_manifest() -> IntegrationManifest:
    return IntegrationManifest.model_validate(
        {
            "id": "test-addon",
            "name": "Test Addon",
            "version": "1.0.0",
            "description": "A test add-on for verification",
            "license": "MIT",
            "category": "developer-tools",
            "integration_type": "mcp",
            "target_agents": ["claude-code"],
            "supported_scopes": ["workspace"],
            "source": {
                "source_type": "package",
                "package_name": "@test/addon",
                "checksum": "sha256:" + "a" * 64,
            },
            "trust": {
                "verification_status": "verified",
                "publisher": {"name": "Test Team"},
            },
            "handler_spec": {
                "mcp": {
                    "transport": "stdio",
                    "runtime": "npx",
                    "package_name": "@test/addon",
                }
            },
        }
    )


def test_transaction_execution_verification_success(tmp_path: Path) -> None:
    wal_dir = tmp_path / "transactions"
    state_dir = tmp_path / "state"
    wal_mgr = TransactionWALManager(transactions_dir=wal_dir)
    state_store = InstalledStateStore(store_dir=state_dir)
    lock_mgr = LockfileManager()

    engine = ExecutionEngine(
        wal_manager=wal_mgr,
        state_store=state_store,
        lockfile_manager=lock_mgr,
    )

    file_op = WriteFileOperation(
        description="Write config file",
        target_root=str(tmp_path),
        file_path="config.txt",
        content="valid_content",
        content_summary="config content",
    )
    plan = _create_sample_plan(tmp_path, [file_op])

    tx = InstallationTransaction(
        transaction_id="tx_test_1",
        phase=TransactionPhase.PLANNED,
        manifest=_create_sample_manifest(),
        agent=_create_sample_agent(),
        requested_scope=Scope.WORKSPACE,
        plan=plan,
        is_dry_run=False,
    )

    result = engine.execute_plan(plan, transaction=tx, dry_run=False)
    assert result.status == ExecutionStatus.SUCCESS
    assert tx.phase == TransactionPhase.COMMITTED

    # Verify installed state was persisted AFTER verification passed
    records = state_store.get_installed()
    assert len(records) == 1
    assert records[0].addon_id == "test-addon"


def test_transaction_verification_failure_rollback(tmp_path: Path) -> None:
    wal_dir = tmp_path / "transactions"
    state_dir = tmp_path / "state"
    wal_mgr = TransactionWALManager(transactions_dir=wal_dir)
    state_store = InstalledStateStore(store_dir=state_dir)

    engine = ExecutionEngine(
        wal_manager=wal_mgr,
        state_store=state_store,
    )

    # Use a mock VerificationEngine that returns verification FAILED
    class FailingVerificationEngine(VerificationEngine):
        def verify_plan(
            self,
            plan: InstallationPlan,
            dry_run: bool = False,
            secret_values: dict[str, str] | None = None,
        ) -> VerificationResult:
            res = super().verify_plan(plan, dry_run=dry_run, secret_values=secret_values)
            res.verified = False
            res.status = VerificationStatus.FAILED
            res.errors = ["Simulated post-install verification failure"]
            return res

    engine.verification_engine = FailingVerificationEngine()

    file_op = WriteFileOperation(
        description="Write config file",
        target_root=str(tmp_path),
        file_path="config.txt",
        content="content",
        content_summary="content",
    )
    plan = _create_sample_plan(tmp_path, [file_op])

    tx = InstallationTransaction(
        transaction_id="tx_test_fail",
        phase=TransactionPhase.PLANNED,
        manifest=_create_sample_manifest(),
        agent=_create_sample_agent(),
        requested_scope=Scope.WORKSPACE,
        plan=plan,
        is_dry_run=False,
    )

    result = engine.execute_plan(plan, transaction=tx, dry_run=False)
    assert result.status == ExecutionStatus.ROLLED_BACK
    assert tx.phase == TransactionPhase.ROLLED_BACK

    # Verify state database was NOT persisted
    records = state_store.get_installed()
    assert len(records) == 0

    # Verify file was rolled back
    assert not (tmp_path / "config.txt").exists()


# ============================================================================
# 7. Dry-Run Behavior Tests
# ============================================================================


def test_dry_run_verification(tmp_path: Path) -> None:
    file_op = WriteFileOperation(
        description="Write config file",
        target_root=str(tmp_path),
        file_path="dry_run.txt",
        content="dry_run_content",
        content_summary="dry run summary",
    )
    plan = _create_sample_plan(tmp_path, [file_op])

    v_engine = VerificationEngine()
    result = v_engine.verify_plan(plan, dry_run=True)

    assert result.verified is True
    assert result.status == VerificationStatus.PASSED
    assert len(result.checks) == 1
    assert result.checks[0].status == VerificationStatus.SKIPPED
    assert "PLAN ONLY" in result.checks[0].description

    # Ensure no file was created on disk
    assert not (tmp_path / "dry_run.txt").exists()
