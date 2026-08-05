"""Phase 1 — le diagnostic.

C'est le seul endroit du projet où une décision d'ingestion est prise. Le probe
lit le PDF, mesure, et sort un `ProbeReport`. Rien en aval ne re-diagnostique.

Contraintes de conception :

- **Aucune heuristique spécifique à un livre.** Le boilerplate est détecté par
  fréquence, pas par regex codée en dur. Un livre précis se corrige par un
  profil YAML, jamais dans ce fichier.
- **Rien n'est avalé.** Une page qui échoue produit une `PageError` visible dans
  le rapport CLI et dans le JSON.
- **Ordre imposé** : on lit les numéros de page imprimés *avant* de retirer le
  boilerplate, parce que sur beaucoup de livres le numéro vit *dans* la ligne
  de boilerplate.
"""

from __future__ import annotations

import hashlib
import re
import statistics
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import Any

import pdfplumber
from pypdf import PdfReader
from pypdf.generic import Destination

from rulelawyer.models import (
    BoilerplatePattern,
    BoilerplateReport,
    ColumnsReport,
    ImagesReport,
    OffsetRun,
    OutlineEntry,
    OutlineReport,
    PageError,
    PageMapMethod,
    PageMapReport,
    PageNumberSource,
    ProbeReport,
    Route,
    TextLayerReport,
    TextQuality,
    TextVerdict,
    TocReport,
)

# --- Seuils. Documentés ici, surchargeables par profil. ---------------------

BOILERPLATE_MIN_RATIO = 0.60
# Part haute/basse de la page traitée comme en-tête/pied. Volontairement
# généreuse : beaucoup de maquettes JDR posent le folio à ~90 % de la hauteur,
# dans un bandeau décoratif, et un seuil serré le manque en silence.
ZONE_FRACTION = 0.12
COLUMN_SAMPLE_SIZE = 20
COLUMN_GAP_FRACTION = 0.06  # écart en x, en fraction de largeur, séparant 2 colonnes
COLUMN_MIN_SHARE = 0.12  # part de lignes minimale pour qu'un cluster compte
LARGE_IMAGE_MIN_PX = 100
OUTLINE_MIN_ENTRIES = 10
OUTLINE_MAX_MEDIAN_SPAN = 30  # pages par section au-delà desquelles c'est trop gros
MAX_PAGE_NUMBER = 4000
# Sous cette couverture, le folio isolé est trop rare pour faire foi et on se
# rabat sur les nombres noyés dans les lignes — en le disant.
FOLIO_MIN_COVERAGE = 0.25

_DIGITS = re.compile(r"\d+")
_WS = re.compile(r"\s+")
_INT_TOKEN = re.compile(r"\b(\d{1,4})\b")
# « Titre … 42 » : une paire titre/numéro. On cherche des *paires dans la ligne*
# et non un motif de ligne entière, parce qu'une TdM sur deux colonnes ressort
# fusionnée en une seule ligne (« Game Terms 7 How are Telepathics Possible? 72 »)
# et que beaucoup de maquettes n'ont aucun point de conduite.
_TOC_PAIR = re.compile(
    r"(?P<title>[^\W\d_][^\d\n]{2,70}?)\s*[.·•‧∙…\-_ ]{1,}(?P<page>\d{1,4})(?=\s|$)"
)
TOC_MIN_PAIRS = 8  # sous ce seuil, c'est une table de jeu, pas une TdM
TOC_MIN_DENSITY = 0.55  # part des lignes de la page qui portent une paire
TOC_SEARCH_FRACTION = 0.12  # la TdM est en liminaire, on ne cherche pas partout
_SHORT_TOKEN_ALLOWLIST = {
    # Mots courts légitimes, fr + en : sans ça tout texte français passe pour du
    # mauvais OCR.
    "a",
    "à",
    "y",
    "en",
    "de",
    "du",
    "le",
    "la",
    "un",
    "ce",
    "se",
    "ne",
    "et",
    "ou",
    "on",
    "il",
    "je",
    "tu",
    "si",
    "sa",
    "so",
    "au",
    "os",
    "an",
    "as",
    "at",
    "be",
    "by",
    "do",
    "go",
    "he",
    "if",
    "in",
    "is",
    "it",
    "me",
    "my",
    "no",
    "of",
    "or",
    "to",
    "up",
    "us",
    "we",
    "d6",
    "d8",
    "d4",
    "1d",
    "2d",
}


