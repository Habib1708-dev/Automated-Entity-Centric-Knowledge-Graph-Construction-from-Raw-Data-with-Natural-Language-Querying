"""Regenerate every figure from the committed records (R129): `uv run python -m figures.scripts.build`.

Role: the one entry point; a figure that is not built here does not exist. The test builds every figure into
a temporary folder the same way.
Must not: read `out/` or MLflow, or call a model or a database.
"""

from pathlib import Path

from . import r126_r128_diagrams, r128_charts, r128_data, r130_r131_diagrams, r131_charts, r131_data
from .style import apply_style

FIGURES = Path(__file__).resolve().parents[1]


def build(results: Path | None = None, diagrams: Path | None = None) -> list[Path]:
    """Every chart and diagram, written as SVG and PNG; returns the files written. `results` and `diagrams`
    (a test's temporary folders) replace `figures/results` and `figures/diagrams`, keeping the subfolders."""
    apply_style()

    def place(default: Path, root: Path | None, kind: str) -> Path:
        # the step's folder under another root: figures/<kind>/<topic>/<step> -> <root>/<topic>/<step>
        return default if root is None else root / default.relative_to(FIGURES / kind)

    tables, choice = r131_data.load_tables(), r131_data.load_choice()
    files = r128_charts.draw_all(
        r128_data.load_table(), r128_data.pairings(), place(r128_charts.OUT, results, "results")
    )
    files += r131_charts.draw_all(
        tables,
        choice,
        r131_data.against_lexical(choice),
        r131_data.reranker_against_pool(),
        place(r131_charts.OUT, results, "results"),
    )
    files += r126_r128_diagrams.draw_all(place(r126_r128_diagrams.OUT, diagrams, "diagrams"))
    files += r130_r131_diagrams.draw_all(tables, choice, place(r130_r131_diagrams.OUT, diagrams, "diagrams"))
    return files


if __name__ == "__main__":
    for path in build():
        print(path.relative_to(Path.cwd()) if path.is_relative_to(Path.cwd()) else path)
