"""API key gate (``OMEGA_API_KEY``) and OpenAI-style SSE streaming shape."""

from __future__ import annotations

import importlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import pytest
from starlette.testclient import TestClient

from omega_studio.config import ModelRecord, RegistryFile, StudioSettings


def _stub_registry() -> RegistryFile:
    return RegistryFile(
        version=1,
        model_folders=[],
        models={"stub-model": ModelRecord(path=r"C:\fake\stub.gguf", format="gguf")},
        settings=StudioSettings(),
    )


@pytest.fixture
def patched_registry(monkeypatch):
    app_mod = importlib.import_module("omega_studio.server.app")
    import omega_studio.server.routes_v1 as rv

    monkeypatch.setattr(app_mod, "load_registry", _stub_registry)
    monkeypatch.setattr(rv, "load_registry", _stub_registry)
    monkeypatch.setattr(rv, "apply_env_overrides", lambda r: r)


class _FakeEngine:
    def __init__(self) -> None:
        self.last_chat_kwargs: dict[str, Any] | None = None

    def is_loaded(self, model_id: str) -> bool:
        return True

    def loaded_ids(self) -> list[str]:
        return []

    def unload(self, model_id: str) -> None:
        pass

    def load_gguf(self, *args: Any, **kwargs: Any) -> None:
        self.last_load_kwargs = dict(kwargs)
        return None

    def create_embedding(self, model_id: str, input_value: Any) -> dict[str, Any]:
        self.last_embedding = {"model_id": model_id, "input": input_value}
        return {
            "object": "list",
            "model": model_id,
            "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}],
            "usage": {"prompt_tokens": 2, "total_tokens": 2},
        }

    def generate_stream(self, *args: Any, **kwargs: Any):
        yield "Hel"
        yield "lo"

    def chat_completion(self, model_id: str, **kwargs: Any) -> dict[str, Any]:
        self.last_chat_kwargs = {"model_id": model_id, **kwargs}
        content = kwargs.get("fake_content", "templated")
        return {
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
        }

    def chat_completion_stream(self, *args: Any, **kwargs: Any):
        yield {
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": "Hel"}}]
        }
        yield {"choices": [{"index": 0, "delta": {"content": "lo"}}]}


def test_omega_api_key_optional(patched_registry, monkeypatch):
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    app = create_app()
    with TestClient(app) as client:
        r = client.get("/v1/models")
        assert r.status_code == 200


