"""The validation check families, in the order they are reported.

Design: Strategy + Open/Closed. To add a check family, write a class with a `run(ctx)` method (see
base.py) and append it to `DEFAULT_CHECKS`; validator.py does not change.
"""

from .base import CheckContext, GraphCheck
from .consistency import SubjectConsistencyCheck
from .provenance import ProvenanceCheck
from .structure import DomainStructureCheck, LexicalStructureCheck

DEFAULT_CHECKS: list[GraphCheck] = [
    DomainStructureCheck(),
    LexicalStructureCheck(),
    ProvenanceCheck(),
    SubjectConsistencyCheck(),
]

__all__ = ["DEFAULT_CHECKS", "CheckContext", "GraphCheck"]
