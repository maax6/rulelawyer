"""Schémas de sortie du probe.

Le `ProbeReport` est le seul objet qui a le droit de décider d'une route
d'ingestion. Tout le reste du pipeline le consomme, personne ne re-diagnostique.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class Route(StrEnum):
    A = "A"  # natif + outline exploitable — CPU, rapide
    B = "B"  # natif, outline absent/inutilisable — CPU, reconstruction
    C = "C"  # scan ou couche texte pourrie — VLM, GPU
    D = "D"  # retrieval visuel — expérimental


class TextVerdict(StrEnum):
    NATIVE = "native"  # couche texte propre, produite à la composition
    SUSPECT = "suspect"  # couche texte présente mais douteuse (OCR moyen)
    BAD = "bad"  # couche texte présente et inexploitable — pire que rien
    ABSENT = "absent"  # aucun caractère : scan pur


class PageError(BaseModel):
    """Une page qui a échoué. Jamais avalée silencieusement (brief §11)."""

    pdf_index: int
    stage: str
    error: str


class TextQuality(BaseModel):
    """Vraisemblance de la couche texte, en signaux language-agnostiques.

    Le taux de mots en dictionnaire n'est qu'un signal parmi d'autres et reste
    optionnel : on interroge des livres anglais en français, et un dictionnaire
    mono-langue classerait un livre étranger comme du mauvais OCR.
    """

    replacement_char_rate: float = Field(description="Taux de U+FFFD")
    nonprintable_rate: float
    letter_ratio: float = Field(description="Part de caractères alphabétiques")
    mean_token_length: float
    short_token_rate: float = Field(
        description="Part de tokens de 1-2 caractères, hors mots courts usuels. "
        "Un OCR qui casse les mots fait exploser ce ratio."
    )
    dictionary_hit_rate: float | None = None
    dictionary_language: str | None = None


class TextLayerReport(BaseModel):
    verdict: TextVerdict
    reasons: list[str] = Field(default_factory=list)
    font_names: list[str] = Field(default_factory=list)
    font_count: int = 0
    embedded_font_count: int = 0
    pages_with_text: int = 0
    median_chars_per_page: float = 0.0
    quality: TextQuality | None = None


class OutlineEntry(BaseModel):
    title: str
    level: int
    pdf_index: int | None = None


class OutlineReport(BaseModel):
    entry_count: int = 0
    max_depth: int = 0
    resolved_count: int = Field(
        default=0, description="Entrées dont la destination a pu être résolue"
    )
    coverage_ratio: float = Field(
        default=0.0, description="Part des pages couvertes par une section"
    )
    largest_gap_pages: int = 0
    usable: bool = False
    reasons: list[str] = Field(default_factory=list)
    entries: list[OutlineEntry] = Field(default_factory=list)


class ColumnsReport(BaseModel):
    dominant: int = 1
    distribution: dict[int, int] = Field(
        default_factory=dict,
        description="nb de colonnes -> nb de pages échantillonnées",
    )
    variance: float = 0.0
    stable: bool = True
    sampled_pages: list[int] = Field(default_factory=list)


class BoilerplatePattern(BaseModel):
    masked: str = Field(description="Ligne normalisée, chiffres masqués par #")
    page_ratio: float
    page_count: int
    example: str
    zone: str = Field(description="header | footer | body")


class BoilerplateReport(BaseModel):
    patterns: list[BoilerplatePattern] = Field(default_factory=list)
    max_page_ratio: float = 0.0


class PageMapMethod(StrEnum):
    PRINTED = "printed"  # numéro imprimé lu dans l'en-tête/pied — mesuré
    TOC = "toc"  # déduit de la table des matières textuelle
    IDENTITY = "identity"  # aucun signal : book_page = pdf_page 1-based
    PROFILE = "profile"  # imposé par un profil


class PageNumberSource(StrEnum):
    """D'où vient le nombre qu'on a pris pour un folio.

    La distinction est tout sauf cosmétique. `STANDALONE` est le folio lui-même :
    une ligne dont le texte entier est un nombre. `EMBEDDED` est un nombre noyé
    dans une ligne plus longue — typiquement la marque d'imposition
    « …Layout 1 02/04/2009 Page 38 », qui est un artefact de fabrication et ne
    coïncide avec le folio que par chance.
    """

    STANDALONE = "standalone"
    EMBEDDED = "embedded"
    NONE = "none"


class OffsetRun(BaseModel):
    """Plage de pages où le décalage est constant.

    Convention, la même que dans les profils :

        pdf_index0 = book_page + page_offset
    """

    pdf_start: int
    pdf_end: int
    page_offset: int


class PageMapReport(BaseModel):
    """Le mapping n'est PAS un scalaire.

    Les couvertures et encarts créent des décalages par plages. Le scalaire du
    schéma de profil est une surcharge utilisateur pour le cas dégénéré, pas la
    représentation interne.
    """

    method: PageMapMethod = PageMapMethod.IDENTITY
    source: PageNumberSource = PageNumberSource.NONE
    confidence: float = 0.0
    monotonic: bool = False
    runs: list[OffsetRun] = Field(default_factory=list)
    measured_pages: int = 0
    uniform_offset: int | None = Field(
        default=None, description="Renseigné seulement si un unique run couvre tout"
    )
    notes: list[str] = Field(default_factory=list)

    def book_page(self, pdf_index: int) -> int | None:
        """Page imprimée, ou `None` si elle n'a pas pu être mesurée.

        On ne devine pas : une page hors de toute plage mesurée doit être
        signalée en aval, pas citée avec un numéro plausible. Seul le mode
        `IDENTITY` — où l'absence de tout folio est le constat, pas un échec —
        retourne le repli `pdf_page` 1-based.
        """
        for run in self.runs:
            if run.pdf_start <= pdf_index <= run.pdf_end:
                return pdf_index - run.page_offset
        if self.method is PageMapMethod.IDENTITY:
            return pdf_index + 1
        return None


class ImagesReport(BaseModel):
    total: int = 0
    large_count: int = Field(
        default=0, description="Images >= 100x100 px, seuil de filtrage du chunking"
    )
    large_per_page: float = 0.0
    median_area_px: float = 0.0
    illustration_heavy: bool = False


class TocReport(BaseModel):
    """Table des matières *textuelle* — le filet de sécurité de la Route B."""

    pages: list[int] = Field(default_factory=list)
    entry_count: int = 0
    sample: list[str] = Field(default_factory=list)


class ProbeReport(BaseModel):
    pdf_path: str
    file_sha256: str
    page_count: int
    producer: str | None = None
    creator: str | None = None
    first_page_text_sha256: str | None = Field(
        default=None,
        description="Empreinte de la première page *porteuse de texte*. La page "
        "d'index 0 est presque toujours une couverture graphique : la hacher "
        "revient à hacher la chaîne vide, et la clé `match` du profil colle "
        "alors à n'importe quel livre.",
    )
    first_page_text_pdf_index: int | None = None

    text_layer: TextLayerReport
    outline: OutlineReport
    columns: ColumnsReport
    boilerplate: BoilerplateReport
    page_map: PageMapReport
    images: ImagesReport
    toc: TocReport

    recommended_route: Route
    route_rationale: list[str] = Field(default_factory=list)
    estimated_cost: str = ""
    matched_profile: str | None = None
    errors: list[PageError] = Field(default_factory=list)
