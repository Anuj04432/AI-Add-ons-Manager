"""CLI command for installing add-ons and generating dry-run installation plans (Phase 5B.10)."""

import json
import sys
from pathlib import Path
from typing import Any, NoReturn

import typer
from rich.console import Console

from aiaddons.agents.manager import AgentDetectionManager
from aiaddons.cli.exit_codes import ExitCode
from aiaddons.core.compatibility.engine import CompatibilityEngine
from aiaddons.core.exceptions import (
    AIAddonsError,
    ExecutableNotFoundError,
    ExternalExecutionError,
    IncompatibleAgentError,
    InstallationPlanningError,
    ProcessExecutionError,
    ProcessTimeoutError,
    SecretResolutionError,
    SecurityValidationError,
    UnsupportedIntegrationTypeError,
    UnsupportedScopeError,
    VerificationError,
    VerificationPathSecurityError,
)
from aiaddons.core.execution.engine import ExecutionEngine
from aiaddons.core.execution.external.security import mask_secrets_in_text
from aiaddons.core.execution.models import ExecutionStatus
from aiaddons.core.installer.engine import InstallationEngine
from aiaddons.core.installer.models import TransactionPhase
from aiaddons.core.models.agent import Scope
from aiaddons.core.secrets.resolver import SecretResolver
from aiaddons.core.verification.engine import VerificationEngine
from aiaddons.core.verification.models import VerificationStatus
from aiaddons.registry.registry import Registry

console = Console()


def _get_symbols() -> tuple[str, str]:
    """Return platform-safe symbols for checkmark and cross mark."""
    try:
        "✓".encode(sys.stdout.encoding or "utf-8")
        return "✓", "✗"
    except Exception:
        return "+", "-"


def _format_json_response(
    addon_id: str,
    addon_name: str,
    agent: str,
    scope: str,
    compatible: bool,
    planned_operations: list[dict[str, Any]],
    warnings: list[str],
    verification_status: str,
    transaction_status: str,
    success: bool,
    error_message: str | None = None,
) -> str:
    """Deterministically format JSON response for scripting interface."""
    data = {
        "addon_id": addon_id,
        "addon_name": addon_name,
        "agent": agent,
        "scope": scope,
        "compatible": compatible,
        "planned_operations": planned_operations,
        "warnings": warnings,
        "verification_status": verification_status,
        "transaction_status": transaction_status,
        "success": success,
        "error_message": error_message,
    }
    return json.dumps(data, indent=2)


def _print_json(data_str: str) -> None:
    """Print raw JSON string directly to stdout without Rich soft-wrapping."""
    sys.stdout.write(data_str + "\n")
    sys.stdout.flush()


def _handle_error(
    msg: str,
    exit_code: int = ExitCode.INVALID_INPUT,
    json_output: bool = False,
    addon_id: str = "",
    addon_name: str = "",
    agent: str = "",
    scope: str = "",
    transaction_status: str = TransactionPhase.FAILED.name,
    secret_values: dict[str, str] | None = None,
) -> NoReturn:
    """Safely report error without leaking secrets or Python tracebacks."""
    secrets_list = list(secret_values.values()) if secret_values else []
    safe_msg = mask_secrets_in_text(msg, secrets_list)
    if json_output:
        formatted = _format_json_response(
            addon_id=addon_id,
            addon_name=addon_name,
            agent=agent,
            scope=scope,
            compatible=False,
            planned_operations=[],
            warnings=[],
            verification_status="failed",
            transaction_status=transaction_status,
            success=False,
            error_message=safe_msg,
        )
        _print_json(formatted)
    else:
        console.print(f"[bold red]Error:[/bold red] {safe_msg}")
    raise typer.Exit(code=exit_code)


