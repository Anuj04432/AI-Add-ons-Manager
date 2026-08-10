"""CLI command for installing add-ons and generating dry-run installation plans."""

from pathlib import Path

import typer
from rich.console import Console

from aiaddons.agents.manager import AgentDetectionManager
from aiaddons.core.exceptions import (
    IncompatibleAgentError,
    InstallationError,
)
from aiaddons.core.installer.engine import InstallationEngine
from aiaddons.core.models.agent import Scope
from aiaddons.registry.registry import Registry

console = Console()


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
) -> None:
    """Install an add-on or output a dry-run installation plan."""
    if not dry_run:
        msg = "[bold yellow]Real installation is not implemented yet. Use --dry-run.[/bold yellow]"
        console.print(msg)
        raise typer.Exit(code=0)

    # 1. Locate registry directory
    target_registry_dir = registry_path
    if target_registry_dir is None:
        default_dir = Path("registry")
        if default_dir.exists() and default_dir.is_dir():
            target_registry_dir = default_dir
        else:
            target_registry_dir = Path.cwd() / "registry"

    if not target_registry_dir.exists():
        console.print(
            f"[bold red]Error:[/bold red] Registry directory '{target_registry_dir}' not found."
        )
        raise typer.Exit(code=1)

    # 2. Load registry and manifest
    registry, load_res = Registry.from_directory(target_registry_dir)
    manifest = registry.get(addon_id)
    if not manifest:
        console.print(f"[bold red]Error:[/bold red] Add-on '{addon_id}' not found in registry.")
        raise typer.Exit(code=1)

    # 3. Parse requested scope
    try:
        parsed_scope = Scope.from_str(scope)
    except ValueError as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc

    # 4. Detect agents
    manager = AgentDetectionManager()
    detected_agents = manager.detect_agents()

    # 5. Filter target agents
    if agent_id:
        target_aid = agent_id.strip().lower()
        if target_aid not in detected_agents:
            console.print(
                f"[bold red]Error:[/bold red] Specified agent '{agent_id}' is not registered."
            )
            raise typer.Exit(code=1)
        agents_to_plan = [detected_agents[target_aid]]
    else:
        # Default to installed agents that match target_agents
        targets = [t.lower() for t in manifest.target_agents]
        agents_to_plan = [
            agent
            for agent in detected_agents.values()
            if agent.installed and ("*" in targets or agent.agent_id.lower() in targets)
        ]
        if not agents_to_plan:
            # Fall back to all installed agents or all detected agents
            installed = [a for a in detected_agents.values() if a.installed]
            agents_to_plan = installed or list(detected_agents.values())

    engine = InstallationEngine()
    plans_generated = 0

    for agent in agents_to_plan:
        try:
            plan = engine.generate_plan(manifest, agent, parsed_scope)
            plans_generated += 1

            # Format and display plan in Rich UI
            console.print()
            console.print(f"[bold cyan]{manifest.name}[/bold cyan]")
            console.print("─" * max(len(manifest.name) + 2, 20))
            console.print()
            console.print("Target:")
            console.print(f"  [bold green]{agent.name}[/bold green]")
            console.print(f"  [bold yellow]{parsed_scope.value.capitalize()}[/bold yellow]")
            console.print()
            console.print("Plan:")
            console.print("  [green]✓[/green] Validate source")
            itype_name = manifest.integration_type.value.upper()
            console.print(f"  [green]✓[/green] Prepare {itype_name} configuration")
            for op in plan.planned_operations:
                console.print(f"  [green]✓[/green] {op.description}")
            console.print()
            console.print("[dim]No changes were made.[/dim]")
            console.print()

        except (IncompatibleAgentError, InstallationError) as exc:
            if agent_id:
                console.print(f"[bold red]Cannot generate plan for {agent.name}:[/bold red] {exc}")
                raise typer.Exit(code=1) from exc

    if plans_generated == 0 and not agent_id:
        msg = (
            f"[bold yellow]No compatible installed agent found to generate plan for "
            f"'{addon_id}'.[/bold yellow]"
        )
        console.print(msg)
