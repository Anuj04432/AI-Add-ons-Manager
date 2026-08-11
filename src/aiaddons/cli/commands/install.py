"""CLI command for installing add-ons and generating dry-run installation plans."""

from pathlib import Path

import typer
from rich.console import Console

from aiaddons.agents.manager import AgentDetectionManager
from aiaddons.core.execution.engine import ExecutionEngine
from aiaddons.core.execution.models import ExecutionStatus
from aiaddons.core.installer.engine import InstallationEngine
from aiaddons.core.installer.models import TransactionPhase
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
        agents_to_process = [detected_agents[target_aid]]
    else:
        targets = [t.lower() for t in manifest.target_agents]
        agents_to_process = [
            agent
            for agent in detected_agents.values()
            if agent.installed and ("*" in targets or agent.agent_id.lower() in targets)
        ]
        if not agents_to_process:
            installed = [a for a in detected_agents.values() if a.installed]
            agents_to_process = installed or list(detected_agents.values())

    engine = InstallationEngine(registry=registry)
    execution_engine = ExecutionEngine()
    actions_completed = 0

    for agent in agents_to_process:
        tx = engine.create_transaction(manifest, agent, parsed_scope, registry=registry)
        if tx.phase == TransactionPhase.FAILED or tx.plan is None:
            if agent_id:
                err_text = (
                    f"[bold red]Cannot install '{manifest.id}' for {agent.name}:[/bold red] "
                    f"{tx.error_message}"
                )
                console.print(err_text)
                raise typer.Exit(code=1)
            continue

        actions_completed += 1
        plan = tx.plan

        if dry_run:
            tx.is_dry_run = True
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
        else:
            tx.is_dry_run = False
            tx.phase = TransactionPhase.REVIEWED
            console.print()
            header_text = (
                f"[bold cyan]Installing {manifest.name} ({manifest.id}) for "
                f"{agent.name} ({parsed_scope.value})...[/bold cyan]"
            )
            console.print(header_text)
            result = execution_engine.execute_plan(plan, transaction=tx, dry_run=False)

            if result.status == ExecutionStatus.SUCCESS:
                success_text = (
                    f"[bold green]✓ Successfully installed {manifest.name} for "
                    f"{agent.name}[/bold green]"
                )
                console.print(success_text)
                for op_res in result.executed_operations:
                    console.print(f"  [green]✓[/green] {op_res.description}")
                console.print()
            else:
                fail_text = (
                    f"[bold red]✗ Installation failed for {agent.name}:[/bold red] "
                    f"{result.error_message}"
                )
                console.print(fail_text)
                if result.rolled_back_operations:
                    console.print("[bold yellow]Rolled back changes:[/bold yellow]")
                    for rb_res in result.rolled_back_operations:
                        console.print(f"  [yellow]↩[/yellow] {rb_res.description}")
                raise typer.Exit(code=1)

    if actions_completed == 0 and not agent_id:
        msg = (
            f"[bold yellow]No compatible installed agent found to install "
            f"'{addon_id}'.[/bold yellow]"
        )
        console.print(msg)
