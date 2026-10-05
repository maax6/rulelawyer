"""Distribution et filtre d'images mesurés sur un PDF original temporaire."""

import random
from pathlib import Path

from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas
from scripts.make_demo_pdf import make_demo_pdf
from typer.testing import CliRunner

from rulelawyer.cli import app
from rulelawyer.ingest import ingest_route_a
from rulelawyer.ingest_stats import IngestStats, ingestion_stats
from rulelawyer.probe import probe


def test_image_filter_checks_pixels_and_encoded_bytes(tmp_path: Path) -> None:
    rng = random.Random(7)
    large = Image.frombytes("RGB", (120, 120), rng.randbytes(120 * 120 * 3))
    small = Image.frombytes("RGB", (80, 80), rng.randbytes(80 * 80 * 3))
    plain = Image.new("RGB", (120, 120), "white")
    pdf = tmp_path / "images.pdf"
    canvas = Canvas(str(pdf))
    for i, image in enumerate([large, small, plain]):
        canvas.drawImage(ImageReader(image), 30 + 150 * i, 400)
    canvas.save()
    demo = tmp_path / "demo.pdf"
    make_demo_pdf(demo)
    chunks = ingest_route_a(demo, probe(demo))
    stats = ingestion_stats(pdf, chunks)
    assert (stats.images_total, stats.images_kept, stats.images_filtered) == (3, 1, 2)
    assert stats.images_indexed == 0
    assert sum(stats.token_distribution.values()) == stats.chunks == 4
    assert stats.token_min <= stats.token_median <= stats.token_max


def test_ingest_cli_writes_reviewable_statistics(tmp_path: Path) -> None:
    pdf = tmp_path / "demo.pdf"
    make_demo_pdf(pdf)
    output = tmp_path / "out"
    result = CliRunner().invoke(app, ["ingest", str(pdf), "--output", str(output)])
    assert result.exit_code == 0, result.output
    stats = IngestStats.model_validate_json((output / "stats.json").read_text())
    assert stats.sections == stats.chunks == 4
    assert stats.images_total == stats.images_indexed == 0
    assert "0 retenues" in result.output and "0 filtrées" in result.output
