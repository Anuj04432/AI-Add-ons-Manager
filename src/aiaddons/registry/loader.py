"""Registry loader module for loading and validating add-on manifests from local YAML/JSON files."""

import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from aiaddons.core.models.manifest import IntegrationManifest


class ManifestValidationError(Exception):
    """Exception raised when a manifest file is malformed or fails validation."""

    def __init__(self, file_path: Path, message: str) -> None:
        self.file_path = file_path
        self.message = message
        super().__init__(f"Validation failed for '{file_path}': {message}")


class RegistryLoadResult(BaseModel):
    """Result object containing successfully loaded manifests and validation errors."""

    manifests: dict[str, IntegrationManifest]
    errors: list[str]


class RegistryLoader:
    """Loader responsible for discovering, reading, and validating registry manifest files."""

    def load_file(self, file_path: Path) -> IntegrationManifest:
        """Load and validate a single YAML or JSON registry manifest file."""
        if not file_path.exists():
            raise ManifestValidationError(file_path, "File does not exist.")

        raw_content = file_path.read_text(encoding="utf-8")
        if not raw_content.strip():
            raise ManifestValidationError(file_path, "File is empty.")

        try:
            if file_path.suffix in (".yaml", ".yml"):
                parsed_data: Any = yaml.safe_load(raw_content)
            elif file_path.suffix == ".json":
                parsed_data = json.loads(raw_content)
            else:
                # Try YAML safe_load first, fallback to JSON
                try:
                    parsed_data = yaml.safe_load(raw_content)
                except Exception:
                    parsed_data = json.loads(raw_content)
        except Exception as exc:
            raise ManifestValidationError(file_path, f"Syntax parsing error: {exc}") from exc

        if not isinstance(parsed_data, dict):
            raise ManifestValidationError(
                file_path, "Manifest root content must be a key-value dictionary."
            )

        try:
            return IntegrationManifest.model_validate(parsed_data)
        except ValidationError as val_err:
            error_details = "; ".join(
                f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}"
                for err in val_err.errors()
            )
            raise ManifestValidationError(file_path, error_details) from val_err
        except ValueError as val_err:
            raise ManifestValidationError(file_path, str(val_err)) from val_err

    def load_directory(self, directory_path: Path) -> RegistryLoadResult:
        """Discover and load all registry manifest files from a directory recursively."""
        manifests: dict[str, IntegrationManifest] = {}
        errors: list[str] = []

        if not directory_path.exists() or not directory_path.is_dir():
            return RegistryLoadResult(
                manifests={},
                errors=[f"Directory '{directory_path}' does not exist or is not a directory."],
            )

        # Look for .yaml, .yml, .json files
        files = sorted(
            [
                f
                for f in directory_path.rglob("*")
                if f.is_file() and f.suffix in (".yaml", ".yml", ".json")
            ]
        )

        for file_path in files:
            try:
                manifest = self.load_file(file_path)
                if manifest.id in manifests:
                    errors.append(f"Duplicate add-on ID '{manifest.id}' in '{file_path}'.")
                else:
                    manifests[manifest.id] = manifest
            except ManifestValidationError as exc:
                errors.append(str(exc))
            except Exception as exc:
                errors.append(f"Unexpected error loading '{file_path}': {exc}")

        return RegistryLoadResult(manifests=manifests, errors=errors)
