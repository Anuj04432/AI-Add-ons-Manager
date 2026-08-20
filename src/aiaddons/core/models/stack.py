"""Pydantic domain models for Add-on Stack files used in batch installations."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from aiaddons.core.exceptions import ManifestValidationError


class StackAddonItem(BaseModel):
    """An individual add-on entry in a stack file with optional pinned version."""

    model_config = ConfigDict(extra="forbid")

    id: str
    version: str | None = None

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        clean_id = v.strip().lower()
        if not clean_id:
            raise ValueError("Add-on ID cannot be empty.")
        if not re.match(r"^[a-z0-9-_]+$", clean_id):
            raise ValueError(f"Invalid add-on ID '{v}'. Must be lowercase alphanumeric or dashes.")
        return clean_id


class AddonStack(BaseModel):
    """Stack file specification for batch installations."""

    model_config = ConfigDict(extra="forbid")

    version: str | None = "1.0"
    name: str | None = None
    description: str | None = None
    addons: list[str | StackAddonItem] = Field(default_factory=list)

    @field_validator("addons")
    @classmethod
    def validate_addons_not_empty(cls, v: list[str | StackAddonItem]) -> list[str | StackAddonItem]:
        if not v:
            raise ValueError("Stack file must contain at least one add-on entry in 'addons'.")
        return v


def parse_stack_file(file_path: Path) -> list[tuple[str, str | None]]:
    """Parse and validate a stack YAML or JSON file.

    Returns a list of (addon_id, pinned_version) tuples.
    Raises ManifestValidationError if the file does not exist, cannot be read, or fails schema validation.
    """
    resolved_path = file_path.expanduser().resolve()
    if not resolved_path.exists() or not resolved_path.is_file():
        raise ManifestValidationError(
            file_path=file_path,
            message=f"Stack file '{file_path}' does not exist or is not a file.",
        )

    try:
        content = resolved_path.read_text(encoding="utf-8").strip()
    except Exception as exc:
        raise ManifestValidationError(
            file_path=file_path,
            message=f"Failed to read stack file '{file_path}': {exc}",
        ) from exc

    if not content:
        raise ManifestValidationError(
            file_path=file_path,
            message=f"Stack file '{file_path}' is empty.",
        )

    try:
        raw_data = yaml.safe_load(content)
    except Exception as exc:
        raise ManifestValidationError(
            file_path=file_path,
            message=f"Failed to parse YAML/JSON in stack file '{file_path}': {exc}",
        ) from exc

    if not isinstance(raw_data, dict):
        raise ManifestValidationError(
            file_path=file_path,
            message=f"Invalid stack file structure in '{file_path}': root element must be a dictionary/object.",
        )

    try:
        stack = AddonStack.model_validate(raw_data)
    except ValidationError as exc:
        error_details = "; ".join(
            f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        raise ManifestValidationError(
            file_path=file_path,
            message=f"Stack file schema validation failed: {error_details}",
        ) from exc

    results: list[tuple[str, str | None]] = []
    for entry in stack.addons:
        if isinstance(entry, str):
            clean_id = entry.strip().lower()
            if not clean_id or not re.match(r"^[a-z0-9-_]+$", clean_id):
                raise ManifestValidationError(
                    file_path=file_path,
                    message=f"Invalid add-on ID '{entry}' in stack file. Must be lowercase alphanumeric or dashes.",
                )
            results.append((clean_id, None))
        elif isinstance(entry, StackAddonItem):
            results.append((entry.id, entry.version))

    return results
