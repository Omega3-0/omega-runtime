"""OpenAI-compatible subset for local GGUF models."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any, List

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

log = logging.getLogger("omega_studio.routes_v1")

from omega_studio import __version__
from omega_studio.config import ModelRecord
from omega_studio.inference.backends import (
    effective_llama_n_gpu_layers,
    get_omega_variant,
    _resolve_backend,
)
from omega_studio.inference.engine import estimate_vram_stub_mb
from omega_studio.inference.thinking_parser import parse_thinking
from omega_studio.registry import (
    apply_env_overrides,
    load_registry,
    merge_scan_into_registry,
    save_registry,
)
from omega_studio.server.tool_call_repair import repair_response_tool_calls

router = APIRouter(prefix="/v1", tags=["v1"])


class ChatMessage(BaseModel):
    role: str
    content: Any


class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[ChatMessage] = Field(min_length=1)
    max_tokens: int = 256
    temperature: float | None = None
    top_p: float | None = None
    stream: bool = False
    tools: list[dict[str, Any]] | None = None
    tool_choice: Any = None
    response_format: dict[str, Any] | None = None
    stop: str | list[str] | None = None
    seed: int | None = None
    n: int | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    logprobs: bool | None = None
    logit_bias: dict[str, float] | None = None
    user: str | None = None
    stream_options: dict[str, Any] | None = None
    hide_thinking: bool = False


class EmbeddingsRequest(BaseModel):
    model: str
    input: Any
    encoding_format: str | None = None
    user: str | None = None


class RegistryFoldersRequest(BaseModel):
    folders: list[str] = Field(min_length=1)


class StudioModelPatch(BaseModel):
    """Partial update for registry row + ``ui_overrides`` (GUI / operator)."""

    ui_overrides: dict[str, Any] | None = None
    accelerator: str | None = None
    pinned: bool | None = None


def _persisted_registry(request: Request):
    reg = getattr(request.app.state, "registry", None)
    if reg is None:
        reg = load_registry()
        request.app.state.registry = reg
    return reg


def _runtime_registry(request: Request):
    return apply_env_overrides(_persisted_registry(request))


def _save_request_registry(request: Request, reg) -> None:
    save_registry(reg)
    request.app.state.registry = reg


def _resolve_generation_params(reg, model_id: str) -> dict[str, Any]:
    st = reg.settings
    rec = reg.models[model_id]
    ui = {str(k): v for k, v in (rec.ui_overrides or {}).items()}
    ov = {str(k): v for k, v in st.per_model_overrides.get(model_id, {}).items()}

    def pick(keys: tuple[str, ...], default: Any) -> Any:
        for src in (ov, ui):
            for k in keys:
                if k in src:
                    return src[k]
        return default

    n_gpu_layers = int(pick(("n_gpu_layers",), st.n_gpu_layers))
    n_gpu_layers = effective_llama_n_gpu_layers(n_gpu_layers)
    batch_val = pick(("batch", "n_batch"), st.batch)
    return {
        "n_ctx": int(pick(("n_ctx",), st.n_ctx)),
        "temperature": float(pick(("temperature",), st.temperature)),
        "top_p": float(pick(("top_p",), st.top_p)),
        "n_gpu_layers": n_gpu_layers,
        "batch": int(batch_val),
        "threads": int(pick(("threads",), st.threads)),
    }


def _sync_pins(request: Request, reg) -> None:
    rm = request.app.state.resource_manager
    for mid, rec in reg.models.items():
        rm.set_pin(mid, rec.pinned)
    max_c = min(15, int(reg.settings.max_concurrent_models))
    rm.max_loaded = max(1, max_c)
    rm.eviction_enabled = bool(reg.settings.lru_eviction_enabled)


def _chat_extra_kwargs(body: ChatCompletionRequest) -> dict[str, Any]:
    # NOTE: ``stream_options`` is intentionally NOT forwarded —
    # llama-cpp-python's ``create_chat_completion`` raises
    # ``unexpected keyword argument 'stream_options'`` because it
    # doesn't implement that part of the OpenAI streaming spec
    # natively. We handle ``include_usage`` ourselves above (see the
    # streaming branch's dedicated usage chunk emission) so the
    # client contract is honored without leaking the kwarg downstream.
    keys = (
        "tools",
        "tool_choice",
        "response_format",
        "stop",
        "seed",
        "n",
        "presence_penalty",
        "frequency_penalty",
        "logprobs",
        "logit_bias",
        "user",
    )
    out: dict[str, Any] = {}
    for key in keys:
        value = getattr(body, key)
        if value is not None:
            out[key] = value
    return out


def _created_now() -> int:
    return int(time.time())


def _inference_timeout_s() -> float:
    raw = os.environ.get("OMEGA_INFERENCE_TIMEOUT_S", "").strip()
    if not raw:
        return 300.0
    try:
        return max(0.001, float(raw))
    except ValueError:
        return 300.0


async def _run_with_timeout(fn, *args, **kwargs):
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(fn, *args, **kwargs),
            timeout=_inference_timeout_s(),
        )
    except TimeoutError as exc:
        raise HTTPException(504, f"inference timed out after {_inference_timeout_s():g}s") from exc


def _normalize_chat_response(raw: dict[str, Any], *, model: str) -> dict[str, Any]:
    out = dict(raw)
    out.setdefault("id", f"chatcmpl-{uuid.uuid4()}")
    out.setdefault("object", "chat.completion")
    if not isinstance(out.get("created"), int) or int(out.get("created") or 0) <= 0:
        out["created"] = _created_now()
    # FORCE the registered model id — llama-cpp-python's response sets
    # `model` to the absolute .gguf file path on disk, which leaks
    # local paths to API clients and breaks dedup-by-model logic.
    out["model"] = model
    out.setdefault("choices", [])
    out.setdefault("usage", {})
    return out


_VALID_RESPONSE_FORMAT_TYPES = frozenset({"text", "json_object", "json_schema"})


def _validate_response_format(rf: dict[str, Any] | None) -> str | None:
    """Reject malformed ``response_format`` upfront and return the resolved
    type tag ('text' / 'json_object' / 'json_schema') or None if not set.

    OpenAI spec: ``{"type": "text"}`` is the default (no enforcement),
    ``{"type": "json_object"}`` is legacy JSON mode (model emits JSON),
    ``{"type": "json_schema", "json_schema": {"name": ..., "schema": ...}}``
    is structured outputs. Anything else gets a 400.
    """
    if rf is None:
        return None
    if not isinstance(rf, dict):
        raise HTTPException(400, "response_format must be an object")
    rf_type = rf.get("type")
    if not isinstance(rf_type, str) or rf_type not in _VALID_RESPONSE_FORMAT_TYPES:
        raise HTTPException(
            400,
            f"response_format.type must be one of {sorted(_VALID_RESPONSE_FORMAT_TYPES)}; "
            f"got {rf_type!r}",
        )
    if rf_type == "json_schema":
        schema = rf.get("json_schema")
        if not isinstance(schema, dict):
            raise HTTPException(
                400, "response_format.type=json_schema requires a json_schema object"
            )
        if not isinstance(schema.get("schema"), dict):
            raise HTTPException(
                400, "response_format.json_schema.schema must be an object"
            )
    return rf_type


def _enforce_json_response(response: dict[str, Any]) -> None:
    """Post-validate that the model produced parseable JSON. Mutates
    `response` in place to attach the parsed object under ``omega.json``
    so clients don't have to re-parse. Raises 502 if the model returned
    non-JSON content under a JSON-mode contract."""
    choices = response.get("choices") or []
    for idx, choice in enumerate(choices):
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise HTTPException(
                502,
                f"model returned empty content under response_format=json_*; "
                f"choice index={idx}",
            )
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            preview = content[:200].replace("\n", " ")
            raise HTTPException(
                502,
                f"model returned invalid JSON under response_format=json_*; "
                f"choice index={idx}; error={exc.msg}; preview={preview!r}",
            ) from exc
        omega = choice.setdefault("omega", {})
        if isinstance(omega, dict):
            omega["json"] = parsed


def _apply_thinking_extension(response: dict[str, Any], *, hide: bool) -> dict[str, Any]:
    if not response.get("choices"):
        return response
    for choice in response["choices"]:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, str) or not content:
            continue
        parsed = parse_thinking(content)
        if parsed.thinking_block is None:
            continue
        omega = choice.setdefault("omega", {})
        if isinstance(omega, dict):
            omega["thinking_block"] = parsed.thinking_block
        if hide:
            message["content"] = parsed.visible.strip()
    return response


def _flatten_message_text_for_tokenization(messages: list[dict[str, Any]]) -> str:
    """Best-effort prompt-text reconstruction for synthetic usage counts.

    OpenAI chat messages can be plain strings or content-parts arrays
    (text + image_url, etc.). For usage estimation we only need the
    TEXT — image_url parts are ignored. The result feeds engine
    tokenize(), so exact whitespace doesn't matter; what matters is
    that the token count tracks the prompt's true size for clients
    that bill by tokens.
    """
    parts: list[str] = []
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if isinstance(role, str) and role:
            parts.append(role + ":")
        content = msg.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for piece in content:
                if isinstance(piece, dict):
                    text = piece.get("text")
                    if isinstance(text, str):
                        parts.append(text)
    return "\n".join(parts)


def _synthesize_streaming_usage(
    *,
    engine: Any,
    model_id: str,
    messages: list[dict[str, Any]],
    accumulated_content: str,
) -> dict[str, Any] | None:
    """Compute prompt + completion token counts when the engine
    didn't emit a native ``usage`` field during streaming. Returns
    None when the engine has no tokenize hook (mocks, future
    backends) so the caller can skip emitting the synthetic chunk
    rather than emit zeros.
    """
    count_tokens = getattr(engine, "count_tokens", None)
    if not callable(count_tokens):
        return None
    try:
        prompt_text = _flatten_message_text_for_tokenization(messages)
        prompt_tokens = int(count_tokens(model_id, prompt_text) or 0)
        completion_tokens = int(count_tokens(model_id, accumulated_content) or 0)
    except Exception:
        return None
    if prompt_tokens == 0 and completion_tokens == 0:
        return None
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


def _normalize_chat_chunk(raw: dict[str, Any], *, cmpl_id: str, model: str) -> dict[str, Any]:
    out = dict(raw)
    # FORCE our cmpl_id across every chunk in a single streaming completion.
    # llama-cpp-python emits chunks with its OWN chatcmpl-<uuid> id; if we
    # only setdefault, intermediate chunks keep llama's id while our
    # manually-constructed chunks (flush tail, end marker) get our id —
    # producing a single completion stream with multiple ids, which
    # violates the OpenAI spec and breaks dedup-by-id clients.
    out["id"] = cmpl_id
    out.setdefault("object", "chat.completion.chunk")
    if not isinstance(out.get("created"), int) or int(out.get("created") or 0) <= 0:
        out["created"] = _created_now()
    # Same path-leak guard as the non-streaming response: llama-cpp-python
    # sets chunk's `model` to the .gguf file path on disk; force the
    # registered model id so streaming chunks don't expose local paths.
    out["model"] = model
    out.setdefault("choices", [])
    return out


def _strip_intermediate_finish_reason(chunk: dict[str, Any]) -> dict[str, Any]:
    """Drop `finish_reason` from per-choice deltas in an intermediate
    streaming chunk. Our own `end` chunk is the canonical end-of-stream
    marker. Without this, llama-cpp-python's native end chunk would
    arrive BEFORE our flush-tail content (when hide_thinking filter is
    active) — clients seeing the first finish_reason treat the stream
    as ended and discard subsequent content.

    Also fixes the simpler regression of TWO finish_reason chunks
    (llama's + ours) in non-filtered streams."""
    out = dict(chunk)
    choices = out.get("choices")
    if not isinstance(choices, list):
        return out
    new_choices = []
    for choice in choices:
        if isinstance(choice, dict) and choice.get("finish_reason") is not None:
            choice = {k: v for k, v in choice.items() if k != "finish_reason"}
            choice["finish_reason"] = None
        new_choices.append(choice)
    out["choices"] = new_choices
    return out


def _next_or_done(iterator):
    try:
        return True, next(iterator)
    except StopIteration:
        return False, None


async def _next_stream_chunk(iterator):
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_next_or_done, iterator),
            timeout=_inference_timeout_s(),
        )
    except TimeoutError as exc:
        raise TimeoutError(f"inference stream timed out after {_inference_timeout_s():g}s") from exc


