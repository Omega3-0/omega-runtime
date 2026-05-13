# Contributing to Omega Runtime Studio

Thanks for your interest in making this better. This guide gets you from
clone to landed PR in under 15 minutes.

## What this project is (and isn't)

Omega Runtime Studio is a **sovereign**, **Windows-first**, **OpenAI-compatible**
local-model control plane — FastAPI `/v1` API + PySide6 GUI, no third-party
inference SaaS. The repo's design priorities (in order):

1. **Sovereignty** — operators own weights, accelerators, and data on disk;
   no telemetry, no vendor account model, no revocable license shapes.
2. **OpenAI-spec compliance** — `/v1/chat/completions`, `/v1/embeddings`,
   `/v1/models`, streaming SSE, `response_format`, `stream_options`,
   `X-Request-ID`, etc. behave the way strict clients (openai-python,
   langchain) expect.
3. **Observability** — every audit finding becomes a permanent regression
   test; every eviction/disconnect/drain decision logs with a `request_id`
   so operators can grep one ID for end-to-end forensics.

Contributions that break any of those three lose by default. Contributions
that improve them are welcome.

## Dev setup

Requires Python 3.11+ on Windows. Linux/macOS work for the server core but
the GUI + build scripts assume Windows.

```powershell
git clone https://github.com/Omega3-0/omega-runtime.git
cd omega-runtime
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu/ -r requirements.txt
pip install -e ".[dev]"
```

The `--extra-index-url` is **required** — installing `llama-cpp-python` from
PyPI on Windows triggers a CMake/MSVC build that hits MAX_PATH on llama.cpp's
vendored Svelte tree and fails. The abetlen index ships pre-built wheels.

## Run the API server

```powershell
omega-studio serve --host 127.0.0.1 --port 11434
# or via the canonical bundle name
omega3-portable serve --host 127.0.0.1 --port 11434
```

Point any OpenAI-compatible client at `http://127.0.0.1:11434/v1`.

## Run the GUI

```powershell
omega3-portable-gui
```

## Run the tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests/
```

164+ tests as of v0.1.0. All must pass before a PR can land.

## Lint

```powershell
ruff check .
ruff format --check .
```

CI runs both on every PR.

## Build the portable bundle

```powershell
.\scripts\build_windows.ps1
```

Output: `dist\Omega3.0-portable\` (CLI + GUI exes via PyInstaller; smoke
gate auto-verifies the bundle serves `/health` before declaring success).

### Building a GPU-variant bundle

The default build uses abetlen's CPU wheel from
`https://abetlen.github.io/llama-cpp-python/whl/cpu/`. For GPU offload:

**CUDA (NVIDIA):**
```powershell
$env:OMEGA_VARIANT = "cuda"
$env:OMEGA_LLAMA_CPP_INDEX = "https://abetlen.github.io/llama-cpp-python/whl/cu122/"
.\scripts\build_windows.ps1
```

**Vulkan (AMD / Intel / NVIDIA cross-vendor) or DML:**

abetlen doesn't ship Vulkan / DML wheels. You need a custom-built
`llama_cpp_python-*.whl` from llama-cpp-python compiled with
`-DGGML_VULKAN=on` (or `-DGGML_BACKEND_DML=on`). Drop the wheel under
`vendor\wheels\<variant>\` and the build script picks it up:

```powershell
mkdir vendor\wheels\vulkan
copy your-prebuilt-llama_cpp_python-*-py3-none-win_amd64.whl `
    vendor\wheels\vulkan\
$env:OMEGA_VARIANT = "vulkan"
.\scripts\build_windows.ps1
```

The build script installs deps from the CPU index first, then
force-overlays your Vulkan wheel via
`pip install --force-reinstall --no-deps`. The bundle ends up with
the Vulkan-capable DLLs and `/v1/version` reports `omega_variant: vulkan`
(baked-in via `_build_info.py` so the tag survives daemon spawn).

Smoke gate validates `llama_supports_gpu_offload=true` on the built
bundle for non-cpu variants. If that flag isn't true post-build, the
wheel didn't actually have GPU code — investigate before shipping.

`vendor/wheels/` is gitignored. Wheels travel as GitHub release assets
when a release is cut.

## How to file a good bug

Use the `Bug report` template under [Issues](../../issues/new/choose).
The template asks for the things we actually need:

- Exact CLI invocation (`Omega3.0-portable-Server.exe daemon --port ...`)
- Server log excerpt — grep by your `X-Request-ID` for the relevant trace
- OS / Python / `omega_variant` from `GET /v1/version`
- What you expected vs what happened

Logs with a request_id grepped to one request are 10× faster to triage
than logs with everything in them. The `X-Request-ID` response header
makes this trivial.

## How to land a PR

1. **One change per PR.** If you find two bugs while fixing one, file the
   second separately.
2. **Tests for every fix.** If you fixed a bug, add a regression test that
   would have failed before your fix. Every bug we've fixed in v0.1.0 has
   one. CI will reject PRs that lower coverage on touched files.
3. **Spec citations for behavior changes.** If you're touching
   `/v1/chat/completions` shape, link the OpenAI API reference section
   that backs your interpretation.
4. **Don't break Windows.** Linux/macOS are nice-to-have; Windows is
   load-bearing. The build pipeline + GUI assume Windows. PRs that
   break Windows fail CI.
5. **Honor the sovereignty doctrine.** No telemetry, no phone-home, no
   anonymous metrics, no auto-update beacons. PRs adding any of those
   get closed.

## What needs help

- Linux / macOS server-side parity (the FastAPI core is portable; only
  Windows-specific paths need replacement)
- More embedding model families wired through ONNX (BGE, E5, MiniLM
  pooling already supported; needs real-model coverage tests)
- GUI feature parity with LM Studio's playground (better tool-call
  inspector, prompt-template editor, history search)
- Operator handbook recipes — every "I figured out how to do X with this"
  is worth a doc PR

## Code style

- Python 3.11+ syntax (`list[str]`, `dict[str, Any]`, `X | None`)
- Ruff for formatting + linting (config in `pyproject.toml`)
- Single Responsibility per module — `inference/`, `server/`, `downloads/`
  don't cross-import
- Public API: type-hint everything; private helpers can be looser
- Comments only when the WHY isn't obvious from the code — see existing
  code for the bar

## Questions

Open a [Discussion](../../discussions) or file an issue with the
`question` label.
