"""The numbers the R231 figures show: the query engine's question-answering accuracy on the four selected
datasets (`r229_data.SELECTED`: NTSB, furniture, held-out, generality), read from the committed outcomes
of the three measurements: R157's benchmark, and R166's and R173's confirmation sets.

Role: the only reader of data for `r231_charts.py`. The test checks these functions against the counts
the records hold.
Design: the outcomes of `r173_data.measurements()`, kept to the selected datasets; every rate is counted
from the recorded booleans with its 95 % Wilson interval (`Proportion.of`). The five-dataset figures of
R157 and R173 stay as they are.
Must not: read `out/`, MLflow or a database, or draw.
"""

from kgbuilder.validation.interval import Proportion

from . import r157_data as base
from . import r173_data
from .r229_data import SELECTED

LABELS = base.LABELS | {"pooled": "Pooled (four datasets)"}


def measurements() -> list[r173_data.Measurement]:
    """The three measurements in time order, each with only the selected datasets' outcomes."""
    return [
        r173_data.Measurement(m.key, m.label, m.engine, tuple(o for o in m.outcomes if o.dataset in SELECTED))
        for m in r173_data.measurements()
    ]


def _accuracy(group: str, rows: list) -> base.Accuracy:
    n = len(rows)
    return base.Accuracy(
        group, Proportion.of(sum(r.exact for r in rows), n), Proportion.of(sum(r.meaning for r in rows), n)
    )


def by_dataset(outcomes: list) -> list[base.Accuracy]:
    """Accuracy per selected dataset in `SELECTED` order, then pooled over the four."""
    return [
        *(_accuracy(d, [o for o in outcomes if o.dataset == d]) for d in SELECTED),
        _accuracy("pooled", outcomes),
    ]
