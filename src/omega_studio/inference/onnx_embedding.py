"""ONNX embedding pipeline: tokenize → infer → pool → L2-normalize.

Wraps an ONNX session that exposes a sentence-encoder model (BGE, E5,
MiniLM, sentence-transformers, etc.) into the OpenAI ``/v1/embeddings``
contract. The pipeline is intentionally model-family-agnostic — pooling
strategy is configurable per-model and defaults to ``mean`` (which is
the right choice for ~80% of public embedding models).

Tokenizer convention: looks for ``tokenizer.json`` in the SAME
directory as the .onnx file. Hugging Face exports follow that layout
when you `optimum-cli export onnx` a sentence-transformers model. If
the tokenizer is missing the loader raises a clear error rather than
running with garbage tokens.

The embedder is created once per model load and reused for every
``embed(...)`` call; the underlying ONNX session does its own thread
pool management, so concurrent calls are safe.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal

log = logging.getLogger("omega_studio.inference.onnx_embedding")

PoolingStrategy = Literal["mean", "cls", "max"]


def _l2_normalize(vec: "Any") -> "Any":
    import numpy as np

    norms = np.linalg.norm(vec, axis=-1, keepdims=True)
    # Avoid divide-by-zero on the off-chance a token sequence produced a
    # zero vector. Returning zeros is the right behavior — the cosine
    # similarity for an all-zero vector is undefined; downstream code
    # decides how to handle it.
    norms = np.where(norms == 0, 1, norms)
    return vec / norms


def _mean_pool(last_hidden: "Any", attention_mask: "Any") -> "Any":
    """Mask-aware mean pool — the standard for E5 / sentence-transformers.

    Without the mask, padding tokens skew the pooled vector toward
    whatever the [PAD] embedding is. Standard fix: zero out padding,
    sum, divide by the unmasked count.
    """
    import numpy as np

    mask = attention_mask.astype(np.float32)[..., None]
    summed = (last_hidden * mask).sum(axis=1)
    counts = mask.sum(axis=1).clip(min=1.0)
    return summed / counts


def _cls_pool(last_hidden: "Any") -> "Any":
    """First-token pool — the standard for BGE-family models."""
    return last_hidden[:, 0, :]


def _max_pool(last_hidden: "Any", attention_mask: "Any") -> "Any":
    """Mask-aware max pool — niche; included for completeness."""
    import numpy as np

    mask = attention_mask.astype(bool)[..., None]
    masked = np.where(mask, last_hidden, np.full_like(last_hidden, -np.inf))
    return masked.max(axis=1)


class ONNXEmbedder:
    """Tokenize → infer → pool → optionally L2-normalize."""

    def __init__(
        self,
        model_path: str | Path,
        *,
        tokenizer_path: str | Path | None = None,
        pooling: PoolingStrategy = "mean",
        normalize: bool = True,
        max_length: int = 512,
    ) -> None:
        try:
            import onnxruntime as ort  # type: ignore
        except ImportError as exc:
            raise RuntimeError("onnxruntime is not installed.") from exc
        try:
            from tokenizers import Tokenizer  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "tokenizers is not installed; required for ONNX embedding "
                "models. pip install tokenizers"
            ) from exc

        self.model_path = Path(model_path)
        if tokenizer_path is None:
            tokenizer_path = self.model_path.parent / "tokenizer.json"
        self.tokenizer_path = Path(tokenizer_path)
        if not self.tokenizer_path.is_file():
            raise FileNotFoundError(
                f"tokenizer.json not found beside ONNX model. "
                f"Expected at: {self.tokenizer_path}"
            )

        self.pooling: PoolingStrategy = pooling
        self.normalize = normalize
        self.max_length = int(max_length)

        # Provider order: VitisAI (NPU) → CUDA → DirectML → CPU
        # matches `ONNXBackend` in onnx_backend.py — keeps every ONNX
        # codepath converging on the same accelerator stack.
        providers = [
            "VitisAIExecutionProvider",
            "CUDAExecutionProvider",
            "DirectMLExecutionProvider",
            "CPUExecutionProvider",
        ]
        self.session = ort.InferenceSession(str(self.model_path), providers=providers)
        self.tokenizer = Tokenizer.from_file(str(self.tokenizer_path))
        # Models exported by `optimum-cli export onnx` typically expect
        # input_ids + attention_mask + token_type_ids. We probe the
        # session inputs to know which ones to send (some encoder-only
        # models drop token_type_ids).
        self._input_names = {inp.name for inp in self.session.get_inputs()}
        log.info(
            "loaded ONNX embedder %s pooling=%s normalize=%s providers=%s",
            self.model_path.name,
            self.pooling,
            self.normalize,
            self.session.get_providers(),
        )

    @property
    def output_dim(self) -> int | None:
        """Dimension of the embedding vector if known statically.

        ONNX shape inference returns a list whose last entry is hidden
        size for sentence-encoder exports; if it's a symbolic dim we
        return None and let the caller discover it on first call.
        """
        outputs = self.session.get_outputs()
        if not outputs:
            return None
        shape = outputs[0].shape or []
        if not shape:
            return None
        last = shape[-1]
        return int(last) if isinstance(last, int) else None

    def embed(self, texts: list[str]) -> "Any":
        """Encode a batch of texts into L2-normalized embedding vectors.

        Returns a numpy array of shape ``(len(texts), hidden_dim)``.
        Single-string callers should wrap in a list.
        """
        import numpy as np

        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        # The HF tokenizer's `enable_truncation` is sticky; set it per
        # call so different `max_length` overrides Just Work without
        # leaking state across calls.
        self.tokenizer.enable_truncation(max_length=self.max_length)
        self.tokenizer.enable_padding()
        encodings = self.tokenizer.encode_batch(list(texts))
        input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
        attention_mask = np.array(
            [e.attention_mask for e in encodings], dtype=np.int64
        )
        feed: dict[str, Any] = {}
        if "input_ids" in self._input_names:
            feed["input_ids"] = input_ids
        if "attention_mask" in self._input_names:
            feed["attention_mask"] = attention_mask
        if "token_type_ids" in self._input_names:
            # Most encoder models accept all-zero token_type_ids for
            # single-segment input — that's the expected use here.
            feed["token_type_ids"] = np.zeros_like(input_ids)
        outputs = self.session.run(None, feed)
        # Convention: first output is `last_hidden_state` for
        # encoder-only exports. Sentence-transformers ONNX exports
        # sometimes have a pooled output as the SECOND tensor; we
        # always pool ourselves to keep behavior consistent.
        last_hidden = outputs[0]
        if self.pooling == "cls":
            pooled = _cls_pool(last_hidden)
        elif self.pooling == "max":
            pooled = _max_pool(last_hidden, attention_mask)
        else:
            pooled = _mean_pool(last_hidden, attention_mask)
        if self.normalize:
            pooled = _l2_normalize(pooled)
        return pooled.astype(np.float32)
