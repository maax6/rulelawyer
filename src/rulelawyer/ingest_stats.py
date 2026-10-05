"""Statistiques sans contenu du corpus, y compris les images non indexées."""

from pathlib import Path
from statistics import median

import pdfplumber
from pydantic import BaseModel

from rulelawyer.chunking import estimate_tokens
from rulelawyer.ingest import Chunk


class IngestStats(BaseModel):
    sections: int
    chunks: int
    token_min: int
    token_median: float
    token_max: int
    token_distribution: dict[str, int]
    images_total: int
    images_kept: int
    images_filtered: int
    images_indexed: int


def ingestion_stats(pdf: Path, chunks: list[Chunk]) -> IngestStats:
    """Compter les occurrences d'images, avec taille du flux PDF encodé.

    Retenue signifie éligible (100×100 px et 5 Ko), pas captionnée/indexée.
    Une erreur de lecture remonte à l'appelant ; aucun compte partiel publié.
    """
    lengths = [estimate_tokens(c.text) for c in chunks]
    if not lengths:
        raise ValueError("Aucun chunk pour les statistiques d'ingestion.")
    total = kept = 0
    with pdfplumber.open(pdf) as document:
        for page in document.pages:
            for image in page.images:
                total += 1
                width, height = image["srcsize"]
                stream = image["stream"]
                encoded = stream.get_rawdata()
                size = len(encoded if encoded is not None else stream.get_data())
                kept += width >= 100 and height >= 100 and size >= 5000
            page.close()
    return IngestStats(
        sections=len({c.section_path for c in chunks}),
        chunks=len(chunks),
        token_min=min(lengths),
        token_median=median(lengths),
        token_max=max(lengths),
        token_distribution={
            "1-300": sum(n <= 300 for n in lengths),
            "301-600": sum(300 < n <= 600 for n in lengths),
            "601-900": sum(600 < n <= 900 for n in lengths),
            "901-1200": sum(900 < n <= 1200 for n in lengths),
            ">1200": sum(n > 1200 for n in lengths),
        },
        images_total=total,
        images_kept=kept,
        images_filtered=total - kept,
        images_indexed=len({image for c in chunks for image in c.images}),
    )
