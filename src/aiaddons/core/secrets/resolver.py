"""Secret resolution layer for process environment and interactive prompting."""

import getpass
import os
import sys
from collections.abc import Callable
from typing import Protocol

from aiaddons.core.exceptions import SecretResolutionError
from aiaddons.core.models.manifest import EnvVarSpec, IntegrationManifest
from aiaddons.core.secrets.models import (
    ResolvedSecret,
    SecretResolutionSummary,
    SecretStatus,
)


class InputProvider(Protocol):
    """Protocol for secure interactive secret input prompting."""

    def prompt_secret(self, env_name: str, description: str | None = None) -> str: ...


class DefaultTTYInputProvider:
    """Default interactive prompt provider using getpass for no-echo input."""

    def __init__(self, prompt_func: Callable[[str], str] | None = None) -> None:
        self._prompt_func = prompt_func

    def prompt_secret(self, env_name: str, description: str | None = None) -> str:
        """Prompt user securely without echoing input to terminal output."""
        prompt_lines = ["Secret required:", f"  {env_name}"]
        if description:
            prompt_lines.append(f"  ({description})")
        prompt_lines.append("\nEnter value: ")
        prompt_text = "\n".join(prompt_lines)

        if self._prompt_func is not None:
            return self._prompt_func(prompt_text)

        return getpass.getpass(prompt_text)


class SecretResolver:
    """Resolver for managing secret and environment variable requirements."""

    def __init__(
        self,
        input_provider: InputProvider | None = None,
        custom_env: dict[str, str] | None = None,
    ) -> None:
        self.input_provider = input_provider or DefaultTTYInputProvider()
        self.custom_env = custom_env

    def resolve_spec(
        self,
        spec: EnvVarSpec,
        allow_interactive: bool = True,
        override_env: dict[str, str] | None = None,
    ) -> ResolvedSecret:
        """Resolve a single EnvVarSpec against environment or interactive prompt."""
        # 1. Lookup in override_env, custom_env, or os.environ
        env_sources = [override_env, self.custom_env, os.environ]
        val: str | None = None
        for source in env_sources:
            if source is not None and spec.name in source:
                candidate = source[spec.name]
                if candidate:
                    val = candidate
                    break

        if val is not None:
            status = SecretStatus.CONFIGURED if spec.secret else SecretStatus.NOT_SECRET
            return ResolvedSecret(
                name=spec.name,
                is_secret=spec.secret,
                is_required=spec.required,
                description=spec.description,
                status=status,
                value=val,
            )

        # 2. Variable missing or empty
        if spec.required:
            if allow_interactive:
                can_prompt = True
                if isinstance(self.input_provider, DefaultTTYInputProvider):
                    if self.input_provider._prompt_func is None and not sys.stdin.isatty():
                        can_prompt = False

                if can_prompt:
                    try:
                        prompted_val = self.input_provider.prompt_secret(
                            spec.name, spec.description
                        )
                        if prompted_val and prompted_val.strip():
                            status = (
                                SecretStatus.CONFIGURED if spec.secret else SecretStatus.NOT_SECRET
                            )
                            return ResolvedSecret(
                                name=spec.name,
                                is_secret=spec.secret,
                                is_required=spec.required,
                                description=spec.description,
                                status=status,
                                value=prompted_val.strip(),
                            )
                    except SecretResolutionError:
                        raise
                    except Exception as err:
                        msg = f"Failed to prompt for secret '{spec.name}': {err}"
                        raise SecretResolutionError(msg) from err

            if allow_interactive:
                msg = f"Missing required environment variable '{spec.name}'."
            else:
                msg = (
                    f"Missing required environment variable '{spec.name}'. "
                    "Non-interactive mode cannot prompt for secrets."
                )
            raise SecretResolutionError(msg)

        # 3. Optional variable missing
        return ResolvedSecret(
            name=spec.name,
            is_secret=spec.secret,
            is_required=spec.required,
            description=spec.description,
            status=SecretStatus.OPTIONAL_MISSING,
            value=None,
        )

    def resolve_specs(
        self,
        specs: list[EnvVarSpec],
        allow_interactive: bool = True,
        override_env: dict[str, str] | None = None,
    ) -> list[ResolvedSecret]:
        """Resolve a list of EnvVarSpecs."""
        resolved: list[ResolvedSecret] = []
        for spec in specs:
            res = self.resolve_spec(
                spec, allow_interactive=allow_interactive, override_env=override_env
            )
            resolved.append(res)
        return resolved

    def resolve_manifest(
        self,
        manifest: IntegrationManifest,
        allow_interactive: bool = True,
        override_env: dict[str, str] | None = None,
    ) -> list[ResolvedSecret]:
        """Extract and resolve all EnvVarSpecs declared in an IntegrationManifest."""
        specs: list[EnvVarSpec] = []
        if manifest.handler_spec.mcp and manifest.handler_spec.mcp.env_vars:
            specs.extend(manifest.handler_spec.mcp.env_vars)
        return self.resolve_specs(
            specs, allow_interactive=allow_interactive, override_env=override_env
        )

    def summarize(self, resolved_secrets: list[ResolvedSecret]) -> SecretResolutionSummary:
        """Generate a safe, non-sensitive summary of resolution results."""
        resolved_names: list[str] = []
        missing_req: list[str] = []
        missing_opt: list[str] = []

        for r in resolved_secrets:
            if r.status in (SecretStatus.CONFIGURED, SecretStatus.NOT_SECRET):
                resolved_names.append(r.name)
            elif r.status == SecretStatus.REQUIRED_MISSING:
                missing_req.append(r.name)
            elif r.status == SecretStatus.OPTIONAL_MISSING:
                missing_opt.append(r.name)

        return SecretResolutionSummary(
            specs_evaluated=len(resolved_secrets),
            secrets_resolved=resolved_names,
            missing_required=missing_req,
            missing_optional=missing_opt,
            is_complete=len(missing_req) == 0,
        )
