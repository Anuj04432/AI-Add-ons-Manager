"""Registry index abstraction for searching, filtering, and querying loaded add-on manifests."""

from __future__ import annotations

from builtins import list as builtins_list
from collections.abc import Sequence
from pathlib import Path

from aiaddons.core.models.manifest import IntegrationManifest, IntegrationType
from aiaddons.registry.loader import RegistryLoader, RegistryLoadResult


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
        """Search manifests matching ID, name, description, tags, category, or publisher."""
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
                or q in m.trust.publisher.name.lower()
                or any(q in tag.lower() for tag in m.tags)
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
