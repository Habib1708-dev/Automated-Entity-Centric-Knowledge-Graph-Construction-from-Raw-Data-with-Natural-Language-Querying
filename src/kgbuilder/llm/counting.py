"""An embedder that counts what it sends (R117, R119).

Role in the pipeline: the stages that embed at query or index time (`kg retrieve-eval`, `kg index`) wrap the
context's embedder in it and log `embedded_texts` and `embedded_chars`.
Design: Decorator over the `Embedder` protocol. The Gemini API reports no tokens for embeddings, so the
characters sent are the only measure of a call's size (as R92 logged them); `embed_calls` is counted by the
tracker already.
"""

from .base import Embedder


class CountingEmbedder:
    """Passes every batch on to `inner` and counts the texts and characters sent."""

    def __init__(self, inner: Embedder) -> None:
        self._inner = inner
        self.texts = 0
        self.chars = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.texts += len(texts)
        self.chars += sum(len(t) for t in texts)
        return self._inner.embed(texts)
