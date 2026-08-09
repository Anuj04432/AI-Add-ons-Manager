"""Typer CLI main entry point for aiaddons."""

import typer
from rich.console import Console

from aiaddons import __version__

app = typer.Typer(
    name="aiaddons",
    help="AI Add-ons Manager - Package & Integration Manager for AI Agents",
    add_completion=False,
)

console = Console()


def version_callback(value: bool) -> None:
    """Print the version and exit."""
    if value:
        console.print(f"[bold cyan]aiaddons[/bold cyan] version [green]{__version__}[/green]")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool | None = typer.Option(
        None,
        "--version",
        "-v",
        help="Show the version and exit.",
        callback=version_callback,
        is_eager=True,
    ),
) -> None:
    """AI Add-ons Manager CLI."""
    if ctx.invoked_subcommand is None and not version:
        console.print("[bold cyan]AI Add-ons Manager (`aiaddons`)[/bold cyan]")
        console.print("Run [yellow]aiaddons --help[/yellow] for available commands.")


@app.command()
def version() -> None:
    """Show the version of aiaddons."""
    console.print(f"[bold cyan]aiaddons[/bold cyan] version [green]{__version__}[/green]")
