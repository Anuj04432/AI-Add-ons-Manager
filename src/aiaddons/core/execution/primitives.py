"""Safe, atomic filesystem and configuration modification primitives for Phase 5B.1."""

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import yaml

from aiaddons.core.exceptions import InstallationError
from aiaddons.core.execution.security import verify_safe_target_path
from aiaddons.core.installer.models import (
    CopyFileOperation,
    CreateDirectoryOperation,
    ModifyJsonOperation,
    ModifyYamlOperation,
    validate_config_key_path,
)


def create_directory_primitive(op: CreateDirectoryOperation) -> Path:
    """Safely create a directory inside op.target_root."""
    _, resolved_dest = verify_safe_target_path(op.target_root, op.directory_path)
    resolved_dest.mkdir(parents=True, exist_ok=True)
    return resolved_dest


def atomic_write_file_primitive(
    target_root: str,
    file_path: str,
    content: str,
    overwrite: bool = False,
) -> Path:
    """Safely write content to file_path inside target_root using atomic replacement."""
    _, resolved_dest = verify_safe_target_path(target_root, file_path)

    if resolved_dest.exists() and not overwrite:
        msg = f"Target file '{resolved_dest}' already exists and overwrite is False."
        raise InstallationError(msg)

    resolved_dest.parent.mkdir(parents=True, exist_ok=True)

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=resolved_dest.parent,
            encoding="utf-8",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(content)
            temp_file.flush()
            os.fsync(temp_file.fileno())

        os.replace(temp_path, resolved_dest)
        return resolved_dest
    except Exception as err:
        if temp_path and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        raise InstallationError(f"Atomic file write failed for '{file_path}': {err}") from err


def copy_file_primitive(op: CopyFileOperation) -> Path:
    """Safely copy a source file to destination_path inside op.target_root."""
    _, resolved_dest = verify_safe_target_path(op.target_root, op.destination_path)
    source_obj = Path(op.source_path).expanduser()

    if not source_obj.exists() or not source_obj.is_file():
        raise InstallationError(f"Source file '{op.source_path}' does not exist or is not a file.")

    try:
        source_content = source_obj.read_text(encoding="utf-8")
    except Exception as err:
        raise InstallationError(f"Failed to read source file '{op.source_path}': {err}") from err

    return atomic_write_file_primitive(
        target_root=op.target_root,
        file_path=op.destination_path,
        content=source_content,
        overwrite=True,
    )


def _apply_nested_key_value(data: dict[str, Any], key_path: str, value: Any) -> None:
    """Apply nested key assignment on dictionary (e.g. 'mcpServers.test' -> key assignment)."""
    parts = [p.strip() for p in key_path.split(".") if p.strip()]
    if not parts:
        raise ValueError("Invalid key path.")

    curr = data
    for part in parts[:-1]:
        if part not in curr or not isinstance(curr[part], dict):
            curr[part] = {}
        curr = curr[part]

    curr[parts[-1]] = value


def _delete_nested_key(data: dict[str, Any], key_path: str) -> bool:
    """Delete a key in a nested dictionary structure. Returns True if key was deleted."""
    parts = [p.strip() for p in key_path.split(".") if p.strip()]
    if not parts:
        return False

    curr = data
    for part in parts[:-1]:
        if part not in curr or not isinstance(curr[part], dict):
            return False
        curr = curr[part]

    if parts[-1] in curr:
        del curr[parts[-1]]
        return True
    return False


def modify_json_primitive(op: ModifyJsonOperation) -> Path:
    """Safely apply a typed value modification to a JSON config file inside op.target_root."""
    _, resolved_dest = verify_safe_target_path(op.target_root, op.file_path)
    validate_config_key_path(op.json_path)

    data: dict[str, Any] = {}
    if resolved_dest.exists():
        try:
            content = resolved_dest.read_text(encoding="utf-8").strip()
            if content:
                loaded = json.loads(content)
                if isinstance(loaded, dict):
                    data = loaded
                else:
                    msg = f"JSON configuration '{op.file_path}' does not contain a JSON object."
                    raise InstallationError(msg)
        except json.JSONDecodeError as err:
            raise InstallationError(f"Malformed JSON file '{op.file_path}': {err}") from err

    _apply_nested_key_value(data, op.json_path, op.value)
    new_json_str = json.dumps(data, indent=2) + "\n"

    return atomic_write_file_primitive(
        target_root=op.target_root,
        file_path=op.file_path,
        content=new_json_str,
        overwrite=True,
    )


def modify_yaml_primitive(op: ModifyYamlOperation) -> Path:
    """Safely apply a typed value modification to a YAML config file inside op.target_root."""
    _, resolved_dest = verify_safe_target_path(op.target_root, op.file_path)
    validate_config_key_path(op.yaml_path)

    data: dict[str, Any] = {}
    if resolved_dest.exists():
        try:
            content = resolved_dest.read_text(encoding="utf-8").strip()
            if content:
                loaded = yaml.safe_load(content)
                if isinstance(loaded, dict):
                    data = loaded
                elif loaded is not None:
                    msg = f"YAML config '{op.file_path}' does not contain a dictionary."
                    raise InstallationError(msg)
        except Exception as err:
            raise InstallationError(f"Malformed YAML file '{op.file_path}': {err}") from err

    _apply_nested_key_value(data, op.yaml_path, op.value)
    new_yaml_str = yaml.safe_dump(data, sort_keys=False)

    return atomic_write_file_primitive(
        target_root=op.target_root,
        file_path=op.file_path,
        content=new_yaml_str,
        overwrite=True,
    )


def remove_directory_primitive(target_root: str, directory_path: str) -> None:
    """Safely remove a directory inside target_root for rollback."""
    _, resolved_dest = verify_safe_target_path(target_root, directory_path)
    if resolved_dest.exists() and resolved_dest.is_dir():
        shutil.rmtree(resolved_dest)


def remove_file_primitive(target_root: str, file_path: str) -> None:
    """Safely remove a file inside target_root for rollback."""
    _, resolved_dest = verify_safe_target_path(target_root, file_path)
    if resolved_dest.exists() and (resolved_dest.is_file() or resolved_dest.is_symlink()):
        resolved_dest.unlink()


def remove_json_key_primitive(target_root: str, file_path: str, json_path: str) -> None:
    """Safely remove a key from a JSON configuration file inside target_root for rollback."""
    _, resolved_dest = verify_safe_target_path(target_root, file_path)
    if not resolved_dest.exists():
        return

    try:
        content = resolved_dest.read_text(encoding="utf-8").strip()
        if not content:
            return
        data = json.loads(content)
        if isinstance(data, dict):
            if _delete_nested_key(data, json_path):
                new_json = json.dumps(data, indent=2) + "\n"
                atomic_write_file_primitive(target_root, file_path, new_json, overwrite=True)
    except Exception:
        pass
