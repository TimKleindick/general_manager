"""Opt-in owner-only credential storage and serialized refresh for local evals."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
import tempfile
import time
from typing import Any, Iterator

from filelock import FileLock

from .auth import Session, load_mapping, login, state_path
from .errors import EvalError
from .http import API, AUTH, json_request


def credential_path() -> Path:
    return state_path().with_name("session.json")


def private_directory(path: Path) -> None:
    if path.parent.is_symlink() or path.is_symlink():
        raise EvalError("unsafe_storage_path")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.stat().st_uid != os.getuid():
        raise EvalError("unsafe_storage_owner")
    os.chmod(path.parent, 0o700)


def write_record(path: Path, record: dict[str, Any]) -> None:
    private_directory(path)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(record, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_record(path: Path) -> dict[str, Any]:
    private_directory(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as handle:
        info = os.fstat(handle.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            raise EvalError("storage_permissions_must_be_0600")
        record = json.load(handle)
    if not isinstance(record, dict):
        raise EvalError("invalid_saved_session")
    return record


def validate_tokens(tokens: dict[str, Any]) -> None:
    if (
        tokens.get("token_type", "").lower() != "bearer"
        or not isinstance(tokens.get("access_token"), str)
        or not tokens["access_token"]
        or not isinstance(tokens.get("refresh_token"), str)
        or not tokens["refresh_token"]
        or not {"resource.invoke", "chatgpt.tokens.use.direct"}
        <= set(tokens.get("scope", "").split())
        or not 0 < int(tokens.get("expires_in", 0)) <= 3600
    ):
        raise EvalError("invalid_saved_token_response")


class PersistentSession:
    def __init__(self, path: Path, record: dict[str, Any], session: Session) -> None:
        self.path = path
        self.record = record
        self.session = session

    def access_token(self) -> str:
        if self.record.get("refresh_pending"):
            raise EvalError("refresh_interrupted_sign_out_and_sign_in_again")
        if time.time() >= self.session.expires_at - 90:
            # Persist intent before rotation. An interrupted/ambiguous exchange must
            # never automatically replay a potentially consumed refresh token.
            self.record["refresh_pending"] = True
            write_record(self.path, self.record)
            tokens = json_request(
                AUTH + "/api/accounts/oauth/token",
                form=True,
                body={
                    "grant_type": "refresh_token",
                    "client_id": self.session.client,
                    "refresh_token": self.session.tokens["refresh_token"],
                    "resource": API,
                },
            )
            validate_tokens(tokens)
            expires = time.time() + int(tokens["expires_in"])
            self.record.update(tokens=tokens, expires_at=expires, refresh_pending=False)
            write_record(self.path, self.record)
            self.session.tokens.clear()
            self.session.tokens = tokens
            self.session.expires_at = expires
        return self.session.access_token()

    def close(self) -> None:
        self.session.tokens.clear()
        self.record.clear()


def restore_or_login(path: Path) -> PersistentSession:
    if path.exists():
        record = read_record(path)
        mapping = load_mapping(state_path())
        if any(
            record.get(key) != mapping.get(key) or not record.get(key)
            for key in ("host", "client", "subject")
        ):
            raise EvalError("saved_registration_mismatch")
        if record.get("refresh_pending"):
            raise EvalError("refresh_interrupted_sign_out_and_sign_in_again")
        validate_tokens(record["tokens"])
        discovery = json_request(AUTH + "/.well-known/openid-configuration")
        if (
            discovery.get("issuer") != AUTH
            or discovery.get("token_endpoint") != AUTH + "/api/accounts/oauth/token"
        ):
            raise EvalError("unexpected_oidc_configuration")
        session = Session(
            record["client"], record["tokens"], discovery, float(record["expires_at"])
        )
    else:
        session = login()
        try:
            validate_tokens(session.tokens)
            mapping = load_mapping(state_path())
            record = {
                **mapping,
                "tokens": session.tokens,
                "expires_at": session.expires_at,
                "refresh_pending": False,
            }
            write_record(path, record)
        except BaseException:
            if not session.logout():
                print("Widerruf nicht bestätigt. App in ChatGPT Settings trennen.")
            raise
    return PersistentSession(path, record, session)


@contextmanager
def saved_session() -> Iterator[PersistentSession]:
    path = credential_path()
    private_directory(path)
    # Serialize the entire session across processes, including refresh and logout.
    with FileLock(str(path.with_suffix(".lock")), timeout=0):
        session = restore_or_login(path)
        try:
            yield session
        finally:
            session.close()


def logout_saved() -> bool:
    path = credential_path()
    private_directory(path)
    with FileLock(str(path.with_suffix(".lock")), timeout=0):
        if not path.exists():
            return True
        record = read_record(path)
        discovery = json_request(AUTH + "/.well-known/openid-configuration")
        session = Session(record["client"], record["tokens"], discovery, 0)
        confirmed = session.logout()
        path.unlink()
        return confirmed


if __name__ == "__main__":
    try:
        print("logout_confirmed:", logout_saved())
    except Exception:  # noqa: BLE001 -- credential boundary
        print("Abmeldung nicht bestätigt. App in ChatGPT Settings trennen.")
        raise SystemExit(1) from None
