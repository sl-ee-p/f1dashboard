"""Live-timing state machine with a replay source.

F1's real-time feed and OpenF1's real-time tier both need paid access, so the
live page is driven by *replaying* a finished session: OpenF1's timestamped
tables are merged into one event timeline and fed through the same
``TimingState`` a real feed would update. A future real-time source only has to
produce the same ``Event`` objects.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from . import analysis as A


@dataclass(order=True)
class Event:
    date: pd.Timestamp
    kind: str = field(compare=False)
    data: dict = field(compare=False, default_factory=dict)


@dataclass
class Car:
    number: int
    code: str
    team: str
    colour: str
    position: int | None = None
    gap: Any = None
    interval: Any = None
    lap: int = 0
    last_lap: float | None = None
    best_lap: float | None = None
    compound: str | None = None
    stint_start_lap: int | None = None
    tyre_age_at_start: int = 0
    pits: int = 0
    pit_until: pd.Timestamp | None = None
    retired: bool = False

    def tyre_age(self) -> int | None:
        if self.stint_start_lap is None:
            return None
        return self.tyre_age_at_start + max(0, self.lap - self.stint_start_lap)


class TimingState:
    """Everything the timing tower shows, updated one event at a time."""

    def __init__(self, drivers: dict[int, A.Driver], total_laps: int | None = None) -> None:
        self.cars = {n: Car(n, d.code, d.team, d.colour) for n, d in drivers.items()}
        self.messages: deque[dict] = deque(maxlen=8)
        self.track_status = "Green"
        self.leader_lap = 0
        self.total_laps = total_laps
        self.fastest: tuple[float, int] | None = None
        self.clock: pd.Timestamp | None = None
        self.finished = False

    def car(self, number: int) -> Car:
        if number not in self.cars:
            self.cars[number] = Car(number, str(number), "", A.FALLBACK_COLOUR)
        return self.cars[number]

    def apply(self, ev: Event) -> None:
        self.clock = ev.date
        d = ev.data
        if ev.kind == "position":
            self.car(d["driver"]).position = d["position"]
        elif ev.kind == "interval":
            car = self.car(d["driver"])
            car.gap, car.interval = d["gap"], d["interval"]
        elif ev.kind == "lap":
            car = self.car(d["driver"])
            # A lap event marks the *start* of lap N, so lap N-1 is complete.
            car.lap = max(car.lap, d["lap"])
            self.leader_lap = max(self.leader_lap, d["lap"])
        elif ev.kind == "lap_time":
            car = self.car(d["driver"])
            car.last_lap = d["time"]
            if car.best_lap is None or d["time"] < car.best_lap:
                car.best_lap = d["time"]
            if self.fastest is None or d["time"] < self.fastest[0]:
                self.fastest = (d["time"], d["driver"])
        elif ev.kind == "stint":
            car = self.car(d["driver"])
            car.compound = d["compound"]
            car.stint_start_lap = d["lap_start"]
            car.tyre_age_at_start = d["age"] or 0
        elif ev.kind == "pit":
            car = self.car(d["driver"])
            car.pits += 1
            car.pit_until = ev.date + pd.Timedelta(seconds=d.get("lane") or 20)
        elif ev.kind == "rc":
            self._race_control(d)

    def _race_control(self, d: dict) -> None:
        msg = (d.get("message") or "").upper()
        flag = (d.get("flag") or "").upper()
        if "VSC DEPLOYED" in msg or "VIRTUAL SAFETY CAR DEPLOYED" in msg:
            self.track_status = "VSC"
        elif "SAFETY CAR DEPLOYED" in msg:
            self.track_status = "Safety car"
        elif flag == "RED" and d.get("scope") != "Sector":
            self.track_status = "Red flag"
        elif flag == "CHEQUERED":
            self.track_status = "Chequered"
            self.finished = True
        elif self.track_status != "Chequered" and (
            flag == "GREEN" or "TRACK CLEAR" in msg or "VSC ENDING" in msg
        ):
            self.track_status = "Green"
        if "RETIRED" in msg or "STOPPED" in msg:
            for car in self.cars.values():
                if f"CAR {car.number} " in msg + " ":
                    car.retired = True
        if "BLUE FLAG" not in msg:
            self.messages.appendleft(
                {"time": self.clock, "lap": d.get("lap"), "message": d.get("message")}
            )

    def tower(self) -> list[dict]:
        cars = sorted(
            self.cars.values(),
            key=lambda c: (c.retired, c.position if c.position is not None else 99),
        )
        rows = []
        for c in cars:
            rows.append(
                {
                    "position": c.position,
                    "number": c.number,
                    "code": c.code,
                    "team": c.team,
                    "colour": c.colour,
                    "gap": _fmt_gap(c.gap, c.position == 1),
                    "interval": "" if c.position == 1 else _fmt_gap(c.interval, False),
                    "last": A.fmt_laptime(c.last_lap),
                    "best": A.fmt_laptime(c.best_lap),
                    "fastest": bool(self.fastest and self.fastest[1] == c.number),
                    "compound": c.compound,
                    "tyre_age": c.tyre_age(),
                    "pits": c.pits,
                    "in_pit": bool(
                        c.pit_until is not None and self.clock and self.clock < c.pit_until
                    ),
                    "retired": c.retired,
                }
            )
        return rows


def _fmt_gap(value, leader: bool) -> str:
    if leader:
        return "Leader"
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    if isinstance(value, str):
        return value
    return f"+{float(value):.3f}"


def build_timeline(
    laps: pd.DataFrame,
    positions: pd.DataFrame,
    intervals: pd.DataFrame,
    stints: pd.DataFrame,
    pits: pd.DataFrame,
    race_control: pd.DataFrame,
) -> list[Event]:
    """Merge OpenF1 tables into one time-ordered list of events."""
    events: list[Event] = []
    for r in positions.itertuples():
        events.append(
            Event(r.date, "position", {"driver": int(r.driver_number), "position": int(r.position)})
        )
    for r in intervals.itertuples():
        events.append(
            Event(
                r.date,
                "interval",
                {
                    "driver": int(r.driver_number),
                    "gap": A._num(r.gap_to_leader),
                    "interval": A._num(r.interval),
                },
            )
        )
    lap_starts: dict[tuple[int, int], pd.Timestamp] = {}
    if not laps.empty:
        ends = A.lap_end_times(laps)
        for r in ends.itertuples():
            if pd.isna(r.date_start):
                continue
            lap_starts[(int(r.driver_number), int(r.lap_number))] = r.date_start
            events.append(
                Event(
                    r.date_start, "lap", {"driver": int(r.driver_number), "lap": int(r.lap_number)}
                )
            )
            if not pd.isna(r.lap_duration) and not pd.isna(r.date_end):
                events.append(
                    Event(
                        r.date_end,
                        "lap_time",
                        {"driver": int(r.driver_number), "time": float(r.lap_duration)},
                    )
                )
    for r in stints.itertuples():
        start = lap_starts.get((int(r.driver_number), int(r.lap_start or 1)))
        if start is None:
            continue
        events.append(
            Event(
                start,
                "stint",
                {
                    "driver": int(r.driver_number),
                    "compound": (r.compound or "").upper() or None,
                    "lap_start": int(r.lap_start or 1),
                    "age": A._num(r.tyre_age_at_start),
                },
            )
        )
    for r in pits.itertuples():
        if pd.isna(r.date) or A._num(r.lap_number) is None:
            continue
        events.append(
            Event(
                r.date,
                "pit",
                {"driver": int(r.driver_number), "lane": A._num(getattr(r, "lane_duration", None))},
            )
        )
    for r in race_control.itertuples():
        events.append(
            Event(
                r.date,
                "rc",
                {
                    "message": r.message,
                    "flag": r.flag if isinstance(r.flag, str) else None,
                    "category": r.category,
                    "scope": r.scope if isinstance(r.scope, str) else None,
                    "lap": A._num(r.lap_number),
                },
            )
        )
    events.sort()
    return events


class Replay:
    """Steps a ``TimingState`` through a timeline against a virtual clock."""

    def __init__(self, state: TimingState, events: list[Event], start: pd.Timestamp) -> None:
        self.state = state
        self.events = events
        self.idx = 0
        self.clock = start
        # Everything before the start point is applied immediately.
        self.advance_to(start)

    def advance_to(self, when: pd.Timestamp) -> int:
        applied = 0
        while self.idx < len(self.events) and self.events[self.idx].date <= when:
            self.state.apply(self.events[self.idx])
            self.idx += 1
            applied += 1
        self.clock = when
        self.state.clock = when
        return applied

    @property
    def done(self) -> bool:
        return self.idx >= len(self.events)


def replay_start(laps: pd.DataFrame, session: dict, lap: int | None) -> pd.Timestamp:
    """Virtual start time: the leader starting lap ``lap``, or lights out."""
    if lap and lap > 1 and not laps.empty:
        d = laps[laps["lap_number"] == lap].dropna(subset=["date_start"])
        if not d.empty:
            return d["date_start"].min() - pd.Timedelta(seconds=1)
    if not laps.empty:
        d = laps[laps["lap_number"] == 1].dropna(subset=["date_start"])
        if not d.empty:
            return d["date_start"].min() - pd.Timedelta(seconds=30)
    return pd.Timestamp(session["date_start"])
