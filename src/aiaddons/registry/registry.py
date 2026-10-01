"""Registry index abstraction for searching, filtering, and querying loaded add-on manifests."""

from __future__ import annotations

from builtins import list as builtins_list
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from aiaddons.core.models.manifest import IntegrationManifest, IntegrationType
from aiaddons.registry.loader import RegistryLoader, RegistryLoadResult

if TYPE_CHECKING:
    from aiaddons.registry.cache import RegistryCacheManager


class Registry:
    """In-memory index and query engine for AI add-on manifests."""

    def __init__(self, manifests: Sequence[IntegrationManifest] | None = None) -> None:
        self._manifests: dict[str, IntegrationManifest] = {}
        if manifests is not None:
            for manifest in manifests:
                self._manifests[manifest.id] = manifest

    @classmethod
    def from_directory(cls, directory_path: Path) -> tuple[Registry, RegistryLoadResult]:
        """Create a Registry instance by loading manifests from a directory."""
        loader = RegistryLoader()
        result = loader.load_directory(directory_path)
        registry = cls(manifests=list(result.manifests.values()))
        return registry, result

    @classmethod
    def from_cache(cls, cache_manager: RegistryCacheManager | None = None) -> Registry | None:
        """Create a Registry instance by loading from the local registry cache."""
        from aiaddons.registry.cache import RegistryCacheManager

        mgr = cache_manager if cache_manager is not None else RegistryCacheManager()
        return mgr.get_registry()

    @classmethod
    def find_default_registry_dir(cls, start_path: Path | None = None) -> Path | None:
        """Find local registry directory by searching start_path, cwd, and package parents."""
        candidates: list[Path] = []
        if start_path is not None:
            candidates.extend(
                [start_path / "registry" / "addons", start_path / "registry", start_path]
            )

        cwd = Path.cwd()
        candidates.extend([cwd / "registry" / "addons", cwd / "registry"])
        for parent in cwd.parents:
            candidates.extend([parent / "registry" / "addons", parent / "registry"])

        pkg_file = Path(__file__).resolve()
        # Bundled addons in package distribution (e.g. site-packages/aiaddons/registry/addons)
        candidates.extend([pkg_file.parent / "addons", pkg_file.parent])
        for parent in pkg_file.parents:
            candidates.extend([parent / "registry" / "addons", parent / "registry"])

        for candidate in candidates:
            if candidate.exists() and candidate.is_dir():
                if (
                    any(candidate.glob("*.yaml"))
                    or any(candidate.glob("*.yml"))
                    or any(candidate.glob("*.json"))
                ):
                    return candidate
                addons_sub = candidate / "addons"
                if addons_sub.exists() and addons_sub.is_dir() and (
                    any(addons_sub.glob("*.yaml"))
                    or any(addons_sub.glob("*.yml"))
                    or any(addons_sub.glob("*.json"))
                ):
                    return addons_sub

        return None

    @classmethod
    def load_auto(
        cls,
        custom_dir: Path | None = None,
        cache_manager: RegistryCacheManager | None = None,
    ) -> tuple[Registry, str]:
        """Automatically resolve and load Registry.

        Priority:
        1. Explicit custom_dir (if provided and exists)
        2. Local registry cache (if valid cache file exists)
        3. Fallback local registry directory (cwd or package parents / 'registry' / 'addons')
        Returns (Registry, source_description).
        """
        if custom_dir is not None and custom_dir.exists() and custom_dir.is_dir():
            reg, _ = cls.from_directory(custom_dir)
            return reg, f"directory:{custom_dir}"

        # Try loading from local cache
        cached_reg = cls.from_cache(cache_manager)
        if cached_reg is not None and cached_reg.count() > 0:
            return cached_reg, "cache"

        # Fallback to local codebase directory if it exists
        default_dir = cls.find_default_registry_dir(custom_dir)
        if default_dir is not None and default_dir.exists() and default_dir.is_dir():
            reg, _ = cls.from_directory(default_dir)
            return reg, f"directory:{default_dir}"

        return cls(), "empty"

    def add_manifest(self, manifest: IntegrationManifest) -> None:
        """Add or update a manifest entry in the registry."""
        self._manifests[manifest.id] = manifest

    def get(self, addon_id: str) -> IntegrationManifest | None:
        """Retrieve a specific manifest by ID."""
        return self._manifests.get(addon_id.strip().lower())

    def list(self) -> builtins_list[IntegrationManifest]:
        """List all manifests in the registry sorted by ID."""
        return sorted(self._manifests.values(), key=lambda m: m.id)

    def count(self) -> int:
        """Return the total number of manifests in the registry."""
        return len(self._manifests)

    def search(self, query: str) -> builtins_list[IntegrationManifest]:
        """Search manifests matching query against ID, name, description, tags, and metadata."""
        q = query.strip().lower()
        if not q:
            return self.list()

        results: list[IntegrationManifest] = []
        for m in self._manifests.values():
            if (
                q in m.id.lower()
                or q in m.name.lower()
                or q in m.description.lower()
                or q in m.category.lower()
                or q in m.integration_type.value.lower()
                or q in m.trust.publisher.name.lower()
                or any(q in tag.lower() for tag in m.tags)
                or any(q in agent.lower() for agent in m.target_agents)
            ):
                results.append(m)

        return sorted(results, key=lambda m: m.id)

    def filter_by_type(
        self, integration_type: IntegrationType | str
    ) -> builtins_list[IntegrationManifest]:
        """Filter manifests by integration type (mcp, skill, plugin, cli_tool)."""
        target_type = (
            integration_type.value
            if isinstance(integration_type, IntegrationType)
            else str(integration_type).lower()
        )
        return sorted(
            [m for m in self._manifests.values() if m.integration_type.value == target_type],
            key=lambda m: m.id,
        )

    def filter_by_category(self, category: str) -> builtins_list[IntegrationManifest]:
        """Filter manifests by category."""
        target_cat = category.strip().lower()
        return sorted(
            [m for m in self._manifests.values() if m.category.lower() == target_cat],
            key=lambda m: m.id,
        )

    def filter_by_agent(self, agent_id: str) -> builtins_list[IntegrationManifest]:
        """Filter manifests supporting a target agent ID (or wildcard '*')."""
        target = agent_id.strip().lower()
        results: list[IntegrationManifest] = []
        for m in self._manifests.values():
            targets = [t.lower() for t in m.target_agents]
            if "*" in targets or target in targets:
                results.append(m)
        return sorted(results, key=lambda m: m.id)