SHADOW_TOLERANCE = 1.0  # points


def shadow_char_filter(tolerance: float = SHADOW_TOLERANCE) -> Any:
    """Prédicat pdfplumber qui écarte un glyphe déjà dessiné au même endroit.

    Les maquettes JDR dessinent volontiers le même texte deux fois à un demi-point
    d'écart pour obtenir une ombre portée. Les deux copies sont bien dans le
    contenu, l'assemblage en lignes les entrelace, et le folio « 38 » ressort en
    « 3388 » — avec pour effet de faire dérailler tout le mapping de pagination,
    silencieusement.

    `pdfplumber.Page.dedupe_chars` fait le travail mais coûte ~6× le temps
    d'extraction. Ici on bucketise en O(n) : un glyphe est un doublon s'il existe
    déjà un glyphe identique à moins de `tolerance` points en x et en y.

    Une instance de prédicat par page — il est volontairement à état, ce qui
    suppose que `Page.filter` n'évalue qu'une fois. C'est le cas (les objets
    filtrés sont mémoïsés), et `tests/test_probe.py::test_shadow_filter_*` le
    vérifie plutôt que de le supposer.
    """
    import math

    buckets: dict[tuple[str, int, int], list[tuple[float, float]]] = {}

    def keep(obj: dict[str, Any]) -> bool:
        if obj.get("object_type") != "char":
            return True
        text = str(obj.get("text", ""))
        x, y = float(obj["x0"]), float(obj["top"])
        bx, by = math.floor(x / tolerance), math.floor(y / tolerance)
        for i in (-1, 0, 1):
            for j in (-1, 0, 1):
                for kx, ky in buckets.get((text, bx + i, by + j), ()):
                    if abs(kx - x) <= tolerance and abs(ky - y) <= tolerance:
                        return False
        buckets.setdefault((text, bx, by), []).append((x, y))
        return True

    return keep


@dataclass(slots=True)
class Line:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float


@dataclass(slots=True)
class PageContent:
    index: int
    width: float
    height: float
    lines: list[Line] = field(default_factory=list)
    char_count: int = 0
    fonts: set[str] = field(default_factory=set)
    image_sizes: list[tuple[int, int]] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)


# --- Lecture -----------------------------------------------------------------


def _line_from_dict(raw: dict[str, Any]) -> Line | None:
    text = (raw.get("text") or "").strip()
    if not text:
        return None
    return Line(
        text=text,
        x0=float(raw["x0"]),
        x1=float(raw["x1"]),
        top=float(raw["top"]),
        bottom=float(raw["bottom"]),
    )


def _image_size(raw: dict[str, Any]) -> tuple[int, int]:
    """Dimensions en pixels de l'image source, pas sa taille d'affichage.

    Une vignette de 20x20 pt peut être une image 2000x2000 ; c'est la taille
    source qui dit si c'est du contenu ou un filet de maquette.
    """
    srcsize = raw.get("srcsize")
    if isinstance(srcsize, tuple | list) and len(srcsize) == 2:
        return int(srcsize[0]), int(srcsize[1])
    return int(abs(float(raw.get("width", 0)))), int(abs(float(raw.get("height", 0))))


