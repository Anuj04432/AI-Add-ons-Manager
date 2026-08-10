"""Unit tests for Phase 5B.1 target-root path safety and symlink protection."""

from pathlib import Path

import pytest

from aiaddons.core.exceptions import SecurityValidationError
from aiaddons.core.execution.security import verify_safe_target_path


def test_target_root_normal_relative_path(tmp_path: Path) -> None:
    """Verify safe relative path resolution inside target_root."""
    resolved_root, resolved_dest = verify_safe_target_path(tmp_path, "skills/my-skill/SKILL.md")
    assert resolved_root == tmp_path.resolve()
    assert resolved_dest == (tmp_path / "skills/my-skill/SKILL.md").resolve()


def test_target_root_absolute_path_escape(tmp_path: Path) -> None:
    """Verify rejection of absolute paths escaping target_root."""
    with pytest.raises(SecurityValidationError, match="escapes target_root"):
        verify_safe_target_path(tmp_path, "/etc/passwd")


def test_target_root_traversal_escape(tmp_path: Path) -> None:
    """Verify rejection of path traversal escaping target_root."""
    with pytest.raises(SecurityValidationError):
        verify_safe_target_path(tmp_path, "../../outside.txt")


def test_target_root_windows_drive_and_unc(tmp_path: Path) -> None:
    """Verify rejection of Windows drive letters and UNC paths."""
    with pytest.raises(SecurityValidationError):
        verify_safe_target_path(tmp_path, r"C:\Windows\System32")

    with pytest.raises(SecurityValidationError):
        verify_safe_target_path(tmp_path, r"\\server\share\file.txt")


def test_destination_symlink_escape(tmp_path: Path) -> None:
    """Verify rejection when destination is a symlink pointing outside target_root."""
    outside_dir = tmp_path.parent / "outside_dir"
    outside_dir.mkdir(exist_ok=True)
    target_file = outside_dir / "target.txt"
    target_file.write_text("outside data", encoding="utf-8")

    symlink_file = tmp_path / "symlink.txt"
    try:
        symlink_file.symlink_to(target_file)
    except OSError:
        pytest.skip("Symlinks not supported on this platform/privilege level")

    with pytest.raises(SecurityValidationError):
        verify_safe_target_path(tmp_path, "symlink.txt")


def test_parent_symlink_escape(tmp_path: Path) -> None:
    """Verify rejection when parent directory is a symlink pointing outside target_root."""
    outside_dir = tmp_path.parent / "outside_dir_parent"
    outside_dir.mkdir(exist_ok=True)

    symlink_dir = tmp_path / "linked_parent"
    try:
        symlink_dir.symlink_to(outside_dir, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks not supported on this platform/privilege level")

    with pytest.raises(SecurityValidationError):
        verify_safe_target_path(tmp_path, "linked_parent/child.txt")


def test_nested_symlink_escape(tmp_path: Path) -> None:
    """Verify rejection when nested intermediate directories use symlinks pointing outside."""
    outside_dir = tmp_path.parent / "outside_nested"
    outside_dir.mkdir(exist_ok=True)

    subdir = tmp_path / "sub"
    subdir.mkdir()
    symlink_dir = subdir / "nested_link"
    try:
        symlink_dir.symlink_to(outside_dir, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks not supported on this platform/privilege level")

    with pytest.raises(SecurityValidationError):
        verify_safe_target_path(tmp_path, "sub/nested_link/file.txt")
