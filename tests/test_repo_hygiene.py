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
    assert problems and "non declaree" in problems[0]


def test_guard_refuses_an_index_artifact() -> None:
    from scripts.guard_repo import check

    problems = check(["out/chunks.jsonl"], staged=False)
    assert problems and "artefact d'ingestion" in problems[0]


def test_guard_accepts_ordinary_source() -> None:
    from scripts.guard_repo import check

    assert check(["src/rulelawyer/probe.py", "README.md"], staged=False) == []


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        "config/.env.production",
        "config/private.pem",
        "config/private.KEY",
        "config/client.p12",
        "config/client.pfx",
        "config/id_ed25519",
        "config/.aws/credentials",
        "config/.ssh/config",
        ".netrc",
        ".npmrc",
        ".pypirc",
        "config/credentials.json",
        "config/secrets.yaml",
    ],
)
def test_guard_refuses_secret_files(path: str) -> None:
    from scripts.guard_repo import check

    problems = check([path], staged=False)
    assert problems and "secrets" in problems[0]
    problems[0].encode("ascii")


def test_guard_messages_are_pure_ascii() -> None:
    """Le hook doit pouvoir parler sur une console cp850 ou cp1252.

    Une console Windows n'est pas en UTF-8 par défaut. Un « ✗ » ou un tiret
    cadratin y lève un UnicodeEncodeError : le hook meurt sur une trace au lieu
    de son message de refus, et un garde-fou qui plante est un garde-fou en qui
    personne n'a confiance.
    """
    from scripts.guard_repo import check

    problems = check(
        [
            "Corporation.pdf",
            "fixtures/inconnue.pdf",
            "out/chunks.jsonl",
        ],
        staged=False,
    )
    assert problems
    for problem in problems:
        problem.encode("ascii")  # lève UnicodeEncodeError si un caractère sort


def test_hook_is_committed_with_lf_endings() -> None:
    """CRLF sur le hook = « bad interpreter: /bin/sh^M » chez tous les clones
    Windows, et le garde-fou ne tourne plus sans que personne ne le voie."""
    if not _is_git_repo():
        pytest.skip("hors dépôt git")
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "--eol", ".githooks/pre-commit"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "w/lf" in out, out
