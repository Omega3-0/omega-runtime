# Omega Runtime Studio — API Reference

Base URL: `http://<host>:<port>/v1` (default `http://127.0.0.1:11434/v1`).

The API is a strict subset of the OpenAI Chat / Embeddings API plus a small
Studio-only management surface. Everything is HTTP/JSON; streaming endpoints
use Server-Sent Events (SSE) with `Content-Type: text/event-stream`.

## Authentication

When `OMEGA_API_KEY` is set in the server environment, every request to
`/v1/*` and `/admin/*` must carry `Authorization: Bearer <key>`. Otherwise
the gate is disabled and any request is accepted (LAN / single-operator
default).

| Failure | Status | Body |
|---------|--------|------|
| No `Authorization` header | `401` | `{"detail": "missing_bearer_token"}` |
| Wrong token | `403` | `{"detail": "invalid_api_key"}` |

CORS preflights (`OPTIONS` with `Access-Control-Request-Method`) bypass the
auth gate per spec — browsers can't carry credentials on preflight.

## Headers on every response

| Header | Meaning |
|--------|---------|
| `X-Request-ID` | UUID hex (or the sanitized client-supplied value). Logged on every record emitted while serving the request — grep this to trace a single request end-to-end. |
| `Retry-After` | Only present on `503 service_draining` during graceful shutdown. |

## Request size limit

`POST` / `PATCH` bodies are capped at `OMEGA_MAX_REQUEST_BYTES` (default
10 MiB). Bodies past the limit return `413 request_too_large` before the
handler runs.

## Endpoints

### `GET /v1/models`

List models known to the registry. Each entry includes accelerator hints
when the backend snapshot detected them.

```json
{
  "object": "list",
  "data": [
    {
      "id": "qwen2.5-7b-instruct-q4_k_m",
      "object": "model",
      "created": 1715620000,
      "owned_by": "local",
      "omega_backend": "...optional accelerator detail..."
    }
  ]
}
```

### `POST /v1/chat/completions`

OpenAI-compatible chat completion. GGUF / GGML only; other formats return
`400 format not supported for chat`.

**Request body** (selected fields):

| Field | Type | Notes |
|-------|------|-------|
| `model` | string | Must exist in `/v1/models` |
| `messages` | array | `min_length=1`; standard OpenAI shape (text or content-parts) |
| `max_tokens` | int | Default `256` |
| `temperature`, `top_p` | float | Optional; falls back to registry defaults |
| `stream` | bool | `true` = SSE response |
| `stream_options.include_usage` | bool | When true, server emits a dedicated tail chunk with `usage` populated (see Streaming below) |
| `tools`, `tool_choice` | passthrough | Forwarded to llama-cpp-python |
| `response_format` | object | `{"type": "text"\|"json_object"\|"json_schema"}` — see JSON mode below |
| `stop` | string \| array | Forwarded |
| `seed`, `n`, `presence_penalty`, `frequency_penalty`, `logprobs`, `logit_bias`, `user` | passthrough | Forwarded |
| `hide_thinking` | bool | Studio extension: strip `<think>...</think>` blocks; thinking moves to `choices[].omega.thinking_block` |

**Non-streaming response** (`stream: false`, default):

```json
{
  "id": "chatcmpl-<uuid>",
  "object": "chat.completion",
  "created": 1715620000,
  "model": "qwen2.5-7b-instruct-q4_k_m",
  "choices": [
    {
      "index": 0,
      "message": {"role": "assistant", "content": "..."},
      "finish_reason": "stop"
    }
  ],
  "usage": {"prompt_tokens": 42, "completion_tokens": 64, "total_tokens": 106}
}
```

**Errors**:

| Status | Detail | Cause |
|--------|--------|-------|
| `400` | `unknown model: ...` | `body.model` not in registry |
| `400` | `format not supported for chat: ...` | Non-GGUF model |
| `400` | `response_format.type must be one of [...]` | Invalid `response_format` shape |
| `502` | `model returned invalid JSON under response_format=json_*; ...` | JSON mode contract violation (see below) |
| `504` | `inference timed out after Ns` | `OMEGA_INFERENCE_TIMEOUT_S` exceeded |
| `503` | `service_draining` | Server is in graceful shutdown (see Drain below) |

#### Streaming

When `stream: true`, the response is `text/event-stream` with the canonical
shape:

