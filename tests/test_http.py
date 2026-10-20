import pytest
import requests

from f1dash.data.http import Fetcher, HostBudget, RateLimited, UpstreamError


def test_budget_refuses_long_waits():
    b = HostBudget(per_minute=60, burst=2)
    b.take(max_wait=0)
    b.take(max_wait=0)
    with pytest.raises(RateLimited):
        b.take(max_wait=0.1)


class FakeResponse:
    def __init__(self, status, headers=None):
        self.status_code = status
        self.headers = headers or {}

    def json(self):
        return {"ok": True}


def test_long_retry_after_fails_fast(tmp_path, monkeypatch):
    f = Fetcher(tmp_path, min_interval=0)
    calls = []
    monkeypatch.setattr(
        f.session,
        "get",
        lambda *a, **k: calls.append(1) or FakeResponse(429, {"Retry-After": "60"}),
    )
    with pytest.raises(RateLimited):
        f.get_json("https://api.openf1.org/v1/laps", {"session_key": 1})
    assert len(calls) == 1
    # The host stays blocked: the next call doesn't even hit the network.
    with pytest.raises(RateLimited):
        f.get_json("https://api.openf1.org/v1/stints", {"session_key": 1})
    assert len(calls) == 1


def test_unreachable_host_cools_down(tmp_path, monkeypatch):
    f = Fetcher(tmp_path, min_interval=0, retries=0)
    calls = []

    def refuse(*a, **k):
        calls.append(1)
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(f.session, "get", refuse)
    with pytest.raises(UpstreamError):
        f.get_json("https://api.openf1.org/v1/laps", {"session_key": 1})
    assert len(calls) == 1
    # Later calls fail straight away instead of waiting through timeouts again.
    with pytest.raises(UpstreamError):
        f.get_json("https://api.openf1.org/v1/stints", {"session_key": 1})
    assert len(calls) == 1


def test_server_errors_cool_down(tmp_path, monkeypatch):
    f = Fetcher(tmp_path, min_interval=0, retries=0)
    calls = []
    monkeypatch.setattr(f.session, "get", lambda *a, **k: calls.append(1) or FakeResponse(502))
    with pytest.raises(UpstreamError):
        f.get_json("https://api.jolpi.ca/ergast/f1/2026.json")
    with pytest.raises(UpstreamError):
        f.get_json("https://api.jolpi.ca/ergast/f1/2025.json")
    assert len(calls) == 1
