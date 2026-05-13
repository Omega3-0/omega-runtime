"""#594 — ONNX embedding pipeline (tokenize → infer → pool → normalize)."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from starlette.testclient import TestClient

from omega_studio.config import ModelRecord, RegistryFile, StudioSettings
from omega_studio.inference.onnx_embedding import (
    _cls_pool,
    _l2_normalize,
    _max_pool,
    _mean_pool,
)


# ─────────────────────────────────────────────────────────────────
# Pooling + normalize unit tests (pure numpy, no model required)
# ─────────────────────────────────────────────────────────────────

def test_l2_normalize_unit_vectors():
    v = np.array([[3.0, 4.0]], dtype=np.float32)  # norm = 5
    out = _l2_normalize(v)
    np.testing.assert_allclose(out, [[0.6, 0.8]], rtol=1e-5)
    np.testing.assert_allclose(np.linalg.norm(out, axis=-1), [1.0], rtol=1e-5)


def test_l2_normalize_zero_vector_safe():
    """A zero vector must NOT divide by zero — that produces NaN that
    poisons every downstream cosine-sim computation."""
    v = np.zeros((1, 4), dtype=np.float32)
    out = _l2_normalize(v)
    assert not np.any(np.isnan(out))
    assert (out == 0).all()


def test_mean_pool_masks_padding():
    """Padding tokens must NOT contribute to the pooled vector —
    that's the whole reason mean-pool needs the attention mask."""
    last_hidden = np.array(
        [[[1.0, 1.0], [3.0, 3.0], [99.0, 99.0]]],  # third token is padding
        dtype=np.float32,
    )
    attention_mask = np.array([[1, 1, 0]], dtype=np.int64)
    out = _mean_pool(last_hidden, attention_mask)
    # Mean of first two tokens only: [(1+3)/2, (1+3)/2] = [2, 2]
    np.testing.assert_allclose(out, [[2.0, 2.0]], rtol=1e-5)


def test_mean_pool_all_masked_returns_zeros():
    """Edge case: empty input (all-padding sequence) — must not divide
    by zero. We clip the count to 1 so the result is the zero vector."""
    last_hidden = np.array([[[5.0, 5.0]]], dtype=np.float32)
    attention_mask = np.array([[0]], dtype=np.int64)
    out = _mean_pool(last_hidden, attention_mask)
    assert not np.any(np.isnan(out))
    assert (out == 0).all()


def test_cls_pool_returns_first_token():
    last_hidden = np.array(
        [[[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]]], dtype=np.float32
    )
    out = _cls_pool(last_hidden)
    np.testing.assert_array_equal(out, [[1.0, 2.0]])


def test_max_pool_masks_padding_with_neg_inf():
    """Max pool must ignore padding — replacing with -inf works
    because real activations are bounded."""
    last_hidden = np.array(
        [[[1.0, 1.0], [3.0, 2.0], [9999.0, 9999.0]]],  # padding
        dtype=np.float32,
    )
    attention_mask = np.array([[1, 1, 0]], dtype=np.int64)
    out = _max_pool(last_hidden, attention_mask)
    # Max across non-padded: per-dim [3, 2]
    np.testing.assert_array_equal(out, [[3.0, 2.0]])


# ─────────────────────────────────────────────────────────────────
# ONNXEmbedder integration with mocked session + tokenizer
# ─────────────────────────────────────────────────────────────────

def _make_fake_tokenizer(monkeypatch):
    """Patch tokenizers.Tokenizer.from_file to return a stub that
    encodes any text into a fixed [1, 5, 0, 0] sequence with mask
    [1, 1, 0, 0] — small enough to verify shapes without needing a
    real BPE/sentencepiece model."""

    class _FakeEncoding:
        def __init__(self, ids, mask):
            self.ids = ids
            self.attention_mask = mask

    class _FakeTokenizer:
        def __init__(self):
            self._truncation = None

        def enable_truncation(self, **kw):
            self._truncation = kw

        def enable_padding(self, **kw):
            pass

        def encode_batch(self, texts):
            return [
                _FakeEncoding([1, 5, 0, 0], [1, 1, 0, 0])
                for _ in texts
            ]

    fake = _FakeTokenizer()

    class _Loader:
        @staticmethod
        def from_file(_path):
            return fake

    import tokenizers
    monkeypatch.setattr(tokenizers, "Tokenizer", _Loader)
    return fake


