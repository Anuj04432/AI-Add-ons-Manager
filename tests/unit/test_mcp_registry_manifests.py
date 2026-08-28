"""Validation and schema compliance test suite for official MCP registry manifests and starter stack."""

from pathlib import Path
import pytest
import yaml

from aiaddons.core.models.manifest import (
    IntegrationManifest,
    IntegrationType,
    MCPRuntime,
    MCPTransport,
    SourceType,
    VerificationStatus,
)
from aiaddons.core.models.stack import AddonStack, parse_stack_file
from aiaddons.registry.loader import RegistryLoader


MANIFESTS_DIR = Path("registry/addons")
STACKS_DIR = Path("registry/stacks")


@pytest.fixture
def registry_loader() -> RegistryLoader:
    return RegistryLoader()


# ============================================================================
# 1. Manifest Loading and Strict Schema Validation
# ============================================================================


def test_github_mcp_manifest_validates(registry_loader: RegistryLoader) -> None:
    """Verify github-mcp manifest loads with valid schema, official repo, and GITHUB_PERSONAL_ACCESS_TOKEN."""
    file_path = MANIFESTS_DIR / "github-mcp.yaml"
    assert file_path.exists()

    manifest = registry_loader.load_file(file_path)
    assert manifest.id == "github-mcp"
    assert manifest.name == "GitHub MCP Server"
    assert manifest.version == "1.2.0"
    assert manifest.integration_type == IntegrationType.MCP
    assert manifest.category == "developer-tools"
    assert manifest.documentation_url == "https://github.com/github/github-mcp-server"
    assert manifest.source.source_type == SourceType.PACKAGE
    assert manifest.source.package_name == "@modelcontextprotocol/server-github"
    assert manifest.source.checksum and manifest.source.checksum.startswith("sha256:")
    assert manifest.trust.verification_status == VerificationStatus.VERIFIED
    assert manifest.trust.allowed_executables == ["npx"]
    assert manifest.handler_spec.mcp is not None
    assert manifest.handler_spec.mcp.runtime == MCPRuntime.NPX
    assert manifest.handler_spec.mcp.transport == MCPTransport.STDIO
    assert manifest.handler_spec.mcp.package_name == "@modelcontextprotocol/server-github"

    env_vars = manifest.handler_spec.mcp.env_vars
    assert len(env_vars) == 1
    assert env_vars[0].name == "GITHUB_PERSONAL_ACCESS_TOKEN"
    assert env_vars[0].required is True
    assert env_vars[0].secret is True


def test_context7_mcp_manifest_validates(registry_loader: RegistryLoader) -> None:
    """Verify context7-mcp manifest loads with valid schema, Upstash publisher, and optional CONTEXT7_API_KEY."""
    file_path = MANIFESTS_DIR / "context7-mcp.yaml"
    assert file_path.exists()

    manifest = registry_loader.load_file(file_path)
    assert manifest.id == "context7-mcp"
    assert manifest.name == "Context7 Documentation MCP Server"
    assert manifest.version == "1.0.0"
    assert manifest.integration_type == IntegrationType.MCP
    assert manifest.category == "documentation"
    assert manifest.documentation_url == "https://github.com/upstash/context7"
    assert manifest.source.source_type == SourceType.PACKAGE
    assert manifest.source.package_name == "@upstash/context7-mcp"
    assert manifest.source.checksum and manifest.source.checksum.startswith("sha256:")
    assert manifest.trust.verification_status == VerificationStatus.VERIFIED
    assert manifest.trust.publisher.name == "Upstash"
    assert manifest.trust.allowed_executables == ["npx"]
    assert manifest.handler_spec.mcp is not None
    assert manifest.handler_spec.mcp.runtime == MCPRuntime.NPX
    assert manifest.handler_spec.mcp.package_name == "@upstash/context7-mcp"

    env_vars = manifest.handler_spec.mcp.env_vars
    assert len(env_vars) == 1
    assert env_vars[0].name == "CONTEXT7_API_KEY"
    assert env_vars[0].required is False
    assert env_vars[0].secret is True


