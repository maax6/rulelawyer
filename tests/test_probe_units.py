"""Tests unitaires du probe — aucun PDF requis.

Ces tests portent sur les mécanismes qui échouent *silencieusement* : une
détection de boilerplate qui rend zéro motif et un mapping de pagination faux
d'une unité se présentent tous les deux comme des résultats légitimes.
"""

from __future__ import annotations

import pytest

from rulelawyer.models import PageMapMethod, PageNumberSource
from rulelawyer.probe import (
    Line,
    PageContent,
    analyze_columns,
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


def folio_page(index: int, printed: int, header: str = "Corporation") -> PageContent:
    """Une page dont le folio est une ligne à part entière — le cas nominal."""
    page = make_page(index, header)
    page.lines.append(
        Line(text=str(printed), x0=60, x1=75, top=FOOTER_TOP, bottom=FOOTER_TOP + 10)
    )
    return page


def test_page_map_measures_uniform_offset() -> None:
    """book_page = pdf_index0 + 1, donc page_offset = -1."""
    pages = [folio_page(i, i + 1) for i in range(40)]
    report = detect_page_map(pages)

    assert report.method is PageMapMethod.PRINTED
    assert report.source is PageNumberSource.STANDALONE
    assert report.uniform_offset == -1
    assert report.confidence == 1.0
    assert report.book_page(0) == 1
    assert report.book_page(39) == 40


def test_page_map_handles_a_piecewise_offset() -> None:
    """Un encart décale la pagination en cours de route.

    Un offset scalaire unique produirait des citations fausses sur toute la
    seconde moitié du livre, sans rien signaler.
    """
    pages = [folio_page(i, i + 1) for i in range(20)]
    pages += [folio_page(i, i - 3) for i in range(20, 40)]
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


def test_page_map_prefers_the_folio_over_the_imposition_mark() -> None:
    """Le piège que le livre de référence ne peut pas révéler.

    Ici la marque d'imposition compte les feuilles du fichier de maquette à
    partir de la couverture (décalage 0) tandis que le folio imprimé démarre
    plus loin (décalage -4). Les deux séries sont parfaitement cohérentes et
    massivement soutenues : en votant sur le volume on prendrait l'imposition,
    à pleine confiance, et chaque citation serait fausse de quatre pages.
    """
    pages = []
    for i in range(40):
        page = make_page(i, f"Corp 2008 Mongoose:Layout 1 09:53 Page {i}")
        page.lines.append(
            Line(text=str(i + 4), x0=60, x1=75, top=FOOTER_TOP, bottom=FOOTER_TOP + 10)
        )
        pages.append(page)

    report = detect_page_map(pages)

    assert report.source is PageNumberSource.STANDALONE
    assert report.uniform_offset == -4
    assert report.book_page(10) == 14
    assert any("suggère un décalage" in note for note in report.notes)


def test_page_map_falls_back_to_embedded_numbers_and_says_so() -> None:
    """Sans folio isolé, on se rabat sur le nombre noyé — sans prétendre à 100 %."""
    pages = [make_page(i, f"Corp Layout 09:53 Page {i + 1}") for i in range(40)]
    report = detect_page_map(pages)

    assert report.source is PageNumberSource.EMBEDDED
    assert report.uniform_offset == -1
    assert report.confidence < 0.6
    assert any("marque d'imposition" in note for note in report.notes)


def test_page_map_interpolates_pages_without_a_folio() -> None:
    """Une illustration pleine page ne change aucun décalage, elle le masque."""
    pages = [
        make_page(i, "Corporation") if i in (17, 18, 19) else folio_page(i, i + 1)
        for i in range(40)
    ]
    report = detect_page_map(pages)

    assert report.uniform_offset == -1
    assert report.measured_pages == 37, "seules les pages réellement lues comptent"
    assert report.book_page(18) == 19
    assert any("interpolées" in note for note in report.notes)


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


# --- Colonnes ----------------------------------------------------------------


def columned_page(index: int, column_x: list[float], rows: int = 12) -> PageContent:
    lines = [
        Line(
            text="du texte de règle qui occupe la colonne",
            x0=x,
            x1=x + 200,
            top=150.0 + 14 * r,
            bottom=162.0 + 14 * r,
        )
        for r in range(rows)
        for x in column_x
    ]
    return PageContent(
        index=index, width=PAGE_W, height=PAGE_H, lines=lines, char_count=2000
    )


def test_analyze_columns_finds_two_columns() -> None:
    pages = [columned_page(i, [60.0, 320.0]) for i in range(60)]
    report = analyze_columns(pages, boilerplate=set())
    assert report.dominant == 2
    assert report.stable


def test_analyze_columns_finds_a_single_column() -> None:
    pages = [columned_page(i, [60.0]) for i in range(60)]
    assert analyze_columns(pages, boilerplate=set()).dominant == 1


def test_analyze_columns_flags_a_layout_that_varies() -> None:
    """« parfois 3 colonnes » doit ressortir dans la variance, pas être lissé."""
    pages = [
        columned_page(i, [60.0, 320.0] if i < 60 else [60.0, 240.0, 420.0])
        for i in range(120)
    ]
    report = analyze_columns(pages, boilerplate=set())
    assert not report.stable
    assert set(report.distribution) == {2, 3}


def test_analyze_columns_ignores_boilerplate() -> None:
    """Un folio pleine largeur ne doit pas compter comme un début de colonne."""
    pages = []
    for i in range(60):
        page = columned_page(i, [60.0, 320.0])
        page.lines.append(
            Line(text="Corp Layout Page 1", x0=200, x1=400, top=300.0, bottom=312.0)
        )
        pages.append(page)
    report = analyze_columns(pages, boilerplate={normalize_line("Corp Layout Page 1")})
    assert report.dominant == 2


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
