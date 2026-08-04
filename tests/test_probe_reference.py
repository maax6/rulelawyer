"""Test d'acceptation sur le PDF de référence.

Le brief donne la vérité terrain de ce livre. On l'assertionne plutôt que de la
lire à l'œil dans le rapport CLI : une détection qui se dégrade se présente
toujours comme un résultat plausible.

Le PDF n'est pas versionné — voir tests/conftest.py.
"""

from __future__ import annotations

import pytest

from rulelawyer.models import PageMapMethod, ProbeReport, Route, TextVerdict

pytestmark = pytest.mark.fixture_pdf


def test_page_count(reference_report: ProbeReport) -> None:
    assert reference_report.page_count == 257


def test_text_layer_is_native(reference_report: ProbeReport) -> None:
    assert reference_report.text_layer.verdict is TextVerdict.NATIVE
    assert reference_report.text_layer.embedded_font_count > 0
    assert reference_report.text_layer.pages_with_text == 256


def test_outline_is_usable(reference_report: ProbeReport) -> None:
    outline = reference_report.outline
    assert outline.entry_count == 160
    assert outline.resolved_count == 160
    assert outline.max_depth == 2
    assert outline.usable


def test_boilerplate_is_detected_on_almost_every_page(
    reference_report: ProbeReport,
) -> None:
    """256/257 pages. Un résultat proche de zéro voudrait dire que le masquage
    des chiffres a sauté — et ça ressemblerait à un livre sans boilerplate."""
    boilerplate = reference_report.boilerplate
    assert boilerplate.patterns
    assert boilerplate.patterns[0].page_count == 256


def test_images_are_all_seen(reference_report: ProbeReport) -> None:
    assert reference_report.images.total == 369


def test_page_map_is_measured_not_guessed(reference_report: ProbeReport) -> None:
    """Le folio imprimé vaut l'index PDF 0-based sur ce livre.

    Mesuré sur les glyphes du folio (p. ex. l'index 141 porte « 141 »), pas
    déduit. Le brief annonçait `index PDF = page livre − 1` ; le PDF dit le
    contraire, et une erreur d'une unité rendrait fausse chaque citation.
    """
    page_map = reference_report.page_map
    assert page_map.method is PageMapMethod.PRINTED
    assert page_map.uniform_offset == 0
    assert page_map.monotonic
    assert page_map.measured_pages == 256
    assert page_map.book_page(141) == 141


def test_two_columns_dominant(reference_report: ProbeReport) -> None:
    assert reference_report.columns.dominant == 2


def test_route_a_is_recommended(reference_report: ProbeReport) -> None:
    assert reference_report.recommended_route is Route.A


def test_no_extraction_error(reference_report: ProbeReport) -> None:
    assert reference_report.errors == []
