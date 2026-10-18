"""``/live``: timing tower over Server-Sent Events, driven by a session replay."""

from __future__ import annotations

import threading
import time

import pandas as pd
from flask import Blueprint, Response, current_app, render_template, stream_with_context

from ..data import analysis as A
from ..data import live as L
from .limits import limited
from .pages import _int_arg, dash

bp = Blueprint("live", __name__)

SPEEDS = (1, 5, 20, 60)
MAX_STREAM_SECONDS = 3 * 3600
_slots: threading.BoundedSemaphore | None = None
_slots_lock = threading.Lock()


def _semaphore() -> threading.BoundedSemaphore:
    global _slots
    with _slots_lock:
        if _slots is None:
            _slots = threading.BoundedSemaphore(current_app.config["F1DASH"].max_live_streams)
        return _slots


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


@bp.get("/live/stream")
@limited(rate=0.2, burst=4)
def stream():
    cfg = current_app.config["F1DASH"]
    key = _int_arg("session")
    speed = _int_arg("speed") or 20
    speed = speed if speed in SPEEDS else 20
    if not cfg.live_enabled or key is None:
        return Response(_sse("status", "Live timing is disabled."), mimetype="text/event-stream")

    d = dash()
    s = d.session(key)
    of = d.of
    laps = of.laps(s)
    events = L.build_timeline(
        laps,
        of.positions(s),
        of.intervals(s),
        of.stints(s),
        of.pits(s),
        of.race_control(s),
    )
    total_laps = int(laps["lap_number"].max()) if not laps.empty else None
    state = L.TimingState(A.driver_map(of.drivers(s)), total_laps)
    start = L.replay_start(laps, s, _int_arg("lap"))
    replay = L.Replay(state, events, start)
    title = (
        f"{(d.of.meeting(s['meeting_key'], s['year']) or {}).get('meeting_name', '')} {s['year']}"
    )
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
            yield _sse("status", "<p>Too many live viewers right now. Try again later.</p>")
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

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
