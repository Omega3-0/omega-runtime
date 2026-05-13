"""Unit tests for ``thinking_parser``."""

from __future__ import annotations

from omega_studio.inference.thinking_parser import parse_thinking, strip_thinking


def test_strip_redacted_thinking():
    raw = "Hello<think>secret</think> world"
    assert strip_thinking(raw) == "Hello world"


def test_parse_redacted_extracts_body():
    raw = "Before<think>\nline2\n</think>After"
    r = parse_thinking(raw)
    assert "line2" in (r.thinking_block or "")
    assert "Before" in r.visible
    assert "After" in r.visible


def test_gemma_channel_style():
    raw = "Hi<|channel>thought\nreason\n<|channel|>there"
    r = parse_thinking(raw)
    assert "reason" in (r.thinking_block or "")
    assert "there" in r.visible


def test_strip_multiple_blocks():
    raw = "<thinking>a</thinking>x<redacted_reasoning>b</redacted_reasoning>y"
    assert strip_thinking(raw) == "xy"
