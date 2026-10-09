"""Regenerate every figure from the committed records (R129): `uv run python -m figures.scripts.build`.

Role: the one entry point; a figure that is not built here does not exist. The test builds every figure into
a temporary folder the same way.
Must not: read `out/` or MLflow, or call a model or a database.
"""

from pathlib import Path

from . import r126_r128_diagrams, r128_charts, r128_data
from .style import apply_style


def build(results: Path = r128_charts.OUT, diagrams: Path = r126_r128_diagrams.OUT) -> list[Path]:
    """Every chart and diagram, written as SVG and PNG; returns the files written."""
    apply_style()
    files = r128_charts.draw_all(r128_data.load_table(), r128_data.pairings(), results)
    files += r126_r128_diagrams.draw_all(diagrams)
    return files


if __name__ == "__main__":
    for path in build():
        print(path.relative_to(Path.cwd()) if path.is_relative_to(Path.cwd()) else path)
