import pytest

from f1dash.data.http import Fetcher, HostBudget, RateLimited


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
