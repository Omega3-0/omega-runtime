import logging

from omega_studio.resource_manager import ResourceManager


def test_eviction_skips_pinned():
    rm = ResourceManager(max_loaded=2, eviction_enabled=True)
    loaded = ["a", "b", "c"]
    rm.set_pin("b", True)
    rm.touch("a")
    rm.touch("b")
    rm.touch("c")

    evicted: list[str] = []

    def unload(mid: str) -> None:
        evicted.append(mid)
        loaded.remove(mid)

    rm.maybe_evict(loaded_ids=lambda: list(loaded), unload_fn=unload)
    assert "b" not in evicted


# ─────────────────────────────────────────────────────────────────
# #581 — eviction observability
# ─────────────────────────────────────────────────────────────────
# Every eviction decision must be greppable in operator logs:
#   - which model went out, with idle time
#   - why (preload_overflow / postload_overflow / caller-specified)
#   - what triggered it (the incoming model_id, if any)
#   - the loaded_before → loaded_after delta + max capacity
#   - which models were skipped because they were pinned
# These tests assert the structured fields are present so log
# regressions show up here, not in production forensics.


def _capture_logs(caplog_fixture, level=logging.INFO):
    caplog_fixture.set_level(level, logger="omega_studio.resource")


def test_eviction_emits_structured_log_with_reason_and_trigger(caplog):
    _capture_logs(caplog)
    rm = ResourceManager(max_loaded=2, eviction_enabled=True)
    loaded = ["old_a", "old_b", "old_c"]
    rm.touch("old_a")
    rm.touch("old_b")
    rm.touch("old_c")

    def unload(mid: str) -> None:
        loaded.remove(mid)

    evicted = rm.maybe_evict(
        loaded_ids=lambda: list(loaded),
        unload_fn=unload,
        reason="preload_overflow",
        trigger_model="incoming_model",
    )
    assert evicted, "should have evicted at least one model"
    msgs = [r.getMessage() for r in caplog.records if r.name == "omega_studio.resource"]
    info_lines = [m for m in msgs if m.startswith("eviction:")]
    assert info_lines, f"expected eviction: line, got {msgs}"
    line = info_lines[0]
    assert "reason=preload_overflow" in line
    assert "trigger=incoming_model" in line
    assert "loaded_before=3" in line
    assert "max=2" in line
    assert "idle_s=" in line


def test_eviction_default_reason_when_caller_omits(caplog):
    """Backward-compat: pre-#581 callers passed no reason. Default
    label should be 'overflow' so old code keeps working AND logs
    something meaningful."""
    _capture_logs(caplog)
    rm = ResourceManager(max_loaded=1, eviction_enabled=True)
    loaded = ["x", "y"]
    rm.touch("x")
    rm.touch("y")

    def unload(mid: str) -> None:
        loaded.remove(mid)

    rm.maybe_evict(loaded_ids=lambda: list(loaded), unload_fn=unload)
    info_lines = [
        r.getMessage()
        for r in caplog.records
        if r.name == "omega_studio.resource" and r.getMessage().startswith("eviction:")
    ]
    assert info_lines
    assert "reason=overflow" in info_lines[0]
    assert "trigger=-" in info_lines[0]


def test_eviction_blocked_by_pins_warns(caplog):
    """If every loaded model is pinned and capacity is exceeded, the
    code can't evict anything — that's an operator-visible event
    (pin pressure > max_loaded). Warn so it surfaces in logs."""
    caplog.set_level(logging.WARNING, logger="omega_studio.resource")
    rm = ResourceManager(max_loaded=1, eviction_enabled=True)
    loaded = ["pin1", "pin2"]
    rm.set_pin("pin1", True)
    rm.set_pin("pin2", True)
    rm.touch("pin1")
    rm.touch("pin2")

    def unload(mid: str) -> None:
        loaded.remove(mid)

    evicted = rm.maybe_evict(
        loaded_ids=lambda: list(loaded),
        unload_fn=unload,
        reason="preload_overflow",
        trigger_model="newcomer",
    )
    assert evicted == []
    blocked = [
        r
        for r in caplog.records
        if r.name == "omega_studio.resource"
        and r.getMessage().startswith("eviction_blocked_by_pins")
    ]
    assert blocked, "expected eviction_blocked_by_pins warning"
    msg = blocked[0].getMessage()
    assert "reason=preload_overflow" in msg
    assert "still_over=1" in msg
    assert "pin1" in msg and "pin2" in msg


def test_eviction_failure_logs_with_reason(caplog):
    """If unload_fn raises, the failure log should still carry reason
    + trigger so the operator can correlate it with the request that
    drove the eviction attempt."""
    caplog.set_level(logging.WARNING, logger="omega_studio.resource")
    rm = ResourceManager(max_loaded=1, eviction_enabled=True)
    loaded = ["broken", "current"]
    rm.touch("broken")
    rm.touch("current")

    def bad_unload(mid: str) -> None:
        raise RuntimeError("handle held by streaming response")

    rm.maybe_evict(
        loaded_ids=lambda: list(loaded),
        unload_fn=bad_unload,
        reason="postload_overflow",
        trigger_model="current",
    )
    failures = [
        r
        for r in caplog.records
        if r.name == "omega_studio.resource" and r.getMessage().startswith("eviction_failed")
    ]
    assert failures
    msg = failures[0].getMessage()
    assert "reason=postload_overflow" in msg
    assert "trigger=current" in msg
    assert "handle held by streaming response" in msg


def test_eviction_disabled_returns_nothing_and_logs_nothing(caplog):
    """eviction_enabled=False short-circuits before any logging —
    we don't want a flood of 'considered but skipped' lines."""
    _capture_logs(caplog)
    rm = ResourceManager(max_loaded=1, eviction_enabled=False)
    loaded = ["a", "b"]
    rm.touch("a")
    rm.touch("b")
    evicted = rm.maybe_evict(loaded_ids=lambda: list(loaded), unload_fn=lambda _: None)
    assert evicted == []
    assert not any(
        r.name == "omega_studio.resource" and r.getMessage().startswith("eviction")
        for r in caplog.records
    )