def test_playwright_mcp_manifest_validates(registry_loader: RegistryLoader) -> None:
    """Verify playwright-mcp manifest loads with Microsoft Playwright package and Apache-2.0 license."""
    file_path = MANIFESTS_DIR / "playwright-mcp.yaml"
    assert file_path.exists()

    manifest = registry_loader.load_file(file_path)
    assert manifest.id == "playwright-mcp"
    assert manifest.name == "Playwright Browser Automation MCP Server"
    assert manifest.version == "1.0.0"
    assert manifest.license == "Apache-2.0"
    assert manifest.integration_type == IntegrationType.MCP
    assert manifest.category == "testing"
    assert manifest.documentation_url == "https://github.com/microsoft/playwright-mcp"
    assert manifest.source.source_type == SourceType.PACKAGE
    assert manifest.source.package_name == "@playwright/mcp"
    assert manifest.source.checksum and manifest.source.checksum.startswith("sha256:")
    assert manifest.trust.verification_status == VerificationStatus.VERIFIED
    assert manifest.trust.publisher.name == "Microsoft Playwright Team"
    assert manifest.trust.allowed_executables == ["npx"]
    assert manifest.handler_spec.mcp is not None
    assert manifest.handler_spec.mcp.runtime == MCPRuntime.NPX
    assert manifest.handler_spec.mcp.package_name == "@playwright/mcp"
    assert len(manifest.handler_spec.mcp.env_vars) == 0


def test_postgres_mcp_manifest_validates(registry_loader: RegistryLoader) -> None:
    """Verify postgres-mcp manifest loads with DATABASE_URL secret configuration."""
    file_path = MANIFESTS_DIR / "postgres-mcp.yaml"
    assert file_path.exists()

    manifest = registry_loader.load_file(file_path)
    assert manifest.id == "postgres-mcp"
    assert manifest.name == "PostgreSQL Database MCP Server"
    assert manifest.version == "1.1.0"
    assert manifest.integration_type == IntegrationType.MCP
    assert manifest.category == "database"
    assert manifest.source.package_name == "@modelcontextprotocol/server-postgres"
    assert manifest.source.checksum and manifest.source.checksum.startswith("sha256:")
    assert manifest.trust.allowed_executables == ["npx"]
    assert manifest.handler_spec.mcp is not None
    assert manifest.handler_spec.mcp.runtime == MCPRuntime.NPX
    assert manifest.handler_spec.mcp.package_name == "@modelcontextprotocol/server-postgres"

    env_vars = manifest.handler_spec.mcp.env_vars
    assert len(env_vars) == 1
    assert env_vars[0].name == "DATABASE_URL"
    assert env_vars[0].required is True
    assert env_vars[0].secret is True


def test_filesystem_mcp_manifest_validates(registry_loader: RegistryLoader) -> None:
    """Verify filesystem-mcp manifest loads correctly for reference filesystem server."""
    file_path = MANIFESTS_DIR / "filesystem-mcp.yaml"
    assert file_path.exists()

    manifest = registry_loader.load_file(file_path)
    assert manifest.id == "filesystem-mcp"
    assert manifest.name == "Filesystem MCP Server"
    assert manifest.version == "1.0.0"
    assert manifest.integration_type == IntegrationType.MCP
    assert manifest.category == "developer-tools"
    assert manifest.source.package_name == "@modelcontextprotocol/server-filesystem"
    assert manifest.source.checksum and manifest.source.checksum.startswith("sha256:")
    assert manifest.trust.allowed_executables == ["npx"]
    assert manifest.handler_spec.mcp is not None
    assert manifest.handler_spec.mcp.runtime == MCPRuntime.NPX
    assert manifest.handler_spec.mcp.package_name == "@modelcontextprotocol/server-filesystem"
    assert len(manifest.handler_spec.mcp.env_vars) == 0


