"""LRU tracking with optional eviction for loaded inference handles."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable

log = logging.getLogger("omega_studio.resource")


@dataclass
class _Entry:
    last_touch: float = field(default_factory=time.time)
    pinned: bool = False


class ResourceManager:
    def __init__(self, max_loaded: int = 15, *, eviction_enabled: bool = True):
        self.max_loaded = max(1, min(15, max_loaded))
        self.eviction_enabled = eviction_enabled
        self._entries: dict[str, _Entry] = {}

    def set_pin(self, model_id: str, pinned: bool) -> None:
        ent = self._entries.setdefault(model_id, _Entry())
        ent.pinned = pinned

    def touch(self, model_id: str) -> None:
        ent = self._entries.setdefault(model_id, _Entry())
        ent.last_touch = time.time()

    def maybe_evict(
        self,
        *,
        loaded_ids: Callable[[], list[str]],
        unload_fn: Callable[[str], None],
        reason: str = "overflow",
        trigger_model: str | None = None,
    ) -> list[str]:
        if not self.eviction_enabled:
            return []
        loaded = [m for m in loaded_ids() if m]
        loaded_before = len(loaded)
        if loaded_before <= self.max_loaded:
            return []
        now = time.time()
        pinned_skipped: list[str] = []
        scored: list[tuple[float, str]] = []
        for mid in loaded:
            ent = self._entries.get(mid) or _Entry()
            if ent.pinned:
                pinned_skipped.append(mid)
                continue
            scored.append((ent.last_touch, mid))
        scored.sort()
        evicted: list[str] = []
        overflow = loaded_before - self.max_loaded
        for last_touch, mid in scored[:overflow]:
            try:
                unload_fn(mid)
                evicted.append(mid)
                self._entries.pop(mid, None)
                log.info(
                    "eviction: model=%s reason=%s trigger=%s idle_s=%.2f "
                    "loaded_before=%d loaded_after=%d max=%d pinned_skipped=%s",
                    mid,
                    reason,
                    trigger_model or "-",
                    max(0.0, now - last_touch),
                    loaded_before,
                    loaded_before - len(evicted),
                    self.max_loaded,
                    pinned_skipped or "-",
                )
            except Exception as exc:
                log.warning(
                    "eviction_failed: model=%s reason=%s trigger=%s err=%s",
                    mid,
                    reason,
                    trigger_model or "-",
                    exc,
                )
        if len(evicted) < overflow:
            # Overflow remains but the only candidates left are pinned —
            # operators want to see this; it means max_loaded is being
            # exceeded because pin pressure outweighs capacity.
            log.warning(
                "eviction_blocked_by_pins: reason=%s trigger=%s loaded=%d "
                "max=%d evicted=%d still_over=%d pinned=%s",
                reason,
                trigger_model or "-",
                loaded_before,
                self.max_loaded,
                len(evicted),
                overflow - len(evicted),
                pinned_skipped or "-",
            )
        return evicted
