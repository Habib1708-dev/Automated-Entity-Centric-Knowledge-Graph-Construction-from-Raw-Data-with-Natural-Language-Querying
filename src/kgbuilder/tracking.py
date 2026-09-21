"""MLflow tracking that degrades to a no-op, so the pipeline never depends on it."""

import logging
from contextlib import contextmanager
from pathlib import Path

from .config import settings

log = logging.getLogger(__name__)


class Run:
    def __init__(self, mlflow=None):
        self._mlflow = mlflow

    def params(self, **kw) -> None:
        if self._mlflow:
            self._mlflow.log_params({k: str(v)[:500] for k, v in kw.items()})

    def metrics(self, **kw) -> None:
        if self._mlflow:
            self._mlflow.log_metrics({k: float(v) for k, v in kw.items() if v is not None})

    def artifact(self, path: Path | str) -> None:
        if self._mlflow and Path(path).exists():
            self._mlflow.log_artifact(str(path))


@contextmanager
def track(name: str, **params):
    """One MLflow run per pipeline stage; nested under an active run if there is one."""
    try:
        import mlflow

        mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
        mlflow.set_experiment(settings.mlflow_experiment)
        active = mlflow.active_run() is not None
        ctx = mlflow.start_run(run_name=name, nested=active)
    except Exception as e:  # mlflow missing or store unusable
        log.warning("MLflow disabled: %s", e)
        yield Run()
        return
    with ctx:
        run = Run(mlflow)
        run.params(**params)
        yield run
