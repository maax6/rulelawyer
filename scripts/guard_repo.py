#!/usr/bin/env python3
"""Garde-fou : empêche qu'un livre de règles entre dans le dépôt.

Deux modes :

    guard_repo.py --staged     # hook pre-commit : inspecte l'index
    guard_repo.py --tracked    # CI : inspecte tout ce qui est déjà versionné

Règles appliquées :

1. Aucun fichier > 1 Mio.
2. Aucun binaire de livre (.pdf, .epub, .cbz, ...) hors ``fixtures/``.
3. Une fixture n'est tolérée que si son chemin apparaît dans
   ``fixtures/SOURCES.md`` — on veut la provenance et la licence, pas juste
   le fichier.
4. Aucun artefact d'ingestion (.jsonl, chunks/, qdrant_storage/) : c'est un
   dérivé de l'œuvre, il ne se partage pas plus que le PDF.
5. Aucun fichier local de secrets ou de cles privees. Ce controle de noms ne
   remplace pas un scan du contenu et de l'historique avec Gitleaks.

Le script ne dépend que de la stdlib : il doit tourner avant toute install.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

MAX_BYTES = 1024 * 1024

BOOK_SUFFIXES = {".pdf", ".epub", ".mobi", ".cbz", ".cbr", ".djvu"}
DERIVED_SUFFIXES = {".jsonl"}
DERIVED_DIRS = ("chunks/", "qdrant_storage/", ".cache/", "data/", "books/")
SECRET_SUFFIXES = {".pem", ".key", ".p12", ".pfx"}
SECRET_NAMES = {
    ".env",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "credentials.json",
    "secrets.json",
    "secrets.yaml",
    "secrets.yml",
}
SECRET_DIRS = {".aws", ".ssh"}

FIXTURE_ROOT = "fixtures/"
SOURCES_RELPATH = "fixtures/SOURCES.md"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout


def repo_root() -> Path:
    """Racine du dépôt, plutôt que le répertoire courant.

    Git lance ses hooks depuis la racine, mais le script tourne aussi en CI et à
    la main : mieux vaut ne pas dépendre du CWD.
    """
    try:
        return Path(_git("rev-parse", "--show-toplevel").strip())
    except (subprocess.CalledProcessError, OSError):
        return Path.cwd()


def staged_paths() -> list[str]:
    out = _git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    return [p for p in out.split("\0") if p]


def tracked_paths() -> list[str]:
    out = _git("ls-files", "-z")
    return [p for p in out.split("\0") if p]


def declared_fixtures() -> set[str]:
    """Chemins de fixtures cités dans SOURCES.md."""
    sources = repo_root() / SOURCES_RELPATH
    if not sources.exists():
        return set()
    text = sources.read_text(encoding="utf-8", errors="replace")
    return {token.strip("`'\"(),") for token in text.split() if FIXTURE_ROOT in token}


def size_of(path: str, *, staged: bool) -> int:
    if staged:
        # Taille du blob dans l'index, pas du fichier sur disque : c'est ce qui
        # partirait réellement dans le commit.
        try:
            return int(_git("cat-file", "-s", f":{path}").strip())
        except subprocess.CalledProcessError:
            pass
    p = repo_root() / path
    return p.stat().st_size if p.exists() else 0


def check(paths: list[str], *, staged: bool) -> list[str]:
    allowed = declared_fixtures()
    problems: list[str] = []

    for path in paths:
        suffix = Path(path).suffix.lower()
        name = Path(path).name.lower()

        if (
            name in SECRET_NAMES
            or name.startswith(".env.")
            or suffix in SECRET_SUFFIXES
            or SECRET_DIRS.intersection(part.lower() for part in Path(path).parts[:-1])
        ):
            problems.append(
                f"{path} : fichier de secrets ou de cles privees interdit. "
                "Conserve les identifiants hors du depot."
            )
            continue

        if suffix in BOOK_SUFFIXES:
            if not path.startswith(FIXTURE_ROOT):
                problems.append(
                    f"{path} : binaire de livre hors fixtures/. "
                    "Ce depot ne distribue aucun contenu de livre."
                )
                continue
            if path not in allowed:
                problems.append(
                    f"{path} : fixture non declaree dans fixtures/SOURCES.md. "
                    "Ajoute-la avec sa licence (OGL / CC) et sa provenance."
                )
                continue

        if suffix in DERIVED_SUFFIXES or any(d in path for d in DERIVED_DIRS):
            problems.append(
                f"{path} : artefact d'ingestion. Un index est un derive de "
                "l'oeuvre, il ne se versionne pas."
            )
            continue

        size = size_of(path, staged=staged)
        if size > MAX_BYTES:
            problems.append(
                f"{path} : {size / 1024 / 1024:.1f} Mio > 1 Mio. "
                "Rien d'aussi gros n'a sa place ici."
            )

    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--staged", action="store_true")
    group.add_argument("--tracked", action="store_true")
    args = parser.parse_args()

    paths = staged_paths() if args.staged else tracked_paths()
    problems = check(paths, staged=args.staged)

    if problems:
        scope = "staged" if args.staged else "suivis"
        print(f"\n  rulelawyer -- fichiers {scope} refuses :\n", file=sys.stderr)
        for problem in problems:
            print(f"  [X] {problem}", file=sys.stderr)
        print(
            "\n  Si c'est delibere et legal, ajuste scripts/guard_repo.py.\n"
            "  N'utilise pas --no-verify pour contourner.\n",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
