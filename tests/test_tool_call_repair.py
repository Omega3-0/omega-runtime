"""#585 — deterministic tool-call argument repair."""

from __future__ import annotations

import importlib
import json
from typing import Any

import pytest
from starlette.testclient import TestClient

from omega_studio.config import ModelRecord, RegistryFile, StudioSettings
from omega_studio.server.tool_call_repair import (
    repair_response_tool_calls,
    repair_tool_call_arguments,
)


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


# ─────────────────────────────────────────────────────────────────
# Unit tests — repair_tool_call_arguments
# ─────────────────────────────────────────────────────────────────

def test_valid_json_passes_unchanged():
    repaired, applied = repair_tool_call_arguments('{"a": 1, "b": "two"}')
    assert repaired == '{"a": 1, "b": "two"}'
    assert applied == []


def test_empty_or_whitespace_returns_as_is():
    repaired, applied = repair_tool_call_arguments("")
    assert repaired == ""
    assert applied == []
    repaired, applied = repair_tool_call_arguments("   \n  ")
    assert applied == []


def test_code_fence_with_json_lang_tag_stripped():
    raw = '```json\n{"city": "SF", "units": "celsius"}\n```'
    repaired, applied = repair_tool_call_arguments(raw)
    assert repaired is not None
    assert "code_fence" in applied
    assert json.loads(repaired) == {"city": "SF", "units": "celsius"}


def test_code_fence_with_no_lang_tag_stripped():
    raw = '```\n{"x": 42}\n```'
    repaired, applied = repair_tool_call_arguments(raw)
    assert repaired is not None
    assert "code_fence" in applied
    assert json.loads(repaired) == {"x": 42}


def test_trailing_comma_removed():
    raw = '{"a": 1, "b": 2,}'
    repaired, applied = repair_tool_call_arguments(raw)
    assert repaired is not None
    assert "trailing_commas" in applied
    assert json.loads(repaired) == {"a": 1, "b": 2}


def test_trailing_comma_in_array_removed():
    raw = '{"items": [1, 2, 3,]}'
    repaired, applied = repair_tool_call_arguments(raw)
    assert repaired is not None
    assert json.loads(repaired) == {"items": [1, 2, 3]}


def test_python_true_false_none_replaced():
    raw = '{"enabled": True, "disabled": False, "value": None}'
    repaired, applied = repair_tool_call_arguments(raw)
    assert repaired is not None
    assert "python_bools" in applied
    parsed = json.loads(repaired)
    assert parsed == {"enabled": True, "disabled": False, "value": None}


def test_single_quoted_json_converted():
    raw = "{'city': 'Portland', 'temp': 65}"
    repaired, applied = repair_tool_call_arguments(raw)
    assert repaired is not None
    assert "single_to_double" in applied
    assert json.loads(repaired) == {"city": "Portland", "temp": 65}


def test_single_quote_repair_skipped_when_double_quotes_exist():
    """Mixed-quote strings get left alone — flipping would break valid
    apostrophes inside double-quoted values."""
    raw = "{\"phrase\": \"it's broken\","  # not repairable but tests safety
    repaired, applied = repair_tool_call_arguments(raw)
    # Either repaired (via trailing comma fix) or untouched, but the
    # single-to-double transform must NOT have fired.
    assert "single_to_double" not in applied


def test_compound_malformations_repaired_together():
    """Code fence + trailing comma + python bool — all common in
    tutorial-trained tool-use outputs."""
    raw = '```json\n{"enabled": True, "items": [1, 2,],}\n```'
    repaired, applied = repair_tool_call_arguments(raw)
    assert repaired is not None
    assert "code_fence" in applied
    parsed = json.loads(repaired)
    assert parsed == {"enabled": True, "items": [1, 2]}


def test_unrepairable_json_returns_none():
    """Genuinely broken structure — no transform can save it. Return
    None so the caller leaves the original in place."""
    raw = "{this is not even close: to json"
    repaired, applied = repair_tool_call_arguments(raw)
    assert repaired is None
    # Repairs may have been ATTEMPTED, but the result still didn't parse
    assert isinstance(applied, list)


# ─────────────────────────────────────────────────────────────────
# repair_response_tool_calls — walks the chat response shape
# ─────────────────────────────────────────────────────────────────

def test_repair_response_no_op_when_no_tool_calls():
    """Plain text responses — no tool_calls field — must pass through
    untouched and return 0."""
    response = {
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "hello"}}
        ]
    }
    count = repair_response_tool_calls(response)
    assert count == 0
    assert "omega" not in response["choices"][0]


