"""Brand-Ops command line, one of the human interfaces over the engine.

Dry run composes and prints the prompt without calling any backend, so prompts
can be reviewed before any spend. Live generation routes through the same engine
entry the MCP server and REST API use.
"""
from __future__ import annotations

import typer
from rich.console import Console
from rich.panel import Panel

from . import engine
from .asset_types import ASSET_TYPES
from .backends.base import BackendError, BackendNotConfigured
from .brand_loader import BrandNotFound, list_brands

app = typer.Typer(add_completion=False, help="Brand-Ops asset generator (Unicorn Commander suite).")
console = Console()


@app.command("list-brands")
def list_brands_cmd() -> None:
    """List the seeded brands."""
    brands = list_brands()
    if not brands:
        console.print("No brands found under brand-kits/.")
        raise typer.Exit(1)
    for slug in brands:
        console.print(f"  {slug}")


@app.command()
def generate(
    brand: str = typer.Option(..., "--brand", help="Brand slug, e.g. magic-unicorn."),
    asset: str = typer.Option(..., "--asset", help="Asset type: " + ", ".join(ASSET_TYPES)),
    variant: str = typer.Option(None, "--variant"),
    topic: str = typer.Option(None, "--topic"),
    platform: str = typer.Option(None, "--platform"),
    backend: str = typer.Option(None, "--backend", help="Override the default backend."),
    count: int = typer.Option(1, "--count", min=1),
    dry_run: bool = typer.Option(False, "--dry-run", help="Compose and print the prompt only."),
) -> None:
    """Generate a brand asset, or compose the prompt only with --dry-run."""
    try:
        outcome = engine.generate_asset(
            brand,
            asset,
            variant=variant,
            topic=topic,
            platform=platform,
            backend=backend,
            count=count,
            dry_run=dry_run,
        )
    except BrandNotFound as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None
    except (KeyError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None
    except BackendNotConfigured as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        raise typer.Exit(2) from None
    except BackendError as exc:
        console.print(f"[red]backend error: {exc}[/red]")
        raise typer.Exit(1) from None

    built = outcome.built
    w, h = built.size
    refs = ", ".join(p.name for p in built.reference_images) or "(none)"

    if dry_run:
        body = (
            f"[bold]brand[/bold]    {brand}\n"
            f"[bold]asset[/bold]    {asset}   size {w}x{h}   transparent={built.transparent}\n"
            f"[bold]backend[/bold]  {built.backend}\n"
            f"[bold]refs[/bold]     {refs}\n"
            f"[bold]output[/bold]   {outcome.planned_path}\n\n"
            f"[bold]prompt[/bold]\n{built.text}"
        )
        console.print(Panel(body, title="Brand-Ops dry run", expand=False))
    else:
        result = outcome.result
        cost = f"{result.cost_cents:.1f}c" if result and result.cost_cents is not None else "n/a"
        console.print(
            f"[green]Generated[/green] {len(outcome.paths)} file(s) via "
            f"[bold]{result.backend}[/bold] ({result.model or 'model n/a'}), est cost {cost}:"
        )
        for path in outcome.paths:
            console.print(f"  {path}")

    if outcome.missing_refs:
        console.print(
            f"[yellow]warning:[/yellow] missing reference images for roles: "
            f"{', '.join(outcome.missing_refs)}"
        )
    for warn in built.warnings:
        console.print(f"[yellow]copy check:[/yellow] {warn}")


@app.command()
def ingest(
    brand: str = typer.Option(None, "--brand", help="Brand slug, or all known brands if omitted."),
) -> None:
    """Ingest a brand's known existing assets into the managed catalog."""
    from .ingest import IMPORT_SOURCES, ingest_brand

    targets = [brand] if brand else list(IMPORT_SOURCES)
    total = 0
    for slug in targets:
        paths = ingest_brand(slug)
        total += len(paths)
        console.print(f"  {slug}: {len(paths)} assets")
    console.print(f"[green]Ingested {total} assets into imported/[/green]")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
