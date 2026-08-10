"""Unit tests for Phase 5B.1 safe structural filesystem and configuration primitives."""

import json
from pathlib import Path

import pytest
import yaml

from aiaddons.core.exceptions import InstallationError
from aiaddons.core.execution.primitives import (
    atomic_write_file_primitive,
    copy_file_primitive,
    create_directory_primitive,
    modify_json_primitive,
    modify_yaml_primitive,
)
from aiaddons.core.installer.models import (
    CopyFileOperation,
    CreateDirectoryOperation,
    ModifyJsonOperation,
    ModifyYamlOperation,
)


def test_create_directory_primitive(tmp_path: Path) -> None:
    """Verify safe directory creation."""
    op = CreateDirectoryOperation(
        description="Create test dir",
        target_root=str(tmp_path),
        directory_path="skills/my-skill",
    )
    dest = create_directory_primitive(op)
    assert dest.exists()
    assert dest.is_dir()


def test_atomic_write_file_primitive_success(tmp_path: Path) -> None:
    """Verify successful atomic file writing and overwriting."""
    target_root = str(tmp_path)
    dest = atomic_write_file_primitive(
        target_root, "config.json", '{"key": "value"}', overwrite=False
    )
    assert dest.exists()
    assert dest.read_text(encoding="utf-8") == '{"key": "value"}'

    # Overwrite = False on existing file should fail
    with pytest.raises(InstallationError, match="already exists"):
        atomic_write_file_primitive(target_root, "config.json", '{"key": "new"}', overwrite=False)

    # Overwrite = True on existing file should succeed
    dest2 = atomic_write_file_primitive(
        target_root, "config.json", '{"key": "new"}', overwrite=True
    )
    assert dest2.read_text(encoding="utf-8") == '{"key": "new"}'


def test_copy_file_primitive_success(tmp_path: Path) -> None:
    """Verify safe file copy primitive."""
    src = tmp_path / "source.txt"
    src.write_text("hello copy", encoding="utf-8")

    op = CopyFileOperation(
        description="Copy test file",
        target_root=str(tmp_path),
        source_path=str(src),
        destination_path="dst/source_copy.txt",
    )
    dst = copy_file_primitive(op)
    assert dst.exists()
    assert dst.read_text(encoding="utf-8") == "hello copy"


def test_modify_json_primitive_success(tmp_path: Path) -> None:
    """Verify typed JSON configuration modification."""
    target_root = str(tmp_path)
    op = ModifyJsonOperation(
        description="Inject MCP server config",
        target_root=target_root,
        file_path="agent_config.json",
        json_path="mcpServers.github",
        value={"command": "npx", "args": ["@modelcontextprotocol/server-github"]},
        value_summary="{...}",
    )
    dest = modify_json_primitive(op)
    assert dest.exists()
    data = json.loads(dest.read_text(encoding="utf-8"))
    assert "mcpServers" in data
    assert data["mcpServers"]["github"]["command"] == "npx"


def test_modify_yaml_primitive_success(tmp_path: Path) -> None:
    """Verify typed YAML configuration modification."""
    target_root = str(tmp_path)
    op = ModifyYamlOperation(
        description="Inject plugin setting",
        target_root=target_root,
        file_path="plugin_config.yaml",
        yaml_path="plugins.code-reviewer",
        value={"enabled": True, "version": "1.0.0"},
        value_summary="{...}",
    )
    dest = modify_yaml_primitive(op)
    assert dest.exists()
    data = yaml.safe_load(dest.read_text(encoding="utf-8"))
    assert "plugins" in data
    assert data["plugins"]["code-reviewer"]["enabled"] is True
