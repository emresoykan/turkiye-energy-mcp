import base64
import hashlib
import json

import pytest
from starlette.datastructures import FormData, QueryParams

from turkiye_energy_mcp.oauth_compat import PublicOAuthCompat, verify_pkce


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


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

    class DummyRequest:
        def __init__(self, **kwargs):
            self._json = kwargs.get("json")
            self.query_params = QueryParams(kwargs.get("query", {}))
            self._form = kwargs.get("form", {})

        async def json(self):
            return self._json

        async def form(self):
            return FormData(self._form)

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
