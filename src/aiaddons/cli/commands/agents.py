"""CLI command for listing and detecting AI coding agents."""

import json
import sys
from pathlib import Path

import typer
from rich.console import Console

from aiaddons.agents.manager import detect_agents

console = Console()


def _get_symbols() -> tuple[str, str]:
    """Return platform and encoding safe status symbols."""
    try:
        "✓".encode(sys.stdout.encoding or "utf-8")
        return "✓", "✗"
    except (UnicodeEncodeError, AttributeError, TypeError):
        return "+", "-"


def agents_command(
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Output raw detection results in JSON format.",
    ),
    project_path: Path | None = typer.Option(
        None,
        "--project-path",
        "-p",
        help="Specify custom project directory for detection.",
    ),
) -> None:
    """List detected AI coding agents and their installation status."""
    results = detect_agents(project_path=project_path)

    if json_output:
        serialized = {k: v.model_dump() for k, v in results.items()}
        console.print(json.dumps(serialized, indent=2))
        return

    console.print("\n[bold cyan]AI Coding Agents[/bold cyan]\n")
    check_sym, cross_sym = _get_symbols()

    for _agent_id, res in results.items():
        if res.installed:
            console.print(f"[bold green]{check_sym}[/bold green] [bold]{res.name}[/bold]")
            if res.version:
                console.print(f"  [dim]Version:[/dim] {res.version}")
            else:
                console.print("  [dim]Version:[/dim] [yellow]Unknown[/yellow]")

            if res.executable_path:
                console.print(f"  [dim]Path:[/dim] {res.executable_path}")

            if res.config_path:
                console.print(f"  [dim]Config:[/dim] {res.config_path}")

            if res.capabilities:
                caps_str = ", ".join(c.value for c in res.capabilities)
                console.print(f"  [dim]Capabilities:[/dim] {caps_str}")
        else:
            console.print(f"[bold red]{cross_sym}[/bold red] [bold]{res.name}[/bold]")
            console.print("  [dim]Not installed[/dim]")

        if res.detection_error:
            console.print(f"  [red]Detection Note:[/red] {res.detection_error}")

        console.print()