def test_repair_response_repairs_one_malformed_call():
    """Single malformed call gets fixed in place + audit marker
    attached under omega.tool_calls_repaired."""
    response = {
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "get_weather",
                                "arguments": '```json\n{"city": "SF"}\n```',
                            },
                        }
                    ],
                },
            }
        ]
    }
    count = repair_response_tool_calls(response)
    assert count == 1
    args = response["choices"][0]["message"]["tool_calls"][0]["function"][
        "arguments"
    ]
    assert json.loads(args) == {"city": "SF"}
    audit = response["choices"][0]["omega"]["tool_calls_repaired"]
    assert len(audit) == 1
    assert audit[0]["index"] == 0
    assert "code_fence" in audit[0]["transforms"]


def test_repair_response_leaves_valid_calls_alone():
    """If args are already valid JSON, no marker, no mutation."""
    response = {
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "get_weather",
                                "arguments": '{"city": "SF"}',
                            },
                        }
                    ],
                },
            }
        ]
    }
    count = repair_response_tool_calls(response)
    assert count == 0
    assert "omega" not in response["choices"][0]
    assert response["choices"][0]["message"]["tool_calls"][0]["function"][
        "arguments"
    ] == '{"city": "SF"}'


def test_repair_response_skips_unrepairable_call():
    """An unrepairable call must NOT crash the whole response — leave
    it alone, surface no marker for that index, let strict clients
    decide whether to surface or fall back."""
    response = {
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "wat",
                                "arguments": "{not even close",
                            },
                        }
                    ],
                },
            }
        ]
    }
    count = repair_response_tool_calls(response)
    assert count == 0
    # Original args untouched
    assert response["choices"][0]["message"]["tool_calls"][0]["function"][
        "arguments"
    ] == "{not even close"


def test_repair_response_handles_multiple_calls_with_mixed_states():
    """Two calls: one valid, one repairable, one unrepairable.
    Repair markers should only cover the second; first stays clean;
    third stays untouched."""
    response = {
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "a",
                            "type": "function",
                            "function": {"name": "f1", "arguments": '{"ok": true}'},
                        },
                        {
                            "id": "b",
                            "type": "function",
                            "function": {"name": "f2", "arguments": "{'k': 'v'}"},
                        },
                        {
                            "id": "c",
                            "type": "function",
                            "function": {"name": "f3", "arguments": "@@@broken@@@"},
                        },
                    ],
                },
            }
        ]
    }
    count = repair_response_tool_calls(response)
    assert count == 1
    audit = response["choices"][0]["omega"]["tool_calls_repaired"]
    assert [e["index"] for e in audit] == [1]


# ─────────────────────────────────────────────────────────────────
# End-to-end via /v1/chat/completions
# ─────────────────────────────────────────────────────────────────

class _FakeEngine:
    def is_loaded(self, model_id: str, *, embedding: bool = False) -> bool:
        return True

    def loaded_ids(self) -> list[str]:
        return []

    def unload(self, model_id: str) -> None:
        pass

    def load_gguf(self, *args: Any, **kwargs: Any) -> None:
        return None


def test_chat_completion_repairs_malformed_tool_call_in_response(
    patched_registry, monkeypatch
):
    """The /v1/chat/completions handler must run repair_response_tool_calls
    on its way out. Verify by sending a request whose stub engine returns
    a code-fenced tool_call and checking the response has clean JSON."""
    monkeypatch.delenv("OMEGA_API_KEY", raising=False)
    from omega_studio.server.app import create_app

    class ToolCallEngine(_FakeEngine):
        def chat_completion(self, model_id: str, **kwargs: Any) -> dict[str, Any]:
            return {
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_x",
                                    "type": "function",
                                    "function": {
                                        "name": "get_weather",
                                        "arguments": '```json\n{"city": "SF"}\n```',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            }

    app = create_app()
    with TestClient(app) as client:
        client.app.state.engine = ToolCallEngine()
        r = client.post(
            "/v1/chat/completions",
            json={
                "model": "stub-model",
                "messages": [{"role": "user", "content": "weather in SF"}],
                "tools": [
                    {
                        "type": "function",
                        "function": {"name": "get_weather", "parameters": {}},
                    }
                ],
            },
        )
    assert r.status_code == 200
    body = r.json()
    args = body["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
    # args is a JSON STRING per OpenAI spec — must be parseable
    assert json.loads(args) == {"city": "SF"}
    # Audit marker attached
    audit = body["choices"][0]["omega"]["tool_calls_repaired"]
    assert audit[0]["index"] == 0
    assert "code_fence" in audit[0]["transforms"]