class _ThinkingStreamFilter:
    _PAIRS = (
        ("<think>", "</think>"),
        ("<thinking>", "</thinking>"),
        ("<reasoning>", "</reasoning>"),
        ("<redacted_reasoning>", "</redacted_reasoning>"),
    )

    def __init__(self) -> None:
        self._pending = ""
        self._close: str | None = None

    def feed(self, text: str) -> str:
        self._pending += text
        visible: list[str] = []
        while self._pending:
            low = self._pending.lower()
            if self._close is not None:
                idx = low.find(self._close)
                if idx < 0:
                    keep = max(0, len(self._close) - 1)
                    self._pending = self._pending[-keep:] if keep else ""
                    break
                self._pending = self._pending[idx + len(self._close) :]
                self._close = None
                continue
            found = [
                (low.find(open_tag), open_tag, close_tag)
                for open_tag, close_tag in self._PAIRS
                if low.find(open_tag) >= 0
            ]
            if not found:
                keep = max(len(open_tag) for open_tag, _ in self._PAIRS) - 1
                if len(self._pending) <= keep:
                    break
                visible.append(self._pending[:-keep])
                self._pending = self._pending[-keep:]
                break
            idx, open_tag, close_tag = min(found, key=lambda item: item[0])
            visible.append(self._pending[:idx])
            self._pending = self._pending[idx + len(open_tag) :]
            self._close = close_tag
        return "".join(visible)

    def flush(self) -> str:
        if self._close is not None:
            self._pending = ""
            return ""
        out = self._pending
        self._pending = ""
        return out


