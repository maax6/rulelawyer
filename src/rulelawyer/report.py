"""Rendu CLI du `ProbeReport`.

Objectif affiché dans le brief : l'utilisateur doit comprendre *pourquoi* son
livre va prendre deux minutes sur CPU ou quarante sur GPU. Le rapport explique
la décision, il ne se contente pas de l'annoncer.
"""

from __future__ import annotations

from rich.console import Console, ConsoleRenderable, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from rulelawyer.models import PageNumberSource, ProbeReport, Route, TextVerdict

_VERDICT_STYLE = {
    TextVerdict.NATIVE: "green",
    TextVerdict.SUSPECT: "yellow",
    TextVerdict.BAD: "red",
    TextVerdict.ABSENT: "red",
}

_SOURCE_LABEL = {
    PageNumberSource.STANDALONE: "folio isolé — mesuré directement",
    PageNumberSource.EMBEDDED: "nombre noyé en en-tête/pied — indirect",
    PageNumberSource.NONE: "aucune",
}

_ROUTE_LABEL = {
    Route.A: "A — natif + outline",
    Route.B: "B — natif, hiérarchie à reconstruire",
    Route.C: "C — VLM (scan ou couche texte pourrie)",
    Route.D: "D — retrieval visuel (expérimental)",
}


def _kv_table() -> Table:
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="dim", no_wrap=True)
    table.add_column()
    return table