def _make_fake_session(monkeypatch, hidden=8):
    """Patch ort.InferenceSession to return a stub that emits a
    deterministic last_hidden_state matching the requested shape."""

    class _FakeInput:
        def __init__(self, name):
            self.name = name

    class _FakeOutput:
        def __init__(self):
            self.shape = ["batch", "seq", hidden]

    class _FakeSession:
        def __init__(self, *_a, **_kw):
            self._inputs = [
                _FakeInput("input_ids"),
                _FakeInput("attention_mask"),
            ]

        def get_inputs(self):
            return self._inputs

        def get_outputs(self):
            return [_FakeOutput()]

        def get_providers(self):
            return ["CPUExecutionProvider"]

        def run(self, _outs, feed):
            input_ids = feed["input_ids"]
            batch, seq = input_ids.shape
            # Deterministic: each token's hidden vector is [token_id, ...zeros]
            arr = np.zeros((batch, seq, hidden), dtype=np.float32)
            for b in range(batch):
                for s in range(seq):
                    arr[b, s, 0] = float(input_ids[b, s])
            return [arr]

    import onnxruntime as ort
    monkeypatch.setattr(ort, "InferenceSession", _FakeSession)
    return _FakeSession


def test_onnx_embedder_loads_when_tokenizer_present(tmp_path, monkeypatch):
    model = tmp_path / "test.onnx"
    model.write_bytes(b"fake onnx")
    (tmp_path / "tokenizer.json").write_text("{}", encoding="utf-8")
    _make_fake_tokenizer(monkeypatch)
    _make_fake_session(monkeypatch)

    from omega_studio.inference.onnx_embedding import ONNXEmbedder

    emb = ONNXEmbedder(model)
    assert emb.pooling == "mean"
    assert emb.normalize is True


def test_onnx_embedder_errors_when_tokenizer_missing(tmp_path, monkeypatch):
    """No tokenizer.json beside the .onnx file → clear FileNotFoundError,
    not a confusing tokenization error mid-inference."""
    model = tmp_path / "test.onnx"
    model.write_bytes(b"fake onnx")
    _make_fake_tokenizer(monkeypatch)
    _make_fake_session(monkeypatch)

    from omega_studio.inference.onnx_embedding import ONNXEmbedder

    with pytest.raises(FileNotFoundError, match="tokenizer.json"):
        ONNXEmbedder(model)


def test_onnx_embedder_embed_returns_normalized_vectors(tmp_path, monkeypatch):
    """End-to-end: tokenize → run → mean-pool (with mask) → L2 normalize.
    Stub session produces predictable outputs we can verify."""
    model = tmp_path / "test.onnx"
    model.write_bytes(b"fake onnx")
    (tmp_path / "tokenizer.json").write_text("{}", encoding="utf-8")
    _make_fake_tokenizer(monkeypatch)
    _make_fake_session(monkeypatch, hidden=4)

    from omega_studio.inference.onnx_embedding import ONNXEmbedder

    emb = ONNXEmbedder(model)
    out = emb.embed(["hello", "world"])
    assert out.shape == (2, 4)
    # Each row should be unit-length (L2-normalized)
    norms = np.linalg.norm(out, axis=-1)
    np.testing.assert_allclose(norms, [1.0, 1.0], rtol=1e-5)


def test_onnx_embedder_disabling_normalize_yields_raw_vectors(tmp_path, monkeypatch):
    model = tmp_path / "test.onnx"
    model.write_bytes(b"fake onnx")
    (tmp_path / "tokenizer.json").write_text("{}", encoding="utf-8")
    _make_fake_tokenizer(monkeypatch)
    _make_fake_session(monkeypatch, hidden=4)

    from omega_studio.inference.onnx_embedding import ONNXEmbedder

    emb = ONNXEmbedder(model, normalize=False)
    out = emb.embed(["hi"])
    norms = np.linalg.norm(out, axis=-1)
    # NOT normalized → norm != 1 (unless coincidentally; here mask
    # gives non-zero pooled values so norm > 0 and != 1)
    assert norms[0] != pytest.approx(1.0, abs=1e-3)


def test_onnx_embedder_cls_pooling_takes_first_token(tmp_path, monkeypatch):
    """BGE-family models need cls pooling; verify the option propagates."""
    model = tmp_path / "test.onnx"
    model.write_bytes(b"fake onnx")
    (tmp_path / "tokenizer.json").write_text("{}", encoding="utf-8")
    _make_fake_tokenizer(monkeypatch)
    _make_fake_session(monkeypatch, hidden=4)

    from omega_studio.inference.onnx_embedding import ONNXEmbedder

    emb = ONNXEmbedder(model, pooling="cls", normalize=False)
    out = emb.embed(["x"])
    # First token is id=1 (per fake tokenizer); first-position embedding
    # is [1, 0, 0, 0] per fake session. CLS pool returns that exactly.
    np.testing.assert_array_equal(out[0], [1.0, 0.0, 0.0, 0.0])


def test_onnx_embedder_empty_input_returns_empty(tmp_path, monkeypatch):
    model = tmp_path / "test.onnx"
    model.write_bytes(b"fake onnx")
    (tmp_path / "tokenizer.json").write_text("{}", encoding="utf-8")
    _make_fake_tokenizer(monkeypatch)
    _make_fake_session(monkeypatch)

    from omega_studio.inference.onnx_embedding import ONNXEmbedder

    emb = ONNXEmbedder(model)
    out = emb.embed([])
    assert out.shape == (0, 0)


