import base64
import hashlib
import json

import pytest
from starlette.datastructures import FormData, QueryParams

from turkiye_energy_mcp import oauth_compat
from turkiye_energy_mcp.oauth_compat import (
    DEFAULT_ALLOWED_REDIRECT_HOSTS,
    PublicOAuthCompat,
    parse_redirect_hosts,
    verify_pkce,
)

CLAUDE_CALLBACK = "https://claude.ai/api/mcp/auth_callback"
VERIFIER = "0123456789012345678901234567890123456789013"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


CHALLENGE = _b64url(hashlib.sha256(VERIFIER.encode("ascii")).digest())


class DummyRequest:
    def __init__(self, **kwargs):
        self._json = kwargs.get("json")
        self.query_params = QueryParams(kwargs.get("query", {}))
        self._form = kwargs.get("form", {})

    async def json(self):
        return self._json

    async def form(self):
        return FormData(self._form)


async def _authorize(oauth, client_id, redirect_uri):
    return await oauth.authorize(
        DummyRequest(
            query={
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "code_challenge": CHALLENGE,
                "code_challenge_method": "S256",
            }
        )
    )


async def _register(oauth, redirect_uris):
    response = await oauth.register(DummyRequest(json={"redirect_uris": redirect_uris}))
    assert response.status_code == 201
    return json.loads(response.body.decode())["client_id"]


def test_verify_pkce_s256():
    verifier = "0123456789012345678901234567890123456789013"
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    assert verify_pkce(verifier, challenge, "S256")
    assert not verify_pkce("wrong-verifier-value-012345678901234567890", challenge, "S256")


@pytest.mark.asyncio
async def test_oauth_register_authorize_token_flow():
    oauth = PublicOAuthCompat("https://example.test", "/mcp")
    assert oauth.protected_resource_metadata()["resource"] == "https://example.test/mcp"
    assert "registration_endpoint" in oauth.authorization_server_metadata()

    register = await oauth.register(
        DummyRequest(
            json={
                "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
                "client_name": "claude",
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
            }
        )
    )
    assert register.status_code == 201
    client_payload = json.loads(register.body.decode())
    client_id = client_payload["client_id"]

    verifier = "0123456789012345678901234567890123456789013"
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    authorize = await oauth.authorize(
        DummyRequest(
            query={
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": "abc",
            }
        )
    )
    assert authorize.status_code == 302
    location = authorize.headers["location"]
    assert "code=" in location and "state=abc" in location
    code = location.split("code=")[1].split("&")[0]

    token = await oauth.token(
        DummyRequest(
            form={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                "client_id": client_id,
                "code_verifier": verifier,
            }
        )
    )
    assert token.status_code == 200
    payload = json.loads(token.body.decode())
    assert payload["token_type"] == "bearer"
    assert payload["access_token"]
    assert payload["refresh_token"]


@pytest.mark.parametrize(
    "redirect_uri",
    [
        CLAUDE_CALLBACK,
        "https://claude.com/api/mcp/auth_callback",
        "http://localhost:33418/callback",
        "http://127.0.0.1:6274/oauth/callback",
        "https://localhost/cb",
        "http://[::1]:8080/cb",
    ],
)
async def test_authorize_first_seen_client_allowlisted_redirect(redirect_uri):
    oauth = PublicOAuthCompat("https://example.test")
    response = await _authorize(oauth, "https://claude.ai/oauth/mcp-client.json", redirect_uri)
    assert response.status_code == 302
    assert response.headers["location"].startswith(redirect_uri + "?code=")


@pytest.mark.parametrize(
    "redirect_uri",
    [
        "https://evil.test/cb",
        "http://claude.ai/api/mcp/auth_callback",  # https only for non-loopback
        "https://claude.ai.evil.test/cb",
        "https://evil.claude.ai/cb",
        "https://claude.ai@evil.test/cb",
        "https://evil.test\\@claude.ai/cb",
        "https://claude.ai/cb#frag",
        "javascript://localhost/%0aalert(1)",
        "//evil.test/cb",
        "https://claude.ai:bad/cb",
    ],
)
async def test_authorize_rejects_unlisted_redirect_without_redirecting(redirect_uri):
    oauth = PublicOAuthCompat("https://example.test")
    response = await _authorize(oauth, "attacker-client", redirect_uri)
    assert response.status_code == 400
    assert "location" not in response.headers
    assert json.loads(response.body.decode())["error"] == "invalid_request"
    assert "attacker-client" not in oauth.store.clients
    assert not oauth.store.codes