def _filter_stream_chunk_thinking(
    chunk: dict[str, Any],
    flt: _ThinkingStreamFilter,
) -> dict[str, Any] | None:
    out = dict(chunk)
    choices = out.get("choices")
    if not isinstance(choices, list):
        return out
    kept_choices: list[dict[str, Any]] = []
    for choice in choices:
        if not isinstance(choice, dict):
            kept_choices.append(choice)
            continue
        delta = choice.get("delta")
        if not isinstance(delta, dict) or not isinstance(delta.get("content"), str):
            kept_choices.append(choice)
            continue
        filtered = flt.feed(delta["content"])
        new_choice = dict(choice)
        new_delta = dict(delta)
        if filtered:
            new_delta["content"] = filtered
        else:
            new_delta.pop("content", None)
        new_choice["delta"] = new_delta
        if new_delta or choice.get("finish_reason") is not None:
            kept_choices.append(new_choice)
    out["choices"] = kept_choices
    return out if kept_choices or out.get("usage") is not None else None


def _stream_error(exc: Exception) -> dict[str, Any]:
    return {
        "error": {
            "message": str(exc),
            "type": "server_error",
            "code": "generation_failed",
        }
    }


def _loaded_ids_fn(engine) -> list[str]:
    fn = getattr(engine, "loaded_ids", None)
    if callable(fn):
        return fn()
    handles = getattr(engine, "_handles", None)
    if isinstance(handles, dict):
        return list(handles.keys())
    return []


