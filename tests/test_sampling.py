"""The sampler (sampling.py) and `kg sample`: which rows and sections a subset keeps, the guards around
it, and that the committed samples/ are exactly what presets.yaml describes. No Neo4j, no network."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from kgbuilder.cli import app
from kgbuilder.config import read_presets
from kgbuilder.core.errors import ConfigurationError, MissingInputError
from kgbuilder.sampling import RootRows, SampleSpec, cut_sections, preset_samples, write_sample

REPO = Path(__file__).resolve().parent.parent

# Two products; each has an assembly; parts are supplied by suppliers. Supplier S3 supplies only product 2.
TABLES = {
    "products.csv": "product_id,name\nP1,Chair\nP2,Table\n",
    "assemblies.csv": "assembly_id,product_id\nA1,P1\nA2,P2\n",
    "parts.csv": "part_id,assembly_id,supplier_id\nX1,A1,S1\nX2,A1,S2\nX3,A2,S3\n",
    "suppliers.csv": "supplier_id,city\nS1,Oslo\nS2,Lund\nS3,Pune\n",
}
REVIEWS = "# Chair\n\n## One\ngood\n\n---\n\n## Two\nbad\n\n---\n\n## Three\nok\n"


def dataset(root: Path) -> Path:
    source = root / "data"
    (source / "reviews").mkdir(parents=True)
    for name, text in TABLES.items():
        (source / name).write_text(text, encoding="utf-8")
    (source / "reviews" / "chair.md").write_text(REVIEWS, encoding="utf-8")
    return source


def spec(source: Path, **overrides) -> SampleSpec:
    values = {
        "source": source,
        "root": RootRows(file="products.csv", column="product_id", values=["P1"]),
        "documents": ["reviews/chair.md"],
        "sections": 2,
    }
    return SampleSpec(**(values | overrides))


def test_a_subset_keeps_the_root_rows_their_descendants_and_what_they_reference(tmp_path):
    report = write_sample(spec(dataset(tmp_path)), tmp_path / "out")
    # down from P1: assembly A1, parts X1 and X2; up from those parts: suppliers S1 and S2, not S3
    assert report.rows == {"products.csv": 1, "assemblies.csv": 1, "parts.csv": 2, "suppliers.csv": 2}
    assert (tmp_path / "out" / "suppliers.csv").read_text(
        encoding="utf-8"
    ) == "supplier_id,city\nS1,Oslo\nS2,Lund\n"
    assert report.sections == {"reviews/chair.md": 2}
    assert (tmp_path / "out" / "reviews" / "chair.md").read_text(encoding="utf-8").endswith("## Two\nbad\n")


def test_a_document_with_fewer_sections_is_kept_whole():
    assert cut_sections(REVIEWS, 5) == REVIEWS
    assert cut_sections(REVIEWS, 1) == "# Chair\n\n## One\ngood\n"


def test_a_root_that_matches_nothing_or_does_not_exist_is_a_clear_error(tmp_path):
    source = dataset(tmp_path)
    with pytest.raises(ConfigurationError, match="no row of products.csv"):
        write_sample(
            spec(source, root=RootRows(file="products.csv", column="product_id", values=["P9"])),
            tmp_path / "o",
        )
    with pytest.raises(ConfigurationError, match="does not exist"):
        write_sample(
            spec(source, root=RootRows(file="products.csv", column="sku", values=["P1"])), tmp_path / "o"
        )
    with pytest.raises(MissingInputError, match="missing.md"):
        write_sample(spec(source, documents=["missing.md"]), tmp_path / "o")


def test_the_sampler_never_replaces_the_source_or_a_directory_holding_it(tmp_path):
    source = dataset(tmp_path)
    for target in (source, tmp_path):
        with pytest.raises(ConfigurationError, match="refusing to replace"):
            write_sample(spec(source), target)
    assert (source / "products.csv").exists()


def test_rebuilding_replaces_the_old_subset(tmp_path):
    source, out = dataset(tmp_path), tmp_path / "out"
    (out / "stale").mkdir(parents=True)
    (out / "stale" / "old.csv").write_text("a\n1\n", encoding="utf-8")
    write_sample(spec(source), out)
    assert not (out / "stale").exists()


def test_presets_without_a_sample_block_are_skipped_unless_named():
    presets = {
        "smoke": {
            "data_dir": "samples/smoke",
            "sample": {
                "source": "data",
                "root": RootRows(file="p.csv", column="id", values=["1"]).model_dump(),
            },
        },
        "quality": {"data_dir": "data"},
    }
    assert list(preset_samples(presets, [])) == ["smoke"]
    with pytest.raises(ConfigurationError, match="needs both a sample block"):
        preset_samples(presets, ["quality"])
    with pytest.raises(ConfigurationError, match="unknown preset"):
        preset_samples(presets, ["prod"])
    with pytest.raises(ConfigurationError, match="invalid sample block"):
        preset_samples({"x": {"data_dir": "d", "sample": {"source": "data"}}}, [])


@pytest.mark.parametrize("name", ["smoke", "dev"])
def test_the_committed_samples_are_exactly_what_the_config_describes(name, tmp_path):
    # config and files must not drift: after editing a sample block, run `uv run kg sample` and commit
    sample_spec, target = preset_samples(read_presets(REPO / "presets.yaml"), [name])[name]
    sample_spec.source = REPO / sample_spec.source
    write_sample(sample_spec, tmp_path / name)

    def files(root: Path) -> dict[str, str]:
        # line endings may differ with git's autocrlf; the content may not
        return {
            p.relative_to(root).as_posix(): p.read_bytes().decode("utf-8").replace("\r\n", "\n")
            for p in sorted(root.rglob("*"))
            if p.is_file()
        }

    assert files(tmp_path / name) == files(REPO / target)


def test_kg_sample_rebuilds_from_the_presets_file_and_reports_errors(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # presets.yaml is read from the working directory, like .env
    dataset(tmp_path)
    (tmp_path / "presets.yaml").write_text(
        "tiny:\n  data_dir: samples/tiny\n  sample:\n    source: data\n"
        "    root: {file: products.csv, column: product_id, values: [P2]}\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(app, ["sample"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "samples" / "tiny" / "suppliers.csv").read_text(
        encoding="utf-8"
    ) == "supplier_id,city\nS3,Pune\n"
    result = CliRunner().invoke(app, ["sample", "prod"])
    assert result.exit_code == 1 and "unknown preset" in result.output
