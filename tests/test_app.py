import pytest

from .conftest import QUALI, RACE, SPRINT


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/season/2026",
        f"/session/{RACE}",
        f"/session/{RACE}?focus=16,3",
        f"/session/{QUALI}",
        "/live",
        "/healthz",
    ],
)
def test_pages_render(client, path):
    resp = client.get(path)
    assert resp.status_code == 200, resp.data[:500]


@pytest.mark.parametrize(
    "name", ["results", "positions", "gaps", "laptimes", "stints", "weather", "racecontrol"]
)
def test_race_panels(client, name):
    resp = client.get(f"/session/{RACE}/panel/{name}?focus=3,16")
    assert resp.status_code == 200
    assert b"panel-error" not in resp.data or name == "racecontrol"


@pytest.mark.parametrize(
    "path",
    [
        f"/session/{SPRINT}",
        f"/session/{SPRINT}/panel/results",
        f"/session/{SPRINT}/panel/positions",
        f"/session/{SPRINT}/panel/gaps",
        f"/session/{SPRINT}/panel/laptimes",
        f"/session/{SPRINT}/panel/stints",
        f"/session/{SPRINT}/panel/weather",
        f"/session/{SPRINT}/panel/racecontrol",
        f"/session/{SPRINT}/telemetry",
        f"/session/{SPRINT}/telemetry?a=3&b=16",
        f"/api/v1/sessions/{SPRINT}",
        f"/api/v1/sessions/{SPRINT}/charts/laptimes",
        f"/api/v1/sessions/{SPRINT}/telemetry?a=3&b=16",
    ],
)
def test_session_without_data_renders(bare_client, path):
    resp = bare_client.get(path)
    assert resp.status_code == 200, resp.data[:500]


def test_session_without_data_shows_empty_state(bare_client):
    html = bare_client.get(f"/session/{SPRINT}/panel/results").get_data(as_text=True)
    assert "No data published" in html
    assert bare_client.get(f"/api/v1/sessions/{SPRINT}").get_json()["results"] == []


def test_results_panel_content(client):
    html = client.get(f"/session/{RACE}/panel/results").get_data(as_text=True)
    assert html.index("VER") < html.index("LEC") < html.index("ANT")
    assert "1:42:00.785" in html


def test_focus_assigns_slots_in_given_order(client):
    import json
    import re

    html = client.get(f"/session/{RACE}/panel/positions?focus=16,3").get_data(as_text=True)
    spec = json.loads(re.search(r'class="chart-data">(.*?)</script>', html, re.S).group(1))
    slots = {s["code"]: s["slot"] for s in spec["series"] if s["slot"]}
    assert slots == {"LEC": 1, "VER": 2}
    assert spec["bands"] == [
        {"kind": "VSC", "start": 8, "end": 9},
        {"kind": "VSC", "start": 39, "end": 40},
    ]


def test_telemetry_panel(client):
    resp = client.get(f"/session/{RACE}/telemetry?a=3&b=16")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "VER" in html and "LEC" in html and '"kind":"telemetry"' in html
    assert '"kind":"track"' in html


def test_unknown_things_404(client):
    assert client.get("/session/1").status_code == 404
    assert client.get(f"/session/{RACE}/panel/nope").status_code == 404
    assert client.get("/season/1900").status_code == 404
    assert client.get("/api/v1/sessions/1").status_code == 404


def test_api(client):
    idx = client.get("/api/v1/").get_json()
    assert "endpoints" in idx
    s = client.get(f"/api/v1/sessions/{RACE}").get_json()
    assert s["results"][0]["code"] == "VER"
    chart = client.get(f"/api/v1/sessions/{RACE}/charts/stints").get_json()
    assert chart["kind"] == "stints" and chart["drivers"]
    tele = client.get(f"/api/v1/sessions/{RACE}/telemetry?a=3&b=16").get_json()
    assert len(tele["traces"]) == 2 and "delta" in tele
    assert client.get(f"/api/v1/sessions/{RACE}/telemetry").status_code == 400
    season = client.get("/api/v1/seasons/2026").get_json()
    assert len(season["calendar"]) == 23
    prog = client.get("/api/v1/seasons/2026/progression/drivers").get_json()
    assert prog["series"][0]["code"] == "ANT"


def test_live_stream_first_frames(client):
    resp = client.get(f"/live/stream?session={RACE}&speed=60&lap=10")
    assert resp.mimetype == "text/event-stream"
    chunks = iter(resp.response)
    first = next(chunks)
    frame = next(chunks)
    resp.close()
    text = (first if isinstance(first, str) else first.decode()) + (
        frame if isinstance(frame, str) else frame.decode()
    )
    assert "retry:" in text
    assert "event: tower" in text and "event: status" in text
    assert "VER" in text


def test_security_headers(client):
    resp = client.get("/healthz")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
