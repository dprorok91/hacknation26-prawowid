"""Lazy multilingual E5 embeddings from a pinned, local model cache."""

import asyncio
import re
from collections.abc import Sequence

import numpy as np

DEFAULT_MODEL = "intfloat/multilingual-e5-small"
DEFAULT_REVISION = "fd1525a9fd15316a2d503bf26ab031a61d056e98"
EMBEDDING_DIMENSIONS = 384


class EmbeddingUnavailable(RuntimeError):
    """The model could not produce a usable embedding."""


class E5Embedder:
    def __init__(self, model_name: str = DEFAULT_MODEL, *, revision: str | None = None):
        if revision is None and model_name == DEFAULT_MODEL:
            revision = DEFAULT_REVISION
        if revision is None or not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError("E5 models require an immutable model revision")
        self.model_name = model_name
        self.revision = revision
        self._model = None
        self._slot = asyncio.Semaphore(1)
        self._pending: asyncio.Task[list[list[float]]] | None = None

    @property
    def model_id(self) -> str:
        return f"{self.model_name}@{self.revision}"

    async def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        return await self._embed(texts, prefix="query")

    async def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        return await self._embed(texts, prefix="passage")

    async def _embed(self, texts: Sequence[str], *, prefix: str) -> list[list[float]]:
        if not texts:
            return []
        await self._slot.acquire()
        task = asyncio.create_task(asyncio.to_thread(self._encode, texts, prefix))
        self._pending = task
        task.add_done_callback(self._release)
        # A cancelled caller cannot interrupt native inference. Keep the slot until it finishes.
        return await asyncio.shield(task)

    def _release(self, task: asyncio.Task[list[list[float]]]) -> None:
        self._pending = None
        self._slot.release()
        if not task.cancelled():
            task.exception()

    def _encode(self, texts: Sequence[str], prefix: str) -> list[list[float]]:
        try:
            if self._model is None:
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(
                    self.model_name,
                    revision=self.revision,
                    local_files_only=True,
                    trust_remote_code=False,
                    token=False,
                    model_kwargs={"use_safetensors": True},
                )
            matrix = np.asarray(
                self._model.encode(
                    [f"{prefix}: {text}" for text in texts],
                    normalize_embeddings=True,
                    convert_to_numpy=True,
                    show_progress_bar=False,
                ),
                dtype=np.float32,
            )
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            raise EmbeddingUnavailable("The local E5 model is unavailable.") from exc
        if (
            matrix.shape != (len(texts), EMBEDDING_DIMENSIONS)
            or not np.isfinite(matrix).all()
            or not np.any(matrix, axis=1).all()
        ):
            raise EmbeddingUnavailable("E5 returned an unusable embedding.")
        return matrix.tolist()
