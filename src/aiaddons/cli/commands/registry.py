"""CLI commands for listing, searching, updating, and inspecting registry metadata."""

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from aiaddons.core.exceptions import AIAddonsError
from aiaddons.core.models.manifest import IntegrationType
from aiaddons.registry.cache import RegistryCacheManager
from aiaddons.registry.client import DEFAULT_REGISTRY_URL, RegistryClient
from aiaddons.registry.registry import Registry

console = Console()


def _get_default_registry(custom_dir: Path | None = None) -> Registry:
    """Load registry from custom directory, local cache, or default codebase directory."""
    registry, _source = Registry.load_auto(custom_dir=custom_dir)
    return registry


def list_command(
    integration_type: str | None = typer.Option(
        None,
        "--type",
        "-t",
        help="Filter by integration type (mcp, skill, plugin, cli_tool).",
    ),
    category: str | None = typer.Option(
        None,
        "--category",
        "-c",
        help="Filter by category.",
    ),
    agent: str | None = typer.Option(
        None,
        "--agent",
        "-a",
        help="Filter by supported target agent (e.g. claude-code, codex).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Output results in JSON format.",
    ),
    registry_dir: Path | None = typer.Option(
        None,
        "--registry-dir",
        "-r",
        help="Path to custom registry directory.",
    ),
) -> None:
    """List available add-ons in the registry."""
    registry = _get_default_registry(registry_dir)
    manifests = registry.list()

    if integration_type:
        manifests = [m for m in manifests if m.integration_type.value == integration_type.lower()]
    if category:
        manifests = [m for m in manifests if m.category.lower() == category.lower()]
    if agent:
        target_a = agent.lower()
        manifests = [
            m
            for m in manifests
            if "*" in [t.lower() for t in m.target_agents]
            or target_a in [t.lower() for t in m.target_agents]
        ]

    if json_output:
        serialized = [m.model_dump() for m in manifests]
        print(json.dumps(serialized, indent=2))
        return

    if not manifests:
        console.print("[yellow]No add-ons found matching the criteria.[/yellow]")
        return

    table = Table(title=f"AI Add-ons Registry ({len(manifests)} items)")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Name", style="bold")
    table.add_column("Version", style="magenta")
    table.add_column("Type", style="green")
    table.add_column("Category", style="yellow")
    table.add_column("Publisher", style="blue")
    table.add_column("Status", style="bold white")

    for m in manifests:
        status_color = (
            "green"
            if m.trust.verification_status == "verified"
            else ("yellow" if m.trust.verification_status == "community" else "red")
        )
        status_str = f"[{status_color}]{m.trust.verification_status.value}[/{status_color}]"
        table.add_row(
            m.id,
            m.name,
            m.version,
            m.integration_type.value,
            m.category,
            m.trust.publisher.name,
            status_str,
        )

    console.print(table)


def search_command(
    query: str = typer.Argument(..., help="Search term for add-on ID, name, tag, or description."),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Output results in JSON format.",
    ),
    registry_dir: Path | None = typer.Option(
        None,
        "--registry-dir",
        "-r",
        help="Path to custom registry directory.",
    ),
) -> None:
    """Search for add-ons in the local registry cache or directory by keyword."""
    registry = _get_default_registry(registry_dir)
    manifests = registry.search(query)

    if json_output:
        serialized = [m.model_dump() for m in manifests]
        print(json.dumps(serialized, indent=2))
        return

    if not manifests:
        console.print(f"[yellow]No add-ons found matching '{query}'.[/yellow]")
        return

    table = Table(title=f"Search Results for '{query}' ({len(manifests)} items)")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Name", style="bold")
    table.add_column("Version", style="magenta")
    table.add_column("Type", style="green")
    table.add_column("Category", style="yellow")
    table.add_column("Publisher", style="blue")

    for m in manifests:
        table.add_row(
            m.id,
            m.name,
            m.version,
            m.integration_type.value,
            m.category,
            m.trust.publisher.name,
        )

    console.print(table)


