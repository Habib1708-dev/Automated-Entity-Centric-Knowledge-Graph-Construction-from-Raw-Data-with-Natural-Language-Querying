"""The example values a schema shows (R164, core/examples.py), without Neo4j or DuckDB: the shape of a value
and the choice of examples, one of each shape among the lowest values, then the lowest not yet taken. The
graph schema's and the source tables' use of the rule is tested in test_query_graph.py (needs Neo4j) and
test_agent_structured.py."""

from neo4j.time import Date

from kgbuilder.core.examples import value_shape, varied_examples


def test_a_values_shape_keeps_its_punctuation_and_drops_its_digits_and_words():
    """Runs of digits, of words and of spaces each become one mark, so values written alike share a shape
    whatever they hold; a value that is not a text has its type's shape."""
    assert value_shape("2004") == value_shape("17") == "9"
    assert value_shape("2004-06") == "9-9" and value_shape("B-12  north") == "a-9 a"
    assert value_shape("Kettle 5s") == value_shape("Pot 12x") == "a 9a"
    assert value_shape("Ølstue") == "a"  # a letter of any script is a letter
    assert value_shape(3) == value_shape(1999) != value_shape(2.5)
    assert value_shape(Date(2015, 12, 10)) == value_shape(Date(2020, 1, 2))


def test_a_names_word_count_is_no_shape_of_its_own():
    """Words joined by whitespace are one run, so a name of two words has the shape of a name of one; a
    column of such names keeps its lowest values, where counting words would have swapped one of them for
    the first two-word name met."""
    assert value_shape("Old  Mill Road") == value_shape("Quay") == "a"
    assert value_shape("Pier 4 East") == "a 9 a" and value_shape("Ward (3)") == "a (9)"
    assert varied_examples(["Ashford", "Brill", "Crewe", "Port Wenn"], 3) == ["Ashford", "Brill", "Crewe"]


def test_the_examples_show_each_shape_the_lowest_values_hold():
    """A column of years with a few year-months showed only its three lowest years, and a query written from
    them compared with '=' and missed the month-dated rows; the first value of each shape is taken, then the
    lowest values not yet taken, in the order given."""
    years = ["2001", "2002", "2003", "2004-06", "2005", "2006-01-02"]
    assert varied_examples(years, 3) == ["2001", "2004-06", "2006-01-02"]
    assert varied_examples(["2001", "2002", "2003", "2004-06", "2005"], 3) == ["2001", "2002", "2004-06"]
    # one shape, or numbers of one type: the lowest values, as before R164
    assert varied_examples(["K1", "K2", "K3", "K4"], 3) == ["K1", "K2", "K3"]
    assert varied_examples([-4, 1, 2, 30], 3) == [-4, 1, 2]
    # more shapes than examples: the first ones met; fewer values than examples: all of them
    assert varied_examples(["a", "a-b", "a 1", "a (1)"], 3) == ["a", "a-b", "a 1"]
    assert varied_examples(["x", "x-1"], 3) == ["x", "x-1"] and varied_examples([], 3) == []