def read_pages(path: Path) -> tuple[list[PageContent], list[PageError]]:
    """Une seule passe pdfplumber. Tout le reste travaille sur cette structure."""
    pages: list[PageContent] = []
    errors: list[PageError] = []

    with pdfplumber.open(path) as pdf:
        for index, page in enumerate(pdf.pages):
            content = PageContent(
                index=index,
                width=float(page.width),
                height=float(page.height),
            )
            try:
                deduped = page.filter(shadow_char_filter())
                chars = deduped.chars
                for raw in deduped.extract_text_lines(layout=False):
                    line = _line_from_dict(raw)
                    if line is not None:
                        content.lines.append(line)
                content.char_count = len(chars)
                content.fonts = {str(char.get("fontname", "")) for char in chars} - {""}
            except Exception as exc:
                errors.append(
                    PageError(
                        pdf_index=index,
                        stage="text",
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
            try:
                content.image_sizes = [_image_size(im) for im in page.images]
            except Exception as exc:
                errors.append(
                    PageError(
                        pdf_index=index,
                        stage="images",
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
            pages.append(content)
            page.close()

    return pages, errors


# --- 1. Couche texte ---------------------------------------------------------


def _load_dictionary() -> tuple[set[str], str] | None:
    """Dictionnaire système, si présent. Signal d'appoint uniquement.

    macOS et la plupart des Linux n'exposent qu'un dictionnaire anglais : on ne
    peut pas en faire le critère principal sans classer tout livre non anglais
    comme du mauvais OCR.
    """
    for candidate in (Path("/usr/share/dict/words"), Path("/usr/dict/words")):
        if candidate.exists():
            words = {
                w.strip().lower()
                for w in candidate.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()
                if w.strip()
            }
            if words:
                return words, "en"
    return None


def measure_text_quality(sample: str) -> TextQuality:
    total = len(sample) or 1
    replacement = sample.count("�")
    nonprintable = sum(
        1
        for ch in sample
        if unicodedata.category(ch) in {"Cc", "Cf", "Co", "Cn"} and ch not in "\n\t"
    )
    letters = sum(1 for ch in sample if ch.isalpha())

    tokens = [t for t in re.split(r"[^\w'-]+", sample) if t]
    alpha_tokens = [t for t in tokens if any(c.isalpha() for c in t)]
    mean_len = statistics.fmean(len(t) for t in alpha_tokens) if alpha_tokens else 0.0
    short = sum(
        1
        for t in alpha_tokens
        if len(t) <= 2 and t.lower() not in _SHORT_TOKEN_ALLOWLIST
    )
    short_rate = short / len(alpha_tokens) if alpha_tokens else 0.0

    hit_rate: float | None = None
    language: str | None = None
    loaded = _load_dictionary()
    if loaded is not None and alpha_tokens:
        words, language = loaded
        candidates = [t.lower().strip("'-") for t in alpha_tokens if len(t) > 3]
        if candidates:
            hit_rate = sum(1 for t in candidates if t in words) / len(candidates)

    return TextQuality(
        replacement_char_rate=replacement / total,
        nonprintable_rate=nonprintable / total,
        letter_ratio=letters / total,
        mean_token_length=mean_len,
        short_token_rate=short_rate,
        dictionary_hit_rate=hit_rate,
        dictionary_language=language,
    )


def analyze_text_layer(
    pages: list[PageContent], embedded_fonts: set[str]
) -> TextLayerReport:
    page_count = len(pages) or 1
    with_text = [p for p in pages if p.char_count > 20]
    fonts = sorted({f for p in pages for f in p.fonts})
    report = TextLayerReport(
        verdict=TextVerdict.ABSENT,
        font_names=fonts[:40],
        font_count=len(fonts),
        embedded_font_count=len(embedded_fonts),
        pages_with_text=len(with_text),
        median_chars_per_page=(
            statistics.median([p.char_count for p in pages]) if pages else 0.0
        ),
    )

    text_ratio = len(with_text) / page_count
    if text_ratio < 0.05:
        report.reasons.append(
            f"{len(with_text)}/{page_count} pages portent du texte — scan."
        )
        return report

    # Échantillon au milieu du livre : les liminaires sont peu représentatifs.
    middle = with_text[len(with_text) // 4 : len(with_text) // 4 + 25] or with_text[:25]
    quality = measure_text_quality("\n".join(p.text for p in middle))
    report.quality = quality

    flags: list[str] = []
    if quality.replacement_char_rate > 0.005:
        flags.append(
            f"caractères de remplacement : {quality.replacement_char_rate:.1%}"
        )
    if quality.nonprintable_rate > 0.01:
        flags.append(f"caractères non imprimables : {quality.nonprintable_rate:.1%}")
    if quality.letter_ratio < 0.50:
        flags.append(f"part de lettres faible : {quality.letter_ratio:.1%}")
    if quality.short_token_rate > 0.35:
        flags.append(
            f"mots hachés : {quality.short_token_rate:.1%} de tokens de 1-2 lettres"
        )
    if quality.mean_token_length < 3.0:
        flags.append(f"longueur moyenne de mot : {quality.mean_token_length:.1f}")
    if embedded_fonts:
        report.reasons.append(
            f"{len(embedded_fonts)} polices embarquées — "
            "texte de composition, pas d'OCR."
        )
    else:
        flags.append(
            "aucune police embarquée : couche texte probablement ajoutée par OCR"
        )
    if text_ratio < 0.60:
        flags.append(f"seulement {text_ratio:.0%} des pages portent du texte")

    report.reasons.extend(flags)
    if len(flags) >= 2:
        report.verdict = TextVerdict.BAD
    elif flags:
        report.verdict = TextVerdict.SUSPECT
    else:
        report.verdict = TextVerdict.NATIVE
    return report


# --- 2. Outline --------------------------------------------------------------


def _walk_outline(
    node: Any, reader: PdfReader, level: int, out: list[OutlineEntry]
) -> None:
    if isinstance(node, list):
        for child in node:
            _walk_outline(child, reader, level + 1 if out else level, out)
        return
    if isinstance(node, Destination):
        try:
            pdf_index = reader.get_destination_page_number(node)
        except Exception:
            pdf_index = None
        out.append(
            OutlineEntry(title=str(node.title), level=level, pdf_index=pdf_index)
        )


def analyze_outline(reader: PdfReader, page_count: int) -> OutlineReport:
    report = OutlineReport()
    try:
        raw = reader.outline
    except Exception as exc:
        report.reasons.append(f"outline illisible : {type(exc).__name__}: {exc}")
        return report
    if not raw:
        report.reasons.append("aucun outline dans le PDF.")
        return report

    entries: list[OutlineEntry] = []
    for child in raw:
        _walk_outline(child, reader, 1, entries)

    report.entries = entries
    report.entry_count = len(entries)
    report.max_depth = max((e.level for e in entries), default=0)
    resolved = sorted(e.pdf_index for e in entries if e.pdf_index is not None)
    report.resolved_count = len(resolved)

    if not resolved:
        report.reasons.append("aucune entrée d'outline ne résout vers une page.")
        return report

    starts = sorted(set(resolved))
    spans = [b - a for a, b in pairwise(starts)]
    spans.append(page_count - starts[-1])
    report.largest_gap_pages = max([*spans, starts[0]])
    report.coverage_ratio = (page_count - starts[0]) / page_count
    median_span = statistics.median(spans) if spans else float(page_count)

    if report.entry_count < OUTLINE_MIN_ENTRIES:
        report.reasons.append(
            f"{report.entry_count} entrées pour {page_count} pages — trop grossier."
        )
    if report.resolved_count / report.entry_count < 0.8:
        report.reasons.append(
            f"{report.resolved_count}/{report.entry_count} entrées résolues seulement."
        )
    if median_span > OUTLINE_MAX_MEDIAN_SPAN:
        report.reasons.append(
            f"médiane de {median_span:.0f} pages par section — sections trop larges."
        )
    if report.coverage_ratio < 0.80:
        report.reasons.append(
            f"couverture {report.coverage_ratio:.0%} : "
            f"les {starts[0]} premières pages ne sont dans aucune section."
        )

    report.usable = not report.reasons
    if report.usable:
        report.reasons.append(
            f"{report.entry_count} entrées sur {report.max_depth} niveaux, "
            f"médiane {median_span:.0f} pages/section — exploitable telle quelle."
        )
    return report


# --- 3. Colonnes -------------------------------------------------------------


def _cluster_starts(values: list[float], gap: float) -> list[list[float]]:
    clusters: list[list[float]] = []
    for value in sorted(values):
        if clusters and value - clusters[-1][-1] <= gap:
            clusters[-1].append(value)
        else:
            clusters.append([value])
    return clusters


def analyze_columns(pages: list[PageContent], boilerplate: set[str]) -> ColumnsReport:
    """Compte les colonnes en groupant les *débuts* de lignes.

    On groupe les `x0` et non tous les mots : un titre pleine largeur, une table
    ou un folio étalent l'histogramme et font croire à une colonne unique.
    """
    candidates = [p for p in pages if len(p.lines) >= 8]
    if not candidates:
        return ColumnsReport(sampled_pages=[])

    # Milieu du livre : les liminaires sont mono-colonne et mentent.
    start = len(candidates) // 4
    window = candidates[start : start + max(COLUMN_SAMPLE_SIZE * 3, 1)]
    step = max(1, len(window) // COLUMN_SAMPLE_SIZE)
    sample = window[::step][:COLUMN_SAMPLE_SIZE]

    counts: Counter[int] = Counter()
    for page in sample:
        gap = page.width * COLUMN_GAP_FRACTION
        top_zone = page.height * ZONE_FRACTION
        bottom_zone = page.height * (1 - ZONE_FRACTION)
        starts = [
            line.x0
            for line in page.lines
            if normalize_line(line.text) not in boilerplate
            and top_zone < line.top < bottom_zone
            and (line.x1 - line.x0) < page.width * 0.70
        ]
        if len(starts) < 8:
            counts[1] += 1
            continue
        clusters = _cluster_starts(starts, gap)
        significant = [c for c in clusters if len(c) >= COLUMN_MIN_SHARE * len(starts)]
        counts[max(1, len(significant))] += 1

    distribution = dict(sorted(counts.items()))
    observed = [n for n, c in counts.items() for _ in range(c)]
    dominant = counts.most_common(1)[0][0] if counts else 1
    variance = statistics.pvariance(observed) if len(observed) > 1 else 0.0
    return ColumnsReport(
        dominant=dominant,
        distribution=distribution,
        variance=variance,
        stable=variance < 0.25,
        sampled_pages=[p.index for p in sample],
    )


# --- 4. Boilerplate ----------------------------------------------------------


def normalize_line(text: str) -> str:
    """Normalise une ligne pour la comparer d'une page à l'autre.

    Le masquage des chiffres est indispensable : la ligne d'imposition contient
    le numéro de page, donc sans masquage chaque occurrence est unique et la
    détection ne trouve rien — en se présentant comme « ce livre n'a pas de
    boilerplate », ce qui est faux et silencieux.
    """
    collapsed = _WS.sub(" ", text.strip().lower())
    return _DIGITS.sub("#", collapsed)


def detect_boilerplate(pages: list[PageContent]) -> BoilerplateReport:
    page_count = len(pages) or 1
    seen: defaultdict[str, set[int]] = defaultdict(set)
    example: dict[str, str] = {}
    zone_votes: defaultdict[str, Counter[str]] = defaultdict(Counter)

    for page in pages:
        top_zone = page.height * ZONE_FRACTION
        bottom_zone = page.height * (1 - ZONE_FRACTION)
        for line in page.lines:
            key = normalize_line(line.text)
            if len(key) < 4:
                continue
            seen[key].add(page.index)
            example.setdefault(key, line.text)
            if line.top < top_zone:
                zone_votes[key]["header"] += 1
            elif line.bottom > bottom_zone:
                zone_votes[key]["footer"] += 1
            else:
                zone_votes[key]["body"] += 1

    patterns = [
        BoilerplatePattern(
            masked=key,
            page_ratio=len(indexes) / page_count,
            page_count=len(indexes),
            example=example[key],
            zone=zone_votes[key].most_common(1)[0][0],
        )
        for key, indexes in seen.items()
        if len(indexes) / page_count >= BOILERPLATE_MIN_RATIO
    ]
    patterns.sort(key=lambda p: p.page_ratio, reverse=True)
    return BoilerplateReport(
        patterns=patterns,
        max_page_ratio=patterns[0].page_ratio if patterns else 0.0,
    )


# --- 5. Numéro de page imprimé ----------------------------------------------


def _collect_offset_votes(
    pages: list[PageContent],
) -> tuple[dict[int, Counter[int]], dict[int, Counter[int]]]:
    """Candidats de décalage par page, séparés selon leur provenance.

    `standalone` : la ligne entière est un nombre. C'est le folio.
    `embedded`   : le nombre est noyé dans une ligne plus longue. C'est le plus
                   souvent une marque d'imposition, un millésime ou un prix.
    """
    standalone: dict[int, Counter[int]] = {}
    embedded: dict[int, Counter[int]] = {}

    for page in pages:
        top_zone = page.height * ZONE_FRACTION
        bottom_zone = page.height * (1 - ZONE_FRACTION)
        for line in page.lines:
            if not (line.top < top_zone or line.bottom > bottom_zone):
                continue
            text = line.text.strip()
            if text.isdigit():
                value = int(text)
                if 0 < value <= MAX_PAGE_NUMBER:
                    votes = standalone.setdefault(page.index, Counter())
                    votes[page.index - value] += 1
                continue
            for match in _INT_TOKEN.finditer(text):
                value = int(match.group(1))
                if 0 < value <= MAX_PAGE_NUMBER:
                    embedded.setdefault(page.index, Counter())[page.index - value] += 1

    return standalone, embedded


def _runs_from_votes(
    per_page_offsets: dict[int, Counter[int]], page_count: int
) -> tuple[list[OffsetRun], int]:
    """Réduit les votes en plages de décalage constant.

    Le décalage retenu pour une page est celui, parmi ses candidats, le mieux
    soutenu sur l'ensemble du livre : ça élimine les nombres parasites sans
    supposer un décalage unique.
    """
    if not per_page_offsets:
        return [], 0

    global_votes: Counter[int] = Counter()
    for offsets in per_page_offsets.values():
        global_votes.update(offsets.keys())

    runs: list[OffsetRun] = []
    for index in sorted(per_page_offsets):
        offset = max(per_page_offsets[index], key=lambda o: (global_votes[o], -abs(o)))
        if runs and runs[-1].page_offset == offset and runs[-1].pdf_end == index - 1:
            runs[-1].pdf_end = index
        else:
            runs.append(OffsetRun(pdf_start=index, pdf_end=index, page_offset=offset))

    # Une plage d'une seule page est du bruit, pas un encart.
    solid = [r for r in runs if r.pdf_end - r.pdf_start >= 2]
    measured = sum(r.pdf_end - r.pdf_start + 1 for r in solid)

    # Les pages sans folio — illustrations pleine page, ouvertures de section —
    # fragmentent les plages sans qu'aucun décalage ne change. On les recolle
    # quand le décalage est identique de part et d'autre : c'est une
    # interpolation, pas une mesure, et `measured_pages` continue de ne compter
    # que les pages réellement lues.
    merged: list[OffsetRun] = []
    for run in solid:
        if merged and merged[-1].page_offset == run.page_offset:
            merged[-1].pdf_end = run.pdf_end
        else:
            merged.append(run)

    return merged, measured


def _dominant_offset(runs: list[OffsetRun]) -> int | None:
    if not runs:
        return None
    weights: Counter[int] = Counter()
    for run in runs:
        weights[run.page_offset] += run.pdf_end - run.pdf_start + 1
    return weights.most_common(1)[0][0]


def detect_page_map(pages: list[PageContent]) -> PageMapReport:
    """Mesure `pdf_index0 = book_page + page_offset`, par plages.

    Deux précautions, chacune contre une erreur qui ne se voit pas :

    1. **Le folio prime sur la marque d'imposition.** Un nombre noyé dans une
       ligne — « …Layout 1 02/04/2009 Page 38 » — est un artefact de fabrication
       qui compte les feuilles du fichier de maquette. Il coïncide parfois avec
       le folio, jamais par construction. Un livre dont la maquette compte la
       couverture donne deux décalages parfaitement cohérents et différents : en
       votant sur le volume, on prendrait l'imposition, à 100 % de confiance, et
       chaque citation serait fausse d'une page. On ne retient donc l'imposition
       que faute de folio, et un désaccord entre les deux fait chuter la
       confiance au lieu d'être arbitré en silence.

    2. **Le décalage n'est pas un scalaire.** Couvertures et encarts en
       introduisent de nouveaux en cours de livre.
    """
    page_count = len(pages) or 1
    standalone, embedded = _collect_offset_votes(pages)

    folio_runs, folio_measured = _runs_from_votes(standalone, page_count)
    imposition_runs, imposition_measured = _runs_from_votes(embedded, page_count)

    notes: list[str] = []
    if folio_measured >= FOLIO_MIN_COVERAGE * page_count:
        runs, measured = folio_runs, folio_measured
        source = PageNumberSource.STANDALONE
        notes.append(
            f"Folio lu directement sur {folio_measured} pages "
            "(ligne dont le texte entier est un nombre)."
        )
        other = _dominant_offset(imposition_runs)
        mine = _dominant_offset(folio_runs)
        if (
            other is not None
            and mine is not None
            and other != mine
            and imposition_measured >= 0.5 * page_count
        ):
            notes.append(
                f"Un nombre récurrent noyé en en-tête/pied suggère un décalage "
                f"{other}, le folio dit {mine}. Le folio fait foi, mais vérifiez "
                "une citation avant d'indexer tout le livre."
            )
    elif imposition_measured > 0:
        runs, measured = imposition_runs, imposition_measured
        source = PageNumberSource.EMBEDDED
        notes.append(
            f"Aucun folio isolé exploitable ({folio_measured} pages seulement) : "
            "décalage déduit d'un nombre récurrent en en-tête/pied, souvent une "
            "marque d'imposition. À confirmer par un profil."
        )
    else:
        return PageMapReport(
            method=PageMapMethod.IDENTITY,
            source=PageNumberSource.NONE,
            confidence=0.0,
            notes=["Aucun numéro de page trouvé : on retombe sur pdf_page 1-based."],
        )

    covered = sum(r.pdf_end - r.pdf_start + 1 for r in runs)
    uniform = (
        runs[0].page_offset if len(runs) == 1 and covered >= 0.9 * page_count else None
    )
    if covered > measured:
        notes.append(
            f"{covered - measured} page(s) sans folio (illustrations pleine page, "
            "ouvertures de section) sont interpolées : le décalage est identique "
            "de part et d'autre."
        )
    uncovered = page_count - covered
    if uncovered:
        notes.append(
            f"{uncovered} page(s) restent hors de toute plage mesurée. "
            "`book_page()` y renvoie None plutôt qu'un numéro plausible."
        )
    confidence = measured / page_count
    if source is PageNumberSource.EMBEDDED:
        confidence *= 0.5  # mesure indirecte : la confiance ne doit pas dire 100 %

    return PageMapReport(
        method=PageMapMethod.PRINTED,
        source=source,
        confidence=confidence,
        monotonic=all(a.pdf_end < b.pdf_start for a, b in pairwise(runs)),
        runs=runs,
        measured_pages=measured,
        uniform_offset=uniform,
        notes=notes,
    )


# --- 6. Images ---------------------------------------------------------------


def analyze_images(pages: list[PageContent]) -> ImagesReport:
    sizes = [s for page in pages for s in page.image_sizes]
    if not sizes:
        return ImagesReport()
    large = [
        (w, h) for w, h in sizes if w >= LARGE_IMAGE_MIN_PX and h >= LARGE_IMAGE_MIN_PX
    ]
    per_page = len(large) / (len(pages) or 1)
    return ImagesReport(
        total=len(sizes),
        large_count=len(large),
        large_per_page=per_page,
        median_area_px=statistics.median([w * h for w, h in sizes]),
        illustration_heavy=per_page >= 0.8,
    )


# --- 7. Table des matières textuelle ----------------------------------------


def find_toc(pages: list[PageContent], boilerplate: set[str]) -> TocReport:
    """Repère les pages de table des matières textuelle.

    Trois pièges, tous rencontrés sur de vrais livres :

    - la TdM est sur deux colonnes et l'extraction les fusionne, donc on compte
      les *paires* titre/numéro dans la ligne au lieu d'exiger un motif de ligne
      entière ;
    - beaucoup de maquettes n'ont aucun point de conduite, juste un blanc ;
    - une table d'équipement (« Fusil d'assaut 12 ») ressemble à s'y méprendre à
      une TdM, d'où la double condition densité + fenêtre de recherche limitée
      au liminaire.
    """
    report = TocReport()
    window = max(20, int(len(pages) * TOC_SEARCH_FRACTION))

    for page in pages[:window]:
        body = [
            line for line in page.lines if normalize_line(line.text) not in boilerplate
        ]
        if not body:
            continue

        pairs: list[tuple[str, int]] = []
        lines_with_pair = 0
        for line in body:
            found = [
                (m.group("title").strip(), int(m.group("page")))
                for m in _TOC_PAIR.finditer(line.text)
            ]
            found = [(t, n) for t, n in found if len(t) >= 4 and 0 < n <= len(pages)]
            if found:
                lines_with_pair += 1
                pairs.extend(found)

        density = lines_with_pair / len(body)
        if len(pairs) >= TOC_MIN_PAIRS and density >= TOC_MIN_DENSITY:
            report.pages.append(page.index)
            report.entry_count += len(pairs)
            for title, number in pairs:
                if len(report.sample) >= 6:
                    break
                report.sample.append(f"{title} … {number}")

    return report


# --- Routage -----------------------------------------------------------------


def choose_route(
    text_layer: TextLayerReport, outline: OutlineReport, images: ImagesReport
) -> tuple[Route, list[str], str]:
    rationale: list[str] = []

    if text_layer.verdict is TextVerdict.ABSENT:
        rationale.append("Aucune couche texte : il faut un VLM pour lire les pages.")
        return Route.C, rationale, "GPU requis — compter ~30-60 min sur 250 pages."
    if text_layer.verdict is TextVerdict.BAD:
        rationale.append(
            "Couche texte présente mais inexploitable — une mauvaise couche OCR "
            "est pire que pas de couche du tout, on la jette et on re-parse."
        )
        return Route.C, rationale, "GPU requis — compter ~30-60 min sur 250 pages."

    if text_layer.verdict is TextVerdict.SUSPECT:
        rationale.append(
            "Couche texte douteuse : Route B tentée, mais vérifie le rapport — "
            "forcer `--route C` peut donner un bien meilleur résultat."
        )

    if outline.usable:
        rationale.append(
            "Outline exploitable : c'est la vérité terrain des sections, "
            "on ne re-détecte aucun titre."
        )
        if images.illustration_heavy:
            rationale.append(
                f"Livre illustration-lourd ({images.large_per_page:.1f} grandes "
                "images/page) : `--route visual` peut valoir un essai en second."
            )
        return Route.A, rationale, "CPU seul — quelques minutes."

    rationale.append(
        "Outline absent ou inutilisable : la hiérarchie sera reconstruite "
        "depuis la table des matières textuelle et le clustering de styles."
    )
    return Route.B, rationale, "CPU seul — plus lent, compter ~10 min."


# --- Entrée publique ---------------------------------------------------------


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _embedded_fonts(reader: PdfReader) -> set[str]:
    """Polices réellement embarquées (FontFile*).

    C'est le meilleur substitut à `pdffonts` sans dépendre de poppler : une
    couche texte sans police embarquée sent l'OCR ajouté après coup.
    """
    found: set[str] = set()
    for page in reader.pages:
        try:
            resources = page.get("/Resources")
            fonts = resources.get("/Font") if resources else None
            if not fonts:
                continue
            for ref in fonts.values():
                font = ref.get_object()
                descriptor = font.get("/FontDescriptor")
                if descriptor is None:
                    for descendant in font.get("/DescendantFonts", []) or []:
                        descriptor = descendant.get_object().get("/FontDescriptor")
                        if descriptor is not None:
                            break
                if descriptor is None:
                    continue
                if any(
                    key in descriptor
                    for key in ("/FontFile", "/FontFile2", "/FontFile3")
                ):
                    found.add(str(font.get("/BaseFont", "?")))
        except Exception:
            continue
    return found


def probe(path: Path) -> ProbeReport:
    reader = PdfReader(str(path))
    pages, errors = read_pages(path)

    text_layer = analyze_text_layer(pages, _embedded_fonts(reader))
    outline = analyze_outline(reader, len(pages))
    # Ordre imposé : le numéro imprimé se lit AVANT tout retrait de boilerplate.
    page_map = detect_page_map(pages)
    boilerplate = detect_boilerplate(pages)
    columns = analyze_columns(pages, {p.masked for p in boilerplate.patterns})
    images = analyze_images(pages)
    toc = find_toc(pages, {p.masked for p in boilerplate.patterns})

    route, rationale, cost = choose_route(text_layer, outline, images)
    meta = reader.metadata

    # Empreinte pour la clé `match` d'un profil. On saute les pages sans texte :
    # l'index 0 est presque toujours une couverture graphique, et en hacher le
    # texte revient à hacher la chaîne vide — une empreinte qui collerait alors à
    # tous les livres du monde.
    fingerprint_page = next((p for p in pages if len(p.text.strip()) >= 200), None)
    return ProbeReport(
        pdf_path=str(path),
        file_sha256=_sha256(path),
        page_count=len(pages),
        producer=str(meta.producer) if meta and meta.producer else None,
        creator=str(meta.creator) if meta and meta.creator else None,
        first_page_text_sha256=(
            hashlib.sha256(fingerprint_page.text.encode()).hexdigest()
            if fingerprint_page is not None
            else None
        ),
        first_page_text_pdf_index=(
            fingerprint_page.index if fingerprint_page is not None else None
        ),
        text_layer=text_layer,
        outline=outline,
        columns=columns,
        boilerplate=boilerplate,
        page_map=page_map,
        images=images,
        toc=toc,
        recommended_route=route,
        route_rationale=rationale,
        estimated_cost=cost,
        errors=errors,
    )
