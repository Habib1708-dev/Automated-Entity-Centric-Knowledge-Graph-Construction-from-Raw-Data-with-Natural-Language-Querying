"""The built-in `Value` type: a claim whose object is a number with a unit ("rated for 25kg loads").

Role in the pipeline: the text schema may use `Value` as a fact type's object without defining it
(text/schema.py), extraction checks that a value is a number stated in the evidence (text/extraction.py),
the subject-graph writer stores the parsed number and unit on the observation (text/subject_graph.py), and
entity resolution never merges two values (resolution/resolver.py): "25 kg" and "35 kg" spell alike but
are different claims.
Design: a value is still an `:Entity` at the end of an OBJECT edge (R66, the user's choice), so every reader
keeps one observation shape; the observation also carries `value` and `unit` for numeric queries. Only
units code knows for certain are normalised; any other unit ("Martindale rubs") stays verbatim.
Not here: anything that touches Neo4j or the LLM.
"""

import re

from pydantic import BaseModel

VALUE_TYPE = "Value"

# A number as text writes it, then the rest: "3.2kg" -> ("3.2", "kg"), "30,000 Martindale rubs" ->
# ("30,000", "Martindale rubs"). The comma is a thousands separator only before exactly three digits, and
# the number may not be followed by another digit, comma or point, so "3,5" is no number at all (not 35,
# and not 3 with the unit ",5").
_QUANTITY = re.compile(r"\s*(-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?)(?![\d.,])\s*(.*?)\s*")

# Units of measurement, not of any one domain: every spelling maps to one symbol, so "25kg" and
# "25 kilograms" become the same value. A unit missing here is kept as written, never guessed.
# fmt: off
_UNITS = {
    "kg": "kg", "kgs": "kg", "kilogram": "kg", "kilograms": "kg",
    "g": "g", "gram": "g", "grams": "g",
    "lb": "lb", "lbs": "lb", "pound": "lb", "pounds": "lb",
    "mm": "mm", "millimetre": "mm", "millimetres": "mm", "millimeter": "mm", "millimeters": "mm",
    "cm": "cm", "centimetre": "cm", "centimetres": "cm", "centimeter": "cm", "centimeters": "cm",
    "m": "m", "metre": "m", "metres": "m", "meter": "m", "meters": "m",
    "km": "km", "kilometre": "km", "kilometres": "km", "kilometer": "km", "kilometers": "km",
    "in": "in", "inch": "in", "inches": "in",
    "ft": "ft", "foot": "ft", "feet": "ft",
    "mi": "mi", "mile": "mi", "miles": "mi",
    "mph": "mph", "km/h": "km/h", "kph": "km/h",
    "%": "%", "percent": "%",
}
# fmt: on


class Quantity(BaseModel):
    """A parsed value: the number and its unit (a known unit's symbol, else the text's own words)."""

    value: float
    unit: str

    @property
    def text(self) -> str:
        """The canonical spelling, used as the Value entity's name: "25 kg", "30000 Martindale rubs"."""
        # 25.0 prints as "25"; a float format like :g would print 1000000.0 as "1e+06"
        number = str(int(self.value)) if self.value.is_integer() else str(self.value)
        return f"{number} {self.unit}".strip()


def parse_quantity(text: str) -> Quantity | None:
    """The number and unit of `text`, or None when it does not start with a number ("two months")."""
    match = _QUANTITY.fullmatch(text)
    if match is None:
        return None
    number, unit = match.groups()
    return Quantity(value=float(number.replace(",", "")), unit=_UNITS.get(unit.lower(), unit))
