import logging

import httpx
import pytest
from pydantic import SecretStr

from turkiye_energy_mcp import server
from turkiye_energy_mcp.api_key import ApiKeyMiddleware

KEY = "s3cret-key"


class Downstream:
    def __init__(self):
        self.calls = []

    async def __call__(self, scope, receive, send):
        self.calls.append(scope)
        if scope["type"] == "http":
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})


async def _call(app, path, method="GET", headers=()):
    """Drive the middleware with a raw ASGI scope, so paths reach it unnormalised."""
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers],
    }
    await app(scope, receive, send)
    return sent[0]["status"]


@pytest.fixture
def gated():
    downstream = Downstream()
    return downstream, ApiKeyMiddleware(downstream, KEY, server.OPEN_PATHS)


@pytest.mark.parametrize(
    "path,method",
    [
        ("/mcp", "POST"),
        ("/mcp", "GET"),
        ("/mcp", "OPTIONS"),
        ("/mcp", "HEAD"),
        ("/mcp/", "POST"),
        ("//mcp", "POST"),
        ("/MCP", "POST"),
        ("/mcp/../mcp", "POST"),
        ("/health/../mcp", "POST"),  # open path only as an exact match
        ("/token/", "POST"),
        ("/.well-known/../mcp", "POST"),
        ("/unknown", "GET"),
    ],
)
async def test_protected_without_key(gated, path, method):
    downstream, app = gated
    assert await _call(app, path, method) == 401
    assert await _call(app, path, method, [("X-API-Key", "wrong")]) == 401
    assert await _call(app, path, method, [("X-API-Key", KEY + " ")]) == 401
    assert downstream.calls == []


async def test_valid_key_passes(gated):
    downstream, app = gated
    assert await _call(app, "/mcp", "POST", [("X-API-Key", KEY)]) == 200
    assert await _call(app, "/mcp", "POST", [("x-api-key", KEY)]) == 200
    assert len(downstream.calls) == 2


@pytest.mark.parametrize("path", server.OPEN_PATHS)
async def test_open_paths_need_no_key(gated, path):
    downstream, app = gated
    assert await _call(app, path, "POST" if path in ("/register", "/token") else "GET") == 200
    assert len(downstream.calls) == 1


async def test_lifespan_and_websocket_scopes(gated):
    downstream, app = gated
    await app({"type": "lifespan"}, None, None)
    assert downstream.calls == [{"type": "lifespan"}]

    sent = []

    async def send(message):
        sent.append(message)

    await app({"type": "websocket", "path": "/mcp", "headers": []}, None, send)
    assert sent == [{"type": "websocket.close", "code": 1008}]
    assert len(downstream.calls) == 1


@pytest.mark.parametrize("path", ["/%6Dcp", "/mcp%2F", "/%2Fmcp", "/health%2F..%2Fmcp"])
async def test_percent_encoded_paths_over_http(path):
    """httpx/ASGI decode the path before the middleware and router see it."""
    downstream = Downstream()
    app = ApiKeyMiddleware(downstream, KEY, server.OPEN_PATHS)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(path)
    assert response.status_code == 401
    assert downstream.calls == []


async def test_build_http_app_with_key(monkeypatch):
    monkeypatch.setattr(server.settings, "mcp_api_key", SecretStr(KEY))
    app = server.build_http_app()
    assert isinstance(app, ApiKeyMiddleware)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/health")).status_code == 200
        response = await client.post(server.settings.mcp_path, json={})
        assert response.status_code == 401
        assert response.json()["error"] == "unauthorized"


@pytest.mark.parametrize("value", [None, SecretStr(""), SecretStr("   ")])
async def test_build_http_app_without_key_is_unchanged(monkeypatch, caplog, value):
    monkeypatch.setattr(server.settings, "mcp_api_key", value)
    with caplog.at_level(logging.WARNING):
        app = server.build_http_app()
    assert not isinstance(app, ApiKeyMiddleware)
    assert "MCP_API_KEY is not set" in caplog.text