async def test_authorize_registered_client_requires_exact_registered_uri():
    oauth = PublicOAuthCompat("https://example.test")
    client_id = await _register(oauth, ["https://app.example.org/cb"])

    ok = await _authorize(oauth, client_id, "https://app.example.org/cb")
    assert ok.status_code == 302

    for other in ("https://evil.test/cb", CLAUDE_CALLBACK, "https://app.example.org/cb2"):
        rejected = await _authorize(oauth, client_id, other)
        assert rejected.status_code == 400
        assert "location" not in rejected.headers
    # Rejected URIs are never appended to the registered client.
    assert oauth.store.clients[client_id].redirect_uris == ["https://app.example.org/cb"]


async def test_allowed_redirect_hosts_override():
    assert parse_redirect_hosts(None) == DEFAULT_ALLOWED_REDIRECT_HOSTS
    assert parse_redirect_hosts("  ") == DEFAULT_ALLOWED_REDIRECT_HOSTS
    hosts = parse_redirect_hosts("Example.org, ,foo.test")
    assert hosts == ("example.org", "foo.test")

    oauth = PublicOAuthCompat("https://example.test", allowed_redirect_hosts=hosts)
    assert (await _authorize(oauth, "c", "https://example.org/cb")).status_code == 302
    assert (await _authorize(oauth, "c", CLAUDE_CALLBACK)).status_code == 400
    # Loopback stays allowed for local development regardless of the override.
    assert (await _authorize(oauth, "c", "http://localhost:9999/cb")).status_code == 302


SIGNING_KEY = "k" * 64


async def _code_for(oauth, client_id="client-a", redirect_uri=CLAUDE_CALLBACK):
    response = await _authorize(oauth, client_id, redirect_uri)
    assert response.status_code == 302
    return response.headers["location"].split("code=")[1].split("&")[0]


async def _exchange_code(oauth, client_id="client-a"):
    code = await _code_for(oauth, client_id)
    response = await oauth.token(
        DummyRequest(
            form={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": CLAUDE_CALLBACK,
                "client_id": client_id,
                "code_verifier": VERIFIER,
            }
        )
    )
    assert response.status_code == 200
    return json.loads(response.body.decode())


async def _refresh(oauth, refresh_token, client_id="client-a"):
    response = await oauth.token(
        DummyRequest(
            form={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
            }
        )
    )
    body = json.loads(response.body.decode())
    return response.status_code, body


async def test_tokens_survive_restart_with_same_signing_key():
    before = PublicOAuthCompat("https://example.test", signing_key=SIGNING_KEY)
    tokens = await _exchange_code(before)
    assert tokens["expires_in"] == 3600

    after_restart = PublicOAuthCompat("https://example.test", signing_key=SIGNING_KEY)
    assert after_restart.verify_access_token(tokens["access_token"])["cid"] == "client-a"
    status, refreshed = await _refresh(after_restart, tokens["refresh_token"])
    assert status == 200 and refreshed["access_token"]

    other_key = PublicOAuthCompat("https://example.test", signing_key="x" * 64)
    assert other_key.verify_access_token(tokens["access_token"]) is None
    status, body = await _refresh(other_key, tokens["refresh_token"])
    assert (status, body["error"]) == (400, "invalid_grant")


async def test_missing_signing_key_warns_and_uses_random_key(caplog):
    with caplog.at_level("WARNING"):
        first = PublicOAuthCompat("https://example.test")
    assert "OAUTH_SIGNING_KEY" in caplog.text
    tokens = await _exchange_code(first)
    second = PublicOAuthCompat("https://example.test")
    assert second.verify_access_token(tokens["access_token"]) is None


async def test_token_expiry(monkeypatch):
    oauth = PublicOAuthCompat("https://example.test", signing_key=SIGNING_KEY)
    tokens = await _exchange_code(oauth)
    start = oauth_compat._now()

    monkeypatch.setattr(oauth_compat, "_now", lambda: start + 3601)
    assert oauth.verify_access_token(tokens["access_token"]) is None
    status, _ = await _refresh(oauth, tokens["refresh_token"])  # refresh lives ~30 days
    assert status == 200

    stale = oauth.signer.issue("refresh", "client-a", 30 * 86400)
    monkeypatch.setattr(oauth_compat, "_now", lambda: start + 3601 + 30 * 86400)
    status, body = await _refresh(oauth, stale)
    assert (status, body["error"]) == (400, "invalid_grant")