def _engine_is_loaded(engine, model_id: str, *, embedding: bool = False) -> bool:
    try:
        return bool(engine.is_loaded(model_id, embedding=embedding))
    except TypeError:
        return bool(engine.is_loaded(model_id))


def _prepare_gguf_model(
    request: Request,
    model_id: str,
    rec: ModelRecord,
    params: dict[str, Any],
    *,
    embedding: bool = False,
) -> dict[str, Any]:
    engine = request.app.state.engine
    rm = request.app.state.resource_manager
    rm.set_pin(model_id, rec.pinned)
    started = time.perf_counter()
    loaded_before = _engine_is_loaded(engine, model_id, embedding=embedding)

    def unload_fn(mid: str) -> None:
        engine.unload(mid)

    def loaded_ids_fn() -> list[str]:
        return _loaded_ids_fn(engine)

    if not loaded_before:
        def pre_load_ids_fn() -> list[str]:
            loaded = loaded_ids_fn()
            return loaded if model_id in loaded else loaded + [model_id]

        rm.touch(model_id)
        rm.maybe_evict(
            loaded_ids=pre_load_ids_fn,
            unload_fn=unload_fn,
            reason="preload_overflow",
            trigger_model=model_id,
        )

    engine.load_gguf(
        model_id,
        rec.path,
        n_ctx=params["n_ctx"],
        n_gpu_layers=params["n_gpu_layers"],
        n_threads=params["threads"],
        n_batch=params["batch"],
        embedding=embedding,
    )
    rm.touch(model_id)
    rm.maybe_evict(
        loaded_ids=loaded_ids_fn,
        unload_fn=unload_fn,
        reason="postload_overflow",
        trigger_model=model_id,
    )
    return {
        "loaded": True,
        "loaded_before": loaded_before,
        "load_duration_s": round(time.perf_counter() - started, 6),
        "n_ctx": params["n_ctx"],
        "n_gpu_layers": params["n_gpu_layers"],
        "n_batch": params["batch"],
        "threads": params["threads"],
        "embedding": embedding,
        "vram_estimate_mb": rec.vram_estimate_mb or estimate_vram_stub_mb(rec.path, rec.format),
    }


