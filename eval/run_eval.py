"""Entrée CLI : python eval/run_eval.py --pdf /chemin/manuel.pdf."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path

from rulelawyer.agent import Provider, answer_with_anthropic
from rulelawyer.answer import answer_from_passage
from rulelawyer.chunking import DEFAULT_MAX_TOKENS
from rulelawyer.evaluation import evaluate, load_dataset
from rulelawyer.ingest import ingest_route_a, write_chunks
from rulelawyer.probe import probe
from rulelawyer.retrieval import BGEModels, open_index
from rulelawyer.retrieval_config import DEFAULT_THRESHOLD


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument(
        "--questions", type=Path, default=Path(__file__).with_name("questions.yaml")
    )
    parser.add_argument(
        "--provider", choices=list(Provider), default=Provider.OPENROUTER
    )
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--profiles-dir", type=Path, default=Path("profiles"))
    parser.add_argument("--cache-dir", type=Path, default=Path(".cache/evaluation"))
    parser.add_argument("--output", type=Path, default=Path("reports/evaluation.json"))
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument(
        "--generate",
        action="store_true",
        help="Appelle le fournisseur choisi avec les preuves ; nécessite sa clé.",
    )
    args = parser.parse_args()
    try:
        dataset = load_dataset(args.questions)
        if not 0 <= args.threshold <= 1:
            raise ValueError("Le seuil doit etre entre 0 et 1.")
        provider = Provider(args.provider)
        key_name = (
            "ANTHROPIC_API_KEY"
            if provider is Provider.ANTHROPIC
            else "OPENROUTER_API_KEY"
        )
        if args.generate and not os.environ.get(key_name, "").strip():
            raise ValueError(f"{key_name} absente : aucun appel ni indexation.")
        with args.pdf.open("rb") as handle:
            file_hash = hashlib.file_digest(handle, "sha256").hexdigest()
        if file_hash != dataset.file_sha256:
            raise ValueError("Le PDF ne correspond pas au livre du jeu d'evaluation.")
        report = probe(
            args.pdf, profiles_dir=args.profiles_dir, profile_path=args.profile
        )
        chunks = ingest_route_a(args.pdf, report, max_tokens=args.max_tokens)
        write_chunks(chunks, args.cache_dir / "chunks.jsonl")
        print(f"{len(chunks)} chunks ; chargement des modèles locaux…", flush=True)
        with open_index(
            chunks, args.cache_dir / "qdrant_storage", BGEModels()
        ) as index:
            result = evaluate(
                dataset.questions,
                index,
                threshold=args.threshold,
                generate=(
                    answer_from_passage
                    if args.generate and provider is Provider.OPENROUTER
                    else None
                ),
                generate_from_hits=(
                    answer_with_anthropic
                    if args.generate and provider is Provider.ANTHROPIC
                    else None
                ),
                progress=lambda n, total, key: print(f"{n}/{total} {key}", flush=True),
            )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        print(
            f"Recall@6={result.recall_at_6:.3f} ; cible >= 0.85 ; "
            f"rapport : {args.output}"
        )
        return (
            0
            if result.retrieval_target_met and result.generation_target_met is not False
            else 1
        )
    except (ValueError, OSError, RuntimeError, ImportError) as exc:
        print(f"Evaluation impossible : {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
