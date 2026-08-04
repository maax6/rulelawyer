"""Tests unitaires du probe — aucun PDF requis.

Ces tests portent sur les mécanismes qui échouent *silencieusement* : une
détection de boilerplate qui rend zéro motif et un mapping de pagination faux
d'une unité se présentent tous les deux comme des résultats légitimes.
"""

from __future__ import annotations

import pytest

from rulelawyer.models import PageMapMethod
from rulelawyer.probe import (
    Line,
    PageContent,
    detect_boilerplate,
    detect_page_map,
    find_toc,
    measure_text_quality,
    normalize_line,
    shadow_char_filter,
)

PAGE_H = 800.0
PAGE_W = 600.0
FOOTER_TOP = PAGE_H * 0.95


def make_page(index: int, footer: str, body: list[str] | None = None) -> PageContent:
    lines = [Line(text=footer, x0=50, x1=550, top=FOOTER_TOP, bottom=FOOTER_TOP + 10)]
    lines += [
        Line(text=text, x0=50, x1=550, top=200.0 + 12 * i, bottom=210.0 + 12 * i)
        for i, text in enumerate(body or [])
    ]
    return PageContent(
        index=index, width=PAGE_W, height=PAGE_H, lines=lines, char_count=1500
    )


# --- Masquage des chiffres ---------------------------------------------------


def test_normalize_line_masks_digits() -> None:
    assert normalize_line("Corp 2008 Layout Page 57") == "corp # layout page #"


def test_normalize_line_collapses_whitespace_and_case() -> None:
    assert normalize_line("  THE   Rules  ") == "the rules"


def test_boilerplate_survives_the_embedded_page_number() -> None:
    """Le cas qui casse tout si le masquage manque.

    La ligne d'imposition contient le numéro de page : sans masquage, chaque
    occurrence est unique, la détection rend zéro motif, et ça se présente comme
    « ce livre n'a pas de boilerplate ».
    """
    pages = [
        make_page(i, f"Corp 2008 Mongoose:Layout 1 Page {i + 1}") for i in range(50)
    ]
    report = detect_boilerplate(pages)

    assert report.patterns, "boilerplate non détecté : le masquage des chiffres a sauté"
    assert report.patterns[0].page_ratio == 1.0
    assert report.patterns[0].masked == "corp # mongoose:layout # page #"
    assert report.patterns[0].zone == "footer"


def test_boilerplate_ignores_lines_below_threshold() -> None:
    pages = [
        make_page(i, "Corp Page 1", body=["rare"] if i < 5 else []) for i in range(50)
    ]
    masked = {p.masked for p in detect_boilerplate(pages).patterns}
    assert "rare" not in masked


# --- Pagination --------------------------------------------------------------


def test_page_map_measures_uniform_offset() -> None:
    """book_page = pdf_index0 + 1, donc page_offset = -1."""
    pages = [make_page(i, f"Corp Layout Page {i + 1}") for i in range(40)]
    report = detect_page_map(pages)

    assert report.method is PageMapMethod.PRINTED
    assert report.uniform_offset == -1
    assert report.confidence == 1.0
    assert report.book_page(0) == 1
    assert report.book_page(39) == 40


def test_page_map_handles_a_piecewise_offset() -> None:
    """Un encart décale la pagination en cours de route.

    Un offset scalaire unique produirait des citations fausses sur toute la
    seconde moitié du livre, sans rien signaler.
    """
    pages = [make_page(i, f"Page {i + 1}") for i in range(20)]
    pages += [make_page(i, f"Page {i - 3}") for i in range(20, 40)]
    report = detect_page_map(pages)

    assert report.uniform_offset is None, "le décalage n'est pas constant ici"
    assert len(report.runs) == 2
    assert report.monotonic
    assert report.book_page(5) == 6
    assert report.book_page(25) == 22


def test_page_map_ignores_constant_noise_like_a_year() -> None:
    """Une année dans le pied de page ne doit pas passer pour un folio."""
    pages = [make_page(i, f"Copyright 2009 — {i + 1}") for i in range(40)]
    assert detect_page_map(pages).uniform_offset == -1


def test_page_map_reports_identity_when_nothing_is_printed() -> None:
    pages = [make_page(i, "Corporation Core Rulebook") for i in range(20)]
    report = detect_page_map(pages)
    assert report.method is PageMapMethod.IDENTITY
    assert report.confidence == 0.0
    assert report.book_page(7) == 8  # repli : pdf_page 1-based


def test_page_map_ignores_body_numbers() -> None:
    """Seules les zones d'en-tête et de pied comptent."""
    pages = [make_page(i, "Corporation", body=[f"{i + 1}"]) for i in range(20)]
    assert detect_page_map(pages).method is PageMapMethod.IDENTITY


# --- Table des matières textuelle -------------------------------------------


def toc_page(index: int, lines: list[str]) -> PageContent:
    return PageContent(
        index=index,
        width=PAGE_W,
        height=PAGE_H,
        lines=[
            Line(text=t, x0=50, x1=550, top=100.0 + 14 * i, bottom=112.0 + 14 * i)
            for i, t in enumerate(lines)
        ],
        char_count=900,
    )