def _normalize_embedding_response(raw: dict[str, Any], *, model: str) -> dict[str, Any]:
    out = dict(raw)
    out.setdefault("object", "list")
    # Same path-leak guard as `_normalize_chat_response` — force the
    # registered model id so we never return absolute .gguf paths to
    # the client.
    out["model"] = model
    out.setdefault("data", [])
    out.setdefault("usage", {})
    return out


@router.get("/models")
def list_models(request: Request):
    reg = _runtime_registry(request)
    _sync_pins(request, reg)
    engine = request.app.state.engine
    rows = []
    for mid, rec in reg.models.items():
        loaded = engine.is_loaded(mid)
        est = rec.vram_estimate_mb or estimate_vram_stub_mb(rec.path, rec.format)
        effective = _resolve_backend(rec.accelerator, rec.format)
        rows.append(
            {
                "id": mid,
                "object": "model",
                "owned_by": "omega-runtime-studio",
                "omega": {
                    "path": rec.path,
                    "format": rec.format,
                    "accelerator": rec.accelerator,
                    "effective_backend": effective,
                    "loaded": loaded,
                    "pinned": rec.pinned,
                    "vram_estimate_mb": est,
                },
            }
        )
    out: dict = {"object": "list", "data": rows}
    snap = getattr(request.app.state, "backend_snapshot", None)
    if snap is not None:
        out["omega_backend"] = snap.to_public_dict()
    return out


@router.get("/version")
def version():
    return {
        "version": __version__,
        "service": "omega-runtime-studio",
        "omega_variant": get_omega_variant(),
    }


