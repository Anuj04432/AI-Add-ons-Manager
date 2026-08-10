"""Unit tests for Phase 5A installation models, operations, rollback, and security constraints."""

import pytest
from pydantic import ValidationError

from aiaddons.core.installer.models import (
    AddMcpServerOperation,
    AddPluginReferenceOperation,
    AddSkillOperation,
    CopyFileOperation,
    CreateDirectoryOperation,
    InstallationPlan,
    InstallationTransaction,
    ModifyJsonOperation,
    ModifyYamlOperation,
    OperationType,
    RiskLevel,
    RollbackMetadata,
    RollbackOperation,
    TransactionPhase,
    WriteFileOperation,
)
from aiaddons.core.models.agent import Scope
from aiaddons.core.models.manifest import (
    IntegrationType,
    MCPRuntime,
    MCPTransport,
    SourceSpec,
    SourceType,
)


def test_operation_models_instantiation() -> None:
    """Verify all typed installation operation models instantiate correctly with required fields."""
    target_root = "."

    create_dir = CreateDirectoryOperation(
        description="Create directory",
        target_root=target_root,
        directory_path="skills/my-skill",
    )
    assert create_dir.op_type == OperationType.CREATE_DIRECTORY
    assert create_dir.directory_path == "skills/my-skill"
    assert create_dir.target_root == "."

    copy_file = CopyFileOperation(
        description="Copy file",
        target_root=target_root,
        source_path="src/file.txt",
        destination_path="dst/file.txt",
    )
    assert copy_file.op_type == OperationType.COPY_FILE

    write_file = WriteFileOperation(
        description="Write file",
        target_root=target_root,
        file_path="dst/config.json",
        content='{"key": "value"}',
        content_summary="Config JSON",
        overwrite=True,
    )
    assert write_file.op_type == OperationType.WRITE_FILE
    assert write_file.content == '{"key": "value"}'
    assert write_file.overwrite is True

    modify_json = ModifyJsonOperation(
        description="Modify JSON",
        target_root=target_root,
        file_path="config.json",
        json_path="mcpServers.test",
        value={"command": "npx"},
        value_summary="{...}",
    )
    assert modify_json.op_type == OperationType.MODIFY_JSON
    assert modify_json.value == {"command": "npx"}

    modify_yaml = ModifyYamlOperation(
        description="Modify YAML",
        target_root=target_root,
        file_path="config.yaml",
        yaml_path="settings.plugin",
        value={"enabled": True},
        value_summary="{...}",
    )
    assert modify_yaml.op_type == OperationType.MODIFY_YAML

    add_mcp = AddMcpServerOperation(
        description="Add MCP Server",
        target_root="~",
        server_name="github",
        runtime=MCPRuntime.NPX,
        package_name="@modelcontextprotocol/server-github",
        transport=MCPTransport.STDIO,
        env_var_names=["GITHUB_TOKEN"],
        config_path="~/.claude.json",
    )
    assert add_mcp.op_type == OperationType.ADD_MCP_SERVER

    add_skill = AddSkillOperation(
        description="Add Skill",
        target_root=target_root,
        skill_name="my-skill",
        skill_file="SKILL.md",
        destination_dir=".claude/skills/my-skill",
        supporting_files=["helper.py"],
    )
    assert add_skill.op_type == OperationType.ADD_SKILL

    add_plugin = AddPluginReferenceOperation(
        description="Add Plugin Reference",
        target_root=target_root,
        plugin_id="my-plugin",
        component_ids=["component1"],
        config_path="plugin.json",
    )
    assert add_plugin.op_type == OperationType.ADD_PLUGIN_REFERENCE


def test_missing_target_root_rejected() -> None:
    """Verify that operations missing target_root raise a validation error."""
    with pytest.raises(ValidationError):
        CreateDirectoryOperation.model_validate(
            {
                "description": "Missing target root",
                "directory_path": "dir",
            }
        )


def test_path_traversal_rejection_in_operation_fields() -> None:
    """Verify strict rejection of path traversal patterns across operation models."""
    unsafe_paths = [
        "../../file",
        "../../../file",
        "/etc/passwd",
        r"C:\Windows\file",
        r"D:\file",
        r"\\server\share\file",
        "//server/share/file",
        r"..\..\file",
        "%2e%2e/file",
        "file\0null",
    ]

    for bad_path in unsafe_paths:
        with pytest.raises(ValidationError):
            CreateDirectoryOperation(
                description="Bad dir",
                target_root=".",
                directory_path=bad_path,
            )

        with pytest.raises(ValidationError):
            WriteFileOperation(
                description="Bad file",
                target_root=".",
                file_path=bad_path,
                content="test",
                content_summary="test",
            )


def test_mcp_package_name_safety() -> None:
    """Verify MCP package name argument injection prevention."""
    invalid_packages = [
        "--evil-flag",
        "-g",
        "evil pkg",
        "pkg; rm -rf /",
        "pkg | calc",
        "../evil",
        "/etc/passwd",
        r"C:\evil",
        "$(calc)",
    ]

    for bad_pkg in invalid_packages:
        with pytest.raises(ValidationError):
            AddMcpServerOperation(
                description="Bad MCP pkg",
                target_root="~",
                server_name="test",
                runtime=MCPRuntime.NPX,
                package_name=bad_pkg,
                config_path="~/.claude.json",
            )

    # Valid package names must succeed
    valid_packages = [
        "@modelcontextprotocol/server-github",
        "@modelcontextprotocol/server-github@1.0.0",
        "my-mcp-server",
        "my_mcp_server==1.2.3",
    ]
    for good_pkg in valid_packages:
        op = AddMcpServerOperation(
            description="Good MCP pkg",
            target_root="~",
            server_name="test",
            runtime=MCPRuntime.NPX,
            package_name=good_pkg,
            config_path="~/.claude.json",
        )
        assert op.package_name == good_pkg


