"""Lazy bge-m3 embedding wrapper.

The model is ~2GB and takes seconds to load, so nothing imports it at module
level — call get_embedder() only when vectors are actually needed. Everything
downstream (store, pipeline) takes an `embed_fn` argument instead of importing
this directly, which is also what lets tests run without the model.
"""
from __future__ import annotations

from collections.abc import Callable

import numpy as np

EmbedFn = Callable[[list[str]], np.ndarray]

MODEL_NAME = "BAAI/bge-m3"
_model = None


def get_embedder() -> EmbedFn:
    def embed(texts: list[str]) -> np.ndarray:
        global _model
        if _model is None:
            from sentence_transformers import SentenceTransformer
            _model = SentenceTransformer(MODEL_NAME)
        return np.asarray(
            _model.encode(texts, normalize_embeddings=True, show_progress_bar=len(texts) > 8),
            dtype=np.float32,
        )
    return embed
