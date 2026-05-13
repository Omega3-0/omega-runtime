from __future__ import annotations

import sys
import threading
import time
import types
from typing import Any

from omega_studio.inference.engine import InferenceEngine


def test_load_gguf_serializes_first_touch(monkeypatch) -> None:
    calls = 0
    started = threading.Barrier(2)

    class FakeLlama:
        def __init__(self, **_kwargs: Any) -> None:
            nonlocal calls
            calls += 1
            try:
                started.wait(timeout=0.25)
            except threading.BrokenBarrierError:
                pass
            time.sleep(0.05)

    fake_mod = types.ModuleType("llama_cpp")
    fake_mod.Llama = FakeLlama
    monkeypatch.setitem(sys.modules, "llama_cpp", fake_mod)
    monkeypatch.setattr("omega_studio.inference.engine.ensure_vendor_bin_on_path", lambda: None)

    engine = InferenceEngine()
    results: list[Any] = []

    def load() -> None:
        results.append(
            engine.load_gguf(
                "m",
                "m.gguf",
                n_ctx=128,
                n_gpu_layers=0,
                n_threads=1,
                n_batch=1,
            )
        )

    t1 = threading.Thread(target=load)
    t2 = threading.Thread(target=load)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert calls == 1
    assert len(results) == 2
    assert results[0] is results[1]


def test_chat_completion_uses_llama_chat_api() -> None:
    class FakeLlama:
        def __init__(self) -> None:
            self.kwargs: dict[str, Any] | None = None

        def create_chat_completion(self, **kwargs: Any) -> dict[str, Any]:
            self.kwargs = kwargs
            return {
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }

    llm = FakeLlama()
    engine = InferenceEngine()
    engine._handles["m"] = llm
    messages = [{"role": "user", "content": "hi"}]

    out = engine.chat_completion(
        "m",
        messages=messages,
        max_tokens=8,
        temperature=0.2,
        top_p=0.9,
        tools=[{"type": "function"}],
    )

    assert out["usage"]["total_tokens"] == 2
    assert llm.kwargs is not None
    assert llm.kwargs["messages"] == messages
    assert llm.kwargs["tools"] == [{"type": "function"}]


def test_embedding_mode_uses_separate_llama_handle(monkeypatch) -> None:
    inits: list[dict[str, Any]] = []

    class FakeLlama:
        def __init__(self, **kwargs: Any) -> None:
            inits.append(kwargs)
            self.embedding = bool(kwargs.get("embedding"))

        def create_embedding(self, **_kwargs: Any) -> dict[str, Any]:
            return {"data": [{"embedding": [1.0]}]}

    fake_mod = types.ModuleType("llama_cpp")
    fake_mod.Llama = FakeLlama
    monkeypatch.setitem(sys.modules, "llama_cpp", fake_mod)
    monkeypatch.setattr("omega_studio.inference.engine.ensure_vendor_bin_on_path", lambda: None)

    engine = InferenceEngine()
    chat_handle = engine.load_gguf(
        "m",
        "m.gguf",
        n_ctx=128,
        n_gpu_layers=0,
        n_threads=1,
        n_batch=1,
    )
    embed_handle = engine.load_gguf(
        "m",
        "m.gguf",
        n_ctx=128,
        n_gpu_layers=0,
        n_threads=1,
        n_batch=1,
        embedding=True,
    )

    assert chat_handle is not embed_handle
    assert inits[0].get("embedding") is False
    assert inits[1].get("embedding") is True
    assert engine.create_embedding("m", "hello")["data"][0]["embedding"] == [1.0]