@router.post("/chat/completions")
async def chat_completions(request: Request, body: ChatCompletionRequest):
    reg = _runtime_registry(request)
    _sync_pins(request, reg)
    if body.model not in reg.models:
        raise HTTPException(400, f"unknown model: {body.model}")
    rec = reg.models[body.model]
    engine = request.app.state.engine

    # Reject malformed response_format upfront — better than silently
    # passing garbage to llama-cpp-python and 500ing inside the engine.
    response_format_type = _validate_response_format(body.response_format)

    params = _resolve_generation_params(reg, body.model)
    messages = [m.model_dump() for m in body.messages]

    fmt = (rec.format or "").lower()
    if fmt not in ("gguf", "ggml"):
        raise HTTPException(400, f"format not supported for chat: {fmt}")

    try:
        await _run_with_timeout(_prepare_gguf_model, request, body.model, rec, params)
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(500, f"load failed: {exc}") from exc

    gen_temp = body.temperature if body.temperature is not None else params["temperature"]
    gen_top_p = body.top_p if body.top_p is not None else params["top_p"]
    extra = _chat_extra_kwargs(body)

    if body.stream:
        # OpenAI streaming spec: when stream_options.include_usage=true,
        # the server MUST send a dedicated tail chunk with choices=[] and
        # the populated usage block BEFORE [DONE]. All earlier chunks
        # carry usage=null. We capture llama-cpp-python's native usage
        # tail (it honors include_usage when passed through `extra`) and
        # rewrap it on a single canonical chunk so spec-strict clients
        # (langchain, openai-python, etc.) see exactly one usage report.
        include_usage = bool((body.stream_options or {}).get("include_usage"))

        async def gen():
            cmpl_id = f"chatcmpl-{uuid.uuid4()}"
            stream_filter = _ThinkingStreamFilter() if body.hide_thinking else None
            captured_usage: dict[str, Any] | None = None
            accumulated_content: list[str] = []
            iterator = None
            completed_normally = False
            try:
                iterator = iter(
                    engine.chat_completion_stream(
                        body.model,
                        messages=messages,
                        max_tokens=body.max_tokens,
                        temperature=float(gen_temp),
                        top_p=float(gen_top_p),
                        **extra,
                    )
                )
                while True:
                    has_chunk, chunk = await _next_stream_chunk(iterator)
                    if not has_chunk:
                        break
                    if include_usage:
                        raw_usage = chunk.get("usage")
                        if isinstance(raw_usage, dict) and any(
                            raw_usage.get(k) is not None
                            for k in ("prompt_tokens", "completion_tokens", "total_tokens")
                        ):
                            captured_usage = dict(raw_usage)
                            chunk = {k: v for k, v in chunk.items() if k != "usage"}
                            # llama-cpp-python's usage tail typically has
                            # `choices: []`; skip forwarding the now-empty
                            # carrier chunk — the dedicated usage chunk
                            # below is the canonical surface.
                            choices = chunk.get("choices")
                            if not isinstance(choices, list) or not any(
                                isinstance(c, dict) and c for c in choices
                            ):
                                continue
                        # Capture content tokens for the synthesizer below
                        # (engine fallback when llama doesn't emit usage).
                        for choice in chunk.get("choices") or []:
                            if isinstance(choice, dict):
                                delta = choice.get("delta") or {}
                                if isinstance(delta, dict):
                                    content = delta.get("content")
                                    if isinstance(content, str):
                                        accumulated_content.append(content)
                    if stream_filter is not None:
                        chunk = _filter_stream_chunk_thinking(chunk, stream_filter)
                        if chunk is None:
                            continue
                    # Drop llama-cpp-python's native finish_reason from
                    # intermediate chunks. Our own `end` chunk (below)
                    # is the canonical end-of-stream marker. Without
                    # this, hide_thinking-filtered streams would emit
                    # finish_reason BEFORE the flushed visible tail.
                    chunk = _strip_intermediate_finish_reason(chunk)
                    normalized = _normalize_chat_chunk(
                        chunk,
                        cmpl_id=cmpl_id,
                        model=body.model,
                    )
                    yield f"data: {json.dumps(normalized)}\n\n"
                if stream_filter is not None:
                    tail = stream_filter.flush()
                    if tail:
                        chunk = {
                            "choices": [{"index": 0, "delta": {"content": tail}}],
                        }
                        normalized = _normalize_chat_chunk(
                            chunk,
                            cmpl_id=cmpl_id,
                            model=body.model,
                        )
                        yield f"data: {json.dumps(normalized)}\n\n"
                end = {
                    "id": cmpl_id,
                    "object": "chat.completion.chunk",
                    "created": _created_now(),
                    "model": body.model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                }
                yield f"data: {json.dumps(end)}\n\n"
                # Synthesize usage chunk when client asked for it. The
                # OpenAI spec requires this chunk be emitted whenever
                # `stream_options.include_usage` is set. llama-cpp-python
                # doesn't natively emit it; we tokenize the prompt +
                # accumulated content ourselves via the engine's
                # tokenizer (same model that produced the stream, so
                # counts match what llama actually consumed/generated).
                if include_usage:
                    if captured_usage is None:
                        captured_usage = _synthesize_streaming_usage(
                            engine=engine,
                            model_id=body.model,
                            messages=messages,
                            accumulated_content="".join(accumulated_content),
                        )
                    if captured_usage is not None:
                        usage_chunk = {
                            "id": cmpl_id,
                            "object": "chat.completion.chunk",
                            "created": _created_now(),
                            "model": body.model,
                            "choices": [],
                            "usage": captured_usage,
                        }
                        yield f"data: {json.dumps(usage_chunk)}\n\n"
                yield "data: [DONE]\n\n"
                completed_normally = True
            except Exception as exc:
                yield f"data: {json.dumps(_stream_error(exc))}\n\n"
                yield "data: [DONE]\n\n"
                completed_normally = True
            finally:
                # Two reasons we end up here without completed_normally:
                #   1. Client disconnect — Starlette cancels the streaming
                #      task group, which raises CancelledError at the next
                #      yield; falls through to this finally.
                #   2. Server-side hard crash mid-stream.
                # In both cases we close the underlying generator so
                # llama-cpp-python releases its sequence slot. Without
                # this, an aborted long stream pins memory + a slot until
                # GC sweeps the generator — minutes later, under load.
                if iterator is not None:
                    close_fn = getattr(iterator, "close", None)
                    if callable(close_fn):
                        try:
                            close_fn()
                        except Exception:
                            pass
                if not completed_normally:
                    log.info(
                        "stream_disconnected: model=%s cmpl_id=%s",
                        body.model,
                        cmpl_id,
                    )

        return StreamingResponse(gen(), media_type="text/event-stream")

    try:
        raw = await _run_with_timeout(
            engine.chat_completion,
            body.model,
            messages=messages,
            max_tokens=body.max_tokens,
            temperature=float(gen_temp),
            top_p=float(gen_top_p),
            **extra,
        )
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(500, f"generate failed: {exc}") from exc

    normalized = _normalize_chat_response(raw, model=body.model)
    normalized = _apply_thinking_extension(normalized, hide=body.hide_thinking)
    # Deterministic repair of malformed tool_call arguments — local
    # models often emit code-fenced / single-quoted / Python-literal
    # JSON under tool-use templates. Strict clients (langchain,
    # openai-python tool routers) crash on the first malformed call;
    # repairing here keeps the OpenAI contract intact without an
    # extra round-trip.
    repair_response_tool_calls(normalized)
    if response_format_type in ("json_object", "json_schema"):
        # The contract is "model MUST emit valid JSON". Post-validate
        # the content; if llama-cpp's grammar enforcement was bypassed
        # or the model misbehaved, return 502 (upstream contract
        # violation) rather than handing the client a string they have
        # to re-parse and discover broken later.
        _enforce_json_response(normalized)
    return normalized


