"""Opt-in shared-secret gate for the MCP transport (`MCP_API_KEY`).

Mirrors kap-mcp's ApiKeyMiddleware, with one difference: it is default-deny.
Instead of listing protected prefixes (which `/mcp/`, `//mcp` or case variants
could slip past), every path except an explicit allowlist of exact open paths
(health check, OAuth discovery/authorize/token/register) needs a matching
`X-API-Key`. `scope["path"]` is already percent-decoded by the server, and it
is the same value Starlette routes on, so the check and the router agree.

Pure ASGI rather than BaseHTTPMiddleware, which would buffer streamed responses.
"""

from __future__ import annotations

import hmac
import logging
from collections.abc import Iterable
from typing import Any

from starlette.datastructures import Headers
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


class ApiKeyMiddleware:
    def __init__(self, app: Any, api_key: str, open_paths: Iterable[str]) -> None:
        self.app = app
        self.expected = api_key.encode("utf-8")
        self.open_paths = frozenset(open_paths)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket") or scope["path"] in self.open_paths:
            await self.app(scope, receive, send)
            return

        provided = Headers(scope=scope).get("x-api-key", "").encode("utf-8")
        # compare_digest keeps the check constant-time.
        if hmac.compare_digest(provided, self.expected):
            await self.app(scope, receive, send)
            return

        logger.warning("Rejected %s %s: missing or invalid X-API-Key",
                       scope.get("method", "WS"), scope["path"])
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        response = JSONResponse(
            {"error": "unauthorized", "detail": "Valid X-API-Key header required."},
            status_code=401,
        )
        await response(scope, receive, send)
