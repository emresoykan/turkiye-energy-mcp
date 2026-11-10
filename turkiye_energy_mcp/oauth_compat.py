"""Minimal public OAuth endpoints for Claude custom connectors.

The MCP tools themselves remain authless. Claude.ai's connector UI often
attempts Dynamic Client Registration even for public servers; these routes
complete that handshake with auto-approved codes and bearer tokens so the
connector can reach `/mcp`.

Tokens are stateless and HMAC-signed (type + client + expiry), so with a fixed
`OAUTH_SIGNING_KEY` they survive restarts; only short-lived authorization
codes, DCR clients and used refresh-token ids live in bounded memory.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode, urlsplit

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


logger = logging.getLogger(__name__)

AUTH_CODE_TTL_SECONDS = 600
ACCESS_TOKEN_TTL_SECONDS = 3600
REFRESH_TOKEN_TTL_SECONDS = 30 * 86400
MAX_CLIENTS = 1000
MAX_CODES = 1000
MAX_USED_REFRESH_IDS = 10_000
MAX_REDIRECT_URIS = 10
MAX_REDIRECT_URI_LENGTH = 2048


def _now() -> float:
    return time.time()


def _put_bounded(mapping: dict[str, Any], key: str, value: Any, cap: int) -> None:
    """Insert and evict the oldest entries (dicts keep insertion order) beyond `cap`."""
    mapping[key] = value
    while len(mapping) > cap:
        del mapping[next(iter(mapping))]


@dataclass
class PublicOAuthStore:
    """Unauthenticated callers write here, so every collection is capped and pruned."""

    clients: dict[str, _Client] = field(default_factory=dict)
    codes: dict[str, _AuthCode] = field(default_factory=dict)
    # Rotated refresh tokens: jti -> expiry. A token past its expiry is rejected anyway.
    used_refresh_ids: dict[str, float] = field(default_factory=dict)
    max_clients: int = MAX_CLIENTS
    max_codes: int = MAX_CODES
    max_used_refresh_ids: int = MAX_USED_REFRESH_IDS

    def prune(self) -> None:
        now = _now()
        self.codes = {key: value for key, value in self.codes.items() if value.expires_at > now}
        self.used_refresh_ids = {
            key: exp for key, exp in self.used_refresh_ids.items() if exp > now
        }

    def add_client(self, client: _Client) -> None:
        _put_bounded(self.clients, client.client_id, client, self.max_clients)

    def add_code(self, code: _AuthCode) -> None:
        _put_bounded(self.codes, code.code, code, self.max_codes)

    def mark_refresh_used(self, jti: str, expires_at: float) -> None:
        _put_bounded(self.used_refresh_ids, jti, expires_at, self.max_used_refresh_ids)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class TokenSigner:
    """Stateless `v1.<payload>.<hmac-sha256>` tokens carrying type, client, expiry, id."""

    _PREFIX = "v1"

    def __init__(self, key: bytes) -> None:
        self._key = key

    def _sign(self, signing_input: str) -> str:
        return _b64url(hmac.new(self._key, signing_input.encode("ascii"), "sha256").digest())

    def issue(self, token_type: str, client_id: str, ttl_seconds: int) -> str:
        claims = {
            "typ": token_type,
            "cid": client_id,
            "exp": int(_now()) + ttl_seconds,
            "jti": secrets.token_urlsafe(16),
        }
        payload = _b64url(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
        signing_input = f"{self._PREFIX}.{payload}"
        return f"{signing_input}.{self._sign(signing_input)}"

    def verify(self, token: str, token_type: str) -> dict[str, Any] | None:
        """Return the claims of a valid, unexpired token of `token_type`, else None."""
        parts = token.split(".")
        if len(parts) != 3 or parts[0] != self._PREFIX:
            return None
        try:
            signing_input = f"{parts[0]}.{parts[1]}"
            if not hmac.compare_digest(self._sign(signing_input), parts[2]):
                return None
            claims = json.loads(_b64url_decode(parts[1]))
        except (ValueError, TypeError):  # bad base64/JSON, non-ASCII signature
            return None
        if not isinstance(claims, dict) or claims.get("typ") != token_type:
            return None
        exp = claims.get("exp")
        if not isinstance(exp, int) or exp <= _now():
            return None
        if not isinstance(claims.get("cid"), str) or not isinstance(claims.get("jti"), str):
            return None
        return claims


# Redirect hosts accepted for clients that did not register via /register (claude.ai uses
# preconfigured / CIMD client ids). HTTPS only, exact host match.
DEFAULT_ALLOWED_REDIRECT_HOSTS = ("claude.ai", "claude.com")
# Loopback callbacks (Claude Code, MCP inspector, local dev) on any port, http or https.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def parse_redirect_hosts(raw: str | None) -> tuple[str, ...]:
    """Parse comma-separated `OAUTH_ALLOWED_REDIRECT_HOSTS`; empty means the defaults."""
    if not raw or not raw.strip():
        return DEFAULT_ALLOWED_REDIRECT_HOSTS
    return tuple(host.strip().lower() for host in raw.split(",") if host.strip())


def _redirect_host_allowed(uri: str, allowed_hosts: Iterable[str]) -> bool:
    # Backslashes/whitespace are rejected outright: browsers treat `\` as `/`, so
    # `https://evil.test\@claude.ai` would parse as claude.ai here but open evil.test.
    if any(ch == "\\" or ch.isspace() or ord(ch) < 0x20 for ch in uri):
        return False
    try:
        parts = urlsplit(uri)
        host = (parts.hostname or "").lower()
        parts.port  # noqa: B018 - raises ValueError on a malformed port
    except ValueError:
        return False
    if parts.fragment or not host:
        return False
    if host in _LOOPBACK_HOSTS:
        return parts.scheme in ("http", "https")
    return parts.scheme == "https" and host in allowed_hosts


def verify_pkce(code_verifier: str, challenge: str, method: str) -> bool:
    if method != "S256":
        return False
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return secrets.compare_digest(_b64url(digest), challenge)


class PublicOAuthCompat:
    def __init__(
        self,
        public_base_url: str,
        mcp_path: str = "/mcp",
        *,
        allowed_redirect_hosts: Iterable[str] = DEFAULT_ALLOWED_REDIRECT_HOSTS,
        signing_key: str | None = None,
    ) -> None:
        self.public_base_url = public_base_url.rstrip("/")
        self.allowed_redirect_hosts = frozenset(h.lower() for h in allowed_redirect_hosts)
        self.mcp_path = mcp_path if mcp_path.startswith("/") else f"/{mcp_path}"
        self.resource = f"{self.public_base_url}{self.mcp_path}"
        self.store = PublicOAuthStore()
        if signing_key:
            if len(signing_key) < 32:
                logger.warning("OAUTH_SIGNING_KEY is shorter than 32 characters.")
            key = signing_key.encode("utf-8")
        else:
            logger.warning(
                "OAUTH_SIGNING_KEY is not set: using a random per-process key, so OAuth "
                "tokens will not survive restarts/redeploys (connectors must re-authorize)."
            )
            key = secrets.token_bytes(32)
        self.signer = TokenSigner(key)

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
        try:
            payload = await request.json()
        except ValueError:
            payload = None
        redirect_uris = payload.get("redirect_uris") if isinstance(payload, dict) else None
        if (
            not isinstance(redirect_uris, list)
            or not 0 < len(redirect_uris) <= MAX_REDIRECT_URIS
            or not all(
                isinstance(uri, str) and 0 < len(uri) <= MAX_REDIRECT_URI_LENGTH
                for uri in redirect_uris
            )
        ):
            return JSONResponse(
                {
                    "error": "invalid_client_metadata",
                    "error_description": (
                        f"redirect_uris: 1-{MAX_REDIRECT_URIS} strings of at most "
                        f"{MAX_REDIRECT_URI_LENGTH} characters required"
                    ),
                },
                status_code=400,
            )
        # DCR is unauthenticated, so registration must not become a way around the
        # /authorize allowlist: anyone could register https://evil.test and then use
        # this server as an open redirect.
        rejected = [
            uri
            for uri in redirect_uris
            if not _redirect_host_allowed(uri, self.allowed_redirect_hosts)
        ]
        if rejected:
            return JSONResponse(
                {
                    "error": "invalid_redirect_uri",
                    "error_description": "redirect_uri host not allowed",
                },
                status_code=400,
            )
        self.store.prune()
        client_id = f"public-{secrets.token_urlsafe(16)}"
        client = _Client(
            client_id=client_id,
            redirect_uris=list(redirect_uris),
            client_name=str(payload.get("client_name") or "claude-connector")[:200],
        )
        self.store.add_client(client)
        return JSONResponse(
            {
                "client_id": client_id,
                "client_id_issued_at": int(_now()),
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

        # Never redirect to an unvetted URI (open redirect): registered clients get exact
        # matches against their DCR redirect_uris; first-seen clients (CIMD / preconfigured
        # ids, or DCR clients forgotten after a restart) only the host allowlist.
        client = self.store.clients.get(client_id)
        if client is not None:
            allowed = redirect_uri in client.redirect_uris
        else:
            allowed = _redirect_host_allowed(redirect_uri, self.allowed_redirect_hosts)
        if not allowed:
            return JSONResponse(
                {"error": "invalid_request", "error_description": "redirect_uri not allowed"},
                status_code=400,
            )

        self.store.prune()
        code = secrets.token_urlsafe(24)
        self.store.add_code(
            _AuthCode(
                code=code,
                client_id=client_id,
                redirect_uri=redirect_uri,
                code_challenge=code_challenge,
                code_challenge_method=code_challenge_method,
                expires_at=_now() + AUTH_CODE_TTL_SECONDS,
            )
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
            if auth_code is None or auth_code.expires_at < _now():
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            if auth_code.client_id != client_id or auth_code.redirect_uri != redirect_uri:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            if not verify_pkce(
                code_verifier, auth_code.code_challenge, auth_code.code_challenge_method
            ):
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
        elif grant_type == "refresh_token":
            # verify() checks signature, expiry and type, so an access token is not a
            # refresh token. Refresh tokens rotate: each one is accepted once.
            claims = self.signer.verify(str(form.get("refresh_token") or ""), "refresh")
            if (
                claims is None
                or claims["cid"] != client_id
                or claims["jti"] in self.store.used_refresh_ids
            ):
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            self.store.mark_refresh_used(claims["jti"], claims["exp"])
        else:
            return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

        return JSONResponse(
            {
                "access_token": self.signer.issue(
                    "access", client_id, ACCESS_TOKEN_TTL_SECONDS
                ),
                "token_type": "bearer",
                "expires_in": ACCESS_TOKEN_TTL_SECONDS,
                "refresh_token": self.signer.issue(
                    "refresh", client_id, REFRESH_TOKEN_TTL_SECONDS
                ),
                "scope": "mcp",
            }
        )

    def verify_access_token(self, token: str) -> dict[str, Any] | None:
        return self.signer.verify(token, "access")

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
