"""OAuth 2.1 authorization server for the hosted Lifescape MCP endpoint (BUI-1903).

Claude's OAuth client signs the owner in here; sign-in is delegated to GitHub, and only the
GitHub login in ``OWNER_GITHUB_LOGIN`` gets a token. Port of retire-runway BUI-1857.

The MCP SDK handlers serve ``/authorize`` and ``/token`` (they check PKCE S256, the client, the
redirect, and the code's client and expiry). This module adds the server metadata, dynamic
client registration at ``/register``, and the GitHub callback.

The server keeps no database, so any replica can serve any request. The client ID, the
sign-in state, the authorization code, the refresh token and the access token are each a JWT
signed with ``LIFESCAPE_OAUTH_SIGNING_KEY`` under its own ``typ`` and ``aud``, so one kind never
passes for another. Spent codes and refresh tokens are recorded in this process only (see
``SpentGrants``).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import re
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx2
import jwt
from mcp.server.auth.errors import stringify_pydantic_error
from mcp.server.auth.handlers.authorize import AuthorizationHandler
from mcp.server.auth.handlers.metadata import MetadataHandler
from mcp.server.auth.handlers.token import TokenHandler
from mcp.server.auth.json_response import PydanticJSONResponse
from mcp.server.auth.middleware.client_auth import ClientAuthenticator
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    RegistrationErrorCode,
    TokenError,
    construct_redirect_uri,
)
from mcp.server.auth.routes import cors_middleware
from mcp.server.transport_security import (
    DEFAULT_MAX_REQUEST_BODY_SIZE,
    RequestBodyLimitMiddleware,
)
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthMetadata,
    OAuthToken,
)
from pydantic import AnyUrl, BaseModel, ValidationError
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

# The names the fleet sets on Vercel (machine-setup ``plugin-connect``), each read under one name.
OWNER_ENV = "OWNER_GITHUB_LOGIN"
GITHUB_CLIENT_ID_ENV = "LIFESCAPE_GITHUB_OAUTH_CLIENT_ID"
GITHUB_CLIENT_SECRET_ENV = "LIFESCAPE_GITHUB_OAUTH_CLIENT_SECRET"
SIGNING_KEY_ENV = "LIFESCAPE_OAUTH_SIGNING_KEY"
REQUIRED_ENV = (OWNER_ENV, GITHUB_CLIENT_ID_ENV, GITHUB_CLIENT_SECRET_ENV, SIGNING_KEY_ENV)
MIN_SIGNING_KEY_LENGTH = 32

METADATA_PATH = "/.well-known/oauth-authorization-server"
AUTHORIZE_PATH = "/authorize"
TOKEN_PATH = "/token"
REGISTER_PATH = "/register"
GITHUB_CALLBACK_PATH = "/oauth/github/callback"

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"

CLAUDE_REDIRECT_URI = "https://claude.ai/api/mcp/auth_callback"
LOOPBACK_REDIRECT_HOSTS = frozenset({"localhost", "127.0.0.1"})
LOOPBACK_REDIRECT_PATH = "/callback"
MAX_REDIRECT_URIS = 5

SCOPE = "mcp"
GRANT_TYPES = ("authorization_code", "refresh_token")
AUTH_METHODS = ("none", "client_secret_post", "client_secret_basic")
ACCESS_TOKEN_SECONDS = 60 * 60
REFRESH_TOKEN_SECONDS = 30 * 24 * 60 * 60
CODE_SECONDS = 60
STATE_SECONDS = 10 * 60
MAX_CLIENT_STATE = 512
# Claude waits about ten seconds for each endpoint; each GitHub call gets under half of that.
GITHUB_TIMEOUT_SECONDS = 4.0
BINDING_COOKIE = "lifescape_oauth_binding"

# GitHub login: alphanumerics and single inner hyphens, up to 39 characters.
GITHUB_LOGIN_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")
CHALLENGE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")

Kind = Literal["client", "state", "code", "refresh", "access"]
# One explicit type per signed value (RFC 8725 §3.11); access tokens use RFC 9068 ``at+jwt``.
TYP: dict[Kind, str] = {
    "client": "lifescape-client+jwt",
    "state": "lifescape-state+jwt",
    "code": "lifescape-code+jwt",
    "refresh": "lifescape-refresh+jwt",
    "access": "at+jwt",
}


class OAuthConfigError(ValueError):
    """A required OAuth setting is missing or unusable; the server must not start.

    ``missing`` and ``invalid`` name the variables (never their values).
    """

    def __init__(
        self, message: str, *, missing: tuple[str, ...] = (), invalid: tuple[str, ...] = ()
    ) -> None:
        super().__init__(message)
        self.missing = missing
        self.invalid = invalid


class GitHubSignInError(Exception):
    """GitHub did not confirm who signed in."""


@dataclass(frozen=True)
class OAuthSettings:
    owner_login: str
    github_client_id: str
    github_client_secret: str
    signing_key: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> OAuthSettings:
        """Read the settings, naming every missing or unusable variable in one error."""
        values = {name: env.get(name, "").strip() for name in REQUIRED_ENV}
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise OAuthConfigError(
                f"Lifescape OAuth needs {', '.join(missing)}; set it and restart",
                missing=tuple(missing),
            )
        problems: dict[str, str] = {}
        if not GITHUB_LOGIN_PATTERN.match(values[OWNER_ENV]):
            problems[OWNER_ENV] = f"{OWNER_ENV} is not a GitHub login: {values[OWNER_ENV]!r}"
        if len(values[SIGNING_KEY_ENV]) < MIN_SIGNING_KEY_LENGTH:
            problems[SIGNING_KEY_ENV] = (
                f"{SIGNING_KEY_ENV} must be at least {MIN_SIGNING_KEY_LENGTH} characters "
                "(for example the output of `openssl rand -base64 48`)"
            )
        if problems:
            raise OAuthConfigError("; ".join(problems.values()), invalid=tuple(problems))
        return cls(
            owner_login=values[OWNER_ENV],
            github_client_id=values[GITHUB_CLIENT_ID_ENV],
            github_client_secret=values[GITHUB_CLIENT_SECRET_ENV],
            signing_key=values[SIGNING_KEY_ENV],
        )


class SpentGrants:
    """Codes and refresh tokens already exchanged, in this process.

    A replica that did not see the first exchange accepts a replay until the grant expires
    (60 seconds for a code); a store shared by every replica closes that gap.
    """

    def __init__(self) -> None:
        self._spent: dict[str, float] = {}

    def claim(self, grant_id: str, expires_at: float) -> bool:
        """True on the first claim of ``grant_id`` only."""
        now = time.time()
        self._spent = {key: until for key, until in self._spent.items() if until > now}
        if grant_id in self._spent:
            return False
        self._spent[grant_id] = expires_at
        return True


# ------------------------------------------------------------------ redirects


def _loopback_redirect(value: str) -> tuple[str, int | None] | None:
    """(host, port) of a canonical ``http://localhost|127.0.0.1[:port]/callback``, else None."""
    parts = urlsplit(value)
    try:
        port = parts.port
    except ValueError:
        return None
    if (
        parts.scheme != "http"
        or parts.hostname not in LOOPBACK_REDIRECT_HOSTS
        or parts.path != LOOPBACK_REDIRECT_PATH
        or parts.query
        or parts.fragment
        or parts.username is not None
        or parts.password is not None
    ):
        return None
    return parts.hostname, port