def test_environment_variable_safety() -> None:
    """Verify validation and rejection of dangerous process environment variables."""
    dangerous_envs = [
        "LD_PRELOAD",
        "PYTHONPATH",
        "NODE_OPTIONS",
        "PATH",
        "DYLD_INSERT_LIBRARIES",
        "123INVALID",
        "BAD-VAR",
    ]

    for bad_env in dangerous_envs:
        with pytest.raises(ValidationError):
            AddMcpServerOperation(
                description="Dangerous env var",
                target_root="~",
                server_name="test",
                runtime=MCPRuntime.NPX,
                package_name="valid-pkg",
                env_var_names=[bad_env],
                config_path="~/.claude.json",
            )

    # Valid env var names must pass
    valid_op = AddMcpServerOperation(
        description="Safe env var",
        target_root="~",
        server_name="test",
        runtime=MCPRuntime.NPX,
        package_name="valid-pkg",
        env_var_names=["API_KEY", "GITHUB_TOKEN"],
        config_path="~/.claude.json",
    )
    assert valid_op.env_var_names == ["API_KEY", "GITHUB_TOKEN"]


def test_configuration_scope_protection() -> None:
    """Verify that JSON/YAML operations cannot modify restricted root security keys."""
    restricted_keys = [
        "permissions",
        "allow_all",
        "security",
        "telemetry",
        "auto_approve",
        "auto_approve_commands",
        "trusted_folders",
        "execution_policy",
        "sudo",
    ]

    for key in restricted_keys:
        with pytest.raises(ValidationError):
            ModifyJsonOperation(
                description="Restricted key edit",
                target_root=".",
                file_path="config.json",
                json_path=key,
                value={"hacked": True},
                value_summary="hacked",
            )


def test_rollback_safety_validation() -> None:
    """Verify that rollback operations and parameters are strictly validated for safety."""
    # Unsafe path in rollback operation must be rejected
    with pytest.raises(ValidationError):
        RollbackOperation(
            op_type="remove_directory",
            description="Delete created directory",
            target_root=".",
            target_path="../../etc",
            params={"directory_path": "../../etc"},
        )

    # Valid rollback operation
    rb_op = RollbackOperation(
        op_type="remove_directory",
        description="Delete created directory",
        target_root=".",
        target_path=".claude/skills/test",
        params={"directory_path": ".claude/skills/test"},
    )
    rollback_meta = RollbackMetadata(
        reversible=True,
        rollback_operations=[rb_op],
        instructions="Delete directory .claude/skills/test",
    )

    assert rollback_meta.reversible is True
    assert len(rollback_meta.rollback_operations) == 1


def test_installation_plan_safety_validation() -> None:
    """Verify InstallationPlan validate_safety checks both planned_operations and rollback_info."""
    source = SourceSpec(
        source_type=SourceType.PACKAGE,
        package_name="@test/pkg",
        checksum="sha256:1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
    )
    rb_op = RollbackOperation(
        op_type="remove_directory",
        description="Delete created directory",
        target_root=".",
        target_path=".claude/skills/test",
        params={"directory_path": ".claude/skills/test"},
    )
    rb_meta = RollbackMetadata(
        reversible=True,
        rollback_operations=[rb_op],
    )
    op = CreateDirectoryOperation(
        description="Create directory",
        target_root=".",
        directory_path="skills/test",
    )

    plan = InstallationPlan(
        addon_id="test-addon",
        addon_name="Test Addon",
        addon_version="1.0.0",
        target_agent="claude-code",
        target_agent_name="Claude Code",
        target_scope=Scope.WORKSPACE,
        integration_type=IntegrationType.SKILL,
        source=source,
        planned_operations=[op],
        risk_level=RiskLevel.LOW,
        reversible=True,
        rollback_info=rb_meta,
    )

    assert plan.addon_id == "test-addon"
    # Safety validation must pass for clean plan
    plan.validate_safety()


def test_installation_transaction_model() -> None:
    """Verify InstallationTransaction phase state transition tracking."""
    source = SourceSpec(
        source_type=SourceType.PACKAGE,
        package_name="@test/pkg",
        checksum="sha256:1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
    )
    manifest_data = {
        "id": "test-addon",
        "name": "Test Addon",
        "version": "1.0.0",
        "description": "Test description",
        "license": "MIT",
        "category": "developer-tools",
        "integration_type": "skill",
        "target_agents": ["claude-code"],
        "supported_scopes": ["workspace"],
        "source": source.model_dump(),
        "trust": {
            "verification_status": "verified",
            "publisher": {"name": "Test Team"},
        },
        "handler_spec": {"skill": {"skill_file": "SKILL.md"}},
    }
    from aiaddons.core.models.manifest import IntegrationManifest

    manifest = IntegrationManifest.model_validate(manifest_data)

    from aiaddons.core.models.agent import AgentDetectionResult

    agent = AgentDetectionResult(
        agent_id="claude-code",
        name="Claude Code",
        installed=True,
    )

    tx = InstallationTransaction(
        transaction_id="tx_12345",
        phase=TransactionPhase.REQUESTED,
        manifest=manifest,
        agent=agent,
        requested_scope=Scope.WORKSPACE,
        is_dry_run=True,
    )

    assert tx.transaction_id == "tx_12345"
    assert tx.phase == TransactionPhase.REQUESTED
    assert tx.is_dry_run is True