def install_command(
    addon_id: str = typer.Argument(..., help="ID of the add-on to install"),
    scope: str = typer.Option(
        "workspace",
        "--scope",
        "-s",
        help="Target configuration scope ('global' or 'workspace')",
    ),
    agent_id: str | None = typer.Option(
        None,
        "--agent",
        "-a",
        help="Target agent ID (e.g. 'claude-code' or 'codex')",
    ),
    registry_path: Path | None = typer.Option(
        None,
        "--registry",
        "-r",
        help="Path to local registry directory",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Simulate installation and output plan without applying changes",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Automatically confirm installation prompt without interactivity",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Output installation results in machine-readable JSON format",
    ),
) -> None:
    """Install an add-on or output a dry-run installation plan with safety verification."""
    sym_ok, sym_fail = _get_symbols()

    # 1. Locate registry directory
    target_registry_dir = registry_path
    if target_registry_dir is None:
        default_dir = Path("registry")
        if default_dir.exists() and default_dir.is_dir():
            target_registry_dir = default_dir
        else:
            addons_dir = Path.cwd() / "registry" / "addons"
            target_registry_dir = addons_dir if addons_dir.exists() else Path.cwd() / "registry"

    if not target_registry_dir.exists():
        _handle_error(
            f"Registry directory '{target_registry_dir}' not found.",
            exit_code=ExitCode.INVALID_INPUT,
            json_output=json_output,
            addon_id=addon_id,
            scope=scope,
            agent=agent_id or "",
        )

    # 2. Load registry and manifest
    registry, _ = Registry.from_directory(target_registry_dir)
    manifest = registry.get(addon_id)
    if not manifest:
        _handle_error(
            f"Add-on '{addon_id}' not found in registry.",
            exit_code=ExitCode.INVALID_INPUT,
            json_output=json_output,
            addon_id=addon_id,
            scope=scope,
            agent=agent_id or "",
        )

    # 3. Parse requested scope
    try:
        parsed_scope = Scope.from_str(scope)
    except ValueError as exc:
        _handle_error(
            str(exc),
            exit_code=ExitCode.INVALID_INPUT,
            json_output=json_output,
            addon_id=addon_id,
            addon_name=manifest.name,
            scope=scope,
            agent=agent_id or "",
        )

    # 4. Detect agents
    manager = AgentDetectionManager()
    detected_agents = manager.detect_agents()

    # 5. Filter target agents
    if agent_id:
        target_aid = agent_id.strip().lower()
        if target_aid not in detected_agents:
            _handle_error(
                f"Specified agent '{agent_id}' is not registered.",
                exit_code=ExitCode.INVALID_INPUT,
                json_output=json_output,
                addon_id=addon_id,
                addon_name=manifest.name,
                scope=parsed_scope.value,
                agent=agent_id,
            )
        target_agent = detected_agents[target_aid]
        if not target_agent.installed:
            _handle_error(
                f"Specified agent '{agent_id}' is not installed on this system.",
                exit_code=ExitCode.INVALID_INPUT,
                json_output=json_output,
                addon_id=addon_id,
                addon_name=manifest.name,
                scope=parsed_scope.value,
                agent=target_agent.agent_id,
            )
        agents_to_process = [target_agent]
    else:
        targets = [t.lower() for t in manifest.target_agents]
        agents_to_process = [
            agent
            for agent in detected_agents.values()
            if agent.installed and ("*" in targets or agent.agent_id.lower() in targets)
        ]
        if not agents_to_process:
            _handle_error(
                f"No compatible installed agent found to install '{addon_id}'.",
                exit_code=ExitCode.INVALID_INPUT,
                json_output=json_output,
                addon_id=addon_id,
                addon_name=manifest.name,
                scope=parsed_scope.value,
            )

    engine = InstallationEngine(registry=registry)
    execution_engine = ExecutionEngine()
    compat_engine = CompatibilityEngine()

    for agent in agents_to_process:
        # Check compatibility explicitly first
        compat_result = compat_engine.evaluate(manifest, agent, parsed_scope)
        if not compat_result.compatible:
            reasons_str = "; ".join(compat_result.reasons)
            incompat_msg = (
                f"Add-on '{manifest.name}' is incompatible with agent '{agent.name}': {reasons_str}"
            )
            _handle_error(
                incompat_msg,
                exit_code=ExitCode.COMPATIBILITY_FAILURE,
                json_output=json_output,
                addon_id=manifest.id,
                addon_name=manifest.name,
                agent=agent.agent_id,
                scope=parsed_scope.value,
                transaction_status="COMPATIBILITY_CHECKED",
            )

        # Create installation plan / transaction
        try:
            tx = engine.create_transaction(manifest, agent, parsed_scope, registry=registry)
        except IncompatibleAgentError as exc:
            _handle_error(
                str(exc),
                exit_code=ExitCode.COMPATIBILITY_FAILURE,
                json_output=json_output,
                addon_id=manifest.id,
                addon_name=manifest.name,
                agent=agent.agent_id,
                scope=parsed_scope.value,
            )
        except UnsupportedScopeError as exc:
            _handle_error(
                str(exc),
                exit_code=ExitCode.COMPATIBILITY_FAILURE,
                json_output=json_output,
                addon_id=manifest.id,
                addon_name=manifest.name,
                agent=agent.agent_id,
                scope=parsed_scope.value,
            )
        except UnsupportedIntegrationTypeError as exc:
            _handle_error(
                str(exc),
                exit_code=ExitCode.COMPATIBILITY_FAILURE,
                json_output=json_output,
                addon_id=manifest.id,
                addon_name=manifest.name,
                agent=agent.agent_id,
                scope=parsed_scope.value,
            )
        except SecurityValidationError as exc:
            _handle_error(
                str(exc),
                exit_code=ExitCode.SECURITY_FAILURE,
                json_output=json_output,
                addon_id=manifest.id,
                addon_name=manifest.name,
                agent=agent.agent_id,
                scope=parsed_scope.value,
            )
        except (InstallationPlanningError, AIAddonsError) as exc:
            _handle_error(
                str(exc),
                exit_code=ExitCode.EXECUTION_FAILURE,
                json_output=json_output,
                addon_id=manifest.id,
                addon_name=manifest.name,
                agent=agent.agent_id,
                scope=parsed_scope.value,
            )

        if tx.phase == TransactionPhase.FAILED or tx.plan is None:
            err_msg = tx.error_message or "Failed to generate installation plan."
            _handle_error(
                err_msg,
                exit_code=ExitCode.EXECUTION_FAILURE,
                json_output=json_output,
                addon_id=manifest.id,
                addon_name=manifest.name,
                agent=agent.agent_id,
                scope=parsed_scope.value,
            )

        plan = tx.plan

        # Extract planned operations cleanly for display/JSON
        ops_json = [
            {
                "op_type": op.op_type.value,
                "description": op.description.replace("\n", " "),
                "target_root": str(op.target_root).replace("\\", "/"),
                "target_path": str(op.target_path or "").replace("\\", "/"),
            }
            for op in plan.planned_operations
        ]

        if dry_run:
            tx.is_dry_run = True
            tx.phase = TransactionPhase.PLANNED
            if json_output:
                formatted = _format_json_response(
                    addon_id=manifest.id,
                    addon_name=manifest.name,
                    agent=agent.agent_id,
                    scope=parsed_scope.value,
                    compatible=True,
                    planned_operations=ops_json,
                    warnings=plan.warnings,
                    verification_status="skipped",
                    transaction_status=TransactionPhase.PLANNED.name,
                    success=True,
                    error_message=None,
                )
                _print_json(formatted)
            else:
                console.print()
                console.print(f"[bold cyan]{manifest.name}[/bold cyan]")
                console.print("─" * max(len(manifest.name) + 2, 20))
                console.print()
                console.print("Target:")
                console.print(f"  [bold green]{agent.name}[/bold green]")
                console.print(f"  [bold yellow]{parsed_scope.value.capitalize()}[/bold yellow]")
                console.print()
                console.print("Plan:")
                console.print(f"  [green]{sym_ok}[/green] Validate source")
                itype_name = manifest.integration_type.value.upper()
                console.print(f"  [green]{sym_ok}[/green] Prepare {itype_name} configuration")
                for op in plan.planned_operations:
                    console.print(f"  [green]{sym_ok}[/green] {op.description}")
                console.print()
                console.print("[dim]No changes were made.[/dim]")
                console.print()
            continue

        # Real installation flow
        tx.is_dry_run = False

        # Display plan preview in CLI mode before confirmation
        if not json_output:
            console.print()
            console.print(f"[bold cyan]{manifest.name}[/bold cyan]")
            console.print("─" * max(len(manifest.name) + 2, 20))
            console.print()
            console.print("Target:")
            console.print(f"  [bold green]{agent.name}[/bold green]")
            console.print(f"  [bold yellow]{parsed_scope.value.capitalize()}[/bold yellow]")
            console.print()
            console.print("Plan:")
            console.print(f"  [green]{sym_ok}[/green] Validate source")
            itype_name = manifest.integration_type.value.upper()
            console.print(f"  [green]{sym_ok}[/green] Prepare {itype_name} configuration")
            for op in plan.planned_operations:
                console.print(f"  [green]{sym_ok}[/green] {op.description}")
            console.print()
            console.print("[bold yellow]This installation will modify your system.[/bold yellow]")
            console.print()

        # Confirmation handling
        if not yes:
            confirmed = typer.confirm("Continue?", default=False)
            if not confirmed:
                if json_output:
                    formatted = _format_json_response(
                        addon_id=manifest.id,
                        addon_name=manifest.name,
                        agent=agent.agent_id,
                        scope=parsed_scope.value,
                        compatible=True,
                        planned_operations=ops_json,
                        warnings=plan.warnings,
                        verification_status="skipped",
                        transaction_status=TransactionPhase.PLANNED.name,
                        success=False,
                        error_message="Installation cancelled by user.",
                    )
                    _print_json(formatted)
                    raise typer.Exit(code=ExitCode.INVALID_INPUT)
                else:
                    cancel_msg = (
                        "[bold yellow]Installation cancelled. No changes were made.[/bold yellow]"
                    )
                    console.print(cancel_msg)
                    raise typer.Exit(code=ExitCode.SUCCESS)

        tx.phase = TransactionPhase.REVIEWED

        # Resolve required secrets interactively or from env
        secret_map: dict[str, str] = {}
        secret_resolver = SecretResolver()
        try:
            resolved_secrets = secret_resolver.resolve_manifest(
                manifest, allow_interactive=sys.stdin.isatty() and not yes
            )
            for sec in resolved_secrets:
                if sec.value is not None:
                    secret_map[sec.name] = sec.value
        except SecretResolutionError as exc:
            _handle_error(
                str(exc),
                exit_code=ExitCode.EXECUTION_FAILURE,
                json_output=json_output,
                addon_id=manifest.id,
                addon_name=manifest.name,
                agent=agent.agent_id,
                scope=parsed_scope.value,
                transaction_status=TransactionPhase.FAILED.name,
            )

        if not json_output:
            console.print(
                f"[bold cyan]Installing {manifest.name} ({manifest.id}) for "
                f"{agent.name} ({parsed_scope.value})...[/bold cyan]"
            )

        # Execute Plan with VerificationEngine & atomic state persistence
        tx.phase = TransactionPhase.EXECUTING
        try:
            result = execution_engine.execute_plan(
                plan, transaction=tx, dry_run=False, secret_values=secret_map
            )
        except SecurityValidationError as exc:
            _handle_error(
                str(exc),
                exit_code=ExitCode.SECURITY_FAILURE,
                json_output=json_output,
                addon_id=manifest.id,
                addon_name=manifest.name,
                agent=agent.agent_id,
                scope=parsed_scope.value,
                transaction_status=TransactionPhase.ROLLED_BACK.name,
                secret_values=secret_map,
            )
        except (
            ExecutableNotFoundError,
            ProcessExecutionError,
            ProcessTimeoutError,
            ExternalExecutionError,
        ) as exc:
            _handle_error(
                str(exc),
                exit_code=ExitCode.EXECUTION_FAILURE,
                json_output=json_output,
                addon_id=manifest.id,
                addon_name=manifest.name,
                agent=agent.agent_id,
                scope=parsed_scope.value,
                transaction_status=TransactionPhase.ROLLED_BACK.name,
                secret_values=secret_map,
            )
        except (VerificationPathSecurityError, VerificationError) as exc:
            _handle_error(
                str(exc),
                exit_code=ExitCode.VERIFICATION_FAILURE,
                json_output=json_output,
                addon_id=manifest.id,
                addon_name=manifest.name,
                agent=agent.agent_id,
                scope=parsed_scope.value,
                transaction_status=TransactionPhase.ROLLED_BACK.name,
                secret_values=secret_map,
            )
        except AIAddonsError as exc:
            _handle_error(
                str(exc),
                exit_code=ExitCode.EXECUTION_FAILURE,
                json_output=json_output,
                addon_id=manifest.id,
                addon_name=manifest.name,
                agent=agent.agent_id,
                scope=parsed_scope.value,
                transaction_status=TransactionPhase.FAILED.name,
                secret_values=secret_map,
            )

        # Evaluate execution result status
        if result.status == ExecutionStatus.SUCCESS:
            tx.phase = TransactionPhase.COMMITTED
            # Run verification checks summary for output
            verification_engine = VerificationEngine()
            ver_res = verification_engine.verify_plan(
                plan, dry_run=False, secret_values=secret_map
            )

            if json_output:
                formatted = _format_json_response(
                    addon_id=manifest.id,
                    addon_name=manifest.name,
                    agent=agent.agent_id,
                    scope=parsed_scope.value,
                    compatible=True,
                    planned_operations=ops_json,
                    warnings=plan.warnings,
                    verification_status="passed" if ver_res.verified else "failed",
                    transaction_status=TransactionPhase.COMMITTED.name,
                    success=True,
                    error_message=None,
                )
                _print_json(formatted)
            else:
                console.print()
                console.print("[bold cyan]Verification[/bold cyan]")
                for chk in ver_res.checks:
                    chk_desc = mask_secrets_in_text(chk.description, list(secret_map.values()))
                    if chk.status == VerificationStatus.PASSED:
                        console.print(f"  [green]{sym_ok}[/green] {chk_desc}")
                    elif chk.status == VerificationStatus.FAILED:
                        console.print(f"  [red]{sym_fail}[/red] {chk_desc}")
                console.print()
                success_text = (
                    f"[bold green]✓ Successfully installed {manifest.name} for "
                    f"{agent.name}[/bold green]"
                )
                console.print(success_text)
                console.print()
        else:
            tx.phase = TransactionPhase.ROLLED_BACK
            err_detail = result.error_message or "Installation execution failed."
            safe_err = mask_secrets_in_text(err_detail, list(secret_map.values()))

            if json_output:
                formatted = _format_json_response(
                    addon_id=manifest.id,
                    addon_name=manifest.name,
                    agent=agent.agent_id,
                    scope=parsed_scope.value,
                    compatible=True,
                    planned_operations=ops_json,
                    warnings=plan.warnings,
                    verification_status="failed",
                    transaction_status=TransactionPhase.ROLLED_BACK.name,
                    success=False,
                    error_message=safe_err,
                )
                _print_json(formatted)
            else:
                console.print()
                console.print("[bold red]Verification failed.[/bold red]")
                console.print()
                console.print("[bold yellow]Rolling back installation...[/bold yellow]")
                if result.rolled_back_operations:
                    for rb in result.rolled_back_operations:
                        rb_desc = mask_secrets_in_text(rb.description, list(secret_map.values()))
                        console.print(f"  [green]{sym_ok}[/green] {rb_desc}")
                console.print(f"[bold green]{sym_ok} Rollback completed[/bold green]")
                console.print()

            # Determine exit code based on whether verification or execution failed
            exit_code = (
                ExitCode.VERIFICATION_FAILURE
                if "verification" in safe_err.lower()
                else ExitCode.EXECUTION_FAILURE
            )
            raise typer.Exit(code=exit_code)
