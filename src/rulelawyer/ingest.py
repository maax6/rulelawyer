"""Route A : sections de l'outline et provenance page par page."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from rulelawyer.chunking import DEFAULT_MAX_TOKENS, split_section
from rulelawyer.ingest_types import ChunkPage as ChunkPage
from rulelawyer.models import (
    PageMapMethod,
    PageNumberSource,
    ProbeReport,
    Route,
)
from rulelawyer.probe import ZONE_FRACTION, normalize_line, read_pages


class Chunk(BaseModel):
    id: str
    book_id: str
    section_path: str
    text: str
    raw_text: str
    book_page: int | None
    pdf_page: int = Field(ge=1)
    pages: list[ChunkPage]
    type: Literal["rules", "table", "image_caption"] = "rules"
    images: list[str] = Field(default_factory=list)
    route: Route


def _title_key(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def ingest_route_a(
    pdf: Path, report: ProbeReport, *, max_tokens: int = DEFAULT_MAX_TOKENS
) -> list[Chunk]:
    """Consomme le diagnostic ; refuse les destinations ambiguës sans deviner.

    Une feuille peut traverser plusieurs pages. Le texte propre d'un parent
    est conservé dans une section d'introduction, sans dupliquer ses enfants.
    Sur une page partagée, les titres exacts de l'outline servent d'ancres ;
    aucune détection par taille de police ni fenêtre de tokens.
    """
    if report.recommended_route is not Route.A:
        raise ValueError(
            f"Route {report.recommended_route} non implémentée ; Route A seule."
        )
    if report.errors:
        raise ValueError(f"Le probe signale des erreurs : {report.errors}")
    with pdf.open("rb") as handle:
        if hashlib.file_digest(handle, "sha256").hexdigest() != report.file_sha256:
            raise ValueError("Le PDF a changé depuis le probe.")
    pages, errors = read_pages(pdf, text_flow=report.reading_order == "text_flow")
    if errors:
        raise ValueError(f"Échec d'extraction : {errors}")
    boilerplate = {p.masked for p in report.boilerplate.patterns}
    patterns = [re.compile(pattern) for pattern in report.profile_boilerplate_patterns]
    lines = [
        [
            line.text
            for line in page.lines
            if normalize_line(line.text) not in boilerplate
            and not any(pattern.search(line.text) for pattern in patterns)
            and not (
                report.page_map.source is PageNumberSource.STANDALONE
                and line.text == str(report.page_map.book_page(page.index))
                and (
                    line.top < page.height * ZONE_FRACTION
                    or line.bottom > page.height * (1 - ZONE_FRACTION)
                )
            )
        ]
        for page in pages
    ]
    entries = report.outline.entries
    if not entries or any(e.pdf_index is None for e in entries):
        raise ValueError("L'outline contient des destinations non résolues.")
    anchors: list[tuple[int, int]] = []
    paths: list[str] = []
    stack: list[str] = []
    for i, entry in enumerate(entries):
        page_index = entry.pdf_index
        if page_index is None or not 0 <= page_index < len(pages):
            raise ValueError(f"Destination invalide : {entry.title}")
        if not 1 <= entry.level <= len(stack) + 1:
            raise ValueError(f"Hiérarchie d'outline invalide : {entry.title}")
        stack = [*stack[: entry.level - 1], entry.title]
        paths.append(" > ".join(stack))
        heading = report.profile_heading_overrides.get(entry.title)
        title = heading.text if heading is not None else entry.title
        matches = [
            n
            for n, line in enumerate(lines[page_index])
            if _title_key(line) == _title_key(title)
        ]
        if heading is not None:
            if len(matches) < heading.occurrence:
                raise ValueError(f"Ancre de profil introuvable : {entry.title}")
            matches = [matches[heading.occurrence - 1]]
        if len(matches) > 1:
            raise ValueError(
                f"Titre ambigu sur la page PDF {page_index + 1} : {entry.title}"
            )
        if matches:
            line_index = matches[0]
        else:
            # Un titre absent est acceptable pour une destination de page,
            # mais pas entre deux sections sœurs sur cette même page.
            siblings = [
                other
                for j, other in enumerate(entries)
                if j != i
                and other.pdf_index == page_index
                and other.level <= entry.level
            ]
            ancestors = entries[:i]
            if any(
                other not in ancestors or other.level == entry.level
                for other in siblings
            ):
                raise ValueError(f"Frontière de section introuvable : {entry.title}")
            line_index = 0
        anchor = (page_index, line_index)
        anchors.append(anchor)

    # Certains PDF classent les signets par thème plutôt que par page.
    # Les chemins viennent de la hiérarchie ; les bornes suivent les ancres.
    ordered = sorted(range(len(entries)), key=lambda i: (anchors[i], i))
    chunks: list[Chunk] = []
    for position, i in enumerate(ordered):
        start_page, start_line = anchors[i]
        end_page, end_line = (
            anchors[ordered[position + 1]]
            if position + 1 < len(ordered)
            else (len(pages), 0)
        )
        if any(
            _title_key(part)
            in {_title_key(name) for name in report.profile_drop_sections}
            for part in paths[i].split(" > ")
        ):
            continue
        content: list[ChunkPage] = []
        for page_index in range(start_page, min(end_page + 1, len(pages))):
            first = start_line if page_index == start_page else 0
            last = end_line if page_index == end_page else len(lines[page_index])
            selected = lines[page_index][first:last]
            if (
                page_index == start_page
                and selected
                and _title_key(selected[0])
                == _title_key(
                    report.profile_heading_overrides[entries[i].title].text
                    if entries[i].title in report.profile_heading_overrides
                    else entries[i].title
                )
            ):
                selected = selected[1:]
            text = "\n".join(selected).strip()
            if not text:
                continue
            page_map = report.page_map
            # Le repli IDENTITY et les marques d'imposition ne sont pas
            # des folios mesurés. Ils ne deviennent jamais des citations.
            book_page = (
                page_map.book_page(page_index)
                if page_map.method is not PageMapMethod.IDENTITY
                and page_map.source is not PageNumberSource.EMBEDDED
                else None
            )
            content.append(
                ChunkPage(pdf_page=page_index + 1, book_page=book_page, text=text)
            )
        if not content:
            continue
        section_path = paths[i]
        for part, segment in enumerate(
            split_section(content, section_path, max_tokens=max_tokens)
        ):
            raw_text = "\n\n".join(p.text for p in segment)
            chunk_id = hashlib.sha256(
                f"{report.file_sha256}:{i}:{part}:{raw_text}".encode()
            ).hexdigest()
            chunks.append(
                Chunk(
                    id=chunk_id,
                    book_id=report.profile_id or report.file_sha256,
                    section_path=section_path,
                    text=f"{section_path}\n\n{raw_text}",
                    raw_text=raw_text,
                    book_page=segment[0].book_page,
                    pdf_page=segment[0].pdf_page,
                    pages=segment,
                    type="table"
                    if any(p.book_page in report.profile_table_pages for p in segment)
                    else "rules",
                    route=Route.A,
                )
            )
    if not chunks:
        raise ValueError("Aucune section textuelle extraite.")
    return chunks


def write_chunks(chunks: list[Chunk], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(c.model_dump_json() + "\n" for c in chunks), encoding="utf-8"
    )


def read_chunks(path: Path) -> list[Chunk]:
    return [
        Chunk.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