def info_command(
    addon_id: str = typer.Argument(..., help="Add-on ID to inspect."),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Output manifest details in JSON format.",
    ),
    registry_dir: Path | None = typer.Option(
        None,
        "--registry-dir",
        "-r",
        help="Path to custom registry directory.",
    ),
) -> None:
    """Display detailed metadata for a specific add-on."""
    registry = _get_default_registry(registry_dir)
    manifest = registry.get(addon_id)

    if not manifest:
        console.print(f"[bold red]Error:[/bold red] Add-on '{addon_id}' not found in registry.")
        raise typer.Exit(code=1)

    if json_output:
        print(json.dumps(manifest.model_dump(), indent=2))
        return

    console.print(f"\n[bold cyan]{manifest.name}[/bold cyan] [dim](v{manifest.version})[/dim]")
    console.print(f"[italic]{manifest.description}[/italic]\n")

    console.print(f"[bold]ID:[/bold] {manifest.id}")
    console.print(f"[bold]Type:[/bold] [green]{manifest.integration_type.value}[/green]")
    console.print(f"[bold]Category:[/bold] [yellow]{manifest.category}[/yellow]")
    console.print(f"[bold]License:[/bold] {manifest.license}")

    status_color = (
        "green"
        if manifest.trust.verification_status == "verified"
        else ("yellow" if manifest.trust.verification_status == "community" else "red")
    )
    console.print(
        f"[bold]Publisher:[/bold] {manifest.trust.publisher.name} "
        f"[{status_color}]({manifest.trust.verification_status.value})[/{status_color}]"
    )

    if manifest.documentation_url:
        console.print(f"[bold]Docs:[/bold] {manifest.documentation_url}")

    console.print(f"[bold]Target Agents:[/bold] {', '.join(manifest.target_agents)}")
    scopes_str = ", ".join(s.value for s in manifest.supported_scopes)
    console.print(f"[bold]Supported Scopes:[/bold] {scopes_str}")

    if manifest.tags:
        console.print(f"[bold]Tags:[/bold] {', '.join(manifest.tags)}")

    if manifest.source.source_type:
        console.print(f"[bold]Source Type:[/bold] {manifest.source.source_type.value}")
        if manifest.source.repository:
            console.print(f"[bold]Repository:[/bold] {manifest.source.repository}")
        if manifest.source.package_name:
            console.print(f"[bold]Package:[/bold] {manifest.source.package_name}")
        if manifest.source.commit_sha:
            console.print(f"[bold]Commit SHA:[/bold] {manifest.source.commit_sha}")
        if manifest.source.checksum:
            console.print(f"[bold]Checksum:[/bold] {manifest.source.checksum}")

    if manifest.trust.allowed_executables:
        console.print(
            f"[bold]Allowed Executables:[/bold] {', '.join(manifest.trust.allowed_executables)}"
        )

    if manifest.integration_type == IntegrationType.PLUGIN and manifest.handler_spec.plugin:
        components = manifest.handler_spec.plugin.components
        console.print(f"\n[bold]Composite Plugin Components ({len(components)} items):[/bold]")
        for comp_id in components:
            console.print(f"  - {comp_id}")

    if manifest.dependencies:
        console.print("\n[bold]Dependencies:[/bold]")
        for dep in manifest.dependencies:
            req_str = "required" if dep.required else "optional"
            console.print(f"  - {dep.name} ({dep.type.value}, {req_str})")

    console.print()


def _get_symbols() -> tuple[str, str]:
    """Return platform and encoding safe status symbols."""
    import sys
    try:
        "✓".encode(sys.stdout.encoding or "utf-8")
        return "✓", "✗"
    except Exception:
        return "+", "-"


def registry_update_command(
    registry_url: str = typer.Option(
        DEFAULT_REGISTRY_URL,
        "--url",
        "-u",
        help="Registry index endpoint URL to fetch from.",
    ),
    cache_dir: Path | None = typer.Option(
        None,
        "--cache-dir",
        help="Custom registry cache directory.",
    ),
) -> None:
    """Synchronize add-on registry metadata from trusted remote endpoint."""
    sym_ok, _ = _get_symbols()
    console.print("[bold cyan]Updating registry...[/bold cyan]")
    try:
        client = RegistryClient(registry_url=registry_url)
        console.print(f"Connecting to [yellow]{client.registry_url}[/yellow]...")
        index = client.fetch_registry()
        console.print(f"  [green]{sym_ok}[/green] Downloaded registry metadata")
        console.print(f"  [green]{sym_ok}[/green] Schema validated (v{index.schema_version})")
        console.print(f"  [green]{sym_ok}[/green] {len(index.manifests)} add-ons validated")

        cache_mgr = RegistryCacheManager(cache_dir=cache_dir)
        saved_path = cache_mgr.save_cache(index)
        console.print(f"  [green]{sym_ok}[/green] Registry cache updated at [dim]{saved_path}[/dim]")
        console.print("[bold green]Registry update complete.[/bold green]")
    except AIAddonsError as err:
        console.print(f"[bold red]Registry update failed:[/bold red] {err}")
        raise typer.Exit(code=1) from err
    except Exception as err:
        console.print(f"[bold red]Unexpected error during registry update:[/bold red] {err}")
        raise typer.Exit(code=1) from err


def registry_status_command(
    registry_url: str | None = typer.Option(
        None,
        "--url",
        "-u",
        help="Custom registry URL to inspect status for.",
    ),
    cache_dir: Path | None = typer.Option(
        None,
        "--cache-dir",
        help="Custom registry cache directory.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Output status in JSON format.",
    ),
) -> None:
    """Display status and statistics of the local registry cache."""
    client = RegistryClient(registry_url=registry_url)
    cache_mgr = RegistryCacheManager(cache_dir=cache_dir)
    status = cache_mgr.get_status(client.registry_url)

    if json_output:
        print(json.dumps(status, indent=2))
        return

    console.print("\n[bold cyan]AI Add-ons Registry Status[/bold cyan]")
    console.print(f"[bold]Configured URL:[/bold] {status['configured_url']}")
    console.print(f"[bold]Cache Location:[/bold] {status['cache_file']}")

    if status["cache_exists"] and status["is_valid"]:
        console.print("[bold]Cache State:[/bold] [green]Valid[/green]")
        console.print(f"[bold]Schema Version:[/bold] {status['cached_schema_version']}")
        console.print(f"[bold]Last Synced:[/bold] {status['last_synced_at']}")
        console.print(
            f"[bold]Available Add-ons:[/bold] [bold green]{status['addon_count']}[/bold green]"
        )
        console.print("[bold]Sync Status:[/bold] [green]Up to date[/green]\n")
    elif status["cache_exists"] and not status["is_valid"]:
        console.print("[bold]Cache State:[/bold] [bold red]Corrupted / Invalid[/bold red]")
        console.print(f"[bold]Error:[/bold] {status['error']}")
        console.print(
            "[bold]Sync Status:[/bold] [bold yellow]"
            "Synchronization required (run 'aiaddons registry update')[/bold yellow]\n"
        )
    else:
        console.print("[bold]Cache State:[/bold] [yellow]Not Cached[/yellow]")
        console.print(
            "[bold]Sync Status:[/bold] [bold yellow]"
            "Synchronization required (run 'aiaddons registry update')[/bold yellow]\n"
        )
