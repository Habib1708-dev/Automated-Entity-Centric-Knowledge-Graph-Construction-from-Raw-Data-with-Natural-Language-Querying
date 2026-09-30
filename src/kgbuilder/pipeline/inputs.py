"""Input files of a stage: checked to exist, and identified by a content hash for MLflow.

Role in the pipeline: the stages that read a file given on the command line (a gold file, a verdict file, a
judge sheet, an answers file) use these, in stages.py and qa_stages.py alike.
"""

import hashlib
from pathlib import Path

from ..core.errors import MissingInputError


def input_file(path: Path, what: str) -> Path:
    """`path` as a Path, or `MissingInputError` naming `what` is missing."""
    if not Path(path).exists():
        raise MissingInputError(f"{what} '{path}' not found")
    return Path(path)


def digest(path: Path) -> str:
    """Short content hash of an input file, logged as a param so runs scored against different gold or
    verdict files are never compared as if they were the same."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]
