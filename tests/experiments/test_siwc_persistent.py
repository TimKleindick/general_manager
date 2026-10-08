"""Credential persistence tests use only synthetic, worthless token strings."""

import time

import pytest

from experiments.siwc_eval import persistent_auth as auth
from experiments.siwc_eval.auth import Session
from experiments.siwc_eval.errors import EvalError


def tokens(access="synthetic-access", refresh="synthetic-refresh"):
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "scope": "resource.invoke chatgpt.tokens.use.direct",
        "expires_in": 3600,
    }


@pytest.fixture
def saved(tmp_path, monkeypatch):
    path = tmp_path / "private" / "session.json"
    mapping = {
        "host": "synthetic-host",
        "client": "oaiapp_synthetic",
        "subject": "synthetic-subject",
    }
    record = {
        **mapping,
        "tokens": tokens(),
        "expires_at": time.time() + 3600,
        "refresh_pending": False,
    }
    auth.write_record(path, record)
    monkeypatch.setattr(auth, "load_mapping", lambda _: mapping)
    monkeypatch.setattr(
        auth,
        "json_request",
        lambda *_args, **_kwargs: {
            "issuer": auth.AUTH,
            "token_endpoint": auth.AUTH + "/api/accounts/oauth/token",
        },
    )
    return path, record


def test_saved_session_reuses_login_and_preserves_private_storage(saved, monkeypatch):
    path, record = saved
    monkeypatch.setattr(auth, "login", lambda: pytest.fail("unexpected login"))
    session = auth.restore_or_login(path)
    assert session.access_token() == "synthetic-access"
    session.close()
    assert auth.read_record(path) == record
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700


def test_refresh_rotates_and_atomically_persists(saved, monkeypatch):
    path, record = saved
    session = auth.restore_or_login(path)
    session.session.expires_at = 0
    requests = []

    def refresh(url, **kwargs):
        assert auth.read_record(path)["refresh_pending"] is True
        requests.append((url, kwargs))
        return tokens("synthetic-new-access", "synthetic-new-refresh")

    monkeypatch.setattr(auth, "json_request", refresh)
    assert session.access_token() == "synthetic-new-access"
    assert session.access_token() == "synthetic-new-access"
    assert len(requests) == 1
    form = requests[0][1]["body"]
    assert form == {
        "grant_type": "refresh_token",
        "client_id": record["client"],
        "refresh_token": "synthetic-refresh",
        "resource": auth.API,
    }
    stored = auth.read_record(path)
    assert stored["tokens"] == tokens("synthetic-new-access", "synthetic-new-refresh")
    assert stored["refresh_pending"] is False


def test_ambiguous_refresh_is_not_replayed(saved, monkeypatch):
    path, _ = saved
    session = auth.restore_or_login(path)
    session.session.expires_at = 0

    def fail(*args, **kwargs):
        raise EvalError("network_error")

    monkeypatch.setattr(auth, "json_request", fail)
    with pytest.raises(EvalError, match="network_error"):
        session.access_token()
    with pytest.raises(EvalError, match="refresh_interrupted"):
        auth.restore_or_login(path)


def test_account_mismatch_is_rejected(saved):
    path, record = saved
    record["subject"] = "another-account"
    auth.write_record(path, record)
    with pytest.raises(EvalError, match="registration_mismatch"):
        auth.restore_or_login(path)


def test_unprotected_and_symlink_files_are_rejected(saved, tmp_path):
    path, _ = saved
    path.chmod(0o644)
    with pytest.raises(EvalError, match="permissions"):
        auth.read_record(path)
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(EvalError, match="unsafe_storage_path"):
        auth.read_record(link)


def test_logout_revokes_and_removes_only_credentials(saved, monkeypatch):
    path, _ = saved
    calls = []
    monkeypatch.setattr(auth, "credential_path", lambda: path)
    monkeypatch.setattr(
        Session, "logout", lambda self: calls.append(self.client) or True
    )
    assert auth.logout_saved() is True
    assert not path.exists()
    assert calls == ["oaiapp_synthetic"]


def test_session_lock_prevents_overlapping_use(saved, monkeypatch):
    from filelock import Timeout

    path, _ = saved
    monkeypatch.setattr(auth, "credential_path", lambda: path)
    with auth.saved_session():
        with pytest.raises(Timeout):
            with auth.saved_session():
                pytest.fail("concurrent session")


def test_first_login_is_saved_without_logout(tmp_path, monkeypatch):
    path = tmp_path / "private" / "session.json"
    session = Session("oaiapp_synthetic", tokens(), {}, time.time() + 3600)
    monkeypatch.setattr(auth, "login", lambda: session)
    monkeypatch.setattr(
        auth,
        "load_mapping",
        lambda _: {"client": session.client, "host": "host", "subject": "subject"},
    )
    result = auth.restore_or_login(path)
    result.close()
    assert auth.read_record(path)["tokens"] == tokens()


def test_comparison_runs_requested_models_in_order(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from experiments.siwc_eval import compare

    calls = []

    class Saved:
        def access_token(self):
            return "synthetic"

    @contextmanager
    def saved_session():
        yield Saved()

    async def evaluate(transport, datasets, report, path, model):
        calls.append(model)
        report.update(
            completed=True,
            results=[{"passed": True, "requests": 1, "judge_requests": 1}],
        )

    monkeypatch.setattr(compare, "__file__", str(tmp_path / "compare.py"))
    monkeypatch.setattr(compare, "configure", lambda: None)
    monkeypatch.setattr(compare, "saved_session", saved_session)
    monkeypatch.setattr(
        compare,
        "json_request",
        lambda *_args, **_kwargs: {
            "models": [
                {"slug": model, "visibility": "list"} for model in compare.MODELS
            ]
        },
    )
    monkeypatch.setattr(compare, "evaluate_suite", evaluate)
    assert compare.main() == 0
    assert calls == ["gpt-6-astra", "gpt-5.6-sol", "gpt-5.6-luna"]
