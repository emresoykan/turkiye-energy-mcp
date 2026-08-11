"""Minimal public OAuth endpoints for Claude custom connectors.

The MCP tools themselves remain authless. Claude.ai's connector UI often
attempts Dynamic Client Registration even for public servers; these routes
complete that handshake with auto-approved codes and opaque bearer tokens
so the connector can reach `/mcp`.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response


@dataclass
class _Client:
    client_id: str
    redirect_uris: list[str]
    client_name: str | None = None


@dataclass
class _AuthCode:
    code: str
    client_id: str
    redirect_uri: str
    code_challenge: str
    code_challenge_method: str
    expires_at: float


@dataclass
class _Token:
    access_token: str
    client_id: str
    expires_at: float


@dataclass
class PublicOAuthStore:
    clients: dict[str, _Client] = field(default_factory=dict)
    codes: dict[str, _AuthCode] = field(default_factory=dict)
    tokens: dict[str, _Token] = field(default_factory=dict)

    def prune(self) -> None:
        now = time.time()
        self.codes = {key: value for key, value in self.codes.items() if value.expires_at > now}
        self.tokens = {
            key: value for key, value in self.tokens.items() if value.expires_at > now
        }


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def verify_pkce(code_verifier: str, challenge: str, method: str) -> bool:
    if method != "S256":
        return False
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return secrets.compare_digest(_b64url(digest), challenge)


class PublicOAuthCompat:
    def __init__(self, public_base_url: str, mcp_path: str = "/mcp") -> None:
        self.public_base_url = public_base_url.rstrip("/")
        self.mcp_path = mcp_path if mcp_path.startswith("/") else f"/{mcp_path}"
        self.resource = f"{self.public_base_url}{self.mcp_path}"
        self.store = PublicOAuthStore()

    def authorization_server_metadata(self) -> dict[str, Any]:
        return {
            "issuer": self.public_base_url,
            "authorization_endpoint": f"{self.public_base_url}/authorize",
            "token_endpoint": f"{self.public_base_url}/token",
            "registration_endpoint": f"{self.public_base_url}/register",
            "jwks_uri": f"{self.public_base_url}/.well-known/jwks.json",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": ["mcp"],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["none"],
            "client_id_metadata_document_supported": True,
        }

    def protected_resource_metadata(self) -> dict[str, Any]:
        return {
            "resource": self.resource,
            "authorization_servers": [self.public_base_url],
            "bearer_methods_supported": ["header"],
            "scopes_supported": ["mcp"],
        }

    async def register(self, request: Request) -> Response:
        payload = await request.json()
        redirect_uris = payload.get("redirect_uris") or []
        if not isinstance(redirect_uris, list) or not redirect_uris:
            return JSONResponse(
                {"error": "invalid_client_metadata", "error_description": "redirect_uris required"},
                status_code=400,
            )
        client_id = f"public-{secrets.token_urlsafe(16)}"
        client = _Client(
            client_id=client_id,
            redirect_uris=[str(uri) for uri in redirect_uris],
            client_name=str(payload.get("client_name") or "claude-connector"),
        )
        self.store.clients[client_id] = client
        return JSONResponse(
            {
                "client_id": client_id,
                "client_id_issued_at": int(time.time()),
                "client_secret_expires_at": 0,
                "redirect_uris": client.redirect_uris,
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
                "client_name": client.client_name,
            },
            status_code=201,
        )

    async def authorize(self, request: Request) -> Response:
        params = request.query_params
        client_id = params.get("client_id")
        redirect_uri = params.get("redirect_uri")
        response_type = params.get("response_type")
        state = params.get("state")
        code_challenge = params.get("code_challenge")
        code_challenge_method = params.get("code_challenge_method", "S256")

        if response_type != "code":
            return JSONResponse({"error": "unsupported_response_type"}, status_code=400)
        if not client_id or not redirect_uri or not code_challenge:
            return JSONResponse({"error": "invalid_request"}, status_code=400)

        client = self.store.clients.get(client_id)
        if client is None:
            # Accept first-seen public clients (CIMD / preconfigured IDs).
            client = _Client(client_id=client_id, redirect_uris=[redirect_uri])
            self.store.clients[client_id] = client
        if redirect_uri not in client.redirect_uris:
            client.redirect_uris.append(redirect_uri)

        code = secrets.token_urlsafe(24)
        self.store.codes[code] = _AuthCode(
            code=code,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            expires_at=time.time() + 600,
        )
        query = {"code": code}
        if state:
            query["state"] = state
        return RedirectResponse(f"{redirect_uri}?{urlencode(query)}", status_code=302)

    async def token(self, request: Request) -> Response:
        self.store.prune()
        form = await request.form()
        grant_type = str(form.get("grant_type") or "")
        client_id = str(form.get("client_id") or "")

        if grant_type == "authorization_code":
            code = str(form.get("code") or "")
            redirect_uri = str(form.get("redirect_uri") or "")
            code_verifier = str(form.get("code_verifier") or "")
            auth_code = self.store.codes.pop(code, None)
            if auth_code is None or auth_code.expires_at < time.time():
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            if auth_code.client_id != client_id or auth_code.redirect_uri != redirect_uri:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            if not verify_pkce(
                code_verifier, auth_code.code_challenge, auth_code.code_challenge_method
            ):
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
        elif grant_type == "refresh_token":
            refresh = str(form.get("refresh_token") or "")
            token = self.store.tokens.get(refresh)
            if token is None or token.expires_at < time.time() or token.client_id != client_id:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
        else:
            return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

        access_token = secrets.token_urlsafe(32)
        refresh_token = secrets.token_urlsafe(32)
        expires_in = 3600
        expires_at = time.time() + expires_in
        self.store.tokens[access_token] = _Token(
            access_token=access_token, client_id=client_id, expires_at=expires_at
        )
        self.store.tokens[refresh_token] = _Token(
            access_token=refresh_token, client_id=client_id, expires_at=expires_at + 86400
        )
        return JSONResponse(
            {
                "access_token": access_token,
                "token_type": "bearer",
                "expires_in": expires_in,
                "refresh_token": refresh_token,
                "scope": "mcp",
            }
        )

    async def jwks(self, _: Request) -> Response:
        return JSONResponse({"keys": []})

    async def root_help(self, _: Request) -> Response:
        body = f"""<!doctype html>
<html lang="tr"><head><meta charset="utf-8"><title>Türkiye Energy MCP</title></head>
<body style="font-family:sans-serif;max-width:42rem;margin:2rem auto;line-height:1.5">
<h1>Türkiye Energy MCP</h1>
<p>Claude / Cursor bağlanırken MCP URL olarak şunu kullanın:</p>
<pre>{self.resource}</pre>
<p>Sağlık kontrolü: <a href="/health">/health</a></p>
</body></html>"""
        return HTMLResponse(body)
