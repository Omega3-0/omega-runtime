# Omega Runtime Studio

**Canonical product name.** Sovereign, Windows-first local model control plane: **FastAPI** `/v1` API + **PySide6** GUI. No third-party inference SaaS — you own weights, accelerators, and data on disk.

**Quickstart (shipped build):** unzip `dist\Omega3.0-portable\`, double-click **`Omega3.0-portable.exe`**, then point any OpenAI-compatible client at **`http://127.0.0.1:11434/v1`** (change port in **Settings → Server** if needed). Optional: run **`Omega3.0-portable-Server.exe serve`** headless instead of the GUI.

**Telemetry:** **NONE** by default — no phone-home, no anonymous metrics, no update beacons. (You may opt into external HF downloads; that traffic goes only where you configure.)

**Docs:**
- [`docs/operator_handbook.md`](docs/operator_handbook.md) — run / deploy / troubleshoot / migrate
- [`docs/api_reference.md`](docs/api_reference.md) — full API surface, streaming guarantees, env vars, observability
- [`docs/code_signing.md`](docs/code_signing.md) — Authenticode signing, SmartScreen reputation, release manifest

**Contributing:** see [`CONTRIBUTING.md`](CONTRIBUTING.md) for dev setup, test commands, and PR conventions. Bug reports and feature proposals via the [Issues](https://github.com/Omega3-0/omega-runtime/issues) tab use structured templates.

## Why this exists (vs LM Studio / Ollama / Jan)

- **True sovereignty:** operator-controlled bundle, optional air-gapped layout, registry and profiles under **your** `%LOCALAPPDATA%` — not a vendor cloud account model.
- **Omega 3.0 alignment:** same HTTP surface Omega expects (`/v1`); ships beside or instead of heavier internal runtimes.
- **Accelerator transparency:** vendor DLL trees (see **Vendor accelerators**) and ORT provider order are visible and tunable — not an opaque “auto GPU” black box alone.

## Aliases (legacy names — same binaries)

| What you see | Meaning |
|----------------|---------|
| **Omega3.0 portable** | Distribution / zip branding and PyInstaller output folder name |
| **`OmegaRuntimeStudio`** | Source repository folder on disk |
| **`Omega3.0-portable.exe`** | Windowed **GUI** entry |
| **`Omega3.0-portable-Server.exe`** | Console **CLI** (`serve`, `daemon`, `daemon-stop`) |
| **`omega3-portable`** | `pip install` console script → same CLI as Server exe |
| **`omega3-portable-gui`** | Console script → GUI module |
| **`sync_lemonade_vendor.ps1`** | Deprecated — forwards to **`sync_vendor_accelerators.ps1`** |

**Search / docs:** prefer **“Omega Runtime Studio”**; the table above maps older strings.

## OpenAI-compatible API (what works today)

Use base URL `http://<host>:<port>/v1`. Below is the **subset** implemented vs the full OpenAI surface.

| Endpoint | Status | Notes |
|----------|--------|-------|
| `GET /v1/models` | Yes | Includes `omega_backend` / accelerator hints where available |
| `POST /v1/chat/completions` | Yes | GGUF via **llama-cpp-python** `create_chat_completion`; uses GGUF chat templates; **SSE** when `stream: true`; real `usage` when backend returns it |
| `POST /v1/completions` | No | Not implemented |
| `POST /v1/embeddings` | Partial | GGUF / GGML via llama-cpp-python `create_embedding` when the loaded model/backend supports embeddings |
| `POST /v1/audio/transcriptions` | No | Not implemented |
| Tool / function calling | Partial | `tools` / `tool_choice` are passed through to llama-cpp-python; depends on model template and backend support |
| Logprobs | Partial | `logprobs` is accepted and passed through; backend support varies |
| `GET /v1/studio/registry` | Studio-only | Not OpenAI |
| `POST /v1/studio/registry/rescan` | Studio-only | Rescans configured model folders, persists new files, updates in-memory registry |
| `POST /v1/studio/registry/folders` | Studio-only | Replace `model_folders` for headless operators |
| `PATCH /v1/studio/models/{id}` | Studio-only | Registry + `ui_overrides` |
| `POST /admin/models/{id}/load` / `unload` | Studio-only | Explicitly warm or release a GGUF/GGML model |
| `GET /v1/version` | Studio-only | Returns Studio service version |
| `POST /v1/models/hub/download` | Studio-only | HF file download job |
| `GET /v1/models/hub/download/{job_id}/status` | Studio-only | Poll job status |

**Drop-in OpenAI Python SDK:** works for **`chat.completions.create`** on supported GGUF chat models. Studio passes common OpenAI fields through (`tools`, `tool_choice`, `response_format`, `stop`, `seed`, penalties, `logit_bias`, `user`, `stream_options`) but final behavior still depends on the loaded model and llama-cpp-python build.

```python
from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:11434/v1", api_key="not-needed")
r = client.chat.completions.create(
    model="your-registered-model-id",
    messages=[{"role": "user", "content": "Hello"}],
)
print(r.choices[0].message.content)
```

```bash
curl -s http://127.0.0.1:11434/v1/models
curl -s http://127.0.0.1:11434/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"your-model-id","messages":[{"role":"user","content":"Hi"}]}'
```

## Requirements

- **Python:** see **Python compatibility** below.
- **OS:** Windows is the primary target. App data: `%LOCALAPPDATA%\Omega3Portable\` (registry, `backend_profile.json`, GUI settings, daemon logs unless overridden).
- **Inference backend:** full/runtime bundles include `llama-cpp-python`; a “min” bundle without it can serve registry, hub, and admin surfaces but cannot chat or embed locally.

### Python compatibility

| Version | Editable install / dev | Embedded bootstrap default | Notes |
|---------|-------------------------|-----------------------------|--------|
| **3.11** | Supported (`requires-python >=3.11`) | Override `-PythonVersion 3.11.x` on `bootstrap_embedded_python.ps1` | Ruff `target-version = py311` |
| **3.12** | Supported | **Default** embed zip in `bootstrap_embedded_python.ps1` | Recommended for new bundles |
| **3.13** | Try-at-own-risk | Not the default embed pin | May work; not CI-guaranteed here |

If both **3.11** and **3.12** work for your stack, pick **one** per release bundle and lock with `requirements-lock.txt`.

## Install (development)

```powershell
cd F:\OmegaRuntimeStudio
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu/ `
            -r requirements-dev.txt
pip install -e .
```

The `--extra-index-url` is **important on Windows**. PyPI ships only a source tarball for
`llama-cpp-python`; pip extracts it under `%TEMP%` and the bundled Svelte tree
(`vendor/llama.cpp/tools/server/webui/...`) blows past Windows' 260-character MAX_PATH limit,
failing with `[Errno 2] No such file or directory`. abetlen's index serves pre-built wheels
so pip skips the source compile entirely (~2s install vs ~10min compile that fails anyway).

GPU variants (swap into the same `--extra-index-url` slot):

| Wheel index suffix | Backend |
|---|---|
| `/cpu/` | CPU only (default) |
| `/cu121/` | CUDA 12.1 |
| `/cu122/` | CUDA 12.2 |
| `/metal/` | macOS Metal |

For `build_windows.ps1` / `build_release.ps1`, override via:
```powershell
$env:OMEGA_LLAMA_CPP_INDEX = "https://abetlen.github.io/llama-cpp-python/whl/cu122/"
.\scripts\build_release.ps1
```

## Launch modes

| Mode | Who | Command |
|------|-----|---------|
| **GUI** | Operators | `omega3-portable-gui` or `python -m omega_studio` · shipped: **`Omega3.0-portable.exe`** |
| **API (foreground)** | Blocks; console logs | `omega3-portable serve …` · **`Omega3.0-portable-Server.exe serve …`** |
| **Daemon** | Background; no tray | `omega3-portable daemon --pid-file … --log-file …` |

There is no `--foreground` on **daemon** — use **`serve`** for foreground.

### Stop daemon

If you started with `--pid-file`:

```powershell
omega3-portable daemon-stop --pid-file "$env:LOCALAPPDATA\Omega3Portable\daemon.pid"
```

Or by PID:

```powershell
omega3-portable daemon-stop --pid 12345
```

**PowerShell (no CLI):** `Stop-Process -Id (Get-Content $env:LOCALAPPDATA\Omega3Portable\daemon.pid) -Force`

### Run API (foreground)

```powershell
omega3-portable serve --host 127.0.0.1 --port 11434
```

### Daemon (headless)

```powershell
omega3-portable daemon --port 11434 `
  --pid-file "$env:LOCALAPPDATA\Omega3Portable\daemon.pid" `
  --log-file "$env:LOCALAPPDATA\Omega3Portable\daemon.log"
```

## Vendor accelerators (harvested binaries only)

**Important:** This project does **not** depend on any retired third-party *server* product. Operators **reuse harvested native binaries** (llama.cpp builds, Ryzen AI ORT stacks, etc.). Your external harvest tree may still use a top-level `lemonade\bin` folder on disk — that is **only the sync source path**; Studio installs them under **`vendor/accelerators/bin/`** (DLLs and exes, not a network service).

- **Canonical on-disk layout:** `vendor/accelerators/bin/...` after `sync_vendor_accelerators.ps1`.
- **Legacy:** older drops may still have `vendor/lemonade/bin/`; Studio **prefers** `vendor/accelerators/bin` and **falls back** to `vendor/lemonade/bin` when the new folder is absent.
- **`sync_lemonade_vendor.ps1`** remains as a **deprecated wrapper** that forwards to the script below (emits a warning).

```powershell
.\scripts\sync_vendor_accelerators.ps1 -VendorDest F:\OmegaRuntimeStudio
.\scripts\sync_vendor_accelerators.ps1 -VendorDest F:\OmegaRuntimeStudio -PostBuildDist F:\OmegaRuntimeStudio\dist\Omega3.0-portable
```

Set **`OMEGA_BUNDLE_ROOT`** to the folder that contains **`vendor/`** and **`models/`**.

## Hardware / accelerator matrix (out-of-the-box)

Studio now ships as variant-specific bundles. Pick the ZIP matching your hardware for zero-touch acceleration:

| Accelerator | Variant ZIP | Hardware |
| :--- | :--- | :--- |
| CPU (fallback) | `Omega3.0-portable-cpu-x.y.z.zip` | All |
| NVIDIA CUDA | `Omega3.0-portable-cuda-x.y.z.zip` | NVIDIA RTX / Tesla |
| Vulkan | `Omega3.0-portable-vulkan-x.y.z.zip` | AMD / Intel / NVIDIA |

*   **Zero-touch:** No post-install sync required.
*   **Variant detection:** `/v1/version` reports the variant; `/v1/models` reports effective backends.

## Model format matrix

| Format | Load in Studio | Notes |
|--------|----------------|-------|
| **GGUF** | Yes (llama-cpp-python) | Primary path |
| **ONNX** (chat) | Roadmap | ORT EP detection exists; chat path not wired |
| **SafeTensors / HF Transformers** | Export / external | Not loaded natively; use hub download then external converters |
| **GPTQ / AWQ** | — | Not built-in |

## Environment variables (acceleration hints)

| Variable | Purpose |
|----------|---------|
| `OMEGA_BUNDLE_ROOT` | Bundle root with `vendor/` + `models/` |
| `OMEGA_RUNTIME_HARVEST` | Optional external harvest root; sync script reads `<harvest>/lemonade\bin` → copies to `vendor/accelerators/bin` |
| `OMEGA_GLOBAL_N_CTX` / `N_GPU_LAYERS` / `TEMPERATURE` / `TOP_P` | Defaults |
| `OMEGA_MAX_CONCURRENT_MODELS` | Cap (max **15**); LRU eviction when enabled in registry |
| `OMEGA_ORT_EP_ORDER` | Comma ORT EP preference override |
| `OMEGA_API_KEY` | Require `Authorization: Bearer` on `/v1/*` and `/admin/*` |
| `HF_TOKEN` / `HUGGING_FACE_HUB_TOKEN` | Token passed to Hugging Face downloads for gated repos |
| `OMEGA_CORS_ALLOW_ORIGIN_REGEX` | Allowed browser origins for CORS (default loopback only) |
| `OMEGA_MAX_REQUEST_BYTES` | Max HTTP request body size; default **10 MiB**, `0` disables |
| `OMEGA_INFERENCE_TIMEOUT_S` | Chat / embedding call timeout; default **300s** |
| `OMEGA_DAEMON_LOG_MAX_BYTES` | Rotate daemon log at startup when larger than this; default **10 MiB**, `0` disables |

## Omega 3.0 client

Base URL: `http://127.0.0.1:<port>/v1` (match **Server** port).

## Relationship to **omega-portable-lite**

**Complementary:** **omega-portable-lite** (internal) is the heavier multi-modal **Omega cognition substrate** (GGUF, ONNX, SetFit, face, SD, etc.). **Omega Runtime Studio** is the **operator-facing** control plane + branded portable drop for sovereign installs and Omega 3.0 HTTP clients. Pick **Lite** when you need the full embedded runtime stack in-tree; pick **Studio** when you want a focused Windows product bundle with GUI + retail packaging story.

## Portable bundle layout

See previous releases for **`models/`**, **`vendor/`**, optional **`python/`**, **`run-server.bat`**, **`set_env.bat`**, **`Omega3.0-portable.env.example`**.

## Production bundle, prototype bundle, release build

See **Retail checklist** below and scripts:

- `scripts/bootstrap_embedded_python.ps1`
- `scripts/create_portable_venv.ps1`
- `scripts/build_windows.ps1` / `scripts/build_release.ps1`
- `scripts/build_prototype_bundle.ps1`
- `scripts/sign_windows.ps1`

### Prototype bundle vs lean frozen exe

| | **Lean frozen** | **Prototype bundle** |
|---|-----------------|------------------------|
| Layout | PyInstaller `_internal` | + sidecar `python\` with `pip install --target` |
| Tradeoff | Smaller | Larger; closer to dev `pip` |

## Code signing

`.\scripts\sign_windows.ps1` — then `signtool verify /pa …`

## Retail checklist

1. **Build:** `.\scripts\build_release.ps1`
2. **Vendor:** `sync_vendor_accelerators.ps1 -PostBuildDist …`
3. **Profile:** `%LOCALAPPDATA%\Omega3Portable\backend_profile.json`
4. **Sign** both exes
5. **Installer:** `packaging\omega3-portable.iss` (Inno) — `AppId` is a stable **GUID** for Windows “Programs and Features”. It does **not** control where Studio stores data; operator files stay under **`%LOCALAPPDATA%\Omega3Portable\`** (see **Backup and migration**).
6. **`OMEGA_API_KEY`** for exposed hosts
7. **HF jobs:** poll status; job records persist in `hub_jobs.sqlite`
8. **Streaming:** SSE for `stream: true` when llama.cpp available

### Known gaps (extended)

Already documented elsewhere: WiX, ONNX chat, HF private token UI, SmartScreen process.

**Also:**

- **Log rotation:** `daemon.log` rotates once at daemon startup when it exceeds `OMEGA_DAEMON_LOG_MAX_BYTES`; there is no continuous in-process rotation while the child server is running.
- **CORS:** local browser origins are allowed by default via `OMEGA_CORS_ALLOW_ORIGIN_REGEX` (default: `127.0.0.1` / `localhost` with any port). Tighten this before exposing beyond loopback.
- **Request size:** `OMEGA_MAX_REQUEST_BYTES` defaults to **10 MiB**; set `0` to disable or a smaller integer for stricter deployments.
- **Rate limiting:** none on the API — use a reverse proxy if exposed.
- **Model eviction:** when `OMEGA_MAX_CONCURRENT_MODELS` / registry cap is hit, **LRU eviction** applies if enabled in settings; pinned models are skipped — tune in GUI **Models** / registry.
- **Registry runtime overlay:** env / probe-derived values (`--port`, `OMEGA_*`, CPU-only backend hints) are applied at runtime and are **not persisted** back to `registry.json`.
- **External registry edits:** `rescan` re-reads `registry.json` from disk before saving, and headless operators can update folders with `POST /v1/studio/registry/folders`.
- **Registry migration:** `%LOCALAPPDATA%\Omega3Portable\registry.json` has **no automatic version migration** — back up before upgrades (see **Backup**).
- **Hub jobs:** download jobs persist in `%LOCALAPPDATA%\Omega3Portable\hub_jobs.sqlite`; active in-process workers do not resume mid-download after a crash, but completed/error job records survive restart.

## Backup and migration

User state lives primarily under **`%LOCALAPPDATA%\Omega3Portable\`**:

- `registry.json` — model folders, per-model rows, server defaults, LRU / sampling settings
- `backend_profile.json` — last good ORT / GPU probe
- `gui_settings.json` — Backend tab env overrides **and** GUI session restore (window geometry, last tab, Playground model / max_tokens / thinking toggle, “PATCH running server” checkbox, Downloads form fields, last selected model id)
- `hub_jobs.sqlite` — persisted Hub download job status records
- `daemon.log` / `daemon.pid` — if you use defaults

**Move machines:** copy that folder (or export registry JSON from the GUI) after stopping the server. Restore before first start on the new PC.

## Troubleshooting

| Symptom | Check |
|---------|--------|
| Port in use | Change `--port` or kill the old `daemon-stop` |
| `llama-cpp-python` errors | `pip install llama-cpp-python` matching your CUDA/CPU wheel; embeddings require backend `create_embedding` support |
| Model path not found | Paths in registry relative to `models/` under bundle or app data |
| Browser client blocked by CORS | Use loopback origins or set `OMEGA_CORS_ALLOW_ORIGIN_REGEX` |
| Gated HF download fails | Set `HF_TOKEN` or `HUGGING_FACE_HUB_TOKEN` before starting Studio; progress uses Studio's streaming downloader when polling Hub jobs |
| HTTP 413 | Raise `OMEGA_MAX_REQUEST_BYTES` or reduce request size |
| GPU layers too high | Lower `n_gpu_layers` in per-model UI overrides |
| Corrupt `registry.json` | Restore from backup; Studio may fail to start if JSON is invalid |

## Architecture (one paragraph)

**Omega Runtime Studio** GUI (**`Omega3.0-portable.exe`**, PySide6) is a separate process from the API. The GUI typically launches **`Omega3.0-portable-Server.exe`**, which runs **Uvicorn + FastAPI** (`omega_studio.server.app`). Chat requests hit **`InferenceEngine`**, which loads **llama-cpp-python** `Llama` instances per model id (GGUF on disk). **`vendor/accelerators/bin`** (legacy: `vendor/lemonade/bin`) is optionally prepended to `PATH` for accelerated subprocesses (e.g. Vulkan `llama-server`). Hub downloads run in-process **async** jobs.

## Tests / lint

```powershell
pytest
ruff check src tests
```

## Layout

- `src/omega_studio/` — application code
- `tests/` — pytest
- `omega_studio.spec`, `omega_studio_cli.spec`, `pyi_support/`
- `scripts/`, `packaging/`

## License & contributing

- **License:** [LICENSE](LICENSE) (MIT).
- **Issues / contributions:** use your project’s issue tracker (set remote URL when publishing).
