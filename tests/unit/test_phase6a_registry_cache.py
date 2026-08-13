"""Unit tests for Phase 6A RegistryCacheManager."""

from pathlib import Path

from aiaddons.core.models.agent import Scope
from aiaddons.core.models.manifest import (
    HandlerSpecContainer,
    IntegrationManifest,
    IntegrationType,
    MCPHandlerSpec,
    MCPRuntime,
    PublisherClaimSpec,
    SourceSpec,
    SourceType,
    TrustMetadata,
)
from aiaddons.registry.cache import RegistryCacheManager
from aiaddons.registry.models import RegistryIndex


def _make_sample_index(addon_id: str = "cached-addon") -> RegistryIndex:
    manifest = IntegrationManifest(
        id=addon_id,
        name="Cached Addon",
        version="1.0.0",
        description="A cached test addon.",
        license="MIT",
        category="developer-tools",
        integration_type=IntegrationType.MCP,
        target_agents=["claude-code"],
        supported_scopes=[Scope.GLOBAL, Scope.WORKSPACE],
        source=SourceSpec(
            source_type=SourceType.PACKAGE,
            package_name="@scope/cached-addon",
            checksum="sha256:1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
        ),
        trust=TrustMetadata(publisher=PublisherClaimSpec(name="Test")),
        handler_spec=HandlerSpecContainer(
            mcp=MCPHandlerSpec(
                runtime=MCPRuntime.NPX,
                package_name="@scope/cached-addon",
            )
        ),
    )
    return RegistryIndex(
        schema_version="1.0",
        generated_at="2026-08-13T12:00:00Z",
        manifests=[manifest],
    )


def test_first_successful_write(tmp_path: Path) -> None:
    """Test saving a new registry cache creating cache file atomically."""
    cache_dir = tmp_path / "registry_cache"
    mgr = RegistryCacheManager(cache_dir=cache_dir)
    assert not mgr.cache_file.exists()

    index = _make_sample_index()
    saved_path = mgr.save_cache(index)

    assert saved_path.exists()
    assert saved_path == mgr.cache_file

    cached_data, err = mgr.load_cache_data()
    assert err is None
    assert cached_data is not None
    assert cached_data.index.schema_version == "1.0"
    assert len(cached_data.index.manifests) == 1
    assert cached_data.index.manifests[0].id == "cached-addon"


def test_atomic_update_replaces_existing(tmp_path: Path) -> None:
    """Test updating existing cache replaces file atomically."""
    cache_dir = tmp_path / "registry_cache"
    mgr = RegistryCacheManager(cache_dir=cache_dir)

    # First write v1
    index1 = _make_sample_index("addon-v1")
    mgr.save_cache(index1)

    cached1, _ = mgr.load_cache_data()
    assert cached1 is not None
    assert cached1.index.manifests[0].id == "addon-v1"

    # Update v2
    index2 = _make_sample_index("addon-v2")
    mgr.save_cache(index2)

    cached2, _ = mgr.load_cache_data()
    assert cached2 is not None
    assert cached2.index.manifests[0].id == "addon-v2"


def test_corrupted_cache_detection(tmp_path: Path) -> None:
    """Test that a corrupted cache file is detected safely without raising unhandled crash."""
    cache_dir = tmp_path / "registry_cache"
    mgr = RegistryCacheManager(cache_dir=cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Write corrupt data
    mgr.cache_file.write_text("{invalid json corrupt content...", encoding="utf-8")

    cached_data, err = mgr.load_cache_data()
    assert cached_data is None
    assert err is not None
    assert "Corrupted or invalid registry cache" in err


def test_empty_cache_file_behavior(tmp_path: Path) -> None:
    """Test that an empty cache file is reported cleanly."""
    cache_dir = tmp_path / "registry_cache"
    mgr = RegistryCacheManager(cache_dir=cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    mgr.cache_file.write_text("", encoding="utf-8")

    cached_data, err = mgr.load_cache_data()
    assert cached_data is None
    assert err == "Cache file is empty."


def test_get_status(tmp_path: Path) -> None:
    """Test get_status returning correct status metadata."""
    cache_dir = tmp_path / "registry_cache"
    mgr = RegistryCacheManager(cache_dir=cache_dir)

    status_before = mgr.get_status("https://registry.aiaddons.dev/index.json")
    assert not status_before["cache_exists"]
    assert status_before["sync_required"] is True

    # Save cache
    mgr.save_cache(_make_sample_index())

    status_after = mgr.get_status("https://registry.aiaddons.dev/index.json")
    assert status_after["cache_exists"] is True
    assert status_after["is_valid"] is True
    assert status_after["addon_count"] == 1
    assert status_after["cached_schema_version"] == "1.0"
    assert status_after["sync_required"] is False