def test_find_toc_reads_a_two_column_merged_layout() -> None:
    """L'extraction fusionne les deux colonnes d'une TdM en une seule ligne.

    Exiger un motif de ligne entière « titre ... numéro » rate ce cas — et une
    Route B privée de sa source primaire reconstruit une hiérarchie fausse.
    """
    merged = [
        "Game Terms 7 How are Telepathics Possible? 72",
        "Agents: What are they? 8 Using Telepathics 73",
        "Agent Physiology 9 Description of Telepathic Skills 74",
        "Character Creation 12 Character Advancement 77",
        "Rolling Attributes 14 Rank and Rank Points 78",
        "Choosing Skills 17 Improving Skills 80",
    ]
    pages = [toc_page(0, ["Corporation"]), toc_page(1, merged)]
    pages += [toc_page(i, ["du texte courant sans numéro"]) for i in range(2, 100)]

    report = find_toc(pages, boilerplate=set())
    assert report.pages == [1]
    assert report.entry_count >= 12


def test_find_toc_reads_dot_leaders() -> None:
    titles = [
        "Création de personnage",
        "Attributs et compétences",
        "Résolution des actions",
        "Combat rapproché",
        "Combat à distance",
        "Blessures et soins",
        "Équipement standard",
        "Armes et armures",
        "Véhicules",
        "Antagonistes",
    ]
    lines = [f"{t} .......... {(i + 1) * 7}" for i, t in enumerate(titles)]
    pages = [toc_page(0, lines)] + [toc_page(i, ["texte"]) for i in range(1, 100)]
    assert find_toc(pages, boilerplate=set()).pages == [0]


def test_find_toc_is_not_fooled_by_an_equipment_table() -> None:
    """« Fusil d'assaut 12 » ressemble à une entrée de TdM. Ça n'en est pas une."""
    table = [
        "Fusil d'assaut 12",
        "Pistolet lourd 8",
        "Lame monofilament 6",
    ]
    body = ["Le personnage dépense un point de destin pour relancer les dés."] * 8
    pages = [toc_page(0, table + body)]
    pages += [toc_page(i, ["texte courant"]) for i in range(1, 100)]
    assert find_toc(pages, boilerplate=set()).pages == []


def test_find_toc_ignores_boilerplate_lines() -> None:
    """Le boilerplate ne doit ni compter comme entrée ni diluer la densité."""
    titles = [
        "Création de personnage",
        "Attributs et compétences",
        "Résolution des actions",
        "Combat rapproché",
        "Combat à distance",
        "Blessures et soins",
        "Équipement standard",
        "Armes et armures",
        "Véhicules",
        "Antagonistes",
    ]
    lines = ["Corp Layout Page 4"] + [
        f"{t} ..... {(i + 1) * 5}" for i, t in enumerate(titles)
    ]
    pages = [toc_page(0, lines)] + [toc_page(i, ["texte"]) for i in range(1, 100)]
    report = find_toc(pages, boilerplate={normalize_line("Corp Layout Page 4")})
    assert report.pages == [0]
    assert all("Corp Layout" not in s for s in report.sample)


# --- Qualité de la couche texte ---------------------------------------------


def test_text_quality_is_not_fooled_by_french() -> None:
    """Un livre français ne doit pas être classé comme du mauvais OCR."""
    sample = (
        "Le personnage dépense un point de destin pour relancer les dés. "
        "Si le total dépasse la difficulté, l'action réussit et le meneur "
        "décrit les conséquences de cette réussite sur la scène en cours."
    ) * 5
    quality = measure_text_quality(sample)

    assert quality.replacement_char_rate == 0.0
    assert quality.short_token_rate < 0.20
    assert quality.mean_token_length > 3.5


def test_text_quality_flags_shredded_ocr() -> None:
    sample = "Th e ch ar act er sp en ds a po int of de sti ny t o r el au nch " * 20
    quality = measure_text_quality(sample)
    assert quality.short_token_rate > 0.35


def test_text_quality_flags_replacement_characters() -> None:
    quality = measure_text_quality("le personnage ���� dépense ���� un point ����")
    assert quality.replacement_char_rate > 0.005


# --- Ombres portées ----------------------------------------------------------


def _char(text: str, x0: float, top: float) -> dict[str, object]:
    return {"object_type": "char", "text": text, "x0": x0, "top": top}


def test_shadow_filter_drops_the_offset_duplicate() -> None:
    """« 38 » dessiné deux fois à un demi-point ne doit pas devenir « 3388 »."""
    keep = shadow_char_filter()
    chars = [
        _char("3", 76.5, 723.3),
        _char("8", 84.6, 723.3),
        _char("3", 75.9, 722.8),  # ombre
        _char("8", 84.1, 722.8),  # ombre
    ]
    assert [c["text"] for c in chars if keep(c)] == ["3", "8"]


def test_shadow_filter_keeps_genuine_repeats() -> None:
    """Deux glyphes identiques mais réellement distincts restent : « 77 »."""
    keep = shadow_char_filter()
    chars = [_char("7", 100.0, 500.0), _char("7", 108.0, 500.0)]
    assert [c["text"] for c in chars if keep(c)] == ["7", "7"]


def test_shadow_filter_leaves_non_chars_alone() -> None:
    keep = shadow_char_filter()
    assert keep({"object_type": "rect", "x0": 0, "top": 0})


@pytest.mark.parametrize("dx,dy", [(0.0, 0.0), (0.6, 0.5), (-0.9, 0.9)])
def test_shadow_filter_tolerance_boundary(dx: float, dy: float) -> None:
    """Le doublon doit être vu même quand il tombe dans un bucket voisin."""
    keep = shadow_char_filter()
    assert keep(_char("4", 100.0, 200.0))
    assert not keep(_char("4", 100.0 + dx, 200.0 + dy))