```
data: {"id":"chatcmpl-<uuid>","object":"chat.completion.chunk","created":...,"model":"...","choices":[{"index":0,"delta":{"role":"assistant"}}]}

data: {"id":"chatcmpl-<uuid>","object":"chat.completion.chunk","created":...,"model":"...","choices":[{"index":0,"delta":{"content":"Hel"}}]}

data: {"id":"chatcmpl-<uuid>","object":"chat.completion.chunk","created":...,"model":"...","choices":[{"index":0,"delta":{"content":"lo"}}]}

data: {"id":"chatcmpl-<uuid>","object":"chat.completion.chunk","created":...,"model":"...","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

Guarantees:

- **One ID per stream.** Every chunk shares the same `chatcmpl-<uuid>` —
  llama-cpp-python's native per-chunk IDs are normalized out.
- **`finish_reason` only at the end.** The final-content chunk is the
  canonical end-of-stream marker; intermediate chunks have
  `finish_reason: null`.
- **Closure on disconnect.** If the client drops mid-stream, the engine
  generator is `close()`'d so the sequence slot + KV cache are released
  immediately (no minutes-long GC delay).

##### Usage tail chunk (`stream_options.include_usage = true`)

When requested, a final chunk with `choices: []` carries the `usage` block,
emitted AFTER the `finish_reason` chunk and BEFORE `[DONE]`:

```
data: {"id":"chatcmpl-<uuid>","object":"chat.completion.chunk","created":...,"model":"...","choices":[],"usage":{"prompt_tokens":7,"completion_tokens":2,"total_tokens":9}}

data: [DONE]
```

Intermediate chunks have no `usage` field — strict-spec clients (langchain,
openai-python) only parse the dedicated tail.

##### Error mid-stream

If the engine raises mid-generation, the stream emits a single
`data: {"error": {...}}` chunk then `data: [DONE]`. The error envelope is:

```json
{
  "error": {
    "message": "...",
    "type": "server_error",
    "code": "generation_failed"
  }
}
```

#### Response format / JSON mode

`response_format` shapes the contract the server enforces on the model
output:

- `{"type": "text"}` — default; no enforcement
- `{"type": "json_object"}` — model must emit parseable JSON; server
  parses and attaches the result under `choices[].omega.json` for client
  convenience
- `{"type": "json_schema", "json_schema": {"name": "...", "schema": {...}}}`
  — same parse contract as `json_object`; schema validation is a future
  follow-up (currently parsed but not schema-checked)

On contract violation, the server returns `502` with details:

```json
{"detail": "model returned invalid JSON under response_format=json_*; choice index=0; error=Expecting value; preview='Sure, here is your JSON: {oops}'"}
```

#### `hide_thinking`

When `hide_thinking: true`:

- Non-streaming: thinking blocks (`<think>...</think>`,
  `<thinking>...</thinking>`, `<reasoning>...</reasoning>`,
  `<redacted_reasoning>...</redacted_reasoning>`) are stripped from
  `choices[].message.content` and moved to
  `choices[].omega.thinking_block`.
- Streaming: the same blocks are filtered chunk-by-chunk by a tag-aware
  state machine so they never reach the wire (no token leak even if the
  closing tag spans chunk boundaries).

### `POST /v1/embeddings`

OpenAI-compatible embeddings. Currently GGUF / GGML only; other formats
return `400 format not supported for embeddings`.

**Request body**:

| Field | Type | Notes |
|-------|------|-------|
| `model` | string | Must exist; loaded with `embedding=True` |
| `input` | string \| array | Any shape llama-cpp-python's `create_embedding` accepts |
| `encoding_format` | string | Passthrough |
| `user` | string | Passthrough |

**Response**:

```json
{
  "object": "list",
  "model": "bge-m3-q8_0",
  "data": [
    {"object": "embedding", "index": 0, "embedding": [0.123, -0.456, ...]}
  ],
  "usage": {"prompt_tokens": 8, "total_tokens": 8}
}
```

### `GET /v1/version`

Returns service identity + build variant:

```json
{
  "version": "0.1.0",
  "service": "omega-runtime-studio",
  "omega_variant": "cpu"
}
```

`omega_variant` is one of `cpu`, `cuda`, `vulkan`, `dml` — set at build
time via `OMEGA_VARIANT`.

### Studio-only endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /v1/studio/registry` | Full registry view (models + settings) |
| `POST /v1/studio/registry/rescan` | Rescan `model_folders`, persist new files |
| `POST /v1/studio/registry/folders` | Replace `model_folders` for headless operators |
| `PATCH /v1/studio/models/{id}` | Update registry row + `ui_overrides` |
| `POST /v1/models/hub/download` | Start HF file download (returns `job_id`) |
| `GET /v1/models/hub/download/{job_id}/status` | Poll job (see Hub Downloads below) |
| `GET /v1/models/hub/app-data` | Reveal resolved per-user model directory |

### Admin endpoints

| Endpoint | Purpose |
|----------|---------|
| `POST /admin/models/{id}/load` | Warm a model into memory now |
| `POST /admin/models/{id}/unload` | Release a model's handle |

Admin paths require the API key when configured.

### `GET /health`

Always returns `200 {"status": "ok"}`, including during graceful drain —
load balancers and monitoring tools should poll `/health` to detect the
shutting-down state via the operator's logs, not by closing this endpoint.

## Hub downloads

`POST /v1/models/hub/download`:

