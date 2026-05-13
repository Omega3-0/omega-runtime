"""Lazy GGUF loader via optional llama-cpp-python."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from threading import RLock
from typing import Any

from omega_studio.inference.backends import ensure_vendor_bin_on_path

log = logging.getLogger("omega_studio.inference")


class InferenceEngine:
    """Holds at most N llama_cpp.Llama instances; unload drops references."""

    def __init__(self) -> None:
        self._handles: dict[str, Any] = {}
        self._lock = RLock()

    @staticmethod
    def _handle_key(model_id: str, *, embedding: bool = False) -> str:
        return f"{model_id}::embedding" if embedding else model_id

    @staticmethod
    def _model_id_from_key(key: str) -> str:
        return key.removesuffix("::embedding")

    def loaded_ids(self) -> list[str]:
        with self._lock:
            return sorted({self._model_id_from_key(k) for k in self._handles})

    def is_loaded(self, model_id: str, *, embedding: bool = False) -> bool:
        with self._lock:
            return self._handle_key(model_id, embedding=embedding) in self._handles

    def unload(self, model_id: str) -> None:
        with self._lock:
            self._handles.pop(model_id, None)
            self._handles.pop(self._handle_key(model_id, embedding=True), None)

    def load_gguf(
        self,
        model_id: str,
        path: str,
        *,
        n_ctx: int,
        n_gpu_layers: int,
        n_threads: int,
        n_batch: int,
        embedding: bool = False,
    ) -> Any:
        ensure_vendor_bin_on_path()
        try:
            from llama_cpp import Llama  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "llama-cpp-python is not installed. "
                "pip install llama-cpp-python or use the inference extra."
            ) from exc
        key = self._handle_key(model_id, embedding=embedding)
        with self._lock:
            if key in self._handles:
                return self._handles[key]
            log.info("loading GGUF %s <- %s", model_id, path)
            llm = Llama(
                model_path=path,
                n_ctx=n_ctx,
                n_gpu_layers=n_gpu_layers,
                n_threads=n_threads,
                n_batch=n_batch,
                embedding=embedding,
                verbose=False,
            )
            self._handles[key] = llm
            return llm

    def _loaded_handle(self, model_id: str, *, embedding: bool = False) -> Any:
        with self._lock:
            llm = self._handles.get(self._handle_key(model_id, embedding=embedding))
        if llm is None:
            raise RuntimeError("model not loaded")
        return llm

    def generate(
        self,
        model_id: str,
        prompt: str,
        *,
        max_tokens: int,
        temperature: float,
        top_p: float,
    ) -> str:
        llm = self._loaded_handle(model_id)
        out = llm(
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
        )
        choices = out.get("choices") or []
        if not choices:
            return ""
        return str(choices[0].get("text", ""))

    def generate_stream(
        self,
        model_id: str,
        prompt: str,
        *,
        max_tokens: int,
        temperature: float,
        top_p: float,
    ) -> Iterator[str]:
        """Token/text fragments from llama.cpp ``stream=True`` (OpenAI-style SSE assembly)."""
        llm = self._loaded_handle(model_id)
        stream = llm(
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            stream=True,
        )
        for output in stream:
            choices = output.get("choices") or []
            if not choices:
                continue
            ch0 = choices[0]
            piece = ch0.get("text")
            if piece:
                yield str(piece)
            delta = ch0.get("delta") or {}
            if isinstance(delta, dict):
                dtxt = delta.get("content") or delta.get("text")
                if dtxt:
                    yield str(dtxt)

    def chat_completion(
        self,
        model_id: str,
        *,
        messages: list[dict[str, Any]],
        max_tokens: int,
        temperature: float,
        top_p: float,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """OpenAI-style chat completion using the GGUF embedded chat template."""
        llm = self._loaded_handle(model_id)
        return dict(
            llm.create_chat_completion(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                **kwargs,
            )
        )

    def chat_completion_stream(
        self,
        model_id: str,
        *,
        messages: list[dict[str, Any]],
        max_tokens: int,
        temperature: float,
        top_p: float,
        **kwargs: Any,
    ) -> Iterator[dict[str, Any]]:
        """OpenAI-style streaming chat chunks using the GGUF embedded chat template."""
        llm = self._loaded_handle(model_id)
        stream = llm.create_chat_completion(
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            stream=True,
            **kwargs,
        )
        for output in stream:
            yield dict(output)

    def count_tokens(self, model_id: str, text: str) -> int:
        """Tokenize text against the loaded GGUF model and return the
        token count. Used by the streaming usage synthesizer when the
        client asked for ``stream_options.include_usage`` — llama-cpp-python
        doesn't natively emit usage in its streaming output, so the
        server computes it after the stream finishes.

        Returns 0 when the model isn't loaded or tokenization fails;
        callers treat that as "skip the synthetic usage chunk" rather
        than crash on the spec-compliant tail emission.
        """
        try:
            llm = self._loaded_handle(model_id)
        except RuntimeError:
            return 0
        tokenize = getattr(llm, "tokenize", None)
        if not callable(tokenize):
            return 0
        try:
            tokens = tokenize(text.encode("utf-8"), add_bos=False, special=True)
            return len(tokens)
        except Exception:
            return 0

    def load_onnx_embedder(
        self,
        model_id: str,
        path: str,
        *,
        pooling: str = "mean",
        normalize: bool = True,
        max_length: int = 512,
        tokenizer_path: str | None = None,
    ) -> Any:
        """Load an ONNX embedding model. Stored under the same
        ``::embedding`` key namespace as GGUF embedding handles, so
        ``is_loaded(..., embedding=True)`` works uniformly across
        formats and the resource manager evicts both the same way."""
        from omega_studio.inference.onnx_embedding import ONNXEmbedder

        key = self._handle_key(model_id, embedding=True)
        with self._lock:
            if key in self._handles:
                return self._handles[key]
            log.info("loading ONNX embedder %s <- %s", model_id, path)
            embedder = ONNXEmbedder(
                path,
                tokenizer_path=tokenizer_path,
                pooling=pooling,  # type: ignore[arg-type]
                normalize=normalize,
                max_length=max_length,
            )
            self._handles[key] = embedder
            return embedder

    def create_embedding(self, model_id: str, input_value: Any) -> dict[str, Any]:
        """OpenAI-style embedding response. Dispatches by handle type:
        GGUF llama-cpp-python instances expose ``create_embedding``
        directly; ONNX embedders expose ``embed(list[str])``. Either
        way the wire shape is the OpenAI ``object: list`` envelope."""
        handle = self._loaded_handle(model_id, embedding=True)
        if hasattr(handle, "create_embedding"):
            return dict(handle.create_embedding(input=input_value))
        # ONNX path — normalize input to a list of strings, embed,
        # shape into OpenAI envelope.
        if isinstance(input_value, str):
            inputs = [input_value]
        elif isinstance(input_value, list) and all(isinstance(s, str) for s in input_value):
            inputs = list(input_value)
        else:
            raise TypeError(
                f"ONNX embedding input must be str or list[str]; got {type(input_value).__name__}"
            )
        vectors = handle.embed(inputs)
        # Approximate token count — exact count requires per-input
        # encoding which we already did inside `embed`. Re-tokenizing
        # just for the count would double-cost; use sum of len().
        # Operators wanting exact token counts should rely on the
        # model-side reporting once tokenizer integration deepens.
        approx_tokens = sum(len(s.split()) for s in inputs)
        return {
            "object": "list",
            "data": [
                {"object": "embedding", "index": i, "embedding": vec.tolist()}
                for i, vec in enumerate(vectors)
            ],
            "model": model_id,
            "usage": {"prompt_tokens": approx_tokens, "total_tokens": approx_tokens},
        }


def estimate_vram_stub_mb(path: str, fmt: str) -> int | None:
    """Rough placeholder: size on disk / 4 as VRAM heuristic for GGUF."""
    try:
        from pathlib import Path

        p = Path(path)
        if not p.is_file():
            return None
        bytes_sz = p.stat().st_size
        if fmt.lower() in ("gguf", "ggml"):
            return max(256, int(bytes_sz / (4 * 1024 * 1024)))
        return max(128, int(bytes_sz / (8 * 1024 * 1024)))
    except Exception:
        return None
