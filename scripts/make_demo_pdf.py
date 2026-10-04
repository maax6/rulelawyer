"""Petit manuel original sous CC0 ; le PDF produit ne doit pas être versionné."""

from __future__ import annotations

import sys
from pathlib import Path

import reportlab
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas


def make_demo_pdf(path: Path) -> None:
    font_path = Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"
    pdfmetrics.registerFont(TTFont("DemoVera", str(font_path)))
    path.parent.mkdir(parents=True, exist_ok=True)
    canvas = Canvas(str(path), pagesize=(595, 842), invariant=1)
    canvas.setTitle("Manuel des Veilleurs — démonstration CC0")
    sections = [
        (
            "Présentation",
            [
                "Ce petit manuel présente le jeu imaginaire des Veilleurs.",
                "Son univers est une vallée traversée de ponts vaporeux.",
                "Les voyageurs portent des cristaux contenant des étincelles.",
                "Il contient exactement deux règles, chacune dans sa section.",
                "Ce texte original est offert sous CC0, librement redistribuable.",
            ],
        ),
        (
            "Pont de brume",
            [
                "Traverser un Pont de brume coûte exactement 3 étincelles.",
                "Le passage ne demande aucun jet de dé.",
                "Les étincelles sont retirées de la réserve du voyageur.",
                "Cette règle concerne uniquement les Ponts de brume.",
            ],
        ),
        (
            "Repos de cristal",
            [
                "Un Repos de cristal dure 7 minutes sans interruption.",
                "Il rend exactement 2 étincelles au voyageur.",
                "Le voyageur retrouve ces étincelles au terme du repos.",
                "Cette règle concerne uniquement le Repos de cristal.",
            ],
        ),
        (
            "Lexique",
            [
                "Veilleur : nom donné aux voyageurs de cette vallée.",
                "Étincelle : ressource imaginaire conservée dans un cristal.",
                "Pont de brume : passage vaporeux entre les rives.",
                "Ce lexique explique les mots sans ajouter de mécanique.",
                "Fin du manuel de démonstration des Veilleurs.",
            ],
        ),
    ]
    for index, (title, lines) in enumerate(sections):
        canvas.bookmarkPage(f"page-{index}")
        if index == 0:
            canvas.bookmarkPage("manual")
            canvas.addOutlineEntry("Manuel des Veilleurs", "manual", level=0)
        canvas.addOutlineEntry(title, f"page-{index}", level=1)
        canvas.setFont("DemoVera", 16)
        canvas.drawString(50, 720, title)
        canvas.setFont("DemoVera", 11)
        for row, line in enumerate(lines):
            canvas.drawString(50, 680 - row * 24, line)
        canvas.drawString(290, 35, str(41 + index))
        canvas.showPage()
    canvas.save()


if __name__ == "__main__":
    make_demo_pdf(Path(sys.argv[1]))
