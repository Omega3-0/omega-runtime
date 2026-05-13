"""Per-request context — request_id correlation across logs.

A request_id (UUID4) is generated for every incoming HTTP request, stashed
in a contextvar so any code path inside the request handler can read it
without threading it through arguments, and returned to the client via
the ``X-Request-ID`` response header. A logging filter pulls the active
request_id onto every ``LogRecord`` emitted during the request lifetime.

Why this matters for production: when an operator reports a problem
("the chat call at 14:32 failed"), the operator can grab the
``X-Request-ID`` from their client (or browser devtools) and grep for
that exact ID across uvicorn / engine / eviction / hub-download logs
to see the full causal chain. Without it, correlation has to happen
by-timestamp which loses fidelity under any concurrency.

If an upstream proxy / reverse-proxy / load-balancer already sets
``X-Request-ID``, we respect that value so distributed tracing works
end-to-end. Only allocate fresh when no inbound header is present.
"""

from __future__ import annotations

import contextvars
import logging
import re
import uuid

_REQUEST_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "omega_request_id", default=""
)

# Safety net for upstream-supplied IDs. We accept reasonable shapes
# (UUIDs, short alphanumeric tags, hyphen-separated) but cap length so
# a hostile / buggy upstream can't poison logs with megabyte-long IDs.
# Sanitize aggressively: only ASCII letters/digits/hyphen/underscore.
_ID_SAFE = re.compile(r"[^A-Za-z0-9_\-]")
_MAX_ID_LEN = 128


def _sanitize_request_id(raw: str) -> str:
    """Clean an upstream-supplied X-Request-ID. Empty/invalid → ``""``;
    valid → returns the cleaned form (trimmed, length-capped, only
    safe characters)."""
    if not raw:
        return ""
    cleaned = _ID_SAFE.sub("", raw.strip())[:_MAX_ID_LEN]
    return cleaned


def new_request_id() -> str:
    """Generate a fresh request_id (UUID4 without dashes)."""
    return uuid.uuid4().hex


def set_current_request_id(request_id: str) -> contextvars.Token:
    """Bind ``request_id`` to the current async context. Returns a Token
    that callers should reset() after the request completes — done
    automatically by the middleware."""
    return _REQUEST_ID.set(request_id)


def reset_current_request_id(token: contextvars.Token) -> None:
    _REQUEST_ID.reset(token)


def get_current_request_id() -> str:
    """Read the request_id active in the current async context.
    Returns ``""`` outside a request (e.g. background tasks that
    weren't spawned inside a request scope)."""
    return _REQUEST_ID.get()


class RequestIdLogFilter(logging.Filter):
    """Inject the active request_id onto every LogRecord. Use a Filter
    rather than a Formatter so downstream handlers (file rotation,
    JSON sinks) can read the field from ``record.request_id`` directly
    AND text formatters can interpolate ``%(request_id)s`` in their
    format string."""

    def filter(self, record: logging.LogRecord) -> bool:
        rid = _REQUEST_ID.get()
        record.request_id = rid or "-"
        return True


def install_request_id_filter(logger_names: tuple[str, ...] = (
    "omega_studio",
    "uvicorn",
    "uvicorn.access",
    "uvicorn.error",
)) -> None:
    """Attach the RequestIdLogFilter to relevant loggers so log records
    emitted during request handling get the request_id stamp. Idempotent
    — calling more than once attaches the same filter instance only
    once per logger."""
    flt = RequestIdLogFilter()
    for name in logger_names:
        lg = logging.getLogger(name)
        # Avoid duplicate attach if called multiple times
        if not any(isinstance(f, RequestIdLogFilter) for f in lg.filters):
            lg.addFilter(flt)
