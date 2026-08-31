"""CLI command for synchronizing workspace environments with lockfiles or stack files (Phase 6E)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, NoReturn

import typer
from rich.console import Console

from aiaddons.agents.manager import AgentDetectionManager
from aiaddons.cli.exit_codes import ExitCode
from aiaddons.core.exceptions import (
    AIAddonsError,
    IncompatibleAgentError,
    LockfileNotFoundError,
    ManifestValidationError,
    SecretResolutionError,
    SecurityValidationError,
    SyncError,
    SyncVerificationError,
    UnsupportedIntegrationTypeError,
    UnsupportedScopeError,
    VerificationError,
)
from aiaddons.core.execution.external.security import mask_secrets_in_text
from aiaddons.core.models.agent import Scope
from aiaddons.core.secrets.resolver import SecretResolver
from aiaddons.core.sync.engine import SyncEngine
from aiaddons.core.sync.models import SyncDiff, SyncPlan, SyncResult
from aiaddons.registry.registry import Registry
from aiaddons.state.lockfile import LockfileManager
from aiaddons.state.store import InstalledStateStore

console = Console()


def _get_symbols() -> tuple[str, str]:
    """Return platform-safe symbols for checkmark and cross mark."""
    try:
        "✓".encode(sys.stdout.encoding or "utf-8")
        return "✓", "✗"
    except Exception:
        return "+", "-"


def _format_sync_json_response(
    agent: str,
    scope: str,
    in_sync: bool,
    diff: SyncDiff,
    installed: list[str],
    pruned: list[str],
    updated: list[str],
    dry_run: bool,
    success: bool,
    error_message: str | None = None,
) -> str:
    """Format structured machine-readable JSON response for sync command."""
    diff_data = {
        "missing": [
            {
                "addon_id": item.addon_id,
                "name": item.name,
                "target_agent": item.target_agent,
                "expected_version": item.expected_version,
                "integration_type": item.integration_type.value if item.integration_type else None,
            }
            for item in diff.missing
        ],
        "extra": [
            {
                "addon_id": item.addon_id,
                "name": item.name,
                "target_agent": item.target_agent,
                "installed_version": item.installed_version,
                "integration_type": item.integration_type.value if item.integration_type else None,
            }
            for item in diff.extra
        ],
        "mismatched": [
            {
                "addon_id": item.addon_id,
                "name": item.name,
                "target_agent": item.target_agent,
                "installed_version": item.installed_version,
                "expected_version": item.expected_version,
                "integration_type": item.integration_type.value if item.integration_type else None,
            }
            for item in diff.mismatched
        ],
        "synced": [
            {
                "addon_id": item.addon_id,
                "name": item.name,
                "target_agent": item.target_agent,
                "version": item.installed_version or item.expected_version,
                "integration_type": item.integration_type.value if item.integration_type else None,
            }
            for item in diff.synced
        ],
    }
    data = {
        "agent": agent,
        "scope": scope,
        "in_sync": in_sync,
        "diff": diff_data,
        "actions_taken": {
            "installed": installed,
            "pruned": pruned,
            "updated": updated,
        },
        "dry_run": dry_run,
        "success": success,
        "error_message": error_message,
    }
    return json.dumps(data, indent=2)


def _print_json(data_str: str) -> None:
    """Print raw JSON string directly to stdout without Rich soft-wrapping."""
    sys.stdout.write(data_str + "\n")
    sys.stdout.flush()


def _handle_sync_error(
    msg: str,
    exit_code: int = ExitCode.INVALID_INPUT,
    json_output: bool = False,
    agent: str = "",
    scope: str = "",
    diff: SyncDiff | None = None,
    secret_values: dict[str, str] | None = None,
) -> NoReturn:
    """Safely report error without leaking secrets or Python tracebacks."""
    secrets_list = list(secret_values.values()) if secret_values else []
    safe_msg = mask_secrets_in_text(msg, secrets_list)
    if json_output:
        formatted = _format_sync_json_response(
            agent=agent,
            scope=scope,
            in_sync=False,
            diff=diff or SyncDiff(),
            installed=[],
            pruned=[],
            updated=[],
            dry_run=False,
            success=False,
            error_message=safe_msg,
        )
        _print_json(formatted)
    else:
        console.print(f"[bold red]Error:[/bold red] {safe_msg}")
    raise typer.Exit(code=exit_code)


def sync_command(
    file: Path | None = typer.Option(
        None,
        "--file",
        "-f",
        help="Path to workspace lockfile or stack YAML/JSON file to synchronize against",
    ),
    scope: str = typer.Option(
        "workspace",
        "--scope",
        "-s",
        help="Target configuration scope ('workspace' or 'global')",
    ),
    agent_id: str | None = typer.Option(
        None,
        "--agent",
        "-a",
        help="Target agent ID (e.g. 'claude-code', 'codex', or 'antigravity')",
    ),
    registry_path: Path | None = typer.Option(
        None,
        "--registry",
        "-r",
        help="Path to local registry directory",
    ),
    prune: bool = typer.Option(
        False,
        "--prune",
        help="Remove locally-installed add-ons that are not declared in the lockfile",
    ),
    update: bool = typer.Option(
        False,
        "--update",
        help="Update version-mismatched add-ons to match the lockfile specification",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Simulate synchronization and output diff and plan without applying changes",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Automatically confirm synchronization prompt without interactivity",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Output synchronization diff and results in machine-readable JSON format",
    ),
) -> None:
    """Synchronize local environment with a lockfile or stack file, installing missing add-ons."""
    sym_ok, sym_fail = _get_symbols()
    workspace_dir = Path.cwd().resolve()

    # 1. Parse scope
    try:
        parsed_scope = Scope.from_str(scope)
    except ValueError as exc:
        _handle_sync_error(
            str(exc),
            exit_code=ExitCode.INVALID_INPUT,
            json_output=json_output,
            agent=agent_id or "",
            scope=scope,
        )

    # 2. Detect target agent
    manager = AgentDetectionManager()
    detected_agents = manager.detect_agents()

    if agent_id:
        target_aid = agent_id.strip().lower()
        matched_agent = None
        if target_aid in detected_agents:
            matched_agent = detected_agents[target_aid]
        else:
            normalized_target = target_aid.replace(" ", "-").replace("_", "-")
            if normalized_target in detected_agents:
                matched_agent = detected_agents[normalized_target]
            else:
                for ag in detected_agents.values():
                    if ag.agent_id.lower() == target_aid or ag.name.lower() == target_aid:
                        matched_agent = ag
                        break

        if matched_agent is None:
            _handle_sync_error(
                f"Specified agent '{agent_id}' is not registered.",
                exit_code=ExitCode.INVALID_INPUT,
                json_output=json_output,
                agent=agent_id,
                scope=parsed_scope.value,
            )
        target_agent = matched_agent
        if not target_agent.installed:
            _handle_sync_error(
                f"Specified agent '{agent_id}' is not installed on this system.",
                exit_code=ExitCode.INVALID_INPUT,
                json_output=json_output,
                agent=target_agent.agent_id,
                scope=parsed_scope.value,
            )
    else:
        installed_agents = [ag for ag in detected_agents.values() if ag.installed]
        if not installed_agents:
            _handle_sync_error(
                "No installed agent found on this system.",
                exit_code=ExitCode.INVALID_INPUT,
                json_output=json_output,
                scope=parsed_scope.value,
            )
        target_agent = installed_agents[0]

    # 3. Load Registry
    registry, _ = Registry.load_auto(custom_dir=registry_path)

    # 4. Load Expected Specifications from lockfile or stack file
    state_store = InstalledStateStore()
    lockfile_mgr = LockfileManager()
    sync_engine = SyncEngine(
        state_store=state_store,
        lockfile_manager=lockfile_mgr,
        registry=registry,
    )
    try:
        expected_specs = sync_engine.load_expected_specs(
            lockfile_path=file,
            workspace_dir=workspace_dir,
            target_agent=target_agent.agent_id,
            registry=registry,
        )
    except LockfileNotFoundError as exc:
        _handle_sync_error(
            str(exc),
            exit_code=ExitCode.INVALID_INPUT,
            json_output=json_output,
            agent=target_agent.agent_id,
            scope=parsed_scope.value,
        )
    except ManifestValidationError as exc:
        _handle_sync_error(
            str(exc),
            exit_code=ExitCode.INVALID_INPUT,
            json_output=json_output,
            agent=target_agent.agent_id,
            scope=parsed_scope.value,
        )
    except Exception as exc:
        _handle_sync_error(
            f"Failed to read sync specifications: {exc}",
            exit_code=ExitCode.INVALID_INPUT,
            json_output=json_output,
            agent=target_agent.agent_id,
            scope=parsed_scope.value,
        )

    # 5. Compute Diff
    diff = sync_engine.compute_diff(
        expected_specs=expected_specs,
        workspace_dir=workspace_dir,
        target_agent=target_agent.agent_id,
        scope=parsed_scope,
        registry=registry,
    )

    # 6. Generate Plan
    plan = sync_engine.generate_sync_plan(
        diff=diff,
        target_agent=target_agent,
        scope=parsed_scope,
        lockfile_path=file or (workspace_dir / "aiaddons.lock"),
        prune=prune,
        update=update,
        registry=registry,
    )

    # Check if already fully in sync and no actions requested
    num_actions = len(plan.to_install) + len(plan.to_prune) + len(plan.to_update)

    if num_actions == 0 and diff.is_clean:
        if json_output:
            formatted = _format_sync_json_response(
                agent=target_agent.agent_id,
                scope=parsed_scope.value,
                in_sync=True,
                diff=diff,
                installed=[],
                pruned=[],
                updated=[],
                dry_run=dry_run,
                success=True,
                error_message=None,
            )
            _print_json(formatted)
        else:
            console.print()
            console.print(f"[bold cyan]Sync Status for {target_agent.name} ({parsed_scope.value})[/bold cyan]")
            console.print("-" * 40)
            console.print(f"[bold green]{sym_ok} All {len(diff.synced)} add-on(s) are in sync with the lockfile.[/bold green]")
            for item in diff.synced:
                console.print(f"  [green]{sym_ok}[/green] {item.name} ({item.addon_id}) v{item.installed_version}")
            console.print()
            console.print("[dim]No changes required.[/dim]")
            console.print()
        return

    # 7. Format diff overview and plan preview
    if not json_output:
        console.print()
        console.print(f"[bold cyan]Synchronization Plan for {target_agent.name} ({parsed_scope.value})[/bold cyan]")
        console.print("-" * 50)
        lock_display = str(plan.lockfile_path) if plan.lockfile_path else "aiaddons.lock"
        console.print(f"Lockfile: [dim]{lock_display}[/dim]")
        console.print()

        console.print("[bold]Status & Diff:[/bold]")
        if diff.synced:
            console.print(f"  [green]{sym_ok}[/green] In sync ({len(diff.synced)}):")
            for item in diff.synced:
                console.print(f"    - {item.name} ({item.addon_id}) v{item.installed_version}")

        if diff.missing:
            console.print(f"  [bold cyan]+ Missing to install ({len(diff.missing)}):[/bold cyan]")
            for item in diff.missing:
                ver_str = f" v{item.expected_version}" if item.expected_version else ""
                console.print(f"    - [cyan]{item.name}[/cyan] ({item.addon_id}){ver_str}")

        if diff.extra:
            status_tag = "[bold red]Will prune[/bold red]" if prune else "[bold yellow]Unmanaged (use --prune to remove)[/bold yellow]"
            console.print(f"  [bold yellow]! Unmanaged locally ({len(diff.extra)}) - {status_tag}:[/bold yellow]")
            for item in diff.extra:
                ver_str = f" v{item.installed_version}" if item.installed_version else ""
                console.print(f"    - {item.name} ({item.addon_id}){ver_str}")

        if diff.mismatched:
            status_tag = "[bold cyan]Will update[/bold cyan]" if update else "[bold yellow]Mismatch (use --update to update)[/bold yellow]"
            console.print(f"  [bold magenta]~ Version mismatch ({len(diff.mismatched)}) - {status_tag}:[/bold magenta]")
            for item in diff.mismatched:
                console.print(
                    f"    - {item.name} ({item.addon_id}) "
                    f"\\[installed: {item.installed_version}, expected: {item.expected_version}\\]"
                )

        console.print()

        for warn in plan.warnings:
            console.print(f"[bold yellow]Notice:[/bold yellow] {warn}")
        if plan.warnings:
            console.print()

    # 8. Dry-Run output
    if dry_run:
        if json_output:
            formatted = _format_sync_json_response(
                agent=target_agent.agent_id,
                scope=parsed_scope.value,
                in_sync=diff.is_clean,
                diff=diff,
                installed=[],
                pruned=[],
                updated=[],
                dry_run=True,
                success=True,
                error_message=None,
            )
            _print_json(formatted)
        else:
            console.print("[dim]Dry-run mode: No changes were made.[/dim]")
            console.print()
        return

    # If no mutating actions are scheduled (e.g. only unmanaged/mismatched but no flags passed)
    if num_actions == 0:
        if json_output:
            formatted = _format_sync_json_response(
                agent=target_agent.agent_id,
                scope=parsed_scope.value,
                in_sync=diff.is_clean,
                diff=diff,
                installed=[],
                pruned=[],
                updated=[],
                dry_run=False,
                success=True,
                error_message=None,
            )
            _print_json(formatted)
        else:
            console.print("[bold yellow]No mutating actions scheduled. Pass --prune or --update to resolve remaining drift.[/bold yellow]")
            console.print()
        return

    # 9. Confirmation Prompt
    if not yes:
        confirmed = typer.confirm("Continue with synchronization?", default=False)
        if not confirmed:
            if json_output:
                formatted = _format_sync_json_response(
                    agent=target_agent.agent_id,
                    scope=parsed_scope.value,
                    in_sync=False,
                    diff=diff,
                    installed=[],
                    pruned=[],
                    updated=[],
                    dry_run=False,
                    success=False,
                    error_message="Synchronization cancelled by user.",
                )
                _print_json(formatted)
                raise typer.Exit(code=ExitCode.INVALID_INPUT)
            else:
                console.print("[bold yellow]Synchronization cancelled. No changes were made.[/bold yellow]")
                raise typer.Exit(code=ExitCode.SUCCESS)

    # 10. Resolve secrets for missing add-ons
    secret_map: dict[str, str] = {}
    secret_resolver = SecretResolver()
    for item in plan.to_install:
        manifest = registry.get(item.addon_id)
        if manifest:
            try:
                resolved_secrets = secret_resolver.resolve_manifest(
                    manifest, allow_interactive=sys.stdin.isatty() and not yes
                )
                for sec in resolved_secrets:
                    if sec.value is not None:
                        secret_map[sec.name] = sec.value
            except SecretResolutionError as exc:
                _handle_sync_error(
                    str(exc),
                    exit_code=ExitCode.EXECUTION_FAILURE,
                    json_output=json_output,
                    agent=target_agent.agent_id,
                    scope=parsed_scope.value,
                    diff=diff,
                )

    # 11. Execute Sync
    if not json_output:
        console.print("[bold cyan]Applying synchronization changes...[/bold cyan]")

    try:
        sync_result = sync_engine.execute_sync(
            plan=plan,
            expected_specs=expected_specs,
            target_agent=target_agent,
            workspace_dir=workspace_dir,
            registry=registry,
            dry_run=False,
            secret_values=secret_map,
        )
    except IncompatibleAgentError as exc:
        _handle_sync_error(
            str(exc),
            exit_code=ExitCode.COMPATIBILITY_FAILURE,
            json_output=json_output,
            agent=target_agent.agent_id,
            scope=parsed_scope.value,
            diff=diff,
            secret_values=secret_map,
        )
    except UnsupportedScopeError as exc:
        _handle_sync_error(
            str(exc),
            exit_code=ExitCode.COMPATIBILITY_FAILURE,
            json_output=json_output,
            agent=target_agent.agent_id,
            scope=parsed_scope.value,
            diff=diff,
            secret_values=secret_map,
        )
    except UnsupportedIntegrationTypeError as exc:
        _handle_sync_error(
            str(exc),
            exit_code=ExitCode.COMPATIBILITY_FAILURE,
            json_output=json_output,
            agent=target_agent.agent_id,
            scope=parsed_scope.value,
            diff=diff,
            secret_values=secret_map,
        )
    except SecurityValidationError as exc:
        _handle_sync_error(
            str(exc),
            exit_code=ExitCode.SECURITY_FAILURE,
            json_output=json_output,
            agent=target_agent.agent_id,
            scope=parsed_scope.value,
            diff=diff,
            secret_values=secret_map,
        )
    except SyncVerificationError as exc:
        _handle_sync_error(
            str(exc),
            exit_code=ExitCode.VERIFICATION_FAILURE,
            json_output=json_output,
            agent=target_agent.agent_id,
            scope=parsed_scope.value,
            diff=diff,
            secret_values=secret_map,
        )
    except (SyncError, AIAddonsError) as exc:
        _handle_sync_error(
            str(exc),
            exit_code=ExitCode.EXECUTION_FAILURE,
            json_output=json_output,
            agent=target_agent.agent_id,
            scope=parsed_scope.value,
            diff=diff,
            secret_values=secret_map,
        )

    # 12. Final Result Output
    if json_output:
        formatted = _format_sync_json_response(
            agent=target_agent.agent_id,
            scope=parsed_scope.value,
            in_sync=sync_result.in_sync,
            diff=sync_result.diff,
            installed=sync_result.installed_addons,
            pruned=sync_result.pruned_addons,
            updated=sync_result.updated_addons,
            dry_run=False,
            success=True,
            error_message=None,
        )
        _print_json(formatted)
    else:
        console.print()
        console.print("[bold green]✓ Synchronization completed successfully![/bold green]")
        if sync_result.installed_addons:
            console.print(f"  [green]+ Installed:[/green] {', '.join(sync_result.installed_addons)}")
        if sync_result.pruned_addons:
            console.print(f"  [yellow]- Pruned:[/yellow] {', '.join(sync_result.pruned_addons)}")
        if sync_result.updated_addons:
            console.print(f"  [cyan]~ Updated:[/cyan] {', '.join(sync_result.updated_addons)}")
        console.print()
