"""Le dépôt ne doit jamais contenir de contenu de livre.

Le hook pre-commit est contournable (`--no-verify`, push depuis un notebook,
merge d'une PR). Ce test est le filet qui tourne en CI sur ce qui est réellement
versionné.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
GUARD = REPO / "scripts" / "guard_repo.py"


def _is_git_repo() -> bool:
    result = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def test_no_forbidden_file_is_tracked() -> None:
    if not _is_git_repo():
        pytest.skip("hors dépôt git")
    result = subprocess.run(
        [sys.executable, str(GUARD), "--tracked"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_guard_refuses_a_rulebook_pdf() -> None:
    from scripts.guard_repo import check

    problems = check(["Corporation-RPG-Core-Rulebook.pdf"], staged=False)
    assert problems and "hors fixtures" in problems[0]


def test_guard_refuses_an_undeclared_fixture() -> None:
    from scripts.guard_repo import check

    problems = check(["fixtures/pas-declaree.pdf"], staged=False)
    assert problems and "non déclarée" in problems[0]


def test_guard_refuses_an_index_artifact() -> None:
    from scripts.guard_repo import check

    problems = check(["out/chunks.jsonl"], staged=False)
    assert problems and "artefact d'ingestion" in problems[0]


def test_guard_accepts_ordinary_source() -> None:
    from scripts.guard_repo import check

    assert check(["src/rulelawyer/probe.py", "README.md"], staged=False) == []
