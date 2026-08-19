"""Guardrail tests: allowlist + dry-run must hold before anything else is built."""
import pytest

from config import settings
from gh import client as ghc


def make_client(monkeypatch):
    monkeypatch.setattr(ghc.Auth, "Token", lambda t: None)   # PyGithub asserts on empty token
    monkeypatch.setattr(ghc, "Github", lambda auth: object())  # no network
    return ghc.GitHubClient()


def test_refuses_repo_outside_allowlist(monkeypatch):
    monkeypatch.setattr(settings, "github_allowed_repos", "me/allowed")
    c = make_client(monkeypatch)
    with pytest.raises(PermissionError):
        c.post_review_summary("me/not-allowed", 1, "hi")


def test_dry_run_does_not_call_github(monkeypatch, capsys):
    monkeypatch.setattr(settings, "github_allowed_repos", "me/allowed")
    monkeypatch.setattr(settings, "dry_run", True)
    c = make_client(monkeypatch)
    c.post_review_summary("me/allowed", 7, "looks fine")  # would explode if it touched the fake Github()
    assert "[DRY_RUN]" in capsys.readouterr().out


def test_client_has_no_dangerous_methods():
    for name in ("approve", "merge", "request_changes", "push"):
        assert not any(name in attr for attr in dir(ghc.GitHubClient)), name
