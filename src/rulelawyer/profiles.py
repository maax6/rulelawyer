"""Profils locaux : identification du livre et surcharges explicites."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from rulelawyer.models import (
    HeadingAnchor,
    OffsetRun,
    PageMapMethod,
    PageMapReport,
    ProbeReport,
    Route,
)


class ProfileMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_count: int | None = Field(default=None, gt=0)
    producer_contains: str | None = Field(default=None, min_length=1)
    first_page_text_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    file_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def identifying_match(self) -> ProfileMatch:
        if not (
            self.first_page_text_sha256
            or self.file_sha256
            or (self.page_count and self.producer_contains)
        ):
            raise ValueError("Le matching exige une empreinte ou pages + producteur.")
        return self

    def matches(self, report: ProbeReport) -> bool:
        return (
            (self.page_count is None or self.page_count == report.page_count)
            and (
                self.producer_contains is None
                or self.producer_contains in (report.producer or "")
            )
            and (
                self.first_page_text_sha256 is None
                or self.first_page_text_sha256 == report.first_page_text_sha256
            )
            and (self.file_sha256 is None or self.file_sha256 == report.file_sha256)
        )


class BookProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    match: ProfileMatch
    route: Route | None = None
    reading_order: Literal["text_flow", "position"] | None = None
    page_offset: int | None = None
    boilerplate_patterns: list[str] = Field(default_factory=list)
    drop_sections: list[str] = Field(default_factory=list)
    table_pages: list[int] = Field(default_factory=list)
    heading_overrides: dict[str, HeadingAnchor] = Field(default_factory=dict)
    notes: str = ""

    @field_validator("boilerplate_patterns")
    @classmethod
    def valid_patterns(cls, patterns: list[str]) -> list[str]:
        for pattern in patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"Motif de boilerplate invalide : {exc}") from exc
        return patterns

    @field_validator("table_pages")
    @classmethod
    def positive_pages(cls, pages: list[int]) -> list[int]:
        if any(p < 1 for p in pages):
            raise ValueError("table_pages contient des pages imprimees >= 1.")
        return pages


def load_profile(path: Path) -> BookProfile:
    try:
        return BookProfile.model_validate(yaml.safe_load(path.read_text("utf-8")))
    except (ValueError, yaml.YAMLError) as exc:
        raise ValueError(f"Profil invalide {path.name} : {exc}") from exc


def apply_profile(
    report: ProbeReport,
    *,
    directory: Path | None = Path("profiles"),
    explicit: Path | None = None,
) -> ProbeReport:
    if explicit is not None:
        candidates = [(explicit, load_profile(explicit))]
        if not candidates[0][1].match.matches(report):
            raise ValueError("Le profil explicite ne correspond pas a ce PDF.")
    else:
        paths = (
            sorted({*directory.glob("*.yaml"), *directory.glob("*.yml")})
            if directory is not None
            else []
        )
        candidates = [(path, load_profile(path)) for path in paths]
        candidates = [
            (p, profile) for p, profile in candidates if profile.match.matches(report)
        ]
    if len(candidates) > 1:
        names = ", ".join(p.name for p, _ in candidates)
        raise ValueError(
            f"Plusieurs profils correspondent ({names}) : utilisez --profile."
        )
    if not candidates:
        return report
    path, profile = candidates[0]
    result = report.model_copy(deep=True)
    result.matched_profile = profile.name
    result.profile_id = path.stem
    result.profile_drop_sections = profile.drop_sections
    result.profile_boilerplate_patterns = profile.boilerplate_patterns
    result.profile_table_pages = profile.table_pages
    result.profile_heading_overrides = profile.heading_overrides
    if profile.reading_order is not None:
        result.reading_order = profile.reading_order
    result.route_rationale.append(f"Profil applique : {profile.name} ({path.name}).")
    result.route_rationale.append(f"Ordre d'extraction : {result.reading_order}.")
    if profile.route is not None:
        result.recommended_route = profile.route
        result.estimated_cost = "Route imposee par le profil ; cout a verifier."
    if profile.page_offset is not None:
        start = max(0, profile.page_offset + 1)
        if start >= result.page_count:
            raise ValueError("Le page_offset du profil ne donne aucune page positive.")
        result.page_map = PageMapReport(
            method=PageMapMethod.PROFILE,
            runs=[
                OffsetRun(
                    pdf_start=start,
                    pdf_end=result.page_count - 1,
                    page_offset=profile.page_offset,
                )
            ],
            uniform_offset=profile.page_offset,
            confidence=1.0,
            monotonic=True,
            notes=["Pagination imposee par le profil, non mesuree automatiquement."],
        )
    return result


def profile_template(report: ProbeReport, name: str) -> str:
    match = ProfileMatch(
        page_count=report.page_count,
        producer_contains=report.producer or None,
        first_page_text_sha256=report.first_page_text_sha256,
        file_sha256=report.file_sha256
        if report.first_page_text_sha256 is None
        else None,
    )
    profile = BookProfile(
        name=name,
        match=match,
        route=report.recommended_route,
        # Ne pas figer une mesure partielle ou une marque d'imposition.
        notes="Verifiez les pages imprimees avant de renseigner page_offset.",
    )
    return yaml.safe_dump(
        profile.model_dump(mode="json", exclude_none=True),
        allow_unicode=True,
        sort_keys=False,
    )