# ─────────────────────────────────────────────────────────────────
# Engine.create_embedding dispatch — ONNX path returns OpenAI shape
# ─────────────────────────────────────────────────────────────────

def test_engine_create_embedding_routes_onnx_handle(monkeypatch):
    """Engine handle without `create_embedding` (i.e. an ONNXEmbedder)
    must route through the new dispatch path and return OpenAI envelope."""
    from omega_studio.inference.engine import InferenceEngine

    engine = InferenceEngine()

    class _StubEmbedder:
        def embed(self, texts):
            # 4-dim deterministic output
            return np.stack(
                [np.array([float(i + 1)] * 4, dtype=np.float32) for i in range(len(texts))]
            )

    key = engine._handle_key("my-model", embedding=True)
    engine._handles[key] = _StubEmbedder()
    out = engine.create_embedding("my-model", ["one", "two"])
    assert out["object"] == "list"
    assert out["model"] == "my-model"
    assert len(out["data"]) == 2
    assert out["data"][0]["embedding"] == [1.0, 1.0, 1.0, 1.0]
    assert out["data"][1]["embedding"] == [2.0, 2.0, 2.0, 2.0]
    assert "prompt_tokens" in out["usage"]


def test_engine_create_embedding_onnx_rejects_invalid_input_type():
    """ONNX path requires str or list[str]. A dict / int should raise
    TypeError immediately, not a confusing tokenization crash."""
    from omega_studio.inference.engine import InferenceEngine

    engine = InferenceEngine()

    class _StubEmbedder:
        def embed(self, texts):
            return np.zeros((len(texts), 4), dtype=np.float32)

    key = engine._handle_key("m", embedding=True)
    engine._handles[key] = _StubEmbedder()
    with pytest.raises(TypeError, match="str or list"):
        engine.create_embedding("m", {"unsupported": True})


# ─────────────────────────────────────────────────────────────────
# /v1/embeddings end-to-end via /onnx route
# ─────────────────────────────────────────────────────────────────

def _stub_registry_with_onnx(tmp_path: Path) -> RegistryFile:
    onnx_path = tmp_path / "model.onnx"
    onnx_path.write_bytes(b"fake")
    (tmp_path / "tokenizer.json").write_text("{}", encoding="utf-8")
    return RegistryFile(
        version=1,
        model_folders=[],
        models={
            "onnx-embed": ModelRecord(
                path=str(onnx_path),
                format="onnx",
                embedding=True,
            )
        },
        settings=StudioSettings(),
    )


def test_v1_embeddings_routes_onnx_format(tmp_path, monkeypatch):
    """The /v1/embeddings handler used to 400 on .onnx — now it routes
    through _prepare_onnx_embedder and returns a real embedding."""
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    _make_fake_tokenizer(monkeypatch)
    _make_fake_session(monkeypatch, hidden=8)

    reg = _stub_registry_with_onnx(tmp_path)
    app_mod = importlib.import_module("omega_studio.server.app")
    import omega_studio.server.routes_v1 as rv

    monkeypatch.setattr(app_mod, "load_registry", lambda: reg)
    monkeypatch.setattr(rv, "load_registry", lambda: reg)
    monkeypatch.setattr(rv, "apply_env_overrides", lambda r: r)

    from omega_studio.server.app import create_app

    app = create_app()
    with TestClient(app) as client:
        r = client.post(
            "/v1/embeddings",
            json={"model": "onnx-embed", "input": ["hello world", "second"]},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["object"] == "list"
    assert body["model"] == "onnx-embed"
    assert len(body["data"]) == 2
    # Each embedding is L2-normalized → length 8 → norm ≈ 1
    for entry in body["data"]:
        vec = np.array(entry["embedding"])
        assert vec.shape == (8,)
        np.testing.assert_allclose(np.linalg.norm(vec), 1.0, rtol=1e-5)


def test_v1_embeddings_still_rejects_unknown_format(tmp_path, monkeypatch):
    """Other formats (e.g. safetensors, pytorch) must still 400 —
    the .onnx unblock didn't open the gate to everything."""
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    bad = tmp_path / "model.safetensors"
    bad.write_bytes(b"fake")
    reg = RegistryFile(
        version=1,
        model_folders=[],
        models={
            "weird": ModelRecord(path=str(bad), format="safetensors", embedding=True)
        },
        settings=StudioSettings(),
    )
    app_mod = importlib.import_module("omega_studio.server.app")
    import omega_studio.server.routes_v1 as rv

    monkeypatch.setattr(app_mod, "load_registry", lambda: reg)
    monkeypatch.setattr(rv, "load_registry", lambda: reg)
    monkeypatch.setattr(rv, "apply_env_overrides", lambda r: r)

    from omega_studio.server.app import create_app

    app = create_app()
    with TestClient(app) as client:
        r = client.post("/v1/embeddings", json={"model": "weird", "input": "hi"})
    assert r.status_code == 400
    assert "format not supported" in r.json()["detail"]
