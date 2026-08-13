"""Local registry cache management with atomic updates and corrupt cache protection."""

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from aiaddons.core.exceptions import AIAddonsError, ManifestValidationError
from aiaddons.registry.models import RegistryIndex
from aiaddons.registry.registry import Registry
from aiaddons.registry.validator import validate_registry_data


class RegistryCacheData(BaseModel):
    """Container model for cached registry data and synchronization metadata."""

    model_config = ConfigDict(extra="forbid")

    last_synced_at: str
    index: RegistryIndex


class RegistryCacheManager:
    """Manages reading, writing, and status of the local registry cache."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        if cache_dir is None:
            self.cache_dir = Path.home() / ".aiaddons" / "registry"
        else:
            self.cache_dir = cache_dir.expanduser()
        self.cache_file = self.cache_dir / "cache.json"

    def save_cache(self, index: RegistryIndex) -> Path:
        """Atomically write a validated RegistryIndex to the local cache directory."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        cache_data = RegistryCacheData(
            last_synced_at=datetime.now(UTC).isoformat(),
            index=index,
        )
        content = cache_data.model_dump_json(indent=2) + "\n"

        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", dir=self.cache_dir, encoding="utf-8", delete=False
            ) as temp_file:
                temp_path = Path(temp_file.name)
                temp_file.write(content)
                temp_file.flush()
                os.fsync(temp_file.fileno())

            os.replace(temp_path, self.cache_file)
            return self.cache_file
        except Exception as err:
            if temp_path and temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass
            raise AIAddonsError(
                f"Failed atomic write to registry cache '{self.cache_file}': {err}"
            ) from err

    def load_cache_data(self) -> tuple[RegistryCacheData | None, str | None]:
        """Load and validate cached registry data. Returns (data, error_message)."""
        if not self.cache_file.exists() or not self.cache_file.is_file():
            return None, "Cache file does not exist."

        try:
            raw_text = self.cache_file.read_text(encoding="utf-8").strip()
            if not raw_text:
                return None, "Cache file is empty."

            raw_json: Any = json.loads(raw_text)
            if not isinstance(raw_json, dict):
                return None, "Cache file content must be a JSON object."

            cache_data = RegistryCacheData.model_validate(raw_json)

            # Re-run granular manifest validations on the cached index
            validate_registry_data(cache_data.index.model_dump(), source_label=str(self.cache_file))

            return cache_data, None
        except (
            json.JSONDecodeError,
            ValidationError,
            ManifestValidationError,
            AIAddonsError,
            Exception,
        ) as err:
            return None, f"Corrupted or invalid registry cache: {err}"

    def get_registry(self) -> Registry | None:
        """Retrieve a Registry instance populated from valid local cache, or None if invalid."""
        cache_data, err = self.load_cache_data()
        if cache_data is None or err is not None:
            return None
        return Registry(manifests=cache_data.index.manifests)

    def get_status(self, registry_url: str) -> dict[str, Any]:
        """Generate status summary metadata for the local registry cache."""
        cache_data, err = self.load_cache_data()
        exists = self.cache_file.exists()
        is_valid = cache_data is not None and err is None

        last_synced = cache_data.last_synced_at if cache_data else None
        cached_ver = cache_data.index.schema_version if cache_data else None
        addon_count = len(cache_data.index.manifests) if cache_data else 0

        return {
            "configured_url": registry_url,
            "cache_dir": str(self.cache_dir),
            "cache_file": str(self.cache_file),
            "cache_exists": exists,
            "is_valid": is_valid,
            "error": err if exists and not is_valid else None,
            "last_synced_at": last_synced,
            "cached_schema_version": cached_ver,
            "addon_count": addon_count,
            "sync_required": not is_valid,
        }