def _prepare_onnx_embedder(
    request: Request,
    model_id: str,
    rec: ModelRecord,
) -> dict[str, Any]:
    """Load + register an ONNX embedder under the same ``::embedding``
    handle namespace as GGUF embedders so the resource manager evicts
    them uniformly. UI overrides on the model record steer pooling /
    normalize / max_length / explicit tokenizer_path."""
    engine = request.app.state.engine
    rm = request.app.state.resource_manager
    rm.set_pin(model_id, rec.pinned)
    started = time.perf_counter()
    loaded_before = _engine_is_loaded(engine, model_id, embedding=True)

    def unload_fn(mid: str) -> None:
        engine.unload(mid)

    def loaded_ids_fn() -> list[str]:
        return _loaded_ids_fn(engine)

    if not loaded_before:
        def pre_load_ids_fn() -> list[str]:
            loaded = loaded_ids_fn()
            return loaded if model_id in loaded else loaded + [model_id]

        rm.touch(model_id)
        rm.maybe_evict(
            loaded_ids=pre_load_ids_fn,
            unload_fn=unload_fn,
            reason="preload_overflow",
            trigger_model=model_id,
        )

    ui = {str(k): v for k, v in (rec.ui_overrides or {}).items()}
    pooling = str(ui.get("pooling", "mean"))
    normalize = bool(ui.get("normalize", True))
    max_length = int(ui.get("max_length", 512))
    tokenizer_path = ui.get("tokenizer_path")

    engine.load_onnx_embedder(
        model_id,
        rec.path,
        pooling=pooling,
        normalize=normalize,
        max_length=max_length,
        tokenizer_path=str(tokenizer_path) if tokenizer_path else None,
    )
    rm.touch(model_id)
    rm.maybe_evict(
        loaded_ids=loaded_ids_fn,
        unload_fn=unload_fn,
        reason="postload_overflow",
        trigger_model=model_id,
    )
    return {
        "loaded": True,
        "loaded_before": loaded_before,
        "load_duration_s": round(time.perf_counter() - started, 6),
        "pooling": pooling,
        "normalize": normalize,
        "max_length": max_length,
        "embedding": True,
        "format": "onnx",
    }


