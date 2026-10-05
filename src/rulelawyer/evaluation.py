"""Évaluation page par page ; les métriques de génération restent optionnelles."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Literal, Protocol

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from rulelawyer.answer import NOT_ESTABLISHED, NOT_FOUND
from rulelawyer.ingest import ChunkPage
from rulelawyer.retrieval import SearchHit
from rulelawyer.retrieval_config import DEFAULT_THRESHOLD

Category = Literal["factual", "procedural", "cross_section", "out_of_book", "ambiguous"]
MINIMUM_CASES: dict[Category, int] = {
    "factual": 10,
    "procedural": 8,
    "cross_section": 6,
    "out_of_book": 3,
    "ambiguous": 3,
}


class EvalQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    category: Category
    question: str = Field(min_length=1)
    expected_pages: list[int]
    page_alternatives: dict[int, list[int]] = Field(default_factory=dict)
    must_contain: list[str]
    must_contain_any: list[list[str]] = Field(default_factory=list)
    must_not_hallucinate: list[str]

    @model_validator(mode="after")
    def valid_pages(self) -> EvalQuestion:
        if any(p < 1 for p in self.expected_pages) or len(
            set(self.expected_pages)
        ) != len(self.expected_pages):
            raise ValueError("Pages imprimees positives et uniques requises.")
        if (
            self.category in {"factual", "procedural", "cross_section"}
            and not self.expected_pages
        ):
            raise ValueError("Une question dans le livre exige des pages attendues.")
        if self.category == "cross_section" and len(self.expected_pages) < 2:
            raise ValueError("Une question croisee exige au moins deux pages.")
        if self.category == "out_of_book" and self.expected_pages:
            raise ValueError("Une question hors livre ne doit pas avoir de pages.")
        for page, alternatives in self.page_alternatives.items():
            if (
                page not in self.expected_pages
                or not alternatives
                or any(p < 1 for p in alternatives)
            ):
                raise ValueError(
                    "Alternatives positives pour une page attendue requises."
                )
        if any(
            not group or any(not term.strip() for term in group)
            for group in self.must_contain_any
        ):
            raise ValueError("Groupes de termes alternatifs non vides requis.")
        return self


class EvalDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    book_name: str
    file_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    questions: list[EvalQuestion]

    @model_validator(mode="after")
    def balanced_cases(self) -> EvalDataset:
        ids = [q.id for q in self.questions]
        if len(ids) != len(set(ids)):
            raise ValueError("Identifiants de questions dupliques.")
        counts = Counter(q.category for q in self.questions)
        missing = {
            key: count - counts[key]
            for key, count in MINIMUM_CASES.items()
            if counts[key] < count
        }
        if missing:
            raise ValueError(f"Jeu d'evaluation incomplet : {missing}")
        return self


def load_dataset(path: Path) -> EvalDataset:
    try:
        return EvalDataset.model_validate(yaml.safe_load(path.read_text("utf-8")))
    except (ValueError, yaml.YAMLError) as exc:
        raise ValueError(f"Jeu d'evaluation invalide : {exc}") from exc


class SearchIndex(Protocol):
    def search(
        self, question: str, *, threshold: float = DEFAULT_THRESHOLD, top_k: int = 6
    ) -> list[SearchHit]: ...


class CaseResult(BaseModel):
    id: str
    category: Category
    expected_pages: list[int]
    page_alternatives: dict[int, list[int]] = Field(default_factory=dict)
    retrieved_pages: list[int]
    recall_at_6: float | None = None
    retrieval_refused: bool
    answer_checks_passed: bool | None = None
    citation_present: bool | None = None
    citation_exact: bool | None = None
    correct_refusal: bool | None = None
    correct_clarification: bool | None = None
    error: str | None = None


class EvaluationReport(BaseModel):
    mode: Literal["retrieval", "generation"]
    threshold: float
    cases: list[CaseResult]
    recall_at_6: float | None
    recall_by_category: dict[str, float]
    retrieval_refusal_rate: float | None
    answer_pass_rate: float | None
    citation_presence_rate: float | None
    citation_exact_rate: float | None
    correct_refusal_rate: float | None
    clarification_rate: float | None
    retrieval_target_met: bool
    generation_errors: int
    generation_target_met: bool | None = None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _normalized(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _contains(text: str, term: str) -> bool:
    return (
        re.search(r"(?<!\w)" + re.escape(_normalized(term)) + r"(?!\w)", text)
        is not None
    )


def evaluate(
    questions: list[EvalQuestion],
    index: SearchIndex,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    generate: Callable[[str, ChunkPage | None], str] | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> EvaluationReport:
    results: list[CaseResult] = []
    for number, case in enumerate(questions, 1):
        hits = index.search(case.question, threshold=threshold, top_k=6)[:6]
        pages = list(
            dict.fromkeys(
                hit.page.book_page for hit in hits if hit.page.book_page is not None
            )
        )
        answerable = case.category in {"factual", "procedural", "cross_section"}
        page_groups = [
            {page, *case.page_alternatives.get(page, [])}
            for page in case.expected_pages
        ]
        recall = (
            sum(bool(set(pages) & group) for group in page_groups) / len(page_groups)
            if answerable
            else None
        )
        result = CaseResult(
            id=case.id,
            category=case.category,
            expected_pages=case.expected_pages,
            page_alternatives=case.page_alternatives,
            retrieved_pages=pages,
            recall_at_6=recall,
            retrieval_refused=not hits,
        )
        if generate is not None:
            try:
                answer = generate(case.question, hits[0].page if hits else None)
            except (ValueError, OSError, RuntimeError):
                # Pas de message fournisseur, de passage ni de secret dans le rapport.
                result.error = "Generation failed"
                answer = ""
            normalized = _normalized(answer)
            checks = (
                all(_contains(normalized, term) for term in case.must_contain)
                and all(
                    any(_contains(normalized, term) for term in group)
                    for group in case.must_contain_any
                )
                and all(
                    not _contains(normalized, term)
                    for term in case.must_not_hallucinate
                )
            )
            cited = {int(p) for p in re.findall(r"\(p\.\s*(\d+)\)", answer)}
            evidence = {hits[0].page.book_page} if hits else set()
            if answerable:
                result.citation_present = bool(cited)
                result.citation_exact = (
                    bool(cited)
                    and cited <= set().union(*page_groups)
                    and cited <= evidence
                )
                result.answer_checks_passed = (
                    checks
                    and bool(result.citation_exact)
                    and all(bool(cited & group) for group in page_groups)
                    and result.error is None
                )
            elif case.category == "out_of_book":
                result.correct_refusal = (
                    answer in {NOT_FOUND, NOT_ESTABLISHED} and not cited
                )
            else:
                result.correct_clarification = (
                    bool(answer) and "?" in answer and checks and not cited
                )
        results.append(result)
        if progress is not None:
            progress(number, len(questions), case.id)
    recall_values = [r.recall_at_6 for r in results if r.recall_at_6 is not None]
    recall_total = _mean(recall_values)
    by_category = {}
    for category in ("factual", "procedural", "cross_section"):
        value = _mean(
            [
                r.recall_at_6
                for r in results
                if r.category == category and r.recall_at_6 is not None
            ]
        )
        if value is not None:
            by_category[category] = value
    return EvaluationReport(
        mode="generation" if generate is not None else "retrieval",
        threshold=threshold,
        cases=results,
        recall_at_6=recall_total,
        recall_by_category=by_category,
        retrieval_refusal_rate=_mean(
            [float(r.retrieval_refused) for r in results if r.category == "out_of_book"]
        ),
        answer_pass_rate=_mean(
            [
                float(r.answer_checks_passed)
                for r in results
                if r.answer_checks_passed is not None
            ]
        ),
        citation_presence_rate=_mean(
            [
                float(r.citation_present)
                for r in results
                if r.citation_present is not None
            ]
        ),
        citation_exact_rate=_mean(
            [float(r.citation_exact) for r in results if r.citation_exact is not None]
        ),
        correct_refusal_rate=_mean(
            [float(r.correct_refusal) for r in results if r.correct_refusal is not None]
        ),
        clarification_rate=_mean(
            [
                float(r.correct_clarification)
                for r in results
                if r.correct_clarification is not None
            ]
        ),
        retrieval_target_met=recall_total is not None and recall_total >= 0.85,
        generation_errors=sum(r.error is not None for r in results),
        generation_target_met=(
            all(
                r.answer_checks_passed is True
                or r.correct_refusal is True
                or r.correct_clarification is True
                for r in results
            )
            if generate is not None
            else None
        ),
    )
