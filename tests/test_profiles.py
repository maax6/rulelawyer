"""Les profils identifient un livre avant de modifier ses paramètres."""

from pathlib import Path

import pytest
import yaml
from scripts.make_demo_pdf import make_demo_pdf
from typer.testing import CliRunner

from rulelawyer.cli import app
from rulelawyer.ingest import ingest_route_a
from rulelawyer.models import PageMapMethod, Route
from rulelawyer.probe import probe
from rulelawyer.profiles import load_profile


def test_init_stdout_roundtrips_and_overrides_reach_ingestion(tmp_path: Path) -> None:
    pdf = tmp_path / "manual.pdf"
    make_demo_pdf(pdf)
    runner = CliRunner()
    generated = runner.invoke(app, ["profile", "init", str(pdf), "--name", "My manual"])
    assert generated.exit_code == 0, generated.output
    data = yaml.safe_load(generated.stdout)
    assert data["name"] == "My manual"
    assert "3 étincelles" not in generated.stdout
    assert "page_offset" not in data
    data.update(
        reading_order="position",
        page_offset=-7,
        drop_sections=["Lexique"],
        table_pages=[8],
        boilerplate_patterns=["^Le passage ne demande"],
    )
    directory = tmp_path / "profiles"
    directory.mkdir()
    profile = directory / "my-manual.yaml"
    profile.write_text(yaml.safe_dump(data), encoding="utf-8")
    report = probe(pdf, profiles_dir=directory)
    assert report.matched_profile == "My manual"
    assert report.reading_order == "position"
    assert report.page_map.method is PageMapMethod.PROFILE
    chunks = ingest_route_a(pdf, report)
    assert {c.book_id for c in chunks} == {"my-manual"}
    assert all("Lexique" not in c.section_path for c in chunks)
    bridge = next(c for c in chunks if c.section_path.endswith("Pont de brume"))
    assert bridge.book_page == 8
    assert bridge.type == "table"
    assert "aucun jet" not in bridge.raw_text
    assert "3 étincelles" in bridge.raw_text


def test_wrong_book_and_ambiguous_profiles_are_rejected(tmp_path: Path) -> None:
    pdf = tmp_path / "manual.pdf"
    make_demo_pdf(pdf)
    directory = tmp_path / "profiles"
    directory.mkdir()
    data = yaml.safe_load(CliRunner().invoke(app, ["profile", "init", str(pdf)]).stdout)
    first = directory / "one.yaml"
    second = directory / "two.yml"
    first.write_text(yaml.safe_dump(data))
    second.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError, match="Plusieurs profils"):
        probe(pdf, profiles_dir=directory)
    assert probe(pdf, profile_path=first).matched_profile == "manual"
    data["match"]["first_page_text_sha256"] = "0" * 64
    first.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError, match="ne correspond pas"):
        probe(pdf, profile_path=first)
    second.unlink()
    assert probe(pdf, profiles_dir=directory).matched_profile is None


def test_route_override_is_recorded_by_probe_and_respected(tmp_path: Path) -> None:
    pdf = tmp_path / "manual.pdf"
    make_demo_pdf(pdf)
    report = probe(pdf, route=Route.C)
    assert report.recommended_route is Route.C
    assert "--route C" in report.route_rationale[-1]
    with pytest.raises(ValueError, match="Route C"):
        ingest_route_a(pdf, report)


@pytest.mark.parametrize(
    "data",
    [
        {"name": "unsafe", "match": {}},
        {"name": "unsafe", "match": {"page_count": 4}},
        {
            "name": "unsafe",
            "match": {"file_sha256": "0" * 64},
            "boilerplate_patterns": ["["],
        },
        {"name": "unsafe", "match": {"file_sha256": "0" * 64}, "unknown": True},
    ],
)
def test_invalid_profiles_fail_loudly(tmp_path: Path, data: dict[str, object]) -> None:
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError, match="Profil invalide"):
        load_profile(path)


def test_yaml_object_tags_cannot_execute(tmp_path: Path) -> None:
    path = tmp_path / "invalid.yaml"
    path.write_text("!!python/object/apply:os.system ['echo unsafe']")
    with pytest.raises(ValueError, match="Profil invalide"):
        load_profile(path)
