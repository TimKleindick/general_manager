"""Local interactive OAuth; tokens never persisted and refresh never automated."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import socket
import tempfile
import time
import threading
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit
import uuid
import webbrowser

from .http import AUTH, API, json_request, request
from .errors import EvalError, fail

SCOPE = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"


@dataclass(repr=False)
class Attempt:
    host: str
    redirect_uri: str
    client: str = "dynamic_agent_client"
    state: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    nonce: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    verifier: str = field(default_factory=lambda: secrets.token_urlsafe(48))
    created: float = field(default_factory=time.monotonic)
    used: bool = False

    def url(self) -> str:
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )
        query = {
            "client_id": self.client,
            "ext_agent_host_id": self.host,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "scope": SCOPE,
            "resource": API,
            "state": self.state,
            "nonce": self.nonce,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
        }
        if self.client == "dynamic_agent_client":
            query["agent_name_hint"] = "GeneralManager local evaluation"
        return AUTH + "/api/accounts/authorize?" + urlencode(query)

    def exchange_form(self, query: dict[str, list[str]]) -> dict[str, str]:
        if self.used or time.monotonic() - self.created > 180:
            fail("oauth_attempt_expired_or_used")
        self.used = True
        if any(len(values) != 1 for values in query.values()):
            fail("ambiguous_callback")
        if not secrets.compare_digest(query.get("state", [""])[0], self.state):
            fail("oauth_state_mismatch")
        if "error" in query:
            fail("oauth_consent_denied")
        issued = query.get("client_id", [self.client])[0]
        if not issued.startswith("oaiapp_") or (
            self.client != "dynamic_agent_client" and issued != self.client
        ):
            fail("oauth_client_mismatch")
        code = query.get("code", [""])[0]
        if not code:
            fail("oauth_code_missing")
        return {
            "grant_type": "authorization_code",
            "client_id": issued,
            "code": code,
            "code_verifier": self.verifier,
            "redirect_uri": self.redirect_uri,
            "resource": API,
        }


def state_path() -> Path:
    if os.name != "posix":
        fail("use_linux_macos_or_wsl_for_private_storage")
    return Path.home() / ".config" / "general-manager-siwc-eval" / "registration.json"


def save_mapping(path: Path, mapping: dict[str, str]) -> None:
    """Write only explicitly allowed non-token registration fields, atomically."""
    if set(mapping) - {"host", "client", "subject", "pending_client"}:
        fail("refusing_to_persist_credentials")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.is_symlink() or path.is_symlink():
        fail("unsafe_storage_path")
    os.chmod(path.parent, 0o700)
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as file:
            json.dump(mapping, file)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def load_mapping(path: Path) -> dict[str, str]:
    if path.is_symlink():
        fail("unsafe_storage_path")
    if not path.exists():
        mapping = {"host": "urn:uuid:" + str(uuid.uuid4())}
        save_mapping(path, mapping)
        return mapping
    if path.stat().st_mode & 0o077:
        fail("storage_permissions_must_be_0600")
    result = json.loads(path.read_text())
    if (
        not isinstance(result, dict)
        or set(result) - {"host", "client", "subject", "pending_client"}
        or not result.get("host")
    ):
        fail("invalid_registration_mapping")
    return result


def verify_identity(
    tokens: dict[str, Any], client: str, nonce: str, discovery: dict[str, Any]
) -> dict[str, Any]:
    import jwt  # Optional experiment-only PyJWT[crypto] dependency.

    if (
        discovery.get("issuer") != AUTH
        or discovery.get("jwks_uri") != AUTH + "/.well-known/jwks.json"
    ):
        fail("unexpected_oidc_configuration")
    keys = json_request(discovery["jwks_uri"])
    try:
        header = jwt.get_unverified_header(tokens["id_token"])
        key = next(key for key in keys["keys"] if key["kid"] == header["kid"])
        claims: dict[str, Any] = jwt.decode(
            tokens["id_token"],
            jwt.PyJWK.from_dict(key).key,
            algorithms=["RS256"],
            audience=client,
            issuer=AUTH,
            leeway=5,
            options={"require": ["iss", "aud", "sub", "exp", "iat", "nonce"]},
        )
        if (
            claims["nonce"] != nonce
            or not isinstance(claims["sub"], str)
            or not claims["sub"]
        ):
            fail("identity_validation_failed")
    except (jwt.PyJWTError, KeyError, ValueError, TypeError, StopIteration):
        raise EvalError("identity_validation_failed") from None
    return claims


@dataclass(repr=False)
class Session:
    client: str
    tokens: dict[str, Any]
    discovery: dict[str, Any]
    expires_at: float

    def access_token(self) -> str:
        if time.time() >= self.expires_at - 90:
            fail("session_expired_sign_in_again")
        return str(self.tokens["access_token"])

    def logout(self) -> bool:
        confirmed = False
        try:
            endpoint = self.discovery.get("revocation_endpoint", "")
            if urlsplit(endpoint).netloc != "auth.openai.com":
                fail("untrusted_revocation_endpoint")
            refresh = self.tokens.get("refresh_token")
            if refresh:
                with request(
                    endpoint,
                    form=True,
                    body={
                        "token": refresh,
                        "token_type_hint": "refresh_token",
                        "client_id": self.client,
                    },
                ) as response:
                    confirmed = response.status == 200
        except (EvalError, OSError, ValueError):
            confirmed = False
        finally:
            self.tokens.clear()
        return confirmed


class LoopbackServer(HTTPServer):
    """Bound idle reads and total lifetime of each accepted callback request."""

    request_deadline_seconds = 3.0

    def get_request(self) -> tuple[socket.socket, Any]:
        connection, address = super().get_request()
        connection.settimeout(1.0)
        return connection, address

    def finish_request(
        self, request: socket.socket | tuple[bytes, socket.socket], client_address: Any
    ) -> None:
        if not isinstance(request, socket.socket):
            fail("unexpected_callback_socket")

        def expire() -> None:
            try:
                request.shutdown(socket.SHUT_RDWR)
            except OSError:
                return
            finally:
                request.close()

        timer = threading.Timer(self.request_deadline_seconds, expire)
        timer.daemon = True
        timer.start()
        try:
            super().finish_request(request, client_address)
        finally:
            timer.cancel()

    def handle_error(self, request: Any, client_address: Any) -> None:
        # Never emit callback paths or request exception details.
        return


def login() -> Session:
    """Must be called only after action-time terminal confirmation on user's PC."""
    from filelock import FileLock

    path = state_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with FileLock(str(path.parent / "login.lock"), timeout=0):
        mapping = load_mapping(path)
        discovery = json_request(AUTH + "/.well-known/openid-configuration")
        if (
            discovery.get("issuer") != AUTH
            or discovery.get("authorization_endpoint")
            != AUTH + "/api/accounts/authorize"
            or discovery.get("token_endpoint") != AUTH + "/api/accounts/oauth/token"
        ):
            fail("unexpected_oidc_configuration")
        callback: dict[str, list[str]] = {}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass  # Default access logs would expose the authorization code.

            def do_GET(self) -> None:
                parsed = urlsplit(self.path)
                if parsed.path != "/auth/callback":
                    self.send_error(404)
                    return
                query = parse_qs(parsed.query, keep_blank_values=True)
                if query.get("state") != [attempt.state]:
                    self.send_error(400)
                    return
                callback.update(query)
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(
                    b"Return to the local terminal. Validation is pending."
                )

        with LoopbackServer(("127.0.0.1", 0), Handler) as server:
            server.timeout = 1
            attempt = Attempt(
                mapping["host"],
                f"http://127.0.0.1:{server.server_port}/auth/callback",
                mapping.get(
                    "client", mapping.get("pending_client", "dynamic_agent_client")
                ),
            )
            print("Continue with ChatGPT — Anmeldung im lokalen Systembrowser.")
            if not webbrowser.open(attempt.url()):
                fail("system_browser_unavailable")
            while not callback and time.monotonic() - attempt.created < 180:
                server.handle_request()
            form = attempt.exchange_form(callback)
        if "client" not in mapping:
            mapping["pending_client"] = form["client_id"]
            save_mapping(path, mapping)
        tokens = json_request(discovery["token_endpoint"], form=True, body=form)
        session = Session(form["client_id"], tokens, discovery, time.time())
        try:
            claims = verify_identity(tokens, session.client, attempt.nonce, discovery)
            if mapping.get("subject", claims["sub"]) != claims["sub"]:
                fail("returning_account_mismatch")
            mapping.pop("pending_client", None)
            mapping.update(client=session.client, subject=claims["sub"])
            save_mapping(path, mapping)
            scopes = set(tokens.get("scope", "").split())
            if not {"chatgpt.tokens.use.direct", "resource.invoke"} <= scopes:
                fail("plan_usage_not_authorized")
            if tokens.get("token_type", "").lower() != "bearer" or not tokens.get(
                "access_token"
            ):
                fail("invalid_token_response")
            session.expires_at = time.time() + min(int(tokens["expires_in"]), 3600)
        except BaseException:
            if not session.logout():
                print("Widerruf nicht bestätigt. App in ChatGPT Settings trennen.")
            raise
        return session
