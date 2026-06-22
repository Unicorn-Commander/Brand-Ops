"""Brand-Ops command line, one of the human interfaces over the engine.

Dry run composes and prints the prompt without calling any backend, so prompts
can be reviewed before any spend. Live generation routes through the same engine
entry the MCP server and REST API use.
"""
from __future__ import annotations

from pathlib import Path

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
def card(
    brand: str = typer.Option(..., "--brand", help="Brand slug, e.g. brand-ops."),
    asset: str = typer.Option(
        "og_image", "--asset", help="Card type: og_image (1200x630) or social_card (1080x1080)."
    ),
    message: str = typer.Option(
        None, "--message", help="Headline (og) / tagline (social). Defaults to the brand tagline."
    ),
    mark: str = typer.Option(
        None, "--mark", help="Mark filename under the brand's logos; defaults to the brand's mark."
    ),
    variant: str = typer.Option(None, "--variant"),
    out: str = typer.Option(
        None, "--out", help="Write the PNG here instead of the catalog, e.g. web/public/og.png."
    ),
) -> None:
    """Compose a deterministic on-brand share card (og_image / social_card).

    The text is rendered with the brand's real fonts and exact colors over a brand
    background with the mark composited in, so the copy is always correct and on
    brand (no model text rendering, no spend). With --out the PNG is written to
    that path (the web app's default link-preview image is built this way);
    otherwise it is saved into the brand's managed catalog like any other asset.
    """
    try:
        if out:
            png, mark_path = engine.render_card(brand, asset, message=message, mark=mark)
            dest = Path(out)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(png)
            via = f"mark {mark_path.name}" if mark_path else "no mark"
            console.print(f"[green]Wrote[/green] {asset} ({len(png)} bytes, {via}) -> {dest}")
            return
        outcome = engine.generate_card(brand, asset, message=message, mark=mark, variant=variant)
    except BrandNotFound as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None
    except (KeyError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None

    console.print(f"[green]Composed[/green] {asset} for {brand}:")
    for path in outcome.paths:
        console.print(f"  {path}")
    for note in outcome.notes:
        console.print(f"[dim]{note}[/dim]")


@app.command("share-meta")
def share_meta_cmd(
    brand: str = typer.Option(..., "--brand", help="Brand slug, e.g. magic-unicorn."),
    url: str = typer.Option(
        None, "--url", help="The brand's canonical site URL (defaults to its declared website)."
    ),
    image_url: str = typer.Option(
        None, "--image-url", help="Where the brand hosts the image; defaults to <site>/og.png."
    ),
) -> None:
    """Print the Open Graph + Twitter tags a brand pastes into its OWN site.

    These make a shared link to the brand's site unfurl into the brand's 1200x630
    share image (generate one with `brand-ops card`). The preview lives on the
    brand's site, so pass --url (or set the brand's website) and --image-url for
    absolute tags; otherwise the snippet uses a clearly-marked example domain and
    names what to fill in.
    """
    from . import share_meta
    from .brand_loader import load_brand

    try:
        b = load_brand(brand)
    except BrandNotFound as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None

    meta = share_meta.build_share_meta(b, canonical_url=url, image_url=image_url)
    console.print(meta["html"], markup=False, highlight=False)
    if meta["placeholders"]:
        console.print(
            f"[yellow]fill in:[/yellow] {', '.join(meta['placeholders'])} "
            "(currently an example domain; pass --url/--image-url or set the brand's website)"
        )


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


@app.command("backfill-compliance")
def backfill_compliance_cmd() -> None:
    """Stamp every catalog asset with its compliance-derived lifecycle status.

    Runs the deterministic checker over each generated and imported asset and
    writes ``lifecycle_status`` + a compliance summary into its sidecar, so a kit
    built before the lifecycle gate existed gets gated in place. Idempotent.
    """
    from .compliance import backfill_compliance

    counts = backfill_compliance()
    console.print(
        f"[green]Stamped {counts['stamped']} asset(s)[/green]: "
        f"{counts['approved']} approved, {counts['needs_review']} needs review"
    )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
