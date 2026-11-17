"""Per-request context (actor, IP, request id) available to services and audit."""
from __future__ import annotations

import contextvars
from dataclasses import dataclass


@dataclass
class RequestContext:
    user: object | None = None
    ip: str | None = None
    request_id: str | None = None
    api_client_label: str | None = None


_ctx: contextvars.ContextVar[RequestContext] = contextvars.ContextVar("request_ctx", default=RequestContext())


def get_request_context() -> RequestContext:
    return _ctx.get()


def set_request_context(ctx: RequestContext):
    return _ctx.set(ctx)


def reset_request_context(token) -> None:
    _ctx.reset(token)