def allowed_redirect(value: str) -> bool:
    """Claude's hosted callback, or a loopback callback (Claude Code, Claude Desktop)."""
    return value == CLAUDE_REDIRECT_URI or _loopback_redirect(value) is not None


class OwnerClient(OAuthClientInformationFull):
    """A registered client; a loopback redirect matches whatever its port (RFC 8252 §7.3)."""

    def validate_redirect_uri(self, redirect_uri: AnyUrl | None) -> AnyUrl:
        if redirect_uri is not None:
            requested = _loopback_redirect(str(redirect_uri))
            registered = [_loopback_redirect(str(uri)) for uri in self.redirect_uris or []]
            if requested is not None and any(
                entry is not None and entry[0] == requested[0] for entry in registered
            ):
                return redirect_uri
        return super().validate_redirect_uri(redirect_uri)


# ------------------------------------------------------------- signed claims


class _Claims(BaseModel):
    jti: str
    iat: int


class _ClientClaims(_Claims):
    ru: list[str]
    am: str


class _StateClaims(_Claims):
    exp: int
    cid: str
    ru: str
    rx: bool
    cc: str
    sc: list[str]
    cs: str | None = None


class _CodeClaims(_Claims):
    exp: int
    sub: str
    cid: str
    ru: str
    rx: bool
    cc: str
    sc: list[str]


