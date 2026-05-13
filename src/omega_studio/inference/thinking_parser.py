"""Extract or strip model ``thinking`` / reasoning blocks from assistant text."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Pattern

# Ordered: first match wins for extraction; all are stripped when hiding.
_THINKING_RES: list[tuple[Pattern[str], str]] = [
    (re.compile(r"<think>\s*", re.IGNORECASE), r"</think>"),
    (re.compile(r"<redacted_reasoning>\s*", re.IGNORECASE), r"</redacted_reasoning>"),
    (re.compile(r"<thinking>\s*", re.IGNORECASE), r"</thinking>"),
    (re.compile(r"<reasoning>\s*", re.IGNORECASE), r"</reasoning>"),
    # Gemma-style channel blocks (close token varies by template)
    (
        re.compile(r"<\|channel>thought\s*", re.IGNORECASE),
        r"(?:<\|channel\|>|<channel\|>)",
    ),
    (
        re.compile(r"<\|channel>reasoning\s*", re.IGNORECASE),
        r"(?:<\|channel\|>|<channel\|>)",
    ),
]


@dataclass(frozen=True)
class ThinkingParseResult:
    visible: str
    thinking_block: str | None
    raw: str


def _strip_all(raw: str) -> str:
    out = raw
    for open_re, close_lit in _THINKING_RES:
        # Iteratively remove paired blocks (non-greedy inner)
        close_re = re.compile(close_lit, re.IGNORECASE | re.DOTALL)
        while True:
            m = open_re.search(out)
            if not m:
                break
            start = m.start()
            rest = out[m.end() :]
            mc = close_re.search(rest)
            if not mc:
                # Unclosed: drop from open tag onward for strip mode
                out = out[:start]
                break
            end_close = m.end() + mc.end()
            out = out[:start] + out[end_close:]
    return out


def _extract_first(raw: str) -> tuple[str, str | None]:
    """Return (visible_with_others_stripped_or_full, first_thinking_body_or_none)."""
    for open_re, close_lit in _THINKING_RES:
        m = open_re.search(raw)
        if not m:
            continue
        close_re = re.compile(close_lit, re.IGNORECASE | re.DOTALL)
        rest = raw[m.end() :]
        mc = close_re.search(rest)
        if not mc:
            continue
        inner = rest[: mc.start()]
        full_block = raw[m.start() : m.end() + mc.end()]
        before = raw[: m.start()]
        after = raw[m.end() + mc.end() :]
        visible = _strip_all(before + after)
        return visible, inner.strip() or full_block.strip()
    return raw, None


def parse_thinking(text: str) -> ThinkingParseResult:
    """Split assistant output into user-visible text and optional thinking body."""
    raw = text
    visible, thinking = _extract_first(raw)
    return ThinkingParseResult(visible=visible, thinking_block=thinking, raw=raw)


def strip_thinking(text: str) -> str:
    """Remove all known thinking / reasoning wrappers."""
    return _strip_all(text).strip()
