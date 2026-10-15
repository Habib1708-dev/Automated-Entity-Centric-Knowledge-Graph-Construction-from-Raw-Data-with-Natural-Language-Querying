"""The local cross-encoder adapter (R200): a reranking model such as BAAI/bge-reranker-v2-m3, run on this
machine through sentence-transformers, behind the `PairScorer` port.

Role in the pipeline: built only by the composition root (cli.py) for `kg passage-grid`, whose
`CrossEncoderReranker` (hybrid/cross_rerank.py) orders a pool of passages by these scores, beside the LLM
reranker (plan R200-R201).
Design: Adapter. sentence-transformers and torch are imported when the model is built, never at import time,
so the default install (without the `rerank` dependency group) still imports this module and every test
runs without them. The model is read from the local Hugging Face cache only: no download in a run.
Not here: the order and its checks (hybrid/cross_rerank.py), the pools and scores (validation/).
"""

from collections.abc import Sequence

from ..core.errors import ConfigurationError

# How many tokens of a (question, passage) pair the model reads. Passages are at most `chunk_max_chars`
# (1 500) characters, about 400 to 700 tokens, so 2 048 reads every pair whole, as the LLM reranker does;
# bge-reranker-v2-m3 accepts up to 8 192.
MAX_LENGTH = 2048
_BATCH = 16  # pairs scored per forward pass: one pool of 10 or 20 fits an 8 GB GPU in one or two passes


class SentenceTransformersScorer:
    """A cross-encoder from the local Hugging Face cache, on the GPU when there is one (Adapter)."""

    def __init__(self, model: str):
        try:
            import torch
            from sentence_transformers import CrossEncoder
        except ImportError as e:
            raise ConfigurationError(
                "the cross-encoder reranker needs the rerank dependency group: uv sync --group rerank"
            ) from e
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        # raw logits, not the model's sigmoid: the same order, but far-off passages do not round to equal
        # scores (0.0 or 1.0 in float32), which would turn into ties
        self._identity = torch.nn.Identity()
        try:
            self._model = CrossEncoder(
                model, max_length=MAX_LENGTH, device=self.device, local_files_only=True
            )
        except OSError as e:  # not in the local cache: a run never downloads a model
            raise ConfigurationError(f"cross-encoder {model!r} is not in the Hugging Face cache") from e

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        """One relevance score per text against `query` (higher is more relevant), in the texts' order."""
        if not texts:
            return []
        scores = self._model.predict(
            [(query, t) for t in texts],
            batch_size=_BATCH,
            show_progress_bar=False,
            activation_fn=self._identity,
        )
        return [float(s) for s in scores]
