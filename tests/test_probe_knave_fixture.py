"""Probe de la fixture Knave, versionnée dans le dépôt.

Le PDF est redistribuable (CC BY 4.0, voir fixtures/SOURCES.md). Le test ne
se saute pas : le fichier est dans le repo.
"""

from __future__ import annotations

from pathlib import Path

from rulelawyer.models import PageMapMethod, Route, TextVerdict
from rulelawyer.probe import probe

KNAVE = Path(__file__).resolve().parents[1] / "fixtures" / "knave.pdf"


def test_knave_fixture_is_native_route_b_without_outline() -> None:
    report = probe(KNAVE)
    assert report.page_count == 7
    assert report.text_layer.verdict is TextVerdict.NATIVE
    assert report.outline.usable is False
    assert report.recommended_route is Route.B
    assert report.page_map.method is PageMapMethod.IDENTITY
    assert report.errors == []
