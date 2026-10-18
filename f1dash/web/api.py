"""Read-only JSON API (``/api/v1``). Same data the charts render."""

from __future__ import annotations

from dataclasses import asdict

from flask import Blueprint, abort, jsonify, request

from ..data.http import UpstreamError
from ..services import NoSuchSession
from .limits import limited
from .pages import _check_year, _int_arg, dash, focus_raw

bp = Blueprint("api", __name__, url_prefix="/api/v1")

CHARTS = {"positions", "gaps", "laptimes", "stints", "weather"}


@bp.errorhandler(NoSuchSession)
@bp.errorhandler(404)
def _not_found(_exc):
    return jsonify(error="not found"), 404


@bp.errorhandler(UpstreamError)
def _upstream(exc):
    return jsonify(error="upstream unavailable", detail=str(exc)), 503


@bp.errorhandler(429)
def _too_many(_exc):
    return jsonify(error="rate limited"), 429


@bp.get("/")
def index():
    return jsonify(
        endpoints={
            "GET /api/v1/seasons/<year>": "calendar with session keys, standings",
            "GET /api/v1/seasons/<year>/progression/<drivers|teams>": "points after each round",
            "GET /api/v1/sessions/<key>": "session info, results, weather summary",
            "GET /api/v1/sessions/<key>/charts/<name>": f"chart data, name in {sorted(CHARTS)}",
            "GET /api/v1/sessions/<key>/race-control": "race control messages",
            "GET /api/v1/sessions/<key>/telemetry?a=&b=&la=&lb=": "two-lap telemetry compare",
        },
        query={"focus": "comma-separated driver numbers to highlight (max 3)"},
        source="OpenF1 (sessions, 2023+), Jolpica (seasons), MultiViewer (circuits)",
    )


@bp.get("/seasons/<int:year>")
def season(year: int):
    _check_year(year)
    return jsonify(dash().season(year))


@bp.get("/seasons/<int:year>/progression/<what>")
def progression(year: int, what: str):
    _check_year(year)
    if what not in {"drivers", "teams"}:
        abort(404)
    return jsonify(dash().chart_progression(year, what, focus_raw()))


@bp.get("/sessions/<int:session_key>")
def session(session_key: int):
    d = dash()
    s = d.session(session_key)
    ov = d.overview(s, focus_raw())
    return jsonify(
        session=s,
        results=ov["rows"],
        drivers={n: asdict(v) for n, v in ov["drivers"].items()},
        focus=ov["focus"],
        weather=ov["weather"],
    )


@bp.get("/sessions/<int:session_key>/charts/<name>")
def chart(session_key: int, name: str):
    if name not in CHARTS:
        abort(404)
    d = dash()
    s = d.session(session_key)
    focus = d.overview(s, focus_raw())["focus"]
    data = {
        "positions": lambda: d.chart_positions(s, focus),
        "gaps": lambda: d.chart_gaps(s, focus),
        "laptimes": lambda: d.chart_laptimes(s, focus, request.args.get("all") != "1"),
        "stints": lambda: d.chart_stints(s),
        "weather": lambda: d.chart_weather(s),
    }[name]()
    return jsonify(data)


@bp.get("/sessions/<int:session_key>/race-control")
def race_control(session_key: int):
    d = dash()
    return jsonify(d.race_control(d.session(session_key)))


@bp.get("/sessions/<int:session_key>/telemetry")
@limited(rate=0.5, burst=6)
def telemetry(session_key: int):
    a, b = _int_arg("a"), _int_arg("b")
    if a is None or b is None:
        return jsonify(error="query parameters a and b (driver numbers) are required"), 400
    d = dash()
    s = d.session(session_key)
    return jsonify(d.telemetry(s, a, b, _int_arg("la"), _int_arg("lb")))