@router.post("/embeddings")
async def embeddings(request: Request, body: EmbeddingsRequest):
    reg = _runtime_registry(request)
    _sync_pins(request, reg)
    if body.model not in reg.models:
        raise HTTPException(400, f"unknown model: {body.model}")
    rec = reg.models[body.model]
    fmt = (rec.format or "").lower()
    if fmt in ("gguf", "ggml"):
        params = _resolve_generation_params(reg, body.model)
        try:
            await _run_with_timeout(
                _prepare_gguf_model,
                request,
                body.model,
                rec,
                params,
                embedding=True,
            )
            raw = await _run_with_timeout(
                request.app.state.engine.create_embedding,
                body.model,
                body.input,
            )
        except Exception as exc:
            if isinstance(exc, HTTPException):
                raise
            raise HTTPException(500, f"embedding failed: {exc}") from exc
        return _normalize_embedding_response(raw, model=body.model)
    if fmt == "onnx":
        try:
            await _run_with_timeout(_prepare_onnx_embedder, request, body.model, rec)
            raw = await _run_with_timeout(
                request.app.state.engine.create_embedding,
                body.model,
                body.input,
            )
        except Exception as exc:
            if isinstance(exc, HTTPException):
                raise
            raise HTTPException(500, f"embedding failed: {exc}") from exc
        return _normalize_embedding_response(raw, model=body.model)
    raise HTTPException(400, f"format not supported for embeddings: {fmt}")


@router.get("/studio/registry")
def studio_registry_debug(request: Request):
    """Non-standard helper for local operators."""
    reg = _persisted_registry(request)
    return json.loads(reg.model_dump_json())


@router.post("/studio/registry/rescan")
def rescan_studio_registry(request: Request):
    """Rescan configured model folders and persist newly discovered files."""
    # Re-read disk to avoid clobbering operator edits made outside the running process.
    reg = load_registry()
    reg, added = merge_scan_into_registry(reg)
    _save_request_registry(request, reg)
    _sync_pins(request, apply_env_overrides(reg))
    return {"ok": True, "added": added, "total": len(reg.models)}


@router.post("/studio/registry/folders")
def update_studio_registry_folders(request: Request, body: RegistryFoldersRequest):
    """Replace configured model folders for headless operators."""
    reg = _persisted_registry(request)
    reg.model_folders = [str(x).strip() for x in body.folders if str(x).strip()]
    if not reg.model_folders:
        raise HTTPException(422, "folders must contain at least one non-empty path")
    _save_request_registry(request, reg)
    return {"ok": True, "model_folders": list(reg.model_folders)}


@router.patch("/studio/models/{model_id}")
def patch_studio_model(request: Request, model_id: str, body: StudioModelPatch):
    """Update registry model row (persisted JSON). Same fields the GUI writes locally."""
    reg = _persisted_registry(request)
    if model_id not in reg.models:
        raise HTTPException(404, f"unknown model: {model_id}")
    rec: ModelRecord = reg.models[model_id]
    if body.ui_overrides is not None:
        rec.ui_overrides = {str(k): v for k, v in body.ui_overrides.items()}
    if body.accelerator is not None:
        rec.accelerator = str(body.accelerator)
    if body.pinned is not None:
        rec.pinned = bool(body.pinned)
    _save_request_registry(request, reg)
    return {"ok": True, "id": model_id}
