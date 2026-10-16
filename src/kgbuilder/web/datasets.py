"""The datasets the web interface shows (R222): read from `datasets.yaml`, one tab each.

Role in the pipeline: the web composition root (`kg serve`) reads the file once and hands the entries to the
app; every route names a dataset by its `id`.
Design: a pydantic model per entry, so a wrong file fails at start with the field named, not on a click.
Must not: open Neo4j or read a build; the entries only point at them.
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError

from ..core.errors import ConfigurationError

DATASETS_FILE = Path("datasets.yaml")  # relative to the working directory, like presets.yaml


class Dataset(BaseModel):
    """One tab: the dataset's id and display name, the preset it is asked with, its final build folder, and
    the digest Neo4j gives with that build loaded."""

    id: str = Field(pattern=r"^[a-z0-9_]+$")  # in URLs and folder names
    name: str
    preset: str
    build: Path
    digest: str


class _File(BaseModel):
    datasets: list[Dataset] = Field(min_length=1)


def read_datasets(path: Path = DATASETS_FILE) -> list[Dataset]:
    """The datasets of `path`, in tab order. Raises `ConfigurationError` when the file is missing, does not
    fit the model, or names an id twice."""
    if not path.exists():
        raise ConfigurationError(f"{path} does not exist")
    try:
        datasets = _File.model_validate(yaml.safe_load(path.read_text(encoding="utf-8"))).datasets
    except ValidationError as e:
        raise ConfigurationError(f"{path}: {e}") from e
    ids = [d.id for d in datasets]
    if len(set(ids)) != len(ids):
        raise ConfigurationError(f"{path} names a dataset id twice: {ids}")
    return datasets
