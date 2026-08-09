"""CLI command for checking add-on compatibility against detected AI coding agents."""

import json
import sys
from pathlib import Path

import typer
from rich.console import Console

from aiaddons.agents.manager import AgentDetectionManager
from aiaddons.core.compatibility.engine import CompatibilityEngine
from aiaddons.core.models.agent import Scope
from aiaddons.registry.registry import Registry

console = Console()


def _get_symbols() -> tuple[str, str]:
    """Return platform-safe symbols for checkmark and cross mark."""
    try:
        "✓".encode(sys.stdout.encoding or "utf-8")
        return "✓", "✗"
    except Exception:
        return "+", "-"


def check_command(
    addon_id: str = typer.Argument(..., help="Add-on ID to evaluate compatibility for."),
    scope: str = typer.Option(
        "workspace",
        "--scope",
        "-s",
        help="Requested installation scope (global or workspace).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Output evaluation results in JSON format.",
    ),
    registry_dir: Path | None = typer.Option(
        None,
        "--registry-dir",
        "-r",
        help="Path to custom registry directory.",
    ),
) -> None:
    """Evaluate compatibility of an add-on against all detected AI coding agents (read-only)."""
    try:
        req_scope = Scope.from_str(scope)
    except ValueError as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1) from e

    target_dir = registry_dir if registry_dir is not None else Path.cwd() / "registry" / "addons"
    registry, _ = Registry.from_directory(target_dir)

    manifest = registry.get(addon_id)
    if not manifest:
        console.print(f"[bold red]Error:[/bold red] Add-on '{addon_id}' not found in registry.")
        raise typer.Exit(code=1)

    manager = AgentDetectionManager()
    detected_agents = list(manager.detect_agents().values())

    engine = CompatibilityEngine()
    results = engine.evaluate_all(manifest, detected_agents, requested_scope=req_scope)

    if json_output:
        serialized = [r.model_dump() for r in results]
        console.print(json.dumps(serialized, indent=2))
        return

    sym_ok, sym_fail = _get_symbols()

    console.print(
        f"\n[bold cyan]Compatibility Evaluation for '{manifest.name}' ({manifest.id})[/bold cyan]"
    )
    console.print(f"[bold]Requested Scope:[/bold] {req_scope.value}\n")

    for r in results:
        status_str = (
            f"[bold green]{sym_ok} Compatible[/bold green]"
            if r.compatible
            else f"[bold red]{sym_fail} Incompatible[/bold red]"
        )
        console.print(f"[bold]{r.agent_name}[/bold] ({r.agent_id}): {status_str}")

        for reason in r.reasons:
            color = "green" if r.compatible else "yellow"
            console.print(f"  [{color}]• {reason}[/{color}]")

        if r.missing_requirements:
            console.print("  [bold red]Missing Requirements:[/bold red]")
            for req in r.missing_requirements:
                console.print(f"    - {req}")

        if r.unsupported_requirements:
            console.print("  [bold red]Unsupported Requirements:[/bold red]")
            for unsupp in r.unsupported_requirements:
                console.print(f"    - {unsupp}")

        if r.warnings:
            console.print("  [bold yellow]Warnings:[/bold yellow]")
            for warn in r.warnings:
                console.print(f"    - {warn}")

        console.print()
