"""Calibrer l'admission sur un corpus original distinct du manuel évalué."""

from __future__ import annotations

import argparse
import hashlib
import math
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from rulelawyer.retrieval import BGEModels, RetrievalModels


class Passage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: int = Field(ge=1)
    section: str
    text: str


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str
    pages: list[int]


class Corpus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    passages: list[Passage]
    cases: list[Case]


class CalibrationReport(BaseModel):
    corpus_sha256: str
    positive_queries: int
    negative_queries: int
    minimum_positive_score: float
    recommended_threshold: float
    positive_queries_admitted: int
    negative_queries_refused: int
    balanced_accuracy: float
    rule: str = (
        "Largest power of ten maximizing balanced accuracy on the separate corpus"
    )


def calibrate(
    corpus: Corpus, backend: RetrievalModels, digest: str
) -> CalibrationReport:
    pages = [p.page for p in corpus.passages]
    if len(set(pages)) != len(pages) or not pages:
        raise ValueError("Pages du corpus absentes ou dupliquées.")
    positives: list[float] = []
    negatives: list[float] = []
    texts = [p.section + "\n\n" + p.text for p in corpus.passages]
    for case in corpus.cases:
        if not set(case.pages) <= set(pages):
            raise ValueError("Preuve de calibration absente du corpus.")
        scores = backend.rerank(case.question, texts)
        if len(scores) != len(pages) or any(
            not math.isfinite(s) or not 0 <= s <= 1 for s in scores
        ):
            raise ValueError("Scores de calibration invalides.")
        if case.pages:
            # Une preuve suffit à admettre la requête ; les autres restent
            # candidates, même si leur score est inférieur au seuil.
            positives.append(
                max(s for p, s in zip(pages, scores, strict=True) if p in case.pages)
            )
        else:
            negatives.append(max(scores))
    if not positives or not negatives or min(positives) <= 0:
        raise ValueError("Calibration impossible : classes ou scores manquants.")
    minimum = min(positives)

    def accuracy(threshold: float) -> float:
        return (
            sum(s >= threshold for s in positives) / len(positives)
            + sum(s < threshold for s in negatives) / len(negatives)
        ) / 2

    # Grille fixée indépendamment du manuel ; à égalité, favoriser le refus.
    threshold = max((10.0**-n for n in range(7)), key=lambda t: (accuracy(t), t))
    return CalibrationReport(
        corpus_sha256=digest,
        positive_queries=len(positives),
        negative_queries=len(negatives),
        minimum_positive_score=minimum,
        recommended_threshold=threshold,
        positive_queries_admitted=sum(s >= threshold for s in positives),
        negative_queries_refused=sum(s < threshold for s in negatives),
        balanced_accuracy=accuracy(threshold),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus", type=Path, default=Path("fixtures/retrieval-calibration.yaml")
    )
    parser.add_argument("--output", type=Path, default=Path("reports/calibration.json"))
    args = parser.parse_args()
    raw = args.corpus.read_bytes()
    corpus = Corpus.model_validate(yaml.safe_load(raw))
    report = calibrate(corpus, BGEModels(), hashlib.sha256(raw).hexdigest())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    print(report.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
