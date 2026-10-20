"""HTML pages and the htmx partials they lazy-load."""

from __future__ import annotations

from datetime import UTC, datetime

from flask import Blueprint, abort, current_app, redirect, render_template, request, url_for

from ..data import analysis as A
from ..data.http import UpstreamError
from ..data.sources import FIRST_OPENF1_YEAR
from ..services import Dashboard
from .limits import limited

bp = Blueprint("pages", __name__)

PANELS = {"results", "positions", "gaps", "laptimes", "stints", "weather", "racecontrol"}


def dash() -> Dashboard:
    return current_app.extensions["dashboard"]


def _check_year(year: int) -> None:
    if not 1950 <= year <= datetime.now(UTC).year + 1:
        abort(404)


def focus_raw() -> str:
    """``?focus=1,16`` and ``?focus=1&focus=16`` both work."""
    return ",".join(request.args.getlist("focus"))


def _int_arg(name: str) -> int | None:
    value = request.args.get(name, "")
    return int(value) if value.isdigit() else None


@bp.get("/")
def home():
    return render_template("home.html", **dash().home())


@bp.get("/season")
def season_index():
    year = _int_arg("year") or datetime.now(UTC).year
    return redirect(url_for("pages.season", year=year))


@bp.get("/season/<int:year>")
def season(year: int):
    _check_year(year)
    data = dash().season(year)
    if not data["calendar"]:
        abort(404)
    years = list(range(datetime.now(UTC).year, 1949, -1))
    return render_template("season.html", years=years, first_session_year=FIRST_OPENF1_YEAR, **data)


@bp.get("/season/<int:year>/progression/<what>")
def season_progression(year: int, what: str):
    _check_year(year)
    if what not in {"drivers", "teams"}:
        abort(404)
    chart = dash().chart_progression(year, what, focus_raw())
    return render_template("partials/chart.html", chart=chart, chart_id=f"prog-{what}")


@bp.get("/session/<int:session_key>")
def session(session_key: int):
    d = dash()
    s = d.session(session_key)
    ov = d.overview(s, focus_raw())
    return render_template(
        "session.html",
        s=s,
        siblings=d.siblings(s),
        is_race=A.is_race(s),
        is_quali=A.is_quali(s),
        focus=ov["focus"],
        drivers=ov["drivers"],
        order=ov["order"],
        weather=ov["weather"],
        focus_arg=",".join(map(str, ov["focus"])),
    )


@bp.get("/session/<int:session_key>/panel/<name>")
def panel(session_key: int, name: str):
    if name not in PANELS:
        abort(404)
    d = dash()
    s = d.session(session_key)
    try:
        if name == "results":
            rows, _ = d.results(s)
            if not rows:
                return render_template("partials/empty.html")
            return render_template(
                "partials/results.html", rows=rows, is_race=A.is_race(s), is_quali=A.is_quali(s)
            )
        if name == "racecontrol":
            return render_template("partials/racecontrol.html", messages=d.race_control(s))
        focus = d.overview(s, focus_raw())["focus"]
        chart = {
            "positions": lambda: d.chart_positions(s, focus),
            "gaps": lambda: d.chart_gaps(s, focus),
            "laptimes": lambda: d.chart_laptimes(s, focus, request.args.get("all") != "1"),
            "stints": lambda: d.chart_stints(s),
            "weather": lambda: d.chart_weather(s),
        }[name]()
    except UpstreamError as exc:
        current_app.logger.warning("panel %s failed: %s", name, exc)
        return render_template("partials/panel_error.html", url=request.full_path), 200
    if not chart or not (chart.get("series") or chart.get("drivers") or chart.get("minute")):
        return render_template("partials/empty.html")
    return render_template("partials/chart.html", chart=chart, chart_id=f"{name}-{session_key}")


@bp.get("/session/<int:session_key>/telemetry")
@limited(rate=0.5, burst=6)
def telemetry(session_key: int):
    d = dash()
    s = d.session(session_key)
    drivers = d.drivers(s)
    ov = d.overview(s, focus_raw())
    order = [n for n in ov["order"] if n in drivers]
    a = _int_arg("a") or (ov["focus"][0] if ov["focus"] else None)
    b = _int_arg("b") or next((n for n in ov["focus"][1:] + order if n != a), None)
    if a not in drivers or b not in drivers:
        return render_template("partials/empty.html")
    try:
        data = d.telemetry(s, a, b, _int_arg("la"), _int_arg("lb"))
    except UpstreamError as exc:
        current_app.logger.warning("telemetry failed: %s", exc)
        return render_template("partials/panel_error.html", url=request.full_path)
    return render_template(
        "partials/telemetry.html",
        s=s,
        data=data,
        drivers=drivers,
        order=order,
        laps=d.lap_choices(s),
        a=a,
        b=b,
        la=_int_arg("la"),
        lb=_int_arg("lb"),
    )