class _RefreshClaims(_Claims):
    exp: int
    sub: str
    cid: str
    sc: list[str]


class _AccessClaims(_Claims):
    exp: int
    iss: str
    aud: str
    sub: str
    client_id: str
    scope: str


def _binding(state: str) -> str:
    """Cookie value that ties a sign-in state to the browser that started it."""
    digest = hashlib.sha256(state.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _error_page(status: int, title: str, message: str) -> HTMLResponse:
    body = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f"<title>{html.escape(title)}</title></head><body><h1>{html.escape(title)}</h1>"
        f"<p>{html.escape(message)}</p></body></html>"
    )
    return HTMLResponse(
        body,
        status_code=status,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'",
            "X-Content-Type-Options": "nosniff",
        },
    )


class _RegistrationError(BaseModel):
    error: RegistrationErrorCode
    error_description: str


def _registration_error(error: RegistrationErrorCode, description: str) -> Response:
    return PydanticJSONResponse(
        content=_RegistrationError(error=error, error_description=description),
        status_code=400,
        headers={"Cache-Control": "no-store"},
    )


def _github_text(response: httpx2.Response, name: str) -> str:
    if response.status_code != 200:
        raise GitHubSignInError(f"GitHub answered {response.status_code}")
    try:
        body = response.json()
    except ValueError as error:
        raise GitHubSignInError("GitHub answered with something other than JSON") from error
    value = body.get(name) if isinstance(body, dict) else None
    if not isinstance(value, str) or not value:
        raise GitHubSignInError(f"GitHub's answer has no {name}")
    return value


