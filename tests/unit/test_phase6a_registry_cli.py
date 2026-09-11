"""Unit tests for Phase 6A Registry CLI commands."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from aiaddons.cli.main import app
from aiaddons.core.models.agent import Scope
from aiaddons.core.models.manifest import (
    HandlerSpecContainer,
    IntegrationManifest,
    IntegrationType,
    MCPHandlerSpec,
    MCPRuntime,
    PluginHandlerSpec,
    PublisherClaimSpec,
    SourceSpec,
    SourceType,
    TrustMetadata,
)
from aiaddons.registry.cache import RegistryCacheManager
from aiaddons.registry.models import RegistryIndex

runner = CliRunner()


@pytest.fixture
def populated_cache_dir(tmp_path: Path) -> Path:
    """Fixture creating a temporary cache directory populated with sample manifests."""
    cache_dir = tmp_path / "cache"
    cache_mgr = RegistryCacheManager(cache_dir=cache_dir)

    m1 = IntegrationManifest(
        id="github-mcp",
        name="GitHub MCP Server",
        version="1.2.0",
        description="MCP server for GitHub integration.",
        license="MIT",
        category="developer-tools",
        integration_type=IntegrationType.MCP,
        target_agents=["claude-code"],
        supported_scopes=[Scope.GLOBAL, Scope.WORKSPACE],
        source=SourceSpec(
            source_type=SourceType.PACKAGE,
            package_name="@modelcontextprotocol/server-github",
            checksum="sha256:1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
        ),
        trust=TrustMetadata(
            publisher=PublisherClaimSpec(name="MCP Team", declared_verified=True),
            allowed_executables=["npx"],
        ),
        tags=["github", "mcp"],
        handler_spec=HandlerSpecContainer(
            mcp=MCPHandlerSpec(
                runtime=MCPRuntime.NPX,
                package_name="@modelcontextprotocol/server-github",
            )
        ),
    )

    m2 = IntegrationManifest(
        id="python-lint-plugin",
        name="Python Lint Composite Plugin",
        version="1.0.0",
        description="Composite plugin bundling linter and skills.",
        license="MIT",
        category="developer-tools",
        integration_type=IntegrationType.PLUGIN,
        target_agents=["*"],
        supported_scopes=[Scope.GLOBAL, Scope.WORKSPACE],
        source=SourceSpec(
            source_type=SourceType.GIT,
            repository="https://github.com/test/python-lint-plugin",
            commit_sha="1234567890abcdef1234567890abcdef12345678",
        ),
        trust=TrustMetadata(
            publisher=PublisherClaimSpec(name="Plugin Team"),
        ),
        tags=["python", "plugin", "linter"],
        handler_spec=HandlerSpecContainer(
            plugin=PluginHandlerSpec(
                components=["ruff-cli", "caveman"],
            )
        ),
    )

    index = RegistryIndex(
        schema_version="1.0",
        generated_at="2026-08-13T12:00:00Z",
        manifests=[m1, m2],
    )
    cache_mgr.save_cache(index)
    return cache_dir


def test_registry_status_not_cached(tmp_path: Path) -> None:
    """Test `aiaddons registry status` when cache does not exist."""
    empty_cache_dir = tmp_path / "empty_cache"
    result = runner.invoke(app, ["registry", "status", "--cache-dir", str(empty_cache_dir)])
    assert result.exit_code == 0
    assert "Cache State:" in result.stdout
    assert "Not Cached" in result.stdout or "Synchronization required" in result.stdout


def test_registry_status_cached(populated_cache_dir: Path) -> None:
    """Test `aiaddons registry status` with populated cache."""
    result = runner.invoke(app, ["registry", "status", "--cache-dir", str(populated_cache_dir)])
    assert result.exit_code == 0
    assert "Valid" in result.stdout
    assert "Available Add-ons: 2" in result.stdout or "2" in result.stdout


def test_registry_status_json(populated_cache_dir: Path) -> None:
    """Test `aiaddons registry status --json` output."""
    result = runner.invoke(
        app, ["registry", "status", "--json", "--cache-dir", str(populated_cache_dir)]
    )
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert parsed["cache_exists"] is True
    assert parsed["is_valid"] is True
    assert parsed["addon_count"] == 2


@patch("aiaddons.cli.commands.registry.RegistryClient")
def test_registry_update_command(mock_client_cls: MagicMock, tmp_path: Path) -> None:
    """Test `aiaddons registry update` command performing atomic cache update."""
    cache_dir = tmp_path / "cache"
    mock_client = MagicMock()

    m1 = IntegrationManifest(
        id="updated-addon",
        name="Updated Addon",
        version="2.0.0",
        description="Updated addon test.",
        license="MIT",
        category="workflow",
        integration_type=IntegrationType.MCP,
        source=SourceSpec(
            source_type=SourceType.PACKAGE,
            package_name="@scope/updated-addon",
            checksum="sha256:1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
        ),
        trust=TrustMetadata(publisher=PublisherClaimSpec(name="Test")),
        handler_spec=HandlerSpecContainer(
            mcp=MCPHandlerSpec(runtime=MCPRuntime.NPX, package_name="@scope/updated-addon")
        ),
    )

    mock_client.registry_url = "https://registry.aiaddons.dev/index.json"
    mock_client.fetch_registry.return_value = RegistryIndex(
        schema_version="1.0",
        generated_at="2026-08-13T12:00:00Z",
        manifests=[m1],
    )
    mock_client_cls.return_value = mock_client

    result = runner.invoke(app, ["registry", "update", "--cache-dir", str(cache_dir)])
    assert result.exit_code == 0
    assert "Registry update complete." in result.stdout

    # Verify cache file was written
    cache_mgr = RegistryCacheManager(cache_dir=cache_dir)
    data, err = cache_mgr.load_cache_data()
    assert err is None
    assert data is not None
    assert len(data.index.manifests) == 1
    assert data.index.manifests[0].id == "updated-addon"


def test_cli_info_composite_plugin(populated_cache_dir: Path) -> None:
    """Test `aiaddons info python-lint-plugin` displays child components."""
    with patch("aiaddons.registry.cache.RegistryCacheManager") as mock_mgr_cls:
        mock_mgr = RegistryCacheManager(cache_dir=populated_cache_dir)
        mock_mgr_cls.return_value = mock_mgr

        result = runner.invoke(app, ["info", "python-lint-plugin"])
        assert result.exit_code == 0
        assert "Python Lint Composite Plugin" in result.stdout
        assert "Composite Plugin Components" in result.stdout
        assert "ruff-cli" in result.stdout
        assert "caveman" in result.stdout