def test_brave_search_mcp_manifest_validates(registry_loader: RegistryLoader) -> None:
    """Verify brave-search-mcp manifest loads with required BRAVE_API_KEY secret."""
    file_path = MANIFESTS_DIR / "brave-search-mcp.yaml"
    assert file_path.exists()

    manifest = registry_loader.load_file(file_path)
    assert manifest.id == "brave-search-mcp"
    assert manifest.name == "Brave Search MCP Server"
    assert manifest.version == "1.0.0"
    assert manifest.integration_type == IntegrationType.MCP
    assert manifest.category == "search"
    assert manifest.source.package_name == "@modelcontextprotocol/server-brave-search"
    assert manifest.source.checksum and manifest.source.checksum.startswith("sha256:")
    assert manifest.trust.allowed_executables == ["npx"]
    assert manifest.handler_spec.mcp is not None
    assert manifest.handler_spec.mcp.runtime == MCPRuntime.NPX
    assert manifest.handler_spec.mcp.package_name == "@modelcontextprotocol/server-brave-search"

    env_vars = manifest.handler_spec.mcp.env_vars
    assert len(env_vars) == 1
    assert env_vars[0].name == "BRAVE_API_KEY"
    assert env_vars[0].required is True
    assert env_vars[0].secret is True


# ============================================================================
# 2. Strict Schema Guardrails & Security Rejection
# ============================================================================


@pytest.mark.parametrize(
    "manifest_file",
    [
        "github-mcp.yaml",
        "context7-mcp.yaml",
        "playwright-mcp.yaml",
        "postgres-mcp.yaml",
        "filesystem-mcp.yaml",
        "brave-search-mcp.yaml",
    ],
)
def test_manifest_rejects_extra_unrecognized_fields(manifest_file: str) -> None:
    """Ensure extra='forbid' rejects any undeclared or invented fields across all 6 manifests."""
    file_path = MANIFESTS_DIR / manifest_file
    raw_dict = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    raw_dict["unauthorized_custom_field"] = "malicious_payload"

    with pytest.raises(Exception, match="extra_forbidden|Extra inputs are not permitted"):
        IntegrationManifest.model_validate(raw_dict)


@pytest.mark.parametrize(
    "manifest_file",
    [
        "github-mcp.yaml",
        "context7-mcp.yaml",
        "playwright-mcp.yaml",
        "postgres-mcp.yaml",
        "filesystem-mcp.yaml",
        "brave-search-mcp.yaml",
    ],
)
def test_manifest_rejects_shell_injection(manifest_file: str) -> None:
    """Ensure shell metacharacters in package names or fields are blocked by validators."""
    file_path = MANIFESTS_DIR / manifest_file
    raw_dict = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    raw_dict["handler_spec"]["mcp"]["package_name"] = "@scope/pkg; cat /etc/passwd"

    with pytest.raises(ValueError):
        IntegrationManifest.model_validate(raw_dict)


# ============================================================================
# 3. Starter Stack Validation Tests
# ============================================================================


def test_dev_starter_stack_file_parses_correctly() -> None:
    """Verify dev-starter-stack.yaml parses via parse_stack_file and resolves the 3 addon IDs."""
    stack_path = STACKS_DIR / "dev-starter-stack.yaml"
    assert stack_path.exists(), "registry/stacks/dev-starter-stack.yaml must exist"

    items = parse_stack_file(stack_path)
    assert len(items) == 3
    addon_ids = [addon_id for addon_id, _ in items]
    assert addon_ids == ["github-mcp", "context7-mcp", "playwright-mcp"]


def test_dev_starter_stack_model_validation() -> None:
    """Verify AddonStack Pydantic model directly validates dev-starter-stack.yaml."""
    stack_path = STACKS_DIR / "dev-starter-stack.yaml"
    raw_dict = yaml.safe_load(stack_path.read_text(encoding="utf-8"))

    stack = AddonStack.model_validate(raw_dict)
    assert stack.name == "Developer Starter Stack"
    assert len(stack.addons) == 3
    assert "github-mcp" in stack.addons
    assert "context7-mcp" in stack.addons
    assert "playwright-mcp" in stack.addons
