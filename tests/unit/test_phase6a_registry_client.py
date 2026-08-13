"""Unit tests for Phase 6A RegistryClient."""

from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from aiaddons.core.exceptions import RegistryFetchError
from aiaddons.registry.client import RegistryClient

SAMPLE_CHECKSUM = "sha256:1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"


def _sample_valid_registry_data() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "generated_at": "2026-08-13T12:00:00Z",
        "manifests": [
            {
                "id": "sample-mcp",
                "name": "Sample MCP",
                "version": "1.0.0",
                "description": "Sample MCP server for testing.",
                "license": "MIT",
                "category": "developer-tools",
                "integration_type": "mcp",
                "target_agents": ["claude-code"],
                "supported_scopes": ["global", "workspace"],
                "source": {
                    "source_type": "package",
                    "package_name": "@scope/sample-mcp",
                    "checksum": SAMPLE_CHECKSUM,
                },
                "trust": {"publisher": {"name": "Test Publisher"}},
                "handler_spec": {
                    "mcp": {
                        "runtime": "npx",
                        "package_name": "@scope/sample-mcp",
                    }
                },
            }
        ],
    }


def test_insecure_http_endpoint_rejected() -> None:
    """Test that insecure http:// remote endpoint is rejected immediately."""
    client = RegistryClient(registry_url="http://untrusted-remote.com/index.json")
    with pytest.raises(RegistryFetchError) as exc_info:
        client.fetch_registry()
    assert "Insecure HTTP endpoint" in str(exc_info.value)


def test_localhost_http_endpoint_allowed() -> None:
    """Test that http://localhost is allowed for local test/dev server."""
    client = RegistryClient(registry_url="http://localhost:8080/index.json")
    client._validate_endpoint_url(client.registry_url)  # Should not raise


def test_successful_fetch() -> None:
    """Test successful registry metadata fetch and validation."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = _sample_valid_registry_data()

    mock_httpx_client = MagicMock(spec=httpx.Client)
    mock_httpx_client.get.return_value = mock_resp

    client = RegistryClient(
        registry_url="https://registry.aiaddons.dev/index.json",
        httpx_client=mock_httpx_client,
    )

    index = client.fetch_registry()
    assert index.schema_version == "1.0"
    assert len(index.manifests) == 1
    assert index.manifests[0].id == "sample-mcp"


def test_http_404_error() -> None:
    """Test HTTP 404 response raises RegistryFetchError."""
    mock_resp = MagicMock()
    mock_resp.status_code = 404

    mock_httpx_client = MagicMock(spec=httpx.Client)
    mock_httpx_client.get.return_value = mock_resp

    client = RegistryClient(
        registry_url="https://registry.aiaddons.dev/index.json",
        httpx_client=mock_httpx_client,
    )

    with pytest.raises(RegistryFetchError) as exc_info:
        client.fetch_registry()
    assert "HTTP status 404" in str(exc_info.value)


def test_http_timeout_error() -> None:
    """Test connection timeout raises RegistryFetchError."""
    mock_httpx_client = MagicMock(spec=httpx.Client)
    mock_httpx_client.get.side_effect = httpx.TimeoutException("Connection timed out")

    client = RegistryClient(
        registry_url="https://registry.aiaddons.dev/index.json",
        httpx_client=mock_httpx_client,
    )

    with pytest.raises(RegistryFetchError) as exc_info:
        client.fetch_registry()
    assert "timeout" in str(exc_info.value).lower()


def test_malformed_json_response() -> None:
    """Test non-JSON response raises RegistryFetchError."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.side_effect = ValueError("Invalid JSON token")

    mock_httpx_client = MagicMock(spec=httpx.Client)
    mock_httpx_client.get.return_value = mock_resp

    client = RegistryClient(
        registry_url="https://registry.aiaddons.dev/index.json",
        httpx_client=mock_httpx_client,
    )

    with pytest.raises(RegistryFetchError) as exc_info:
        client.fetch_registry()
    assert "parse JSON" in str(exc_info.value)


def test_invalid_registry_schema_rejection() -> None:
    """Test response failing schema validation raises RegistryFetchError."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "schema_version": "9.9",
        "manifests": [],
    }

    mock_httpx_client = MagicMock(spec=httpx.Client)
    mock_httpx_client.get.return_value = mock_resp

    client = RegistryClient(
        registry_url="https://registry.aiaddons.dev/index.json",
        httpx_client=mock_httpx_client,
    )

    with pytest.raises(RegistryFetchError) as exc_info:
        client.fetch_registry()
    assert "validation failed" in str(exc_info.value).lower()