async def test_auth_code_expires(monkeypatch):
    oauth = PublicOAuthCompat("https://example.test", signing_key=SIGNING_KEY)
    code = await _code_for(oauth)
    start = oauth_compat._now()
    monkeypatch.setattr(oauth_compat, "_now", lambda: start + 601)
    response = await oauth.token(
        DummyRequest(
            form={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": CLAUDE_CALLBACK,
                "client_id": "client-a",
                "code_verifier": VERIFIER,
            }
        )
    )
    assert response.status_code == 400
    assert not oauth.store.codes  # pruned


async def test_access_token_is_not_a_refresh_token():
    oauth = PublicOAuthCompat("https://example.test", signing_key=SIGNING_KEY)
    tokens = await _exchange_code(oauth)
    status, body = await _refresh(oauth, tokens["access_token"])
    assert (status, body["error"]) == (400, "invalid_grant")
    assert oauth.verify_access_token(tokens["refresh_token"]) is None


@pytest.mark.parametrize(
    "token",
    ["", "garbage", "v1.e30.", "v1.%%%.sig", "v1.e30.sığ", "v2.a.b", "a.b.c.d"],
)
async def test_malformed_refresh_tokens_rejected(token):
    oauth = PublicOAuthCompat("https://example.test", signing_key=SIGNING_KEY)
    status, body = await _refresh(oauth, token)
    assert (status, body["error"]) == (400, "invalid_grant")


async def test_tampered_token_rejected():
    oauth = PublicOAuthCompat("https://example.test", signing_key=SIGNING_KEY)
    tokens = await _exchange_code(oauth)
    prefix, payload, sig = tokens["access_token"].split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    claims["typ"] = "refresh"
    forged = _b64url(json.dumps(claims).encode())
    status, _ = await _refresh(oauth, f"{prefix}.{forged}.{sig}")
    assert status == 400


async def test_refresh_token_rotates_and_is_single_use():
    oauth = PublicOAuthCompat("https://example.test", signing_key=SIGNING_KEY)
    tokens = await _exchange_code(oauth)

    status, rotated = await _refresh(oauth, tokens["refresh_token"])
    assert status == 200
    assert rotated["refresh_token"] != tokens["refresh_token"]

    status, body = await _refresh(oauth, tokens["refresh_token"])  # replay
    assert (status, body["error"]) == (400, "invalid_grant")

    status, _ = await _refresh(oauth, rotated["refresh_token"])
    assert status == 200


async def test_refresh_token_bound_to_client():
    oauth = PublicOAuthCompat("https://example.test", signing_key=SIGNING_KEY)
    tokens = await _exchange_code(oauth, "client-a")
    status, _ = await _refresh(oauth, tokens["refresh_token"], client_id="client-b")
    assert status == 400


async def test_store_caps_evict_oldest():
    oauth = PublicOAuthCompat("https://example.test", signing_key=SIGNING_KEY)
    oauth.store.max_clients = 3
    oauth.store.max_codes = 2
    oauth.store.max_used_refresh_ids = 2

    client_ids = [await _register(oauth, [CLAUDE_CALLBACK]) for _ in range(5)]
    assert list(oauth.store.clients) == client_ids[-3:]

    codes = [await _code_for(oauth) for _ in range(4)]
    assert list(oauth.store.codes) == codes[-2:]

    refresh = (await _exchange_code(oauth))["refresh_token"]
    for _ in range(4):
        status, body = await _refresh(oauth, refresh)
        assert status == 200
        refresh = body["refresh_token"]
    assert len(oauth.store.used_refresh_ids) == 2


@pytest.mark.parametrize(
    "payload",
    [
        None,
        ["https://claude.ai/cb"],
        {"redirect_uris": []},
        {"redirect_uris": [CLAUDE_CALLBACK] * 11},
        {"redirect_uris": ["https://claude.ai/" + "a" * 3000]},
        {"redirect_uris": [123]},
    ],
)
async def test_register_rejects_oversized_or_malformed_metadata(payload):
    oauth = PublicOAuthCompat("https://example.test", signing_key=SIGNING_KEY)
    response = await oauth.register(DummyRequest(json=payload))
    assert response.status_code == 400
    assert not oauth.store.clients