```json
{"repo_id": "Qwen/Qwen2.5-7B-Instruct-GGUF", "filename": "qwen2.5-7b-instruct-q4_k_m.gguf", "dest_subdir": ""}
```

Response:

```json
{"job_id": "<uuid>", "dest_dir": "C:\\Users\\<u>\\AppData\\Local\\Omega3Portable\\models", "poll": "/v1/models/hub/download/<uuid>/status"}
```

Poll the `poll` URL until `status == "done"` or `"error"`:

```json
{
  "job_id": "<uuid>",
  "status": "running",
  "progress": 0.42,
  "message": "downloading",
  "result_path": null,
  "error": null,
  "bytes_done": 1879048192,
  "bytes_total": 4471884800,
  "rate_mbps": 28.4,
  "eta_seconds": 91.2,
  "recent_events": [
    {"progress": 0.41, "bytes_done": 1834237952, "bytes_total": 4471884800, "rate_mbps": 28.4, "message": "downloading"}
  ]
}
```

`bytes_done` / `bytes_total` populate as soon as the upstream Content-Length
is known. `rate_mbps` is averaged over a 1.5s sliding window so the
displayed number is stable; `eta_seconds` falls out of that.
`recent_events` is sampled at ≥1% progress changes to keep the 64-deep
ring buffer useful across long downloads.

## Graceful drain (shutdown semantics)

When the server starts shutting down (SIGINT, SIGTERM, lifespan exit), a
drain phase fires:

1. The in-flight request counter is captured.
2. New `/v1/*` and `/admin/*` requests return `503 service_draining` with
   `Retry-After: 5`.
3. The server waits up to `OMEGA_DRAIN_TIMEOUT_S` (default 30s) for the
   counter to reach 0, then yields to uvicorn's hard stop.
4. `/health` stays open the entire time so monitoring sees the
   shutting-down state.

Operator log lines during drain:

```
shutdown: draining 3 in-flight request(s)...
shutdown: drained 3 request(s) in 1.27s
```

Or, on timeout:

```
shutdown: drain timeout after 30.00s; 1 request(s) still in-flight, forcing exit
```

## Eviction observability

When the resource manager evicts a loaded model to make room for a new
one, it logs a structured line per eviction:

```
eviction: model=old_model reason=preload_overflow trigger=incoming_model idle_s=237.41 loaded_before=3 loaded_after=2 max=2 pinned_skipped=['pinned_a']
```

Fields:

- `reason` — `preload_overflow` (about to load new model) or
  `postload_overflow` (recovery sweep after a load completed)
- `trigger` — the incoming model whose load forced the eviction
- `idle_s` — seconds since the evicted model was last touched
- `pinned_skipped` — models that were considered but skipped because they
  were pinned

If all candidates are pinned and overflow can't be cleared, a warning fires:

```
eviction_blocked_by_pins: reason=preload_overflow trigger=newcomer loaded=2 max=1 evicted=0 still_over=1 pinned=['pin1', 'pin2']
```

## Environment variables (runtime tuning)

| Variable | Default | Effect |
|----------|---------|--------|
| `OMEGA_API_KEY` | — | Bearer token required on `/v1/*` + `/admin/*` when set |
| `OMEGA_MAX_REQUEST_BYTES` | 10485760 | Hard cap on POST/PATCH body size |
| `OMEGA_INFERENCE_TIMEOUT_S` | 300 | `chat.completions` (non-streaming) timeout; returns `504` on expiry |
| `OMEGA_DRAIN_TIMEOUT_S` | 30 | Max time the server waits for in-flight requests on shutdown |
| `OMEGA_CORS_ALLOW_ORIGIN_REGEX` | `^http://(127\.0\.0\.1\|localhost)(:\d+)?$` | CORS allow-list for the GUI / external apps |
| `OMEGA_VARIANT` | `cpu` | Build variant tag; surfaced on `/v1/version` |
| `OMEGA_ORT_EP_ORDER` | — | ONNX Runtime execution-provider preference (comma-separated; aliases accepted) |
| `OMEGA_LLAMA_CPP_INDEX` | abetlen CPU wheel index | pip extra-index URL for llama-cpp-python wheels |
| `HF_TOKEN` / `HUGGING_FACE_HUB_TOKEN` | — | Auth for HF downloads |

## Tracing a single request

Every request:

1. Allocates an `X-Request-ID` (UUID hex), or sanitizes the client-supplied
   one.
2. Binds the ID to an async contextvar.
3. Every log record emitted during the request gets `record.request_id`
   stamped on it via the `RequestIdLogFilter`.
4. The same ID is returned to the client as `X-Request-ID`.

To trace a single request end-to-end:

```
# Server logs:
grep "<request-id-hex>" omega_studio.log

# Or, in a structured logging setup, filter by the request_id field.
```

Even auth-rejected (`401`/`403`) and drain-rejected (`503`) responses carry
`X-Request-ID` — those are the responses operators most often want to
trace forensically.
