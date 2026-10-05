"""Contrat de provenance d'un passage partagé par ingestion et chunking."""

from pydantic import BaseModel, Field


class ChunkPage(BaseModel):
    pdf_page: int = Field(ge=1, description="Numéro PDF, 1-based ; jamais une citation")
    book_page: int | None
    text: str
