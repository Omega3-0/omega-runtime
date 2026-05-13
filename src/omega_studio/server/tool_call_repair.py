"""Deterministic repair of malformed ``tool_calls[].function.arguments``.

Local GGUF models — especially smaller ones under tool-use templates —
emit JSON with predictable malformations: code-fence wrappers, single
quotes, trailing commas, Python literal booleans. The OpenAI client
contract is that ``arguments`` is a parseable JSON STRING; clients
that strictly ``json.loads`` it (langchain, openai-python tool routers,
agentic frameworks) crash on the first malformed call.

This module attempts conservative deterministic repair — no re-prompt,
no LLM call, no latency cost. If a repair succeeds, the corrected
string replaces the original and we attach a marker under
``choices[].omega.tool_calls_repaired`` so callers can audit the
correction. Unrepairable args are left untouched — let strict clients
see the original so they can decide whether to surface the model
output or fall back.

Why these specific repairs:
  * ``code_fence`` — models trained on tutorial / docs corpora insert
    ```` ```json ... ``` ```` wrappers around tool-call args; very common
    in instruct-tuned small models.
  * ``single_to_double`` — Python-trained models occasionally emit
    Python literals (``'key': 'value'``); flipping to double quotes
    fixes ~90% of these without breaking valid double-quoted JSON.
  * ``trailing_commas`` — every JSON encoder in the wild forbids them
    but every human-written JSON has them; models trained on the
    latter sometimes emit them.
  * ``python_bools`` — ``True`` / ``False`` / ``None`` are JSON-invalid
    but the same models that emit them under tool-use are usually
    fixable with a token-boundary regex.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

log = logging.getLogger("omega_studio.tool_call_repair")


_CODE_FENCE_RE = re.compile(
    r"^\s*```(?:json|JSON)?\s*\n?(.*?)\n?\s*```\s*$",
    re.DOTALL,
)

# Python literals as standalone tokens (not parts of identifiers / strings).
# We do best-effort outside of strings; a perfect repair would require a
# full tokenizer, but for the common malformations (`True` / `False` /
# `None` as values) word-boundary matching is sufficient.
_PYTHON_BOOL_RE = re.compile(r"(?<![\"'\w])(True|False|None)(?![\"'\w])")

# Trailing comma before } or ]. JSON strict mode rejects them.
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


def _strip_code_fence(raw: str) -> tuple[str, bool]:
    m = _CODE_FENCE_RE.match(raw)
    if not m:
        return raw, False
    return m.group(1).strip(), True


def _replace_python_literals(raw: str) -> tuple[str, bool]:
    mapping = {"True": "true", "False": "false", "None": "null"}
    changed = False

    def _sub(match: re.Match[str]) -> str:
        nonlocal changed
        changed = True
        return mapping[match.group(1)]

    out = _PYTHON_BOOL_RE.sub(_sub, raw)
    return out, changed


def _drop_trailing_commas(raw: str) -> tuple[str, bool]:
    out, n = _TRAILING_COMMA_RE.subn(r"\1", raw)
    return out, n > 0


def _swap_single_quotes(raw: str) -> tuple[str, bool]:
    # Only attempt this if the string has NO double quotes — otherwise
    # we're likely to break a valid mixed-quote string. Conservative
    # repair: trade single-quote-only Python-ish JSON for valid JSON.
    if '"' in raw or "'" not in raw:
        return raw, False
    return raw.replace("'", '"'), True


_REPAIR_PIPELINE = (
    ("code_fence", _strip_code_fence),
    ("python_bools", _replace_python_literals),
    ("trailing_commas", _drop_trailing_commas),
    ("single_to_double", _swap_single_quotes),
)


def repair_tool_call_arguments(raw: str) -> tuple[str | None, list[str]]:
    """Attempt to repair `raw` into parseable JSON.

    Returns ``(repaired_string, applied_repairs)`` on success or
    ``(None, attempted_repairs)`` on failure. The repaired string is
    guaranteed to round-trip through ``json.loads`` if non-None.

    Already-valid input returns ``(raw, [])`` — no work, no marker.
    """
    if not isinstance(raw, str) or not raw.strip():
        return raw if isinstance(raw, str) else None, []
    # Fast path: input is already valid JSON
    try:
        json.loads(raw)
        return raw, []
    except json.JSONDecodeError:
        pass

    current = raw
    applied: list[str] = []
    for name, transform in _REPAIR_PIPELINE:
        new, changed = transform(current)
        if changed:
            applied.append(name)
            current = new
            try:
                json.loads(current)
                return current, applied
            except json.JSONDecodeError:
                continue
    # Final attempt with all transforms applied even if some weren't
    # individually parseable mid-pipeline (sometimes two malformations
    # compound; the combined repair still works).
    try:
        json.loads(current)
        return current, applied
    except json.JSONDecodeError:
        return None, applied


def repair_response_tool_calls(response: dict[str, Any]) -> int:
    """Walk a non-streaming chat response and repair any malformed
    ``tool_calls[].function.arguments`` in place. Returns the count of
    tool calls that were repaired.

    Attaches a per-choice ``omega.tool_calls_repaired`` list with one
    entry per repaired call (``{index, transforms}``) so callers can
    audit which corrections fired without diffing the original.
    """
    choices = response.get("choices") or []
    repaired_count = 0
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if not isinstance(message, dict):
            continue
        tool_calls = message.get("tool_calls")
        if not isinstance(tool_calls, list):
            continue
        repaired_entries: list[dict[str, Any]] = []
        for idx, call in enumerate(tool_calls):
            if not isinstance(call, dict):
                continue
            fn = call.get("function")
            if not isinstance(fn, dict):
                continue
            raw_args = fn.get("arguments")
            if not isinstance(raw_args, str):
                continue
            repaired, applied = repair_tool_call_arguments(raw_args)
            if repaired is not None and applied:
                fn["arguments"] = repaired
                repaired_entries.append({"index": idx, "transforms": applied})
                repaired_count += 1
                log.info(
                    "tool_call_repaired: idx=%d transforms=%s",
                    idx,
                    applied,
                )
        if repaired_entries:
            omega = choice.setdefault("omega", {})
            if isinstance(omega, dict):
                omega["tool_calls_repaired"] = repaired_entries
    return repaired_count