def test_cors_allows_localhost_preflight(patched_registry, monkeypatch):
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    app = create_app()
    with TestClient(app) as client:
        r = client.options(
            "/v1/models",
            headers={
                "Origin": "http://127.0.0.1:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"


def test_cors_preflight_bypasses_api_key_gate(patched_registry, monkeypatch):
    """Regression: when OMEGA_API_KEY is set, CORS preflight (OPTIONS +
    Access-Control-Request-Method header) MUST bypass the auth middleware
    so browser clients can complete the preflight handshake. CORS spec
    forbids credentials on preflight — the actual follow-up request is
    where auth gets enforced.

    Original bug: auth middleware ran before CORS, rejecting OPTIONS
    with 401, leaving the browser unable to call the API at all when a
    key was set."""
    monkeypatch.setenv("OMEGA_API_KEY", "test-bearer")
    from omega_studio.server.app import create_app

    app = create_app()
    with TestClient(app) as client:
        # Allowed origin → 200 + allow-origin (preflight passes auth bypass)
        r_allowed = client.options(
            "/v1/models",
            headers={
                "Origin": "http://127.0.0.1:5173",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        # Denied origin → 400 (CORSMiddleware rejects), NOT 401 (no auth gate hit)
        r_denied = client.options(
            "/v1/models",
            headers={
                "Origin": "https://evil.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        # Non-preflight OPTIONS (no Access-Control-Request-Method header)
        # MUST still be auth-gated — bypass is preflight-only.
        r_plain_options = client.options(
            "/v1/models",
            headers={"Origin": "http://127.0.0.1:5173"},
        )
        # Actual non-preflight request still gates normally
        r_actual_no_bearer = client.get("/v1/models")
        r_actual_bearer = client.get(
            "/v1/models",
            headers={"Authorization": "Bearer test-bearer"},
        )

    assert r_allowed.status_code == 200
    assert r_allowed.headers.get("access-control-allow-origin") == "http://127.0.0.1:5173"
    assert r_denied.status_code == 400
    assert "access-control-allow-origin" not in r_denied.headers
    assert r_plain_options.status_code == 401
    assert r_actual_no_bearer.status_code == 401
    assert r_actual_bearer.status_code == 200


def test_request_size_limit_returns_413(patched_registry, monkeypatch):
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    monkeypatch.setenv("OMEGA_MAX_REQUEST_BYTES", "20")
    from omega_studio.server.app import create_app

    app = create_app()
    with TestClient(app) as client:
        r = client.post(
            "/v1/chat/completions",
            json={
                "model": "stub-model",
                "messages": [{"role": "user", "content": "x" * 100}],
            },
        )
    assert r.status_code == 413
    assert r.json()["detail"] == "request_too_large"


def test_chat_completion_rejects_empty_messages(patched_registry, monkeypatch):
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    app = create_app()
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions", json={"model": "stub-model", "messages": []})
    assert r.status_code == 422


def test_omega_api_key_rejects_without_bearer(patched_registry, monkeypatch):
    monkeypatch.setenv("OMEGA_API_KEY", "test-secret-key")
    from omega_studio.server.app import create_app

    app = create_app()
    with TestClient(app) as client:
        assert client.get("/v1/models").status_code == 401
        assert client.get("/admin/status").status_code == 401
        assert client.get("/health").status_code == 200


def test_omega_api_key_accepts_bearer(patched_registry, monkeypatch):
    monkeypatch.setenv("OMEGA_API_KEY", "test-secret-key")
    from omega_studio.server.app import create_app

    app = create_app()
    headers = {"Authorization": "Bearer test-secret-key"}
    with TestClient(app) as client:
        assert client.get("/v1/models", headers=headers).status_code == 200
        assert client.get("/admin/status", headers=headers).status_code == 200


def test_omega_api_key_wrong_token(patched_registry, monkeypatch):
    monkeypatch.setenv("OMEGA_API_KEY", "right")
    from omega_studio.server.app import create_app

    app = create_app()
    with TestClient(app) as client:
        r = client.get("/v1/models", headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 403


def test_chat_completion_stream_sse_shape(patched_registry, monkeypatch):
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    app = create_app()
    with TestClient(app) as client:
        client.app.state.engine = _FakeEngine()
        body = {
            "model": "stub-model",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
            "max_tokens": 8,
        }
        with client.stream("POST", "/v1/chat/completions", json=body) as resp:
            assert resp.status_code == 200
            raw = "".join(resp.iter_text())
    lines = [ln for ln in raw.split("\n") if ln.startswith("data: ")]
    assert any("chat.completion.chunk" in ln for ln in lines)
    payloads = []
    for ln in lines:
        payload = ln[len("data: ") :].strip()
        if payload == "[DONE]":
            continue
        payloads.append(json.loads(payload))
    assert payloads[-1]["choices"][0].get("finish_reason") == "stop"
    # First delta carries assistant role; subsequent carry content tokens from fake engine
    texts = []
    for p in payloads[:-1]:
        d = p["choices"][0].get("delta") or {}
        if "content" in d:
            texts.append(d["content"])
    assert "".join(texts) == "Hello"


def test_chat_completion_stream_chunks_share_one_id(patched_registry, monkeypatch):
    """Regression: every chunk in a single streaming completion MUST
    share the same chatcmpl-<uuid> id. llama-cpp-python emits chunks
    with its own ids; without forcing OUR cmpl_id in _normalize_chat_chunk,
    intermediate chunks (relayed from llama) carry one id while our
    manually-constructed chunks (flush tail, end marker) carry another.
    Spec compliance + dedup-by-id client compat."""
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    class MultiIdStreamEngine(_FakeEngine):
        def chat_completion_stream(self, *args: Any, **kwargs: Any):
            # Simulate llama-cpp-python's behavior: each chunk has its
            # OWN chatcmpl id (different from ours).
            yield {"id": "llama-id-A", "choices": [{"index": 0, "delta": {"role": "assistant"}}]}
            yield {"id": "llama-id-B", "choices": [{"index": 0, "delta": {"content": "hi"}}]}
            yield {"id": "llama-id-C", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}

    app = create_app()
    with TestClient(app) as client:
        client.app.state.engine = MultiIdStreamEngine()
        body = {
            "model": "stub-model",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        }
        with client.stream("POST", "/v1/chat/completions", json=body) as resp:
            assert resp.status_code == 200
            raw = "".join(resp.iter_text())
    payloads = []
    for ln in raw.split("\n"):
        if not ln.startswith("data: "):
            continue
        payload = ln[len("data: "):].strip()
        if payload == "[DONE]":
            continue
        payloads.append(json.loads(payload))

    ids = {p["id"] for p in payloads}
    assert len(ids) == 1, f"expected single id, got {ids}"
    sole_id = ids.pop()
    assert sole_id.startswith("chatcmpl-"), sole_id
    # None of the llama-side ids leaked into the stream
    assert "llama-id-A" not in sole_id
    assert "llama-id-B" not in sole_id
    assert "llama-id-C" not in sole_id


def test_chat_completion_stream_finish_reason_only_at_end(patched_registry, monkeypatch):
    """Regression: streaming completions emit EXACTLY ONE finish_reason
    chunk, and it's the FINAL chunk (before [DONE]). llama-cpp-python
    emits its own end marker with finish_reason=stop; without stripping
    it from intermediate relays, hide_thinking-filtered streams send
    finish_reason BEFORE the flush tail — strict OpenAI clients treat
    the first finish_reason as end-of-message and discard subsequent
    content."""
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    class FinishReasonStreamEngine(_FakeEngine):
        def chat_completion_stream(self, *args: Any, **kwargs: Any):
            yield {"choices": [{"index": 0, "delta": {"role": "assistant"}}]}
            yield {"choices": [{"index": 0, "delta": {"content": "<think>reasoning</think>visible"}}]}
            # llama-cpp-python's native end marker — must be stripped
            yield {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}

    app = create_app()
    with TestClient(app) as client:
        client.app.state.engine = FinishReasonStreamEngine()
        body = {
            "model": "stub-model",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
            "hide_thinking": True,
        }
        with client.stream("POST", "/v1/chat/completions", json=body) as resp:
            raw = "".join(resp.iter_text())

    payloads = []
    for ln in raw.split("\n"):
        if not ln.startswith("data: "):
            continue
        payload = ln[len("data: "):].strip()
        if payload == "[DONE]":
            continue
        payloads.append(json.loads(payload))

    # Find which chunks carry finish_reason
    finish_chunks = [
        i for i, p in enumerate(payloads)
        if any((c.get("finish_reason") == "stop") for c in p.get("choices", []))
    ]
    assert len(finish_chunks) == 1, (
        f"expected exactly one finish_reason chunk, got {len(finish_chunks)} "
        f"at indexes {finish_chunks}"
    )
    # And it's the last chunk
    assert finish_chunks[0] == len(payloads) - 1, (
        f"finish_reason chunk should be last; got at index {finish_chunks[0]} "
        f"of {len(payloads)} total"
    )
    # AND the visible content (flush tail) appears BEFORE the finish chunk
    content_indexes = [
        i for i, p in enumerate(payloads)
        if any(((c.get("delta") or {}).get("content") == "visible") for c in p.get("choices", []))
    ]
    assert content_indexes, "visible content not present in stream"
    assert content_indexes[-1] < finish_chunks[0], (
        "visible content arrived AFTER finish_reason — spec violation"
    )


def test_chat_completion_stream_forwards_usage_tail(patched_registry, monkeypatch):
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    class UsageStreamEngine(_FakeEngine):
        def chat_completion_stream(self, *args: Any, **kwargs: Any):
            yield {"choices": [{"index": 0, "delta": {"content": "ok"}}]}
            yield {
                "choices": [],
                "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
            }

    app = create_app()
    with TestClient(app) as client:
        client.app.state.engine = UsageStreamEngine()
        body = {
            "model": "stub-model",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        with client.stream("POST", "/v1/chat/completions", json=body) as resp:
            assert resp.status_code == 200
            raw = "".join(resp.iter_text())
    payloads = [
        json.loads(ln[len("data: ") :])
        for ln in raw.split("\n")
        if ln.startswith("data: ") and ln[len("data: ") :].strip() != "[DONE]"
    ]
    assert any(p.get("usage", {}).get("total_tokens") == 6 for p in payloads)


# ─────────────────────────────────────────────────────────────────
# #583 — OpenAI spec for stream_options.include_usage
# ─────────────────────────────────────────────────────────────────
# Per OpenAI streaming spec: when stream_options.include_usage=true,
# the server MUST send exactly one tail chunk with choices=[] and the
# populated usage block, AFTER the final content/finish chunk and
# BEFORE the [DONE] sentinel. All earlier chunks carry no usage.
# Strict clients (openai-python, langchain) parse this contract.

def _parse_sse_stream(raw: str) -> list[dict[str, Any]]:
    return [
        json.loads(ln[len("data: ") :])
        for ln in raw.split("\n")
        if ln.startswith("data: ") and ln[len("data: ") :].strip() != "[DONE]"
    ]


def test_chat_response_model_id_overrides_engine_value(patched_registry, monkeypatch):
    """Regression: llama-cpp-python sets `model` to the .gguf file
    path on disk; we MUST override with the registered model id so
    we don't leak local paths to API clients."""
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    class PathLeakEngine(_FakeEngine):
        def chat_completion(self, model_id: str, **kwargs: Any) -> dict[str, Any]:
            return {
                "model": "C:/some/local/path/model.gguf",  # leak source
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }

    app = create_app()
    with TestClient(app) as client:
        client.app.state.engine = PathLeakEngine()
        r = client.post(
            "/v1/chat/completions",
            json={
                "model": "stub-model",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
    assert r.status_code == 200
    body = r.json()
    assert body["model"] == "stub-model", (
        f"engine's model field leaked: {body['model']!r}"
    )


def test_chat_stream_chunk_model_id_overrides_engine_value(patched_registry, monkeypatch):
    """Streaming chunks: every chunk's `model` field must be the
    registered model id, not whatever llama-cpp-python sets it to
    (the .gguf file path). Caught by live model testing — chunks
    were leaking absolute paths even when non-streaming responses
    were clean."""
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    class StreamPathLeakEngine(_FakeEngine):
        def chat_completion_stream(self, *args: Any, **kwargs: Any):
            yield {
                "model": "C:/leak/path.gguf",
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": "ok"}}],
            }
            yield {
                "model": "C:/leak/path.gguf",
                "choices": [{"index": 0, "delta": {"content": "!"}}],
            }

    app = create_app()
    with TestClient(app) as client:
        client.app.state.engine = StreamPathLeakEngine()
        with client.stream(
            "POST",
            "/v1/chat/completions",
            json={
                "model": "stub-model",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            },
        ) as resp:
            raw = "".join(resp.iter_text())
    payloads = [
        json.loads(ln[len("data: ") :])
        for ln in raw.split("\n")
        if ln.startswith("data: ") and ln[len("data: ") :].strip() != "[DONE]"
    ]
    models_seen = {p.get("model") for p in payloads if "model" in p}
    assert models_seen == {"stub-model"}, (
        f"streaming chunks leaked model field: {models_seen}"
    )


def test_embedding_response_model_id_overrides_engine_value(patched_registry, monkeypatch):
    """Same path-leak guard for the embeddings endpoint — registry id
    wins over the engine's `model` field."""
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    class PathLeakEmbedEngine(_FakeEngine):
        def create_embedding(self, model_id: str, input_value: Any) -> dict[str, Any]:
            return {
                "object": "list",
                "model": "F:/abs/path/embedding-model.gguf",
                "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}],
                "usage": {"prompt_tokens": 2, "total_tokens": 2},
            }

    app = create_app()
    with TestClient(app) as client:
        client.app.state.engine = PathLeakEmbedEngine()
        r = client.post(
            "/v1/embeddings",
            json={"model": "stub-model", "input": "hi"},
        )
    assert r.status_code == 200
    assert r.json()["model"] == "stub-model"


def test_stream_synthesizes_usage_when_engine_lacks_native_usage(
    patched_registry, monkeypatch
):
    """Real-world fallback: llama-cpp-python (any version we ship)
    doesn't emit a native `usage` field in streaming output. When
    the client requested `stream_options.include_usage=true` we MUST
    synthesize the usage chunk server-side using engine.count_tokens()
    rather than silently omitting it — that's a spec violation that
    breaks token-billing clients."""
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    class NoUsageEngine(_FakeEngine):
        def chat_completion_stream(self, *args: Any, **kwargs: Any):
            yield {"choices": [{"index": 0, "delta": {"role": "assistant", "content": "Hello"}}]}
            yield {"choices": [{"index": 0, "delta": {"content": " world"}}]}
            # Never emits a chunk with `usage` — same as real llama-cpp.

        def count_tokens(self, model_id: str, text: str) -> int:
            # Deterministic stub: 1 token per whitespace-split word
            return len([w for w in text.split() if w])

    app = create_app()
    with TestClient(app) as client:
        client.app.state.engine = NoUsageEngine()
        with client.stream(
            "POST",
            "/v1/chat/completions",
            json={
                "model": "stub-model",
                "messages": [{"role": "user", "content": "Greet me politely please"}],
                "stream": True,
                "stream_options": {"include_usage": True},
            },
        ) as resp:
            raw = "".join(resp.iter_text())
    payloads = [
        json.loads(ln[len("data: ") :])
        for ln in raw.split("\n")
        if ln.startswith("data: ") and ln[len("data: ") :].strip() != "[DONE]"
    ]
    usage_chunks = [
        p for p in payloads
        if isinstance(p.get("usage"), dict)
        and p["usage"].get("total_tokens") is not None
    ]
    assert len(usage_chunks) == 1, (
        f"expected exactly one synthesized usage chunk; got {len(usage_chunks)}"
    )
    usage = usage_chunks[0]["usage"]
    # Prompt was "user: Greet me politely please" → 5 words after split
    # Accumulated content was "Hello world" → 2 words
    assert usage["prompt_tokens"] > 0
    assert usage["completion_tokens"] == 2
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]
    # Dedicated chunk has empty choices per spec
    assert usage_chunks[0]["choices"] == []


def test_stream_no_usage_when_engine_lacks_count_tokens(
    patched_registry, monkeypatch
):
    """Defensive: when the engine has NO count_tokens method (e.g.
    a future backend), the synthesizer must NOT crash and must NOT
    emit a usage chunk with zeros — just skip. Better than lying
    about token counts."""
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    class NoTokenizerEngine(_FakeEngine):
        def chat_completion_stream(self, *args: Any, **kwargs: Any):
            yield {"choices": [{"index": 0, "delta": {"content": "ok"}}]}

    app = create_app()
    with TestClient(app) as client:
        client.app.state.engine = NoTokenizerEngine()
        with client.stream(
            "POST",
            "/v1/chat/completions",
            json={
                "model": "stub-model",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
                "stream_options": {"include_usage": True},
            },
        ) as resp:
            raw = "".join(resp.iter_text())
    payloads = [
        json.loads(ln[len("data: ") :])
        for ln in raw.split("\n")
        if ln.startswith("data: ") and ln[len("data: ") :].strip() != "[DONE]"
    ]
    usage_chunks = [
        p for p in payloads
        if isinstance(p.get("usage"), dict)
        and p["usage"].get("total_tokens") is not None
    ]
    assert len(usage_chunks) == 0, (
        "must not emit synthetic usage chunk when engine has no tokenizer"
    )
    # Stream itself still has to terminate cleanly
    assert "[DONE]" in raw


def test_stream_options_not_forwarded_to_engine(patched_registry, monkeypatch):
    """Regression: llama-cpp-python's `create_chat_completion` does NOT
    accept `stream_options` as a kwarg (raises TypeError mid-stream).
    We capture include_usage server-side; the engine must never see it.
    Caught by live model testing — fake engines accept **kwargs and
    swallowed this for months."""
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    seen_kwargs: dict[str, Any] = {}

    class StrictEngine(_FakeEngine):
        def chat_completion_stream(self, *args: Any, **kwargs: Any):
            seen_kwargs.update(kwargs)
            yield {"choices": [{"index": 0, "delta": {"content": "ok"}}]}
            yield {
                "choices": [],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }

    app = create_app()
    with TestClient(app) as client:
        client.app.state.engine = StrictEngine()
        body = {
            "model": "stub-model",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        with client.stream("POST", "/v1/chat/completions", json=body) as resp:
            list(resp.iter_text())
    assert "stream_options" not in seen_kwargs, (
        f"stream_options leaked to engine: {seen_kwargs}"
    )


def test_stream_include_usage_emits_dedicated_tail_chunk(patched_registry, monkeypatch):
    """include_usage=True → exactly one chunk has populated usage,
    that chunk has choices=[], and it sits between the finish_reason
    chunk and [DONE]."""
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    class UsageStreamEngine(_FakeEngine):
        def chat_completion_stream(self, *args: Any, **kwargs: Any):
            yield {"choices": [{"index": 0, "delta": {"role": "assistant", "content": "hi"}}]}
            yield {"choices": [{"index": 0, "delta": {"content": " there"}}]}
            yield {
                "choices": [],
                "usage": {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9},
            }

    app = create_app()
    with TestClient(app) as client:
        client.app.state.engine = UsageStreamEngine()
        body = {
            "model": "stub-model",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        with client.stream("POST", "/v1/chat/completions", json=body) as resp:
            assert resp.status_code == 200
            raw_text = "".join(resp.iter_text())
    payloads = _parse_sse_stream(raw_text)
    usage_chunks = [
        i for i, p in enumerate(payloads)
        if isinstance(p.get("usage"), dict) and p["usage"].get("total_tokens") is not None
    ]
    assert len(usage_chunks) == 1, (
        f"expected exactly one usage chunk; got {len(usage_chunks)} at {usage_chunks}"
    )
    usage_chunk = payloads[usage_chunks[0]]
    assert usage_chunk["choices"] == [], "usage chunk MUST have choices=[]"
    assert usage_chunk["usage"]["total_tokens"] == 9
    assert usage_chunk["usage"]["prompt_tokens"] == 7
    assert usage_chunk["usage"]["completion_tokens"] == 2
    finish_chunks = [
        i for i, p in enumerate(payloads)
        if any(
            isinstance(c, dict) and c.get("finish_reason") is not None
            for c in p.get("choices", [])
        )
    ]
    assert finish_chunks, "no finish_reason chunk present"
    assert finish_chunks[0] < usage_chunks[0], (
        "usage chunk must come AFTER the finish_reason chunk per spec"
    )


def test_stream_include_usage_intermediate_chunks_have_no_usage(patched_registry, monkeypatch):
    """Spec: intermediate chunks have usage=null/absent when
    include_usage=true. We strip it from forwarded chunks so the
    dedicated tail is the single source of truth."""
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    class LeakyUsageEngine(_FakeEngine):
        def chat_completion_stream(self, *args: Any, **kwargs: Any):
            # Some llama-cpp-python versions sprinkle usage on intermediate
            # chunks too. Make sure we suppress that on the wire.
            yield {
                "choices": [{"index": 0, "delta": {"content": "leak"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1},
            }
            yield {
                "choices": [],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
            }

    app = create_app()
    with TestClient(app) as client:
        client.app.state.engine = LeakyUsageEngine()
        body = {
            "model": "stub-model",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        with client.stream("POST", "/v1/chat/completions", json=body) as resp:
            raw_text = "".join(resp.iter_text())
    payloads = _parse_sse_stream(raw_text)
    populated = [
        p for p in payloads
        if isinstance(p.get("usage"), dict)
        and p["usage"].get("total_tokens") is not None
    ]
    assert len(populated) == 1, "duplicate usage across chunks — spec violation"
    # And the one populated chunk is the dedicated tail (choices=[])
    assert populated[0]["choices"] == []


def test_stream_without_include_usage_emits_no_usage_chunk(patched_registry, monkeypatch):
    """When include_usage is not requested (default behavior), we
    don't synthesize a tail usage chunk. Backward-compat with clients
    that don't set stream_options."""
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    app = create_app()
    with TestClient(app) as client:
        client.app.state.engine = _FakeEngine()  # no usage in stream
        body = {
            "model": "stub-model",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        }
        with client.stream("POST", "/v1/chat/completions", json=body) as resp:
            assert resp.status_code == 200
            raw_text = "".join(resp.iter_text())
    payloads = _parse_sse_stream(raw_text)
    usage_present = [
        p for p in payloads
        if isinstance(p.get("usage"), dict)
        and p["usage"].get("total_tokens") is not None
    ]
    assert not usage_present, (
        f"unexpected usage chunk(s) when include_usage not set: {usage_present}"
    )


# ─────────────────────────────────────────────────────────────────
# #588 — SSE stream cleanup paths
# ─────────────────────────────────────────────────────────────────
# When a stream ends — for ANY reason: success, server error, or
# client disconnect — gen()'s `finally:` MUST call close() on the
# underlying engine iterator. Without that, llama-cpp-python's
# sequence slot + KV cache stay pinned until the next GC sweep
# (potentially minutes under load), starving incoming requests.
#
# Disconnect-mid-stream is hard to simulate reliably under
# TestClient (asyncio.to_thread + Starlette task-group cancellation
# interact unpredictably with httpx context exit timing). We test
# the more controllable cleanup paths instead: normal exhaustion +
# mid-stream exception. Both run gen()'s finally; the disconnect
# path is the same finally with a different trigger.

def test_stream_calls_close_on_iterator_after_normal_completion(
    patched_registry, monkeypatch
):
    """gen() finally must call iterator.close() even on success —
    that's what releases llama's sequence slot in production."""
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    state = {"close_calls": 0}

    class TrackingStream:
        def __init__(self):
            self._chunks = iter([
                {"choices": [{"index": 0, "delta": {"role": "assistant", "content": "ok"}}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            ])

        def __iter__(self):
            return self

        def __next__(self):
            return next(self._chunks)

        def close(self):
            state["close_calls"] += 1

    class TrackingEngine(_FakeEngine):
        def chat_completion_stream(self, *args: Any, **kwargs: Any):
            return TrackingStream()

    app = create_app()
    with TestClient(app) as client:
        client.app.state.engine = TrackingEngine()
        body = {
            "model": "stub-model",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        }
        with client.stream("POST", "/v1/chat/completions", json=body) as resp:
            raw_text = "".join(resp.iter_text())
    assert "[DONE]" in raw_text
    assert state["close_calls"] >= 1, (
        "iterator.close() must run in gen() finally — even on success — "
        "to release engine slot resources"
    )


def test_stream_calls_close_on_iterator_after_engine_exception(
    patched_registry, monkeypatch
):
    """gen() finally must call iterator.close() when the engine
    iterator raises mid-stream too. This is the same cleanup path
    a client disconnect triggers."""
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    state = {"close_calls": 0, "yielded": 0}

    class ExplodingStream:
        def __iter__(self):
            return self

        def __next__(self):
            state["yielded"] += 1
            if state["yielded"] >= 2:
                raise RuntimeError("engine blew up mid-stream")
            return {"choices": [{"index": 0, "delta": {"content": "ok"}}]}

        def close(self):
            state["close_calls"] += 1

    class ExplodingEngine(_FakeEngine):
        def chat_completion_stream(self, *args: Any, **kwargs: Any):
            return ExplodingStream()

    app = create_app()
    with TestClient(app) as client:
        client.app.state.engine = ExplodingEngine()
        body = {
            "model": "stub-model",
            "messages": [{"role": "user", "content": "go"}],
            "stream": True,
        }
        with client.stream("POST", "/v1/chat/completions", json=body) as resp:
            raw_text = "".join(resp.iter_text())
    # Error chunk and [DONE] should still have been emitted
    assert "engine blew up mid-stream" in raw_text
    assert "[DONE]" in raw_text
    # And the iterator was closed despite the exception
    assert state["close_calls"] >= 1, (
        f"iterator.close() not called on engine-exception path; "
        f"close_calls={state['close_calls']}"
    )


def test_stream_iterator_without_close_method_does_not_crash(
    patched_registry, monkeypatch
):
    """Defensive: some engines (mocks, simple generators called via
    iter()) won't have a .close attribute. Our finally must not
    blow up trying to call it."""
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    # Plain generator function — Python gives it close() by default,
    # but use a list iterator which doesn't have close() to cover the
    # getattr-not-callable branch.
    class NoCloseIterator:
        def __init__(self):
            self._items = iter([
                {"choices": [{"index": 0, "delta": {"content": "ok"}}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            ])

        def __iter__(self):
            return self

        def __next__(self):
            return next(self._items)
        # NOTE: deliberately no `close` method

    class NoCloseEngine(_FakeEngine):
        def chat_completion_stream(self, *args: Any, **kwargs: Any):
            return NoCloseIterator()

    app = create_app()
    with TestClient(app) as client:
        client.app.state.engine = NoCloseEngine()
        body = {
            "model": "stub-model",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        }
        with client.stream("POST", "/v1/chat/completions", json=body) as resp:
            assert resp.status_code == 200
            raw_text = "".join(resp.iter_text())
    assert "[DONE]" in raw_text  # didn't crash trying to call close()


def test_stream_include_usage_chunk_shares_completion_id(patched_registry, monkeypatch):
    """The dedicated usage chunk must share the same chatcmpl id as
    every other chunk in the stream — clients dedupe by id."""
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    class UsageStreamEngine(_FakeEngine):
        def chat_completion_stream(self, *args: Any, **kwargs: Any):
            yield {"id": "llama-x", "choices": [{"index": 0, "delta": {"content": "ok"}}]}
            yield {
                "id": "llama-y",
                "choices": [],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            }

    app = create_app()
    with TestClient(app) as client:
        client.app.state.engine = UsageStreamEngine()
        body = {
            "model": "stub-model",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        with client.stream("POST", "/v1/chat/completions", json=body) as resp:
            raw_text = "".join(resp.iter_text())
    payloads = _parse_sse_stream(raw_text)
    ids = {p["id"] for p in payloads if "id" in p}
    assert len(ids) == 1, f"chunks must share one id; got {ids}"
    only_id = next(iter(ids))
    assert only_id.startswith("chatcmpl-"), f"unexpected id format: {only_id}"


def test_chat_completion_stream_hides_thinking_blocks(patched_registry, monkeypatch):
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    class ThinkingStreamEngine(_FakeEngine):
        def chat_completion_stream(self, *args: Any, **kwargs: Any):
            yield {
                "choices": [
                    {"index": 0, "delta": {"role": "assistant", "content": "<think>sec"}}
                ]
            }
            yield {"choices": [{"index": 0, "delta": {"content": "ret</think>visible"}}]}

    app = create_app()
    with TestClient(app) as client:
        client.app.state.engine = ThinkingStreamEngine()
        body = {
            "model": "stub-model",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
            "hide_thinking": True,
        }
        with client.stream("POST", "/v1/chat/completions", json=body) as resp:
            assert resp.status_code == 200
            raw = "".join(resp.iter_text())
    assert "secret" not in raw
    assert "<think>" not in raw
    assert "visible" in raw


def test_chat_completion_nonstream_times_out(patched_registry, monkeypatch):
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    monkeypatch.setenv("OMEGA_INFERENCE_TIMEOUT_S", "0.01")
    from omega_studio.server.app import create_app

    class SlowEngine(_FakeEngine):
        def chat_completion(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            time.sleep(0.05)
            return super().chat_completion(*args, **kwargs)

    app = create_app()
    with TestClient(app) as client:
        client.app.state.engine = SlowEngine()
        r = client.post(
            "/v1/chat/completions",
            json={"model": "stub-model", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert r.status_code == 504
    assert "timed out" in r.json()["detail"]


def test_chat_completion_uses_chat_template_and_preserves_openai_fields(
    patched_registry, monkeypatch
):
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    # JSON-mode contract now enforces parseable content (#584), so this
    # integration test — which sends response_format=json_object — needs
    # a stub that returns valid JSON. The other field passthroughs are
    # unaffected.
    class _JsonContentEngine(_FakeEngine):
        def chat_completion(self, model_id: str, **kwargs: Any) -> dict[str, Any]:
            self.last_chat_kwargs = {"model_id": model_id, **kwargs}
            return {
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": '{"ok": true}'},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
            }

    app = create_app()
    engine = _JsonContentEngine()
    with TestClient(app) as client:
        client.app.state.engine = engine
        body = {
            "model": "stub-model",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "describe"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA="}},
                    ],
                }
            ],
            "tools": [{"type": "function", "function": {"name": "lookup"}}],
            "tool_choice": "auto",
            "response_format": {"type": "json_object"},
            "stop": ["</stop>"],
            "seed": 123,
            "presence_penalty": 0.1,
            "frequency_penalty": 0.2,
            "logit_bias": {"42": -1},
            "user": "integration-test",
        }
        r = client.post("/v1/chat/completions", json=body)
    assert r.status_code == 200
    payload = r.json()
    assert payload["created"] > 0
    assert payload["usage"] == {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}
    assert engine.last_chat_kwargs is not None
    assert engine.last_chat_kwargs["messages"] == body["messages"]
    assert engine.last_chat_kwargs["tools"] == body["tools"]
    assert engine.last_chat_kwargs["tool_choice"] == "auto"
    assert engine.last_chat_kwargs["response_format"] == {"type": "json_object"}
    assert engine.last_chat_kwargs["stop"] == ["</stop>"]
    assert engine.last_chat_kwargs["seed"] == 123
    assert engine.last_chat_kwargs["presence_penalty"] == 0.1
    assert engine.last_chat_kwargs["frequency_penalty"] == 0.2
    assert engine.last_chat_kwargs["logit_bias"] == {"42": -1}
    assert engine.last_chat_kwargs["user"] == "integration-test"


def test_chat_completion_stream_error_shape_and_done(patched_registry, monkeypatch):
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    class BrokenStreamEngine(_FakeEngine):
        def chat_completion_stream(self, *args: Any, **kwargs: Any):
            raise RuntimeError("boom")
            yield  # pragma: no cover

    app = create_app()
    with TestClient(app) as client:
        client.app.state.engine = BrokenStreamEngine()
        body = {
            "model": "stub-model",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        }
        with client.stream("POST", "/v1/chat/completions", json=body) as resp:
            assert resp.status_code == 200
            raw = "".join(resp.iter_text())
    lines = [ln[len("data: ") :].strip() for ln in raw.split("\n") if ln.startswith("data: ")]
    err = json.loads(lines[0])
    assert err["error"]["message"] == "boom"
    assert err["error"]["type"] == "server_error"
    assert lines[-1] == "[DONE]"


def test_chat_completion_evicts_before_loading_new_model(monkeypatch):
    app_mod = importlib.import_module("omega_studio.server.app")
    import omega_studio.server.routes_v1 as rv

    reg = RegistryFile(
        version=1,
        model_folders=[],
        models={
            "old-model": ModelRecord(path=r"C:\fake\old.gguf", format="gguf"),
            "stub-model": ModelRecord(path=r"C:\fake\stub.gguf", format="gguf"),
        },
        settings=StudioSettings(max_concurrent_models=1, lru_eviction_enabled=True),
    )
    events: list[str] = []

    class EvictingEngine(_FakeEngine):
        def loaded_ids(self) -> list[str]:
            return ["old-model"]

        def unload(self, model_id: str) -> None:
            events.append(f"unload:{model_id}")

        def is_loaded(self, model_id: str) -> bool:
            return model_id == "old-model"

        def load_gguf(self, *args: Any, **kwargs: Any) -> None:
            events.append("load:stub-model")

    monkeypatch.setattr(app_mod, "load_registry", lambda: reg)
    monkeypatch.setattr(rv, "load_registry", lambda: reg)
    monkeypatch.setattr(rv, "apply_env_overrides", lambda r: r)
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    app = create_app()
    with TestClient(app) as client:
        client.app.state.engine = EvictingEngine()
        r = client.post(
            "/v1/chat/completions",
            json={"model": "stub-model", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert r.status_code == 200
    assert events[:2] == ["unload:old-model", "load:stub-model"]


def test_patch_studio_model_ui_overrides(monkeypatch):
    app_mod = importlib.import_module("omega_studio.server.app")
    import omega_studio.server.routes_v1 as rv

    reg = _stub_registry()
    monkeypatch.setattr(app_mod, "load_registry", lambda: reg)
    monkeypatch.setattr(rv, "load_registry", lambda: reg)
    monkeypatch.setattr(rv, "save_registry", lambda _r: None)
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    app = create_app()
    with TestClient(app) as client:
        r = client.patch(
            "/v1/studio/models/stub-model",
            json={"ui_overrides": {"n_ctx": 4096, "n_batch": 128}},
        )
    assert r.status_code == 200
    assert reg.models["stub-model"].ui_overrides["n_ctx"] == 4096
    assert reg.models["stub-model"].ui_overrides["n_batch"] == 128


def test_patch_does_not_persist_env_overrides(monkeypatch):
    app_mod = importlib.import_module("omega_studio.server.app")
    import omega_studio.server.routes_v1 as rv

    reg = _stub_registry()
    saved: list[RegistryFile] = []
    monkeypatch.setattr(app_mod, "load_registry", lambda: reg)
    monkeypatch.setattr(rv, "load_registry", lambda: reg)
    monkeypatch.setattr(rv, "save_registry", lambda r: saved.append(r))
    monkeypatch.setenv("OMEGA_STUDIO_PORT", "11500")
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    app = create_app()
    with TestClient(app) as client:
        r = client.patch("/v1/studio/models/stub-model", json={"pinned": True})
    assert r.status_code == 200
    assert saved[-1].settings.server_port == 11434


def test_chat_completion_uses_lifespan_registry_cache(monkeypatch):
    app_mod = importlib.import_module("omega_studio.server.app")
    import omega_studio.server.routes_v1 as rv

    calls = 0

    def counted_registry() -> RegistryFile:
        nonlocal calls
        calls += 1
        return _stub_registry()

    monkeypatch.setattr(app_mod, "load_registry", _stub_registry)
    monkeypatch.setattr(rv, "load_registry", counted_registry)
    monkeypatch.setattr(rv, "apply_env_overrides", lambda r: r)
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    app = create_app()
    with TestClient(app) as client:
        client.app.state.engine = _FakeEngine()
        r = client.post(
            "/v1/chat/completions",
            json={"model": "stub-model", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert r.status_code == 200
    assert calls == 0


def test_chat_completion_can_hide_thinking_block(patched_registry, monkeypatch):
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    class ThinkingEngine(_FakeEngine):
        def chat_completion(self, model_id: str, **kwargs: Any) -> dict[str, Any]:
            return {
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "<think>private chain</think>public answer",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
            }

    app = create_app()
    with TestClient(app) as client:
        client.app.state.engine = ThinkingEngine()
        r = client.post(
            "/v1/chat/completions",
            json={
                "model": "stub-model",
                "messages": [{"role": "user", "content": "hi"}],
                "hide_thinking": True,
            },
        )
    payload = r.json()
    assert payload["choices"][0]["message"]["content"] == "public answer"
    assert payload["choices"][0]["omega"]["thinking_block"] == "private chain"


def test_embeddings_endpoint_uses_engine_embedding_api(patched_registry, monkeypatch):
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    app = create_app()
    engine = _FakeEngine()
    with TestClient(app) as client:
        client.app.state.engine = engine
        r = client.post("/v1/embeddings", json={"model": "stub-model", "input": "hello"})
    assert r.status_code == 200
    assert r.json()["data"][0]["embedding"] == [0.1, 0.2]
    assert engine.last_embedding == {"model_id": "stub-model", "input": "hello"}
    assert engine.last_load_kwargs["embedding"] is True


def test_studio_registry_rescan_updates_memory_and_disk(monkeypatch, tmp_path):
    app_mod = importlib.import_module("omega_studio.server.app")
    import omega_studio.server.routes_v1 as rv

    model_file = tmp_path / "added.gguf"
    model_file.write_text("fake", encoding="utf-8")
    reg = RegistryFile(
        version=1,
        model_folders=[str(tmp_path)],
        models={},
        settings=StudioSettings(),
    )
    saved: list[RegistryFile] = []
    monkeypatch.setattr(app_mod, "load_registry", lambda: reg)
    monkeypatch.setattr(rv, "load_registry", lambda: reg)
    monkeypatch.setattr(rv, "save_registry", lambda r: saved.append(r))
    monkeypatch.setattr(rv, "apply_env_overrides", lambda r: r)
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    app = create_app()
    with TestClient(app) as client:
        r = client.post("/v1/studio/registry/rescan")
        cached = client.app.state.registry
    assert r.status_code == 200
    assert r.json()["added"] == 1
    assert "added" in cached.models
    assert saved and "added" in saved[-1].models


def test_rescan_reloads_external_registry_edits_before_saving(monkeypatch, tmp_path):
    app_mod = importlib.import_module("omega_studio.server.app")
    import omega_studio.server.routes_v1 as rv

    folder1 = tmp_path / "one"
    folder2 = tmp_path / "two"
    folder1.mkdir()
    folder2.mkdir()
    (folder2 / "external.gguf").write_text("fake", encoding="utf-8")
    startup = RegistryFile(
        version=1,
        model_folders=[str(folder1)],
        models={},
        settings=StudioSettings(),
    )
    external = RegistryFile(
        version=1,
        model_folders=[str(folder2)],
        models={},
        settings=StudioSettings(),
    )
    saved: list[RegistryFile] = []
    monkeypatch.setattr(app_mod, "load_registry", lambda: startup)
    monkeypatch.setattr(rv, "load_registry", lambda: external)
    monkeypatch.setattr(rv, "save_registry", lambda r: saved.append(r))
    monkeypatch.setattr(rv, "apply_env_overrides", lambda r: r.model_copy(deep=True))
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    app = create_app()
    with TestClient(app) as client:
        r = client.post("/v1/studio/registry/rescan")
    assert r.status_code == 200
    assert saved[-1].model_folders == [str(folder2)]
    assert "external" in saved[-1].models


def test_studio_registry_folders_api_persists_and_updates_cache(monkeypatch, tmp_path):
    app_mod = importlib.import_module("omega_studio.server.app")
    import omega_studio.server.routes_v1 as rv

    reg = _stub_registry()
    saved: list[RegistryFile] = []
    monkeypatch.setattr(app_mod, "load_registry", lambda: reg)
    monkeypatch.setattr(rv, "load_registry", lambda: reg)
    monkeypatch.setattr(rv, "save_registry", lambda r: saved.append(r))
    monkeypatch.setattr(rv, "apply_env_overrides", lambda r: r.model_copy(deep=True))
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    app = create_app()
    with TestClient(app) as client:
        r = client.post("/v1/studio/registry/folders", json={"folders": [str(tmp_path)]})
        cached = client.app.state.registry
    assert r.status_code == 200
    assert cached.model_folders == [str(tmp_path)]
    assert saved[-1].model_folders == [str(tmp_path)]


# ─────────────────────────────────────────────────────────────────
# #587 — concurrent-request stress
# ─────────────────────────────────────────────────────────────────
# Parallel /v1/chat/completions calls must NOT cross-wire state.
# Failure modes this catches in regression:
#   - Shared cmpl_id (chat IDs leaking across requests)
#   - Shared state.engine state mutated by overlapping requests
#   - Race conditions in routes_v1._prepare_gguf_model / RM.touch
#   - Streaming chunks from request A reaching request B's response
# llama-cpp-python under TestClient is a stub; real GPU contention
# is out of scope here — we're stressing the server's per-request
# isolation guarantees, not the underlying inference engine.

class _ConcurrentChatEngine(_FakeEngine):
    """Thread-safe stub that counts calls + returns request-distinguishable content."""

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._call_counter = 0

    def chat_completion(self, model_id: str, **kwargs: Any) -> dict[str, Any]:
        with self._lock:
            self._call_counter += 1
            call_id = self._call_counter
        # Add a small per-call jitter so threads genuinely interleave
        time.sleep(0.005)
        return {
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": f"reply-#{call_id}"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4},
        }


def test_concurrent_nonstream_chat_requests_get_unique_ids(patched_registry, monkeypatch):
    """20 parallel non-streaming chat requests — all 200, all unique
    chatcmpl IDs, all return distinct content from the stub engine."""
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    app = create_app()
    with TestClient(app) as client:
        client.app.state.engine = _ConcurrentChatEngine()

        def _hit(i: int):
            return client.post(
                "/v1/chat/completions",
                json={
                    "model": "stub-model",
                    "messages": [{"role": "user", "content": f"req-{i}"}],
                    "max_tokens": 8,
                },
            )

        N = 20
        with ThreadPoolExecutor(max_workers=10) as ex:
            results = list(ex.map(_hit, range(N)))

    # Every request succeeded
    assert all(r.status_code == 200 for r in results), [
        r.status_code for r in results if r.status_code != 200
    ]
    payloads = [r.json() for r in results]
    # Every chat completion got a unique chatcmpl id
    ids = [p.get("id", "") for p in payloads]
    assert all(i.startswith("chatcmpl-") for i in ids), ids
    assert len(set(ids)) == N, f"id collision under load: {len(set(ids))} unique of {N}"
    # Every request got a distinct content string (stub increments a counter)
    contents = {p["choices"][0]["message"]["content"] for p in payloads}
    assert len(contents) == N, (
        f"content cross-wired across requests; only {len(contents)} unique replies"
    )


def test_concurrent_streaming_chat_requests_dont_cross_wire(patched_registry, monkeypatch):
    """Parallel streaming requests — assert each response stream has
    its OWN chatcmpl id (no leakage) and that every stream terminates
    with its own [DONE] marker."""
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    class StreamingPerRequestEngine(_FakeEngine):
        def __init__(self):
            super().__init__()
            self._lock = threading.Lock()
            self._counter = 0

        def chat_completion_stream(self, *args: Any, **kwargs: Any):
            with self._lock:
                self._counter += 1
                cid = self._counter
            # Small interleave window
            time.sleep(0.005)
            yield {"choices": [{"index": 0, "delta": {"role": "assistant", "content": f"r{cid}"}}]}
            yield {"choices": [{"index": 0, "delta": {"content": "-tok"}}]}
            yield {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}

    app = create_app()
    with TestClient(app) as client:
        client.app.state.engine = StreamingPerRequestEngine()

        def _stream(i: int) -> tuple[int, str]:
            with client.stream(
                "POST",
                "/v1/chat/completions",
                json={
                    "model": "stub-model",
                    "messages": [{"role": "user", "content": f"go-{i}"}],
                    "stream": True,
                },
            ) as resp:
                body = "".join(resp.iter_text())
            return resp.status_code, body

        N = 10
        with ThreadPoolExecutor(max_workers=N) as ex:
            results = list(ex.map(_stream, range(N)))

    assert all(code == 200 for code, _ in results)
    # Each stream's chunks must share ONE id (already covered by #574)
    # AND every stream must have its OWN id (no cross-wiring)
    seen_ids: set[str] = set()
    for _, body in results:
        chunk_ids: set[str] = set()
        for line in body.split("\n"):
            if not line.startswith("data: ") or line[len("data: ") :].strip() == "[DONE]":
                continue
            try:
                payload = json.loads(line[len("data: ") :])
            except json.JSONDecodeError:
                continue
            if "id" in payload:
                chunk_ids.add(payload["id"])
        assert len(chunk_ids) == 1, (
            f"single stream contained multiple chatcmpl ids: {chunk_ids}"
        )
        seen_ids.update(chunk_ids)
        assert "[DONE]" in body, "stream missing terminal [DONE]"
    assert len(seen_ids) == N, (
        f"stream ID leakage across requests: {len(seen_ids)} unique of {N}"
    )


# ─────────────────────────────────────────────────────────────────
# #584 — response_format JSON mode enforcement
# ─────────────────────────────────────────────────────────────────
# OpenAI spec: response_format = {"type": "text" | "json_object" |
# "json_schema", ...}. When json_object/json_schema is requested,
# the server must guarantee the model returned valid JSON or surface
# an upstream-contract error. Without server-side enforcement,
# clients get a string they have to re-parse downstream and discover
# broken hours later in production.

class _CannedChatEngine(_FakeEngine):
    """Returns canned content controllable per-request via a body field."""
    def __init__(self) -> None:
        super().__init__()
        self.last_chat_kwargs: dict[str, Any] | None = None

    def chat_completion(self, model_id: str, **kwargs: Any) -> dict[str, Any]:
        self.last_chat_kwargs = {"model_id": model_id, **kwargs}
        content = kwargs.get("fake_content", '{"answer": 42}')
        return {
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }


def test_response_format_invalid_type_rejected(patched_registry, monkeypatch):
    """Garbage `type` values must 400 upfront, not 500 inside engine."""
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    app = create_app()
    with TestClient(app) as client:
        client.app.state.engine = _CannedChatEngine()
        r = client.post(
            "/v1/chat/completions",
            json={
                "model": "stub-model",
                "messages": [{"role": "user", "content": "hi"}],
                "response_format": {"type": "xml"},
            },
        )
    assert r.status_code == 400
    assert "response_format.type" in r.json()["detail"]


def test_response_format_must_be_object(patched_registry, monkeypatch):
    """response_format passed as a string instead of object → 400."""
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    app = create_app()
    with TestClient(app) as client:
        client.app.state.engine = _CannedChatEngine()
        r = client.post(
            "/v1/chat/completions",
            json={
                "model": "stub-model",
                "messages": [{"role": "user", "content": "hi"}],
                "response_format": "json",
            },
        )
    # Pydantic-level validation rejects with 422 before our handler;
    # both 400 and 422 are acceptable as long as it's not 500.
    assert r.status_code in (400, 422)


def test_response_format_json_schema_requires_schema(patched_registry, monkeypatch):
    """Structured-outputs request without a schema field → 400."""
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    app = create_app()
    with TestClient(app) as client:
        client.app.state.engine = _CannedChatEngine()
        r = client.post(
            "/v1/chat/completions",
            json={
                "model": "stub-model",
                "messages": [{"role": "user", "content": "hi"}],
                "response_format": {"type": "json_schema"},
            },
        )
    assert r.status_code == 400
    assert "json_schema" in r.json()["detail"]


def test_response_format_text_passes_through_unchanged(patched_registry, monkeypatch):
    """Default `text` mode: no validation, plain string content
    flows back without parsing — even when content isn't JSON-shaped."""
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    class PlainTextEngine(_FakeEngine):
        def chat_completion(self, model_id: str, **kwargs: Any) -> dict[str, Any]:
            return {
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "hello world"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }

    app = create_app()
    with TestClient(app) as client:
        client.app.state.engine = PlainTextEngine()
        r = client.post(
            "/v1/chat/completions",
            json={
                "model": "stub-model",
                "messages": [{"role": "user", "content": "hi"}],
                "response_format": {"type": "text"},
            },
        )
    assert r.status_code == 200
    payload = r.json()
    assert payload["choices"][0]["message"]["content"] == "hello world"
    assert "json" not in payload["choices"][0].get("omega", {})


def test_json_object_mode_parses_and_attaches_under_omega(patched_registry, monkeypatch):
    """json_object mode: parse content, attach parsed dict under
    `choices[].omega.json` so clients don't re-parse."""
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    class JsonEngine(_FakeEngine):
        def chat_completion(self, model_id: str, **kwargs: Any) -> dict[str, Any]:
            return {
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": '{"name": "alice", "age": 30}',
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }

    app = create_app()
    with TestClient(app) as client:
        client.app.state.engine = JsonEngine()
        r = client.post(
            "/v1/chat/completions",
            json={
                "model": "stub-model",
                "messages": [{"role": "user", "content": "give json"}],
                "response_format": {"type": "json_object"},
            },
        )
    assert r.status_code == 200
    choice = r.json()["choices"][0]
    assert choice["omega"]["json"] == {"name": "alice", "age": 30}


def test_json_object_mode_502s_on_invalid_json(patched_registry, monkeypatch):
    """Model returned non-JSON despite the contract → 502 (upstream
    contract violation), not 200 with a broken string."""
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    class BrokenJsonEngine(_FakeEngine):
        def chat_completion(self, model_id: str, **kwargs: Any) -> dict[str, Any]:
            return {
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "Sure, here's your JSON: {oops}",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }

    app = create_app()
    with TestClient(app) as client:
        client.app.state.engine = BrokenJsonEngine()
        r = client.post(
            "/v1/chat/completions",
            json={
                "model": "stub-model",
                "messages": [{"role": "user", "content": "give json"}],
                "response_format": {"type": "json_object"},
            },
        )
    assert r.status_code == 502
    detail = r.json()["detail"]
    assert "invalid JSON" in detail
    assert "preview=" in detail


def test_json_object_mode_502s_on_empty_content(patched_registry, monkeypatch):
    """Model returned empty content under JSON-mode contract → 502."""
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    class EmptyEngine(_FakeEngine):
        def chat_completion(self, model_id: str, **kwargs: Any) -> dict[str, Any]:
            return {
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "   "},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1},
            }

    app = create_app()
    with TestClient(app) as client:
        client.app.state.engine = EmptyEngine()
        r = client.post(
            "/v1/chat/completions",
            json={
                "model": "stub-model",
                "messages": [{"role": "user", "content": "give json"}],
                "response_format": {"type": "json_object"},
            },
        )
    assert r.status_code == 502
    assert "empty" in r.json()["detail"]


def test_json_schema_mode_validates_and_parses(patched_registry, monkeypatch):
    """json_schema mode also runs the content-parse enforcement
    (full schema validation is downstream-of-llama work; the
    minimum contract here is 'output is parseable JSON')."""
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    class SchemaEngine(_FakeEngine):
        def chat_completion(self, model_id: str, **kwargs: Any) -> dict[str, Any]:
            return {
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": '{"x": 1, "y": [2, 3]}',
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }

    app = create_app()
    with TestClient(app) as client:
        client.app.state.engine = SchemaEngine()
        r = client.post(
            "/v1/chat/completions",
            json={
                "model": "stub-model",
                "messages": [{"role": "user", "content": "give json"}],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "shape",
                        "schema": {
                            "type": "object",
                            "properties": {
                                "x": {"type": "integer"},
                                "y": {"type": "array"},
                            },
                        },
                    },
                },
            },
        )
    assert r.status_code == 200
    assert r.json()["choices"][0]["omega"]["json"] == {"x": 1, "y": [2, 3]}


def test_concurrent_models_endpoint_handles_parallel_reads(patched_registry, monkeypatch):
    """The /v1/models endpoint touches registry state on every call —
    parallel reads must not raise (registry cache shared across
    requests via app.state)."""
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    app = create_app()
    with TestClient(app) as client:
        def _hit():
            return client.get("/v1/models")

        with ThreadPoolExecutor(max_workers=20) as ex:
            futures = [ex.submit(_hit) for _ in range(40)]
            results = [f.result() for f in as_completed(futures)]

    assert all(r.status_code == 200 for r in results)
    # All payloads have at least one model
    for r in results:
        assert r.json().get("data"), "models list response missing data"
