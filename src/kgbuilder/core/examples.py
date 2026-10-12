"""The example values a schema shows for a property or a column: a few lowest values, one of each shape.

Role in the pipeline: the query side shows the answering model a few values per graph property
(query/graph_schema.py) and per source table column (agent/tables.py), so it writes a filter in the values'
own format. Both read the lowest distinct values (at most `EXAMPLE_SCAN`) and keep `EXAMPLE_COUNT` of them
with `varied_examples` (R164): a text column of years and year-months shows both forms, not only its three
lowest years. A value's shape is its form with its content dropped: a run of digits is one mark, and so is a
run of words, so 'Canada' and 'South Africa' share a shape while '2004' and '2004-06' do not.
Not here: reading the values (Neo4j or DuckDB) and writing them as literals, which stay with each reader.
"""

import re
from collections.abc import Sequence

EXAMPLE_COUNT = 3  # example values per property or column: enough to show a format ("2019", "K-12")
# The lowest distinct values read per property or column to find the shapes among them (R164): a bound, so a
# large label or table is still read quickly, set above what the five evaluated datasets hold (171 values at
# most in a staged column, a few hundred in a graph property such as the pipeline's mention names); a shape
# first met beyond it is not shown
EXAMPLE_SCAN = 1000
# Runs of digits, of words and of whitespace, each turned into one mark by `value_shape`. A word is a run of
# letters (a word character that is neither a digit nor '_', in any script); words joined by whitespace are
# one run, so a name's word count is no shape of its own ('South Africa' is 'a', as 'Canada' is)
_DIGITS = re.compile(r"\d+")
_WORDS = re.compile(r"[^\W\d_]+(?:\s+[^\W\d_]+)*")
_SPACES = re.compile(r"\s+")


def value_shape(value: object) -> str:
    """The form of `value` with its content dropped (R164): in a text, each run of digits becomes '9', each
    run of words (letters, with whitespace between words) 'a' and each other run of whitespace one space,
    punctuation kept ('2004-06' -> '9-9', 'Pot 12x' -> 'a 9a', 'South Africa' -> 'a'); any other value is
    its type's name, so the numbers, dates or lists of one type share one shape."""
    if not isinstance(value, str):
        return type(value).__name__
    # digits first: the '9' they leave is no letter, so the words' pass cannot take it
    return _SPACES.sub(" ", _WORDS.sub("a", _DIGITS.sub("9", value)))


def varied_examples(values: Sequence[object], count: int) -> list[object]:
    """`count` of `values` (distinct and in ascending order) showing as many of their shapes as fit (R164):
    the first value of each shape met, up to `count`, then the lowest values not yet taken, returned in the
    order given. A text column of years and year-months shows both forms; its three lowest values were all
    years, and a query written from them compared a year with '=' and missed the month-dated rows (R157's
    trace). A column of one shape (names of one or more words, codes of one form, numbers) shows its lowest
    values, as before."""
    taken: list[int] = []
    shapes: set[str] = set()
    for index, value in enumerate(values):
        if len(taken) == count:
            break
        shape = value_shape(value)
        if shape not in shapes:
            shapes.add(shape)
            taken.append(index)
    first = set(taken)
    rest = [index for index in range(len(values)) if index not in first][: count - len(taken)]
    return [values[index] for index in sorted(taken + rest)]
