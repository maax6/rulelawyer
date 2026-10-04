"""Diagnostic, ingestion Route A et questions sourcées."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from rulelawyer import __version__
from rulelawyer.answer import answer_from_passage
from rulelawyer.ingest import ingest_route_a, read_chunks, write_chunks
from rulelawyer.probe import probe
from rulelawyer.report import render

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Assistant de règles pour JDR papier. N'inclut aucun livre : "
    "vous apportez votre propre PDF.",
)
console = Console()
errors = Console(stderr=True)


def _ingest(pdf: Path, output: Path) -> Path:
    report = probe(pdf)
    chunks = ingest_route_a(pdf, report)
    output.mkdir(parents=True, exist_ok=True)
    (output / "probe.json").write_text(
        report.model_dump_json(indent=2), encoding="utf-8"
    )
    path = output / "chunks.jsonl"
    write_chunks(chunks, path)
    return path


@app.command("ingest")
def ingest_command(
    pdf: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    output: Annotated[Path, typer.Option("--output", "-o")] = Path(".cache/rulelawyer"),
) -> None:
    """Extrait un chunks.jsonl par sections, après diagnostic Route A."""
    try:
        path = _ingest(pdf, output)
    except (ValueError, OSError) as exc:
        errors.print(str(exc), markup=False)
        raise typer.Exit(code=1) from exc
    console.print(str(path), markup=False)


@app.command("ask")
def ask_command(
    pdf: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    question: Annotated[str, typer.Argument(help="Question sur ce manuel.")],
    cache_dir: Annotated[Path, typer.Option("--cache-dir")] = Path(".cache/rulelawyer"),
    threshold: Annotated[float, typer.Option("--threshold", min=0, max=1)] = 0.5,
    debug: Annotated[bool, typer.Option("--debug")] = False,
) -> None:
    """Interroge un PDF natif : index local, retrieval hybride, OpenRouter."""
    try:
        if not question.strip():
            raise ValueError("La question est vide.")
        from rulelawyer.retrieval import BGEModels, open_index

        with errors.status("Diagnostic et extraction des sections…"):
            chunks_path = _ingest(pdf, cache_dir)
            chunks = read_chunks(chunks_path)
        with (
            errors.status("Index local, recherche hybride et reranking…"),
            open_index(chunks, cache_dir / "qdrant_storage", BGEModels()) as index,
        ):
            hits = index.search(question, threshold=threshold)
        if debug:
            for hit in hits:
                errors.print(
                    f"{hit.chunk.section_path} | book_page={hit.page.book_page} "
                    f"pdf_page={hit.page.pdf_page} | BM25 rang={hit.bm25_rank} "
                    f"dense rang={hit.dense_rank} RRF={hit.rrf_score:.4f} "
                    f"rerank={hit.score:.4f}",
                    markup=False,
                )
        with errors.status("Réponse depuis le passage retenu via OpenRouter…"):
            answer = answer_from_passage(question, hits[0].page if hits else None)
    except ImportError as exc:
        errors.print("Dépendances d'index absentes : lancez uv sync --extra index.")
        raise typer.Exit(code=2) from exc
    except (ValueError, OSError, RuntimeError) as exc:
        errors.print(str(exc), markup=False)
        raise typer.Exit(code=1) from exc
    console.print(answer, markup=False, highlight=False)


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
