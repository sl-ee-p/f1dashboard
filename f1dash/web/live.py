"""``/live``: timing tower over Server-Sent Events, driven by a session replay."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable

import pandas as pd
from flask import Blueprint, Response, current_app, render_template, stream_with_context

from ..data import analysis as A
from ..data import live as L
from ..data.http import UpstreamError
from ..services import NoSuchSession
from .limits import limited
from .pages import _int_arg, dash

bp = Blueprint("live", __name__)

SPEEDS = (1, 5, 20, 60)
MAX_STREAM_SECONDS = 3 * 3600
_slots_lock = threading.Lock()


def _semaphore() -> threading.BoundedSemaphore:
    """Stream slots for this app (per app, so each app's config applies)."""
    with _slots_lock:
        slots = current_app.extensions.get("live_slots")
        if slots is None:
            slots = threading.BoundedSemaphore(current_app.config["F1DASH"].max_live_streams)
            current_app.extensions["live_slots"] = slots
        return slots


def _replay_sessions() -> list[dict]:
    """Finished races and sprints from this season and last, newest first."""
    d = dash()
    year = pd.Timestamp.now(tz="UTC").year
    out = []
    for y in (year, year - 1):
        for s in d.of.sessions(year=y):
            if s["session_type"] == "Race" and d.of.is_final(s) and not s.get("is_cancelled"):
                out.append(s)
    out.sort(key=lambda s: s["date_start"], reverse=True)
    return out


@bp.get("/live")
def page():
    cfg = current_app.config["F1DASH"]
    d = dash()
    sessions = _replay_sessions() if cfg.live_enabled else []
    # One request per season, not one per meeting: OpenF1 allows ~30/min.
    meetings = {
        m["meeting_key"]: m for year in {s["year"] for s in sessions} for m in d.of.meetings(year)
    }
    meetings = {s["meeting_key"]: meetings.get(s["meeting_key"], {}) for s in sessions}
    key = _int_arg("session") or (sessions[0]["session_key"] if sessions else None)
    speed = _int_arg("speed") or 20
    return render_template(
        "live.html",
        enabled=cfg.live_enabled,
        sessions=sessions,
        meetings=meetings,
        key=key,
        speed=speed if speed in SPEEDS else 20,
        speeds=SPEEDS,
        lap=_int_arg("lap") or 1,
        next_event=d.next_event(pd.Timestamp.now(tz="UTC").year),
    )


def _sse(event: str, html: str) -> str:
    data = "\n".join(f"data: {line}" for line in html.splitlines() or [""])
    return f"event: {event}\n{data}\n\n"


def _notice(html: str) -> str:
    """A status message followed by ``end``, which tells the page to stop.

    Without ``end``, the browser would reconnect every few seconds to get
    the same answer again.
    """
    return _sse("status", html) + _sse("end", "")


@bp.get("/live/stream")
@limited(rate=0.2, burst=4)
def stream():
    cfg = current_app.config["F1DASH"]
    key = _int_arg("session")
    speed = _int_arg("speed") or 20
    speed = speed if speed in SPEEDS else 20
    if not cfg.live_enabled:
        return _event_stream(_notice("<p>Live timing is turned off on this server.</p>"))
    if key is None:
        return _event_stream(_notice("<p>Pick a race to replay.</p>"))

    d = dash()
    of = d.of
    try:
        s = d.session(key)
        laps = of.laps(s)
        events = L.build_timeline(
            laps,
            of.positions(s),
            of.intervals(s),
            of.stints(s),
            of.pits(s),
            of.race_control(s),
        )
        drivers = A.driver_map(of.drivers(s))
        meeting = of.meeting(s["meeting_key"], s["year"]) or {}
    except NoSuchSession:
        return _event_stream(_notice("<p>There is no such session to replay.</p>"))
    except UpstreamError as exc:
        current_app.logger.warning("live replay of %s failed: %s", key, exc)
        return _event_stream(
            _notice("<p>The data source didn't answer. Reload to try again shortly.</p>")
        )
    total_laps = int(laps["lap_number"].max()) if not laps.empty else None
    state = L.TimingState(drivers, total_laps)
    start = L.replay_start(laps, s, _int_arg("lap"))
    replay = L.Replay(state, events, start)
    title = f"{meeting.get('meeting_name', '')} {s['year']}"
    slots = _semaphore()

    def render(final: bool = False) -> str:
        return (
            _sse("tower", render_template("partials/live_tower.html", rows=state.tower()))
            + _sse(
                "status",
                render_template(
                    "partials/live_status.html",
                    state=state,
                    title=title,
                    session=s,
                    speed=speed,
                    final=final,
                ),
            )
            + _sse("rc", render_template("partials/live_rc.html", messages=list(state.messages)))
        )

    @stream_with_context
    def generate():
        if not slots.acquire(blocking=False):
            yield _notice("<p>Too many live viewers right now. Reload to try again later.</p>")
            return
        try:
            yield "retry: 15000\n\n"
            yield render()
            wall0 = time.monotonic()
            last_sent = wall0
            while not replay.done and time.monotonic() - wall0 < MAX_STREAM_SECONDS:
                time.sleep(1)
                now = time.monotonic()
                virtual = start + pd.Timedelta(seconds=(now - wall0) * speed)
                if replay.advance_to(virtual) or now - last_sent > 5:
                    yield render()
                    last_sent = now
            yield render(final=True)
            yield _sse("end", "")
        finally:
            slots.release()

    return _event_stream(generate())


def _event_stream(body: str | Iterable[str]) -> Response:
    return Response(
        body,
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
