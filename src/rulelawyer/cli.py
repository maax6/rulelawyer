"""Point d'entrée CLI.

Les commandes d'ingestion (`ingest`, `ask`, ...) arriveront aux étapes
suivantes du brief ; seul `probe` est implémenté pour l'instant.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from rulelawyer import __version__
from rulelawyer.probe import probe
from rulelawyer.report import render

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Assistant de règles pour JDR papier. N'inclut aucun livre : "
    "vous apportez votre propre PDF.",
)
console = Console()


@app.command("probe")
def probe_command(
    pdf: Annotated[Path, typer.Argument(help="Le PDF à diagnostiquer.")],
    json_out: Annotated[
        Path | None,
        typer.Option("--json", help="Écrit le rapport complet en JSON à ce chemin."),
    ] = None,
    quiet: Annotated[
        bool, typer.Option("--quiet", "-q", help="N'affiche rien, écrit juste le JSON.")
    ] = False,
) -> None:
    """Diagnostique un PDF et recommande une route d'ingestion.

    C'est le seul endroit où la route est décidée ; l'ingestion consomme ce
    rapport sans re-diagnostiquer.
    """
    if not pdf.exists():
        console.print(f"[red]Fichier introuvable :[/red] {pdf}")
        raise typer.Exit(code=2)

    with console.status(f"Analyse de {pdf.name}…", spinner="dots"):
        report = probe(pdf)

    if not quiet:
        render(report, console)

    if json_out is not None:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(
            json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        if not quiet:
            console.print(f"[dim]Rapport JSON écrit : {json_out}[/dim]")

    if report.errors:
        raise typer.Exit(code=1)


@app.command("version")
def version_command() -> None:
    """Affiche la version."""
    console.print(__version__)


if __name__ == "__main__":
    app()