class OwnerAuthorizationServer(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    """The authorization server and the token verifier for one owner.

    ``issuer`` is the origin serving the OAuth endpoints; ``resource`` is the MCP resource
    identifier every access token is issued for (its ``aud``).
    """

    def __init__(
        self,
        settings: OAuthSettings,
        *,
        issuer: str,
        resource: str,
        github_transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._issuer = issuer
        self._resource = resource
        self._github_transport = github_transport
        self._spent = SpentGrants()
        self._callback_url = f"{issuer}{GITHUB_CALLBACK_PATH}"
        self._authorize_handler = AuthorizationHandler(self)
        self._token_handler = TokenHandler(self, ClientAuthenticator(self))
        self._metadata = OAuthMetadata.model_validate(
            {
                "issuer": issuer,
                "authorization_endpoint": f"{issuer}{AUTHORIZE_PATH}",
                "token_endpoint": f"{issuer}{TOKEN_PATH}",
                "registration_endpoint": f"{issuer}{REGISTER_PATH}",
                "scopes_supported": [SCOPE],
                "response_types_supported": ["code"],
                "grant_types_supported": list(GRANT_TYPES),
                "token_endpoint_auth_methods_supported": list(AUTH_METHODS),
                "code_challenge_methods_supported": ["S256"],
            }
        )

    # --------------------------------------------------------- signed values

    def _audience(self, kind: Kind) -> str:
        return self._resource if kind == "access" else f"urn:lifescape:{kind}"

    def _sign(self, kind: Kind, claims: Mapping[str, Any], lifetime: int | None) -> str:
        now = int(time.time())
        payload: dict[str, Any] = {
            **claims,
            "iss": self._issuer,
            "aud": self._audience(kind),
            "iat": now,
            "jti": secrets.token_urlsafe(16),
        }
        if lifetime is not None:
            payload["exp"] = now + lifetime
        return jwt.encode(
            payload, self._settings.signing_key, algorithm="HS256", headers={"typ": TYP[kind]}
        )

    def _open[M: _Claims](self, kind: Kind, token: str, model: type[M]) -> M | None:
        """The claims of a value this server signed as ``kind``, else None."""
        required = ["iss", "aud", "iat", "jti"] + ([] if kind == "client" else ["exp"])
        try:
            decoded = jwt.decode_complete(
                token,
                self._settings.signing_key,
                algorithms=["HS256"],
                audience=self._audience(kind),
                issuer=self._issuer,
                options={"require": required},
            )
        except jwt.PyJWTError:
            return None
        if decoded["header"].get("typ") != TYP[kind]:
            return None
        try:
            return model.model_validate(decoded["payload"])
        except ValidationError:
            return None

    def _client_secret(self, client_id: str) -> str:
        key = self._settings.signing_key.encode()
        digest = hmac.new(key, f"client-secret:{client_id}".encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).decode().rstrip("=")

    def _is_owner(self, login: str) -> bool:
        return login.lower() == self._settings.owner_login.lower()

    # ------------------------------------------------- SDK provider protocol

    def _client(self, client_id: str, claims: _ClientClaims) -> OwnerClient:
        secret = None if claims.am == "none" else self._client_secret(client_id)
        return OwnerClient.model_validate(
            {
                "client_id": client_id,
                "client_id_issued_at": claims.iat,
                "client_secret": secret,
                "client_secret_expires_at": None if secret is None else 0,
                "redirect_uris": claims.ru,
                "token_endpoint_auth_method": claims.am,
                "grant_types": list(GRANT_TYPES),
                "response_types": ["code"],
                "scope": SCOPE,
            }
        )

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        # The signature proves ``register`` checked these redirects and the auth method.
        claims = self._open("client", client_id, _ClientClaims)
        return None if claims is None else self._client(client_id, claims)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        raise NotImplementedError(
            "clients register through OwnerAuthorizationServer.register, which signs the "
            "client ID; the SDK registration handler is not mounted"
        )

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        if params.resource is not None and params.resource.rstrip("/") != self._resource:
            raise AuthorizeError("invalid_target", "resource is not this MCP server")
        if not CHALLENGE_PATTERN.match(params.code_challenge):
            raise AuthorizeError("invalid_request", "code_challenge must be an S256 challenge")
        if params.state is not None and len(params.state) > MAX_CLIENT_STATE:
            raise AuthorizeError("invalid_request", "state is too long")
        state = self._sign(
            "state",
            {
                "cid": client.client_id,
                "ru": str(params.redirect_uri),
                "rx": params.redirect_uri_provided_explicitly,
                "cc": params.code_challenge,
                "sc": params.scopes or [SCOPE],
                "cs": params.state,
            },
            STATE_SECONDS,
        )
        query = urlencode(
            {
                "client_id": self._settings.github_client_id,
                "redirect_uri": self._callback_url,
                "state": state,
                "allow_signup": "false",
            }
        )
        return f"{GITHUB_AUTHORIZE_URL}?{query}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        claims = self._open("code", authorization_code, _CodeClaims)
        if claims is None or claims.cid != client.client_id:
            return None
        # Spent before the SDK checks the verifier, so a stolen code allows one guess.
        if not self._spent.claim(f"code:{claims.jti}", claims.exp):
            return None
        return AuthorizationCode(
            code=authorization_code,
            scopes=claims.sc,
            expires_at=claims.exp,
            client_id=claims.cid,
            code_challenge=claims.cc,
            redirect_uri=AnyUrl(claims.ru),
            redirect_uri_provided_explicitly=claims.rx,
            resource=self._resource,
            subject=claims.sub,
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        if not self._is_owner(authorization_code.subject or ""):
            raise TokenError("invalid_grant", "the authorization code is not for the owner")
        return self._issue(authorization_code.client_id, authorization_code.scopes)

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        claims = self._open("refresh", refresh_token, _RefreshClaims)
        if claims is None or claims.cid != client.client_id:
            return None
        if not self._spent.claim(f"refresh:{claims.jti}", claims.exp):
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=claims.cid,
            scopes=claims.sc,
            expires_at=claims.exp,
            resource=self._resource,
            subject=claims.sub,
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        if not self._is_owner(refresh_token.subject or ""):
            raise TokenError("invalid_grant", "the refresh token is not for the owner")
        return self._issue(refresh_token.client_id, scopes)

    async def load_access_token(self, token: str) -> AccessToken | None:
        claims = self._open("access", token, _AccessClaims)
        if claims is None or not self._is_owner(claims.sub):
            return None
        return AccessToken(
            token=token,
            client_id=claims.client_id,
            scopes=claims.scope.split(),
            expires_at=claims.exp,
            resource=claims.aud,
            subject=claims.sub,
            claims={"iss": claims.iss},
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        raise NotImplementedError("token revocation is not offered: the endpoint is not mounted")

    async def verify_token(self, token: str) -> AccessToken | None:
        """``TokenVerifier`` for the resource server (BUI-1901)."""
        return await self.load_access_token(token)

    def _issue(self, client_id: str, scopes: list[str]) -> OAuthToken:
        owner = self._settings.owner_login
        access = self._sign(
            "access",
            {"sub": owner, "client_id": client_id, "scope": " ".join(scopes)},
            ACCESS_TOKEN_SECONDS,
        )
        refresh = self._sign(
            "refresh", {"sub": owner, "cid": client_id, "sc": scopes}, REFRESH_TOKEN_SECONDS
        )
        return OAuthToken(
            access_token=access,
            expires_in=ACCESS_TOKEN_SECONDS,
            scope=" ".join(scopes),
            refresh_token=refresh,
        )

    # ------------------------------------------------------------- endpoints

    async def register(self, request: Request) -> Response:
        """RFC 7591 dynamic client registration; the client ID is the signed registration."""
        try:
            metadata = OAuthClientMetadata.model_validate_json(await request.body())
        except ValidationError as error:
            return _registration_error("invalid_client_metadata", stringify_pydantic_error(error))
        redirects = [str(uri) for uri in metadata.redirect_uris or []]
        if (
            not redirects
            or len(redirects) > MAX_REDIRECT_URIS
            or not all(allowed_redirect(uri) for uri in redirects)
        ):
            return _registration_error(
                "invalid_redirect_uri",
                f"redirect_uris must list up to {MAX_REDIRECT_URIS} of {CLAUDE_REDIRECT_URI}, "
                f"http://localhost[:port]{LOOPBACK_REDIRECT_PATH} or "
                f"http://127.0.0.1[:port]{LOOPBACK_REDIRECT_PATH}",
            )
        method = metadata.token_endpoint_auth_method or "client_secret_post"
        if method not in AUTH_METHODS:
            return _registration_error(
                "invalid_client_metadata",
                f"token_endpoint_auth_method must be one of {', '.join(AUTH_METHODS)}",
            )
        if "authorization_code" not in metadata.grant_types or not set(
            metadata.grant_types
        ).issubset(GRANT_TYPES):
            return _registration_error(
                "invalid_client_metadata",
                "grant_types must include authorization_code and may add refresh_token",
            )
        if set(metadata.response_types) != {"code"}:
            return _registration_error("invalid_client_metadata", "response_types must be code")
        if metadata.scope is not None and set(metadata.scope.split()) != {SCOPE}:
            return _registration_error("invalid_client_metadata", f"scope must be {SCOPE}")
        client_id = self._sign("client", {"ru": redirects, "am": method}, None)
        claims = _ClientClaims(jti="", iat=int(time.time()), ru=redirects, am=method)
        name = metadata.client_name[:100] if metadata.client_name else None
        registered = self._client(client_id, claims).model_copy(update={"client_name": name})
        return PydanticJSONResponse(
            content=registered, status_code=201, headers={"Cache-Control": "no-store"}
        )

    async def authorize_endpoint(self, request: Request) -> Response:
        """The SDK authorization handler, plus the cookie that binds sign-in to this browser."""
        response = await self._authorize_handler.handle(request)
        location = response.headers.get("location", "")
        if response.status_code == 302 and location.startswith(f"{GITHUB_AUTHORIZE_URL}?"):
            state = parse_qs(urlsplit(location).query)["state"][0]
            response.set_cookie(
                BINDING_COOKIE,
                _binding(state),
                max_age=STATE_SECONDS,
                path=GITHUB_CALLBACK_PATH,
                secure=self._issuer.startswith("https://"),
                httponly=True,
                samesite="lax",
            )
        return response

    async def github_callback(self, request: Request) -> Response:
        """Finish sign-in: only the owner's GitHub login gets an authorization code."""
        params = request.query_params
        sealed = params.get("state", "")
        state = self._open("state", sealed, _StateClaims) if sealed else None
        if state is None:
            return self._refuse(400, "The sign-in response is incomplete or was not issued here.")
        cookie = request.cookies.get(BINDING_COOKIE, "")
        if not hmac.compare_digest(cookie.encode(), _binding(sealed).encode()):
            return self._refuse(
                400, "The sign-in did not start in this browser. Start again from Claude."
            )
        code = params.get("code", "")
        if "error" in params or not code:
            return self._refuse(400, "GitHub did not complete the sign-in.")
        try:
            login = await self._github_login(code)
        except GitHubSignInError as error:
            return self._refuse(502, f"GitHub could not confirm who you are ({error}). Try again.")
        if not self._is_owner(login):
            return self._refuse(
                403, "This server serves only its owner. Sign in with the owner's GitHub account."
            )
        # The state is signed only after the SDK matched ``ru`` to a registered redirect.
        authorization_code = self._sign(
            "code",
            {
                "sub": self._settings.owner_login,
                "cid": state.cid,
                "ru": state.ru,
                "rx": state.rx,
                "cc": state.cc,
                "sc": state.sc,
            },
            CODE_SECONDS,
        )
        target = construct_redirect_uri(state.ru, code=authorization_code, state=state.cs)
        response = RedirectResponse(target, status_code=302, headers={"Cache-Control": "no-store"})
        self._clear_binding(response)
        return response

    def _refuse(self, status: int, message: str) -> Response:
        response = _error_page(status, "Sign-in refused", message)
        self._clear_binding(response)
        return response

    def _clear_binding(self, response: Response) -> None:
        response.delete_cookie(
            BINDING_COOKIE,
            path=GITHUB_CALLBACK_PATH,
            secure=self._issuer.startswith("https://"),
            httponly=True,
            samesite="lax",
        )

    async def _github_login(self, code: str) -> str:
        """Exchange GitHub's code for a GitHub token, then read the login it belongs to."""
        try:
            async with httpx2.AsyncClient(
                transport=self._github_transport, timeout=GITHUB_TIMEOUT_SECONDS
            ) as client:
                exchange = await client.post(
                    GITHUB_TOKEN_URL,
                    json={
                        "client_id": self._settings.github_client_id,
                        "client_secret": self._settings.github_client_secret,
                        "code": code,
                        "redirect_uri": self._callback_url,
                    },
                    headers={"Accept": "application/json"},
                )
                github_token = _github_text(exchange, "access_token")
                user = await client.get(
                    GITHUB_USER_URL,
                    headers={
                        "Accept": "application/vnd.github+json",
                        "Authorization": f"Bearer {github_token}",
                        "X-GitHub-Api-Version": "2022-11-28",
                        "User-Agent": "lifescape-oauth",
                    },
                )
                return _github_text(user, "login")
        except httpx2.HTTPError as error:
            raise GitHubSignInError(
                f"GitHub could not be reached: {type(error).__name__}"
            ) from error

    def routes(self) -> list[Route]:
        """The authorization-server routes, to mount beside the MCP resource server."""
        cors = Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["POST", "OPTIONS"],
            allow_headers=["content-type", "authorization"],
        )
        body_limit = Middleware(
            RequestBodyLimitMiddleware, max_body_size=DEFAULT_MAX_REQUEST_BODY_SIZE
        )
        return [
            Route(
                METADATA_PATH,
                endpoint=cors_middleware(
                    MetadataHandler(self._metadata).handle, ["GET", "OPTIONS"]
                ),
                methods=["GET", "OPTIONS"],
            ),
            Route(
                AUTHORIZE_PATH,
                endpoint=self.authorize_endpoint,
                methods=["GET", "POST"],
                middleware=[body_limit],
            ),
            Route(
                TOKEN_PATH,
                endpoint=self._token_handler.handle,
                methods=["POST", "OPTIONS"],
                middleware=[cors, body_limit],
            ),
            Route(
                REGISTER_PATH,
                endpoint=self.register,
                methods=["POST", "OPTIONS"],
                middleware=[cors, body_limit],
            ),
            Route(GITHUB_CALLBACK_PATH, endpoint=self.github_callback, methods=["GET"]),
        ]
