"""Fixtures de test.

Le PDF de référence n'est pas dans le dépôt et ne le sera jamais. Les tests qui
en ont besoin le trouvent via ``RULELAWYER_FIXTURE_PDF`` et se sautent
proprement s'il est absent — la CI passe sans lui.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from rulelawyer.models import ProbeReport
from rulelawyer.probe import probe


@pytest.fixture(scope="session")
def fixture_pdf() -> Path:
    raw = os.environ.get("RULELAWYER_FIXTURE_PDF")
    if not raw:
        pytest.skip("RULELAWYER_FIXTURE_PDF non défini")
    path = Path(raw)
    if not path.exists():
        pytest.skip(f"PDF de référence introuvable : {path}")
    return path


@pytest.fixture(scope="session")
def reference_report(fixture_pdf: Path) -> ProbeReport:
    return probe(fixture_pdf)
