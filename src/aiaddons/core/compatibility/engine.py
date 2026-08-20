"""Pure decision engine for evaluating add-on compatibility with detected AI coding agents."""

from __future__ import annotations

import shutil
import sys
from typing import TYPE_CHECKING

from aiaddons.core.compatibility.models import (
    CompatibilityResult,
    DependencyCheckResult,
    DependencyStatus,
)
from aiaddons.core.models.agent import AgentCapability, AgentDetectionResult, Scope
from aiaddons.core.models.manifest import (
    DependencyType,
    IntegrationManifest,
    IntegrationType,
)

if TYPE_CHECKING:
    from aiaddons.registry.registry import Registry


def _normalize_platform(raw_platform: str | None = None) -> str:
    """Normalize system or target platform string to standard OS identifier."""
    target = (raw_platform or sys.platform).lower().strip()
    if target.startswith("win"):
        return "windows"
    if target.startswith("darwin") or target.startswith("mac"):
        return "darwin"
    if target.startswith("linux"):
        return "linux"
    return target


class CompatibilityEngine:
    """In-memory decision engine evaluating manifest compatibility against agents."""

    def __init__(
        self,
        platform_override: str | None = None,
        registry: Registry | None = None,
    ) -> None:
        self.platform = _normalize_platform(platform_override)
        self.registry = registry

    def _evaluate_dependencies(
        self, manifest: IntegrationManifest, agent: AgentDetectionResult
    ) -> list[DependencyCheckResult]:
        """Evaluate declared dependencies without executing arbitrary commands or network calls."""
        results: list[DependencyCheckResult] = []

        for dep in manifest.dependencies:
            if dep.type in (DependencyType.CLI, DependencyType.RUNTIME):
                found_path = shutil.which(dep.name)
                if found_path:
                    results.append(
                        DependencyCheckResult(
                            name=dep.name,
                            type=dep.type,
                            required=dep.required,
                            status=DependencyStatus.SATISFIED,
                            details=f"Executable binary found at '{found_path}'.",
                        )
                    )
                else:
                    results.append(
                        DependencyCheckResult(
                            name=dep.name,
                            type=dep.type,
                            required=dep.required,
                            status=DependencyStatus.MISSING,
                            details=f"CLI executable '{dep.name}' not found in system PATH.",
                        )
                    )

            elif dep.type == DependencyType.AGENT_CAPABILITY:
                try:
                    cap_enum = AgentCapability(dep.name.lower())
                    if cap_enum in agent.capabilities:
                        results.append(
                            DependencyCheckResult(
                                name=dep.name,
                                type=dep.type,
                                required=dep.required,
                                status=DependencyStatus.SATISFIED,
                                details=f"Agent capability '{cap_enum.value}' is present.",
                            )
                        )
                    else:
                        results.append(
                            DependencyCheckResult(
                                name=dep.name,
                                type=dep.type,
                                required=dep.required,
                                status=DependencyStatus.MISSING,
                                details=f"Agent does not declare capability '{cap_enum.value}'.",
                            )
                        )
                except ValueError:
                    results.append(
                        DependencyCheckResult(
                            name=dep.name,
                            type=dep.type,
                            required=dep.required,
                            status=DependencyStatus.UNKNOWN,
                            details=f"Unknown capability identifier '{dep.name}'.",
                        )
                    )

            elif dep.type == DependencyType.ADDON:
                results.append(
                    DependencyCheckResult(
                        name=dep.name,
                        type=dep.type,
                        required=dep.required,
                        status=DependencyStatus.UNKNOWN,
                        details=f"Dependency '{dep.name}' will be checked during installation.",
                    )
                )

        return results

    def evaluate_single(
        self,
        manifest: IntegrationManifest,
        agent: AgentDetectionResult,
        requested_scope: Scope = Scope.WORKSPACE,
    ) -> CompatibilityResult:
        """Evaluate single-node compatibility without resolving composite plugin children."""
        reasons: list[str] = []
        warnings: list[str] = []
        missing_reqs: list[str] = []
        unsupported_reqs: list[str] = []

        is_installed = agent.installed
        if not is_installed:
            reasons.append(f"Agent '{agent.name}' is not installed on this system.")
            missing_reqs.append(f"Agent installation ({agent.name})")

        # 1. Agent Target Check
        targets = [t.lower() for t in manifest.target_agents]
        agent_id_lower = agent.agent_id.lower()
        if "*" not in targets and agent_id_lower not in targets:
            reasons.append(f"Add-on '{manifest.name}' does not list target agent '{agent.name}'.")
            unsupported_reqs.append(f"Target agent: {agent.name}")

        # 2. Scope Compatibility Check
        if requested_scope not in manifest.supported_scopes:
            reasons.append(
                f"Add-on '{manifest.name}' does not support scope '{requested_scope.value}'."
            )
            unsupported_reqs.append(f"Scope: {requested_scope.value}")

        if requested_scope not in agent.supported_scopes:
            reasons.append(
                f"Agent '{agent.name}' does not support requested scope '{requested_scope.value}'."
            )
            unsupported_reqs.append(f"Scope: {requested_scope.value}")

        # 3. Agent Capability Matching
        # Note: IntegrationType.PLUGIN is an aiaddons composite abstraction,
        # NOT a native capability required from agents such as Claude Code or Codex.
        required_capability: AgentCapability | None = None
        if manifest.integration_type == IntegrationType.MCP:
            required_capability = AgentCapability.MCP
        elif manifest.integration_type == IntegrationType.SKILL:
            required_capability = AgentCapability.SKILL

        if required_capability and required_capability not in agent.capabilities:
            reasons.append(
                f"Agent '{agent.name}' does not declare capability '{required_capability.value}'."
            )
            missing_reqs.append(f"Agent capability: {required_capability.value}")

        # 4. Dependency Checks
        dep_results = self._evaluate_dependencies(manifest, agent)
        for dep in dep_results:
            if dep.status == DependencyStatus.MISSING and dep.required:
                reasons.append(f"Required dependency '{dep.name}' ({dep.type.value}) is missing.")
                missing_reqs.append(f"Dependency: {dep.name}")

        # 5. Source Installability Check
        source_ok, source_warning = manifest.source.check_installable()
        if not source_ok and source_warning:
            warnings.append(source_warning)

        # 6. Final Decision for single node
        is_compatible = is_installed and len(reasons) == 0

        if is_compatible and not reasons:
            reasons.append(f"Compatible with {agent.name} for {requested_scope.value} scope.")

        return CompatibilityResult(
            compatible=is_compatible,
            agent_id=agent.agent_id,
            agent_name=agent.name,
            addon_id=manifest.id,
            addon_name=manifest.name,
            requested_scope=requested_scope,
            reasons=reasons,
            warnings=warnings,
            missing_requirements=missing_reqs,
            unsupported_requirements=unsupported_reqs,
            dependencies=dep_results,
            is_installed_agent=is_installed,
            source_installable=source_ok,
            source_install_warning=source_warning,
        )

    def evaluate(
        self,
        manifest: IntegrationManifest,
        agent: AgentDetectionResult,
        requested_scope: Scope = Scope.WORKSPACE,
        registry: Registry | None = None,
        visited: set[str] | None = None,
        stack: list[str] | None = None,
    ) -> CompatibilityResult:
        """Evaluate compatibility of an IntegrationManifest against an AgentDetectionResult."""
        result = self.evaluate_single(manifest, agent, requested_scope)
        if not result.compatible:
            return result

        active_registry = registry or self.registry
        if manifest.integration_type == IntegrationType.PLUGIN:
            if active_registry:
                from aiaddons.core.exceptions import (
                    IncompatibleAgentError,
                    InstallationPlanningError,
                )
                from aiaddons.integrations.plugin import resolve_plugin_components

                try:
                    resolve_plugin_components(
                        manifest=manifest,
                        registry=active_registry,
                        agent=agent,
                        scope=requested_scope,
                        compatibility_engine=self,
                        visited=visited,
                        stack=stack,
                    )
                except (IncompatibleAgentError, InstallationPlanningError) as err:
                    err_msg = str(err)
                    result.compatible = False
                    result.reasons = [err_msg]
                    if isinstance(err, IncompatibleAgentError):
                        result.unsupported_requirements.append(err_msg)
                    else:
                        result.missing_requirements.append(err_msg)
            else:
                spec = manifest.handler_spec.plugin
                if spec and spec.components:
                    err_msg = (
                        "Registry reference is required to resolve child components "
                        f"for plugin '{manifest.name}'."
                    )
                    result.compatible = False
                    result.reasons = [err_msg]
                    result.missing_requirements.append("Registry reference for plugin components")

        return result

    def evaluate_all(
        self,
        manifest: IntegrationManifest,
        agents: list[AgentDetectionResult],
        requested_scope: Scope = Scope.WORKSPACE,
        registry: Registry | None = None,
    ) -> list[CompatibilityResult]:
        """Evaluate an IntegrationManifest against multiple detected agents."""
        return [
            self.evaluate(manifest, agent, requested_scope, registry=registry) for agent in agents
        ]

    def evaluate_batch(
        self,
        manifests: list[IntegrationManifest],
        agent: AgentDetectionResult,
        requested_scope: Scope = Scope.WORKSPACE,
        registry: Registry | None = None,
    ) -> list[CompatibilityResult]:
        """Evaluate compatibility for a batch of manifests against an agent, catching inter-addon conflicts."""
        active_registry = registry or self.registry
        results: list[CompatibilityResult] = []
        batch_ids = {m.id.lower() for m in manifests}

        # 1. Evaluate individual compatibility
        for manifest in manifests:
            res = self.evaluate(manifest, agent, requested_scope, registry=active_registry)
            results.append(res)

        # 2. Check inter-addon resource conflicts
        # Check MCP server name collisions with different configurations
        mcp_servers: dict[str, str] = {}
        for manifest in manifests:
            if manifest.integration_type == IntegrationType.MCP and manifest.handler_spec.mcp:
                server_name = manifest.id.lower()
                pkg = manifest.handler_spec.mcp.package_name
                if server_name in mcp_servers and mcp_servers[server_name] != pkg:
                    conflict_msg = (
                        f"Inter-addon conflict: Multiple add-ons configure MCP server '{server_name}' "
                        f"with differing package names ('{mcp_servers[server_name]}' vs '{pkg}')."
                    )
                    for r in results:
                        if r.addon_id.lower() == manifest.id.lower():
                            r.compatible = False
                            r.reasons.append(conflict_msg)
                            r.unsupported_requirements.append(conflict_msg)
                else:
                    mcp_servers[server_name] = pkg

        # Check dependency conflicts between manifests in the batch
        dep_constraints: dict[str, tuple[str, str | None]] = {}
        from packaging.specifiers import InvalidSpecifier, SpecifierSet

        for manifest in manifests:
            for dep in manifest.dependencies:
                dep_key = f"{dep.type.value}:{dep.name.lower()}"
                if dep.version_constraint:
                    if dep_key in dep_constraints:
                        prev_manifest_id, prev_constraint = dep_constraints[dep_key]
                        if prev_constraint:
                            try:
                                combined = SpecifierSet(f"{prev_constraint},{dep.version_constraint}")
                                test_versions = [
                                    f"{maj}.{min_}.{pat}"
                                    for maj in range(0, 15)
                                    for min_ in range(0, 20)
                                    for pat in (0, 1, 5)
                                ]
                                if not any(v in combined for v in test_versions):
                                    conflict_msg = (
                                        f"Inter-addon dependency conflict on '{dep.name}': '{manifest.name}' "
                                        f"requires '{dep.version_constraint}', conflicting with '{prev_manifest_id}' "
                                        f"requiring '{prev_constraint}'."
                                    )
                                    for r in results:
                                        if r.addon_id.lower() in (manifest.id.lower(), prev_manifest_id.lower()):
                                            r.compatible = False
                                            r.reasons.append(conflict_msg)
                                            r.unsupported_requirements.append(conflict_msg)
                            except (InvalidSpecifier, Exception):
                                pass
                    else:
                        dep_constraints[dep_key] = (manifest.id, dep.version_constraint)

                # Check ADDON dependencies within batch
                if dep.type == DependencyType.ADDON and dep.required:
                    if dep.name.lower() not in batch_ids:
                        if active_registry and not active_registry.get(dep.name):
                            missing_dep_msg = (
                                f"Required add-on dependency '{dep.name}' for '{manifest.name}' "
                                "is not present in the batch or registry."
                            )
                            for r in results:
                                if r.addon_id.lower() == manifest.id.lower():
                                    r.compatible = False
                                    r.reasons.append(missing_dep_msg)
                                    r.missing_requirements.append(missing_dep_msg)

        return results

