"""Découpage borné aux titres, paragraphes et phrases, avec provenance."""

from __future__ import annotations

import math
import re
from itertools import pairwise

from rulelawyer.ingest_types import ChunkPage

DEFAULT_MAX_TOKENS = 1200


def estimate_tokens(text: str) -> int:
    """Estimation CPU sans tokenizer : trois octets UTF-8 par token.

    La vraie limite du modèle reste vérifiée dans le backend BGE ; aucun
    texte n'est tronqué si ce budget estimé ne suffit pas.
    """
    return math.ceil(len(text.encode("utf-8")) / 3)


def split_section(
    pages: list[ChunkPage],
    section_path: str,
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    overlap: float = 0.15,
) -> list[list[ChunkPage]]:
    if max_tokens < 1 or not 0 <= overlap < 0.5:
        raise ValueError("Budget ou chevauchement invalide.")
    prefix = estimate_tokens(section_path + "\n\n")
    budget = max_tokens - prefix
    if budget < 1:
        raise ValueError("Le chemin de section dépasse le budget de chunking.")
    body = "\n\n".join(p.text for p in pages)
    if estimate_tokens(body) <= budget:
        return [pages]
    # Les titres internes sont conservés au début de leur unité sémantique.
    headings = [
        m.start()
        for m in re.finditer(r"(?m)^.*$", body)
        if m.group().strip().isupper()
        and any(c.isalpha() for c in m.group())
        and len(m.group().strip()) <= 120
    ]
    starts = sorted({0, *headings, len(body)})
    spans: list[tuple[int, int]] = []
    for start, end in pairwise(starts):
        if estimate_tokens(body[start:end]) <= budget:
            spans.append((start, end))
            continue
        # Dernier recours : paragraphes / fins de phrases. Une ligne longue
        # sans frontière sémantique est refusée, jamais coupée en tokens.
        cuts = [start]
        cuts.extend(
            start + m.end()
            for m in re.finditer(r"\n\s*\n|(?<=[.!?])\s+", body[start:end])
        )
        cuts.append(end)
        units = [(a, b) for a, b in pairwise(cuts) if a < b]
        pending: list[tuple[int, int]] = []
        for a, b in units:
            if estimate_tokens(body[a:b]) > budget:
                # Tables et listes : garder chaque ligne entière.
                line_cuts = [
                    a,
                    *(a + m.end() for m in re.finditer(r"\n", body[a:b])),
                    b,
                ]
                fragments = [(x, y) for x, y in pairwise(line_cuts) if x < y]
            else:
                fragments = [(a, b)]
            for x, y in fragments:
                if estimate_tokens(body[x:y]) > budget:
                    raise ValueError(
                        f"Paragraphe ou ligne indivisible trop long : {section_path}"
                    )
                if pending and estimate_tokens(body[pending[0][0] : y]) > budget:
                    spans.append((pending[0][0], pending[-1][1]))
                    tail: list[tuple[int, int]] = []
                    for unit in reversed(pending):
                        if (
                            estimate_tokens(body[unit[0] : pending[-1][1]])
                            > budget * overlap
                        ):
                            # Une unité entière peut dépasser la cible de 15 %.
                            # La conserver si elle tient avec le nouveau texte
                            # évite un chevauchement nul sur un paragraphe bref.
                            if (
                                not tail
                                and overlap > 0
                                and estimate_tokens(body[unit[0] : y]) <= budget
                            ):
                                tail = [unit]
                            break
                        tail.insert(0, unit)
                    while tail and estimate_tokens(body[tail[0][0] : y]) > budget:
                        tail.pop(0)
                    pending = tail
                pending.append((x, y))
        if pending:
            spans.append((pending[0][0], pending[-1][1]))
    # Garder les sous-titres comme frontières possibles, sans produire un
    # micro-chunk pour chaque intitulé de table ou de caractéristique.
    packed: list[tuple[int, int]] = []
    for start, end in spans:
        if (
            packed
            and packed[-1][1] == start
            and estimate_tokens(body[packed[-1][0] : end]) <= budget
        ):
            packed[-1] = (packed[-1][0], end)
        else:
            packed.append((start, end))
    offsets: list[tuple[int, int, ChunkPage]] = []
    position = 0
    for page in pages:
        offsets.append((position, position + len(page.text), page))
        position += len(page.text) + 2
    result: list[list[ChunkPage]] = []
    for start, end in packed:
        selected = []
        for page_start, page_end, page in offsets:
            text = (
                body[max(start, page_start) : min(end, page_end)].strip()
                if max(start, page_start) < min(end, page_end)
                else ""
            )
            if text:
                selected.append(page.model_copy(update={"text": text}))
        if selected:
            result.append(selected)
    return result
