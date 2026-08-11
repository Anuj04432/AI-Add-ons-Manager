"""Pure decision engine for evaluating add-on compatibility with detected AI coding agents."""

import shutil
import sys

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

    def __init__(self, platform_override: str | None = None) -> None:
        self.platform = _normalize_platform(platform_override)

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

    def evaluate(
        self,
        manifest: IntegrationManifest,
        agent: AgentDetectionResult,
        requested_scope: Scope = Scope.WORKSPACE,
    ) -> CompatibilityResult:
        """Evaluate compatibility of an IntegrationManifest against an AgentDetectionResult."""
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
        required_capability: AgentCapability | None = None
        if manifest.integration_type == IntegrationType.MCP:
            required_capability = AgentCapability.MCP
        elif manifest.integration_type == IntegrationType.SKILL:
            required_capability = AgentCapability.SKILL
        elif manifest.integration_type == IntegrationType.PLUGIN:
            required_capability = AgentCapability.PLUGIN

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

        # 6. Final Decision
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

    def evaluate_all(
        self,
        manifest: IntegrationManifest,
        agents: list[AgentDetectionResult],
        requested_scope: Scope = Scope.WORKSPACE,
    ) -> list[CompatibilityResult]:
        """Evaluate an IntegrationManifest against multiple detected agents."""
        return [self.evaluate(manifest, agent, requested_scope) for agent in agents]
