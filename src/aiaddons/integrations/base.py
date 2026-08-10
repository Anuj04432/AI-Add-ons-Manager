"""Base integration installer defining interface for plan generation and validation."""

from abc import ABC, abstractmethod

from aiaddons.core.compatibility.models import CompatibilityResult
from aiaddons.core.installer.models import InstallationPlan
from aiaddons.core.models.agent import AgentDetectionResult, Scope
from aiaddons.core.models.manifest import IntegrationManifest, IntegrationType


class BaseIntegrationInstaller(ABC):
    """Abstract base class for type-specific integration plan generators and validators."""

    @abstractmethod
    def supported_types(self) -> list[IntegrationType]:
        """Return list of IntegrationTypes supported by this installer."""
        ...

    @abstractmethod
    def validate(
        self,
        manifest: IntegrationManifest,
        agent: AgentDetectionResult,
        scope: Scope,
    ) -> list[str]:
        """Validate manifest against target agent and scope, returning error messages if invalid."""
        ...

    @abstractmethod
    def generate_plan(
        self,
        manifest: IntegrationManifest,
        agent: AgentDetectionResult,
        scope: Scope,
        compatibility: CompatibilityResult,
    ) -> InstallationPlan:
        """Generate a structured installation plan without executing any external changes."""
        ...