def render(report: ProbeReport, console: Console) -> None:
    console.print()
    console.print(
        Panel(
            Text.assemble(
                (report.pdf_path, "bold"),
                ("\n", ""),
                (f"{report.page_count} pages", "dim"),
                (" · ", "dim"),
                (f"sha256 {report.file_sha256[:12]}…", "dim"),
                (f"\nproducer: {report.producer or '—'}", "dim"),
            ),
            title="rulelawyer probe",
            border_style="cyan",
        )
    )

    # --- Couche texte
    tl = report.text_layer
    table = _kv_table()
    table.add_row("verdict", Text(tl.verdict.value, style=_VERDICT_STYLE[tl.verdict]))
    table.add_row("pages avec texte", f"{tl.pages_with_text}/{report.page_count}")
    table.add_row("caractères/page (médiane)", f"{tl.median_chars_per_page:.0f}")
    table.add_row(
        "polices", f"{tl.font_count} distinctes, {tl.embedded_font_count} embarquées"
    )
    if tl.quality is not None:
        q = tl.quality
        table.add_row("longueur moyenne de mot", f"{q.mean_token_length:.1f}")
        table.add_row("tokens 1-2 lettres", f"{q.short_token_rate:.1%}")
        table.add_row("caractères de remplacement", f"{q.replacement_char_rate:.2%}")
        if q.dictionary_hit_rate is not None:
            table.add_row(
                f"mots en dictionnaire ({q.dictionary_language})",
                f"{q.dictionary_hit_rate:.0%}  [signal d'appoint]",
            )
    body: list[ConsoleRenderable] = [table]
    if tl.reasons:
        body.append(Text("\n".join(f"· {r}" for r in tl.reasons), style="dim"))
    console.print(Panel(Group(*body), title="1 · Couche texte", border_style="blue"))

    # --- Outline
    ol = report.outline
    table = _kv_table()
    table.add_row(
        "exploitable",
        Text("oui" if ol.usable else "non", style="green" if ol.usable else "yellow"),
    )
    table.add_row("entrées", f"{ol.entry_count} ({ol.resolved_count} résolues)")
    table.add_row("profondeur max", str(ol.max_depth))
    table.add_row("couverture", f"{ol.coverage_ratio:.0%}")
    table.add_row("plus grand trou", f"{ol.largest_gap_pages} pages")
    console.print(
        Panel(
            Group(table, Text("\n".join(f"· {r}" for r in ol.reasons), style="dim")),
            title="2 · Outline",
            border_style="blue",
        )
    )

    # --- Colonnes
    col = report.columns
    dist = "  ".join(f"{n} col: {c} pages" for n, c in col.distribution.items())
    table = _kv_table()
    table.add_row("dominant", f"{col.dominant} colonne(s)")
    table.add_row("variance", f"{col.variance:.2f}")
    table.add_row(
        "stabilité",
        Text(
            "stable" if col.stable else "variable dans le livre",
            style="green" if col.stable else "yellow",
        ),
    )
    table.add_row("distribution", dist or "—")
    table.add_row("échantillon", f"{len(col.sampled_pages)} pages du milieu du livre")
    console.print(Panel(table, title="3 · Colonnes", border_style="blue"))

    # --- Boilerplate
    bp = report.boilerplate
    if bp.patterns:
        table = Table(box=None, pad_edge=False)
        table.add_column("pages", style="dim", no_wrap=True)
        table.add_column("zone", style="dim", no_wrap=True)
        table.add_column("exemple")
        for pattern in bp.patterns[:8]:
            table.add_row(
                f"{pattern.page_count} ({pattern.page_ratio:.0%})",
                pattern.zone,
                Text(pattern.example[:80], style="italic"),
            )
        note = Text(
            "Chiffres masqués avant hachage : sans ça, le numéro de page inclus "
            "dans la ligne rend chaque occurrence unique et la détection rate tout.",
            style="dim",
        )
        console.print(
            Panel(Group(table, note), title="4 · Boilerplate", border_style="blue")
        )
    else:
        console.print(
            Panel(
                Text("aucune ligne présente sur plus de 60 % des pages.", style="dim"),
                title="4 · Boilerplate",
                border_style="blue",
            )
        )

    # --- Pagination
    pm = report.page_map
    table = _kv_table()
    table.add_row("méthode", pm.method.value)
    table.add_row(
        "source",
        Text(
            _SOURCE_LABEL[pm.source],
            style="green" if pm.source is PageNumberSource.STANDALONE else "yellow",
        ),
    )
    table.add_row("pages mesurées", f"{pm.measured_pages}/{report.page_count}")
    table.add_row("confiance", f"{pm.confidence:.0%}")
    table.add_row("monotone", "oui" if pm.monotonic else "non")
    if pm.uniform_offset is not None:
        table.add_row(
            "décalage",
            f"pdf_index0 = book_page + ({pm.uniform_offset})"
            " — constant sur tout le livre",
        )
    else:
        table.add_row("plages", f"{len(pm.runs)} plages de décalage distinct")
        for run in pm.runs[:6]:
            table.add_row(
                "",
                f"pdf {run.pdf_start}-{run.pdf_end} → offset {run.page_offset}",
            )
    pagination: list[ConsoleRenderable] = [table]
    if pm.notes:
        pagination.append(Text("\n".join(f"· {n}" for n in pm.notes), style="dim"))
    console.print(
        Panel(
            Group(*pagination), title="5 · Numéro de page imprimé", border_style="blue"
        )
    )

    # --- Images
    im = report.images
    table = _kv_table()
    table.add_row("total", str(im.total))
    table.add_row(
        "retenues (≥100×100 px)", f"{im.large_count}  ({im.large_per_page:.2f}/page)"
    )
    table.add_row("aire médiane", f"{im.median_area_px:,.0f} px²")
    table.add_row("illustration-lourd", "oui" if im.illustration_heavy else "non")
    console.print(Panel(table, title="6 · Images", border_style="blue"))

    # --- TdM textuelle
    toc = report.toc
    table = _kv_table()
    table.add_row("pages de TdM", ", ".join(map(str, toc.pages)) or "—")
    table.add_row("entrées repérées", str(toc.entry_count))
    if toc.sample:
        table.add_row("extrait", Text("\n".join(toc.sample[:4]), style="italic dim"))
    console.print(
        Panel(table, title="7 · Table des matières textuelle", border_style="blue")
    )

    # --- Erreurs
    if report.errors:
        table = Table(box=None, pad_edge=False)
        table.add_column("page", style="dim")
        table.add_column("étape", style="dim")
        table.add_column("erreur", style="red")
        for err in report.errors[:20]:
            table.add_row(str(err.pdf_index), err.stage, err.error)
        console.print(
            Panel(
                Group(
                    table,
                    Text(
                        f"{len(report.errors)} page(s) en échec. "
                        "Ces pages seront absentes de l'index.",
                        style="bold red",
                    ),
                ),
                title="⚠ Erreurs d'extraction",
                border_style="red",
            )
        )

    # --- Décision
    console.print(
        Panel(
            Group(
                Text(_ROUTE_LABEL[report.recommended_route], style="bold green"),
                Text(f"Profil : {report.matched_profile or 'aucun'}", style="dim"),
                Text(report.estimated_cost, style="cyan"),
                Text(""),
                Text("\n".join(f"· {r}" for r in report.route_rationale)),
                Text(
                    "\nForcer une autre route : rulelawyer probe <pdf> --route B",
                    style="dim",
                ),
            ),
            title="→ Route recommandée",
            border_style="green",
        )
    )
    console.print()
