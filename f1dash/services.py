"""View-model builders shared by the HTML pages and the JSON API."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pandas as pd

from .data import analysis as A
from .data.sources import FIRST_OPENF1_YEAR, DataHub, parse_ts

MAX_FOCUS = 3

SESSION_ORDER = {
    "Practice 1": 1,
    "Practice 2": 2,
    "Practice 3": 3,
    "Sprint Qualifying": 4,
    "Sprint Shootout": 4,
    "Sprint": 5,
    "Qualifying": 6,
    "Race": 7,
}

JOLPICA_SESSIONS = [
    ("FirstPractice", "Practice 1"),
    ("SecondPractice", "Practice 2"),
    ("ThirdPractice", "Practice 3"),
    ("SprintQualifying", "Sprint Qualifying"),
    ("SprintShootout", "Sprint Shootout"),
    ("Sprint", "Sprint"),
    ("Qualifying", "Qualifying"),
]


class NoSuchSession(LookupError):
    pass


def parse_focus(raw: str | None, valid: set[int], fallback: list[int]) -> list[int]:
    """``?focus=1,16,44`` -> up to three valid driver numbers, in the given order."""
    out: list[int] = []
    for part in (raw or "").split(","):
        part = part.strip()
        if part.isdigit() and int(part) in valid and int(part) not in out:
            out.append(int(part))
    return (out or fallback)[:MAX_FOCUS]


class Dashboard:
    def __init__(self, hub: DataHub) -> None:
        self.hub = hub
        self.of = hub.openf1
        self.jp = hub.jolpica

    # ------------------------------------------------------------------ session
    def session(self, session_key: int) -> dict:
        s = self.of.session(session_key)
        if not s:
            raise NoSuchSession(session_key)
        meeting = self.of.meeting(s["meeting_key"], s["year"]) or {}
        return {**s, "meeting": meeting}

    def siblings(self, session: dict) -> list[dict]:
        rows = self.of.sessions(meeting_key=session["meeting_key"])
        return [
            {"key": r["session_key"], "name": r["session_name"]}
            for r in rows
            if not r.get("is_cancelled")
        ]

    def drivers(self, session: dict) -> dict[int, A.Driver]:
        return A.driver_map(self.of.drivers(session))

    def results(self, session: dict) -> tuple[list[dict], dict[int, A.Driver]]:
        drivers = self.drivers(session)
        laps = self.of.laps(session)
        res = self.of.results(session)
        grid, pits = None, None
        if A.is_race(session):
            grid_df = self.of.starting_grid(session)
            if not grid_df.empty:
                grid = {int(r.driver_number): int(r.position) for r in grid_df.itertuples()}
            else:
                grid = A.grid_from_positions(self.of.positions(session), session)
            pits = self.of.pits(session)
        return A.results_rows(session, res, drivers, laps, grid, pits), drivers

    def overview(self, session: dict, focus_raw: str | None) -> dict:
        rows, drivers = self.results(session)
        fallback = A.default_focus(rows)
        focus = parse_focus(focus_raw, set(drivers), fallback)
        weather = A.weather_summary(self.of.weather(session))
        return {
            "rows": rows,
            "drivers": drivers,
            "order": A.finishing_order(rows, drivers),
            "focus": focus,
            "weather": weather,
        }

    @staticmethod
    def _series_meta(number: int, drivers: dict, focus: list[int]) -> dict:
        d = drivers.get(number)
        return {
            "driver": number,
            "code": d.code if d else str(number),
            "team": d.team if d else "",
            "slot": focus.index(number) + 1 if number in focus else None,
        }

    def chart_positions(self, session: dict, focus: list[int]) -> dict:
        drivers = self.drivers(session)
        laps = self.of.laps(session)
        pos = A.position_by_lap(laps, self.of.positions(session))
        series = []
        for number, d in pos.groupby("driver_number"):
            d = d.sort_values("lap_number")
            series.append(
                {
                    **self._series_meta(int(number), drivers, focus),
                    "x": d["lap_number"].astype(int).tolist(),
                    "y": d["position"].astype(int).tolist(),
                }
            )
        last_lap = int(laps["lap_number"].max()) if not laps.empty else None
        return {
            "kind": "lines",
            "title": "Position by lap",
            "x_title": "Lap",
            "y_title": "Position",
            "y_reversed": True,
            "y_integer": True,
            "series": series,
            "bands": A.neutralisations(self.of.race_control(session), last_lap),
        }

    def chart_gaps(self, session: dict, focus: list[int]) -> dict:
        drivers = self.drivers(session)
        laps = self.of.laps(session)
        gaps = A.gap_to_leader_by_lap(laps)
        series = []
        for number, d in gaps.groupby("driver_number"):
            d = d.sort_values("lap_number")
            series.append(
                {
                    **self._series_meta(int(number), drivers, focus),
                    "x": d["lap_number"].astype(int).tolist(),
                    "y": d["gap"].tolist(),
                }
            )
        last_lap = int(laps["lap_number"].max()) if not laps.empty else None
        return {
            "kind": "lines",
            "title": "Gap to leader",
            "x_title": "Lap",
            "y_title": "Seconds behind leader",
            "y_reversed": True,
            "y_max": 120,
            "series": series,
            "bands": A.neutralisations(self.of.race_control(session), last_lap),
        }

    def chart_laptimes(self, session: dict, focus: list[int], quick: bool = True) -> dict:
        rows, drivers = self.results(session)
        order = A.finishing_order(rows, drivers)
        data = A.lap_time_series(self.of.laps(session), order, quick=quick)
        return {
            "kind": "distribution",
            "title": "Lap times" + (" (quick laps)" if quick else ""),
            "y_title": "Lap time",
            "series": [{**self._series_meta(d["driver"], drivers, focus), **d} for d in data],
        }

    def chart_stints(self, session: dict) -> dict:
        rows, drivers = self.results(session)
        order = A.finishing_order(rows, drivers)
        laps = self.of.laps(session)
        stints = A.stint_rows(self.of.stints(session), order, laps)
        pits = self.of.pits(session)
        stops = {}
        if not pits.empty:
            for r in pits.dropna(subset=["lap_number"]).itertuples():
                stops.setdefault(int(r.driver_number), []).append(
                    {"lap": int(r.lap_number), "lane": A._num(getattr(r, "lane_duration", None))}
                )
        for s in stints:
            meta = self._series_meta(s["driver"], drivers, [])
            s["code"] = meta["code"]
            s["stops"] = stops.get(s["driver"], [])
        return {"kind": "stints", "title": "Tyre strategy", "drivers": stints}

    def chart_weather(self, session: dict) -> dict | None:
        w = self.of.weather(session)
        if w.empty:
            return None
        return {
            "kind": "weather",
            "title": "Temperature",
            **A.weather_series(w, pd.Timestamp(session["date_start"])),
        }

    def race_control(self, session: dict) -> list[dict]:
        rc = self.of.race_control(session)
        if rc.empty:
            return []
        start = pd.Timestamp(session["date_start"])
        out = []
        for r in rc.sort_values("date").itertuples():
            if "BLUE FLAG" in (r.message or ""):
                continue
            out.append(
                {
                    "minute": int((r.date - start).total_seconds() // 60),
                    "lap": A._num(r.lap_number),
                    "category": r.category,
                    "flag": r.flag if isinstance(r.flag, str) else None,
                    "message": r.message,
                }
            )
        return out

    # ---------------------------------------------------------------- telemetry
    def lap_choices(self, session: dict) -> dict[int, list[dict]]:
        laps = self.of.laps(session)
        out: dict[int, list[dict]] = {}
        if laps.empty:
            return out
        laps = laps.dropna(subset=["lap_duration"])
        for r in laps.sort_values(["driver_number", "lap_number"]).itertuples():
            out.setdefault(int(r.driver_number), []).append(
                {"lap": int(r.lap_number), "time": A.fmt_laptime(r.lap_duration)}
            )
        return out

    def _lap_window(self, laps: pd.DataFrame, driver: int, lap: int | None):
        if laps.empty:
            return None
        d = laps[(laps["driver_number"] == driver)].dropna(subset=["lap_duration", "date_start"])
        if d.empty:
            return None
        match = d[d["lap_number"] == lap] if lap is not None else d.iloc[0:0]
        # Unknown lap (e.g. after switching driver) falls back to the fastest one.
        row = match.iloc[0] if not match.empty else d.loc[d["lap_duration"].idxmin()]
        start = row["date_start"]
        end = start + pd.Timedelta(seconds=float(row["lap_duration"]))
        return int(row["lap_number"]), float(row["lap_duration"]), start, end

    def telemetry(
        self, session: dict, a: int, b: int, lap_a: int | None, lap_b: int | None
    ) -> dict:
        drivers = self.drivers(session)
        laps = self.of.laps(session)
        out = {"kind": "telemetry", "drivers": [], "traces": []}
        cars, tracks = [], []
        for slot, (number, lap) in enumerate(((a, lap_a), (b, lap_b)), start=1):
            win = self._lap_window(laps, number, lap)
            if not win:
                continue
            lap_no, lap_time, start, end = win
            car = self.of.car_data(session, number, start, end)
            if car.empty:
                continue
            loc = self.of.location(session, number, start, end)
            cars.append(car)
            meta = self._series_meta(number, drivers, [])
            out["drivers"].append(
                {**meta, "slot": slot, "lap": lap_no, "time": A.fmt_laptime(lap_time)}
            )
            out["traces"].append({**meta, "slot": slot, **A.telemetry_trace(car)})
            if not loc.empty:
                tracks.append(A.track_points(loc, car))
        if len(cars) == 2:
            out["delta"] = A.time_delta(cars[0], cars[1])
        out["track"] = self._track(session, tracks)
        return out

    def _track(self, session: dict, tracks: list[pd.DataFrame]) -> dict | None:
        if not tracks:
            return None
        circuit = self.hub.multiviewer.circuit(session["circuit_key"], session["year"]) or {}
        rotation = float(circuit.get("rotation", 0))
        base = tracks[0]
        x, y = A.rotate(base["x"], base["y"], rotation)
        track = {
            "x": x.round(1).tolist(),
            "y": y.round(1).tolist(),
            "speed": base["speed"].astype(int).tolist(),
            "corners": [],
        }
        for c in circuit.get("corners", []):
            # Labels sit 500 units off the racing line, along the corner's angle.
            ox, oy = A.rotate([500.0], [0.0], float(c.get("angle", 0)))
            cx, cy = A.rotate(
                [c["trackPosition"]["x"] + ox[0]], [c["trackPosition"]["y"] + oy[0]], rotation
            )
            track["corners"].append(
                {"n": f"{c['number']}{c.get('letter') or ''}", "x": float(cx[0]), "y": float(cy[0])}
            )
        if len(tracks) == 2:
            sectors = 25
            winner = A.minisector_dominance(tracks[0], tracks[1], sectors)
            length = base["distance"].max()
            idx = (base["distance"] / length * sectors).clip(upper=sectors - 1).astype(int)
            track["dominance"] = [winner[i] + 1 for i in idx]
        return track

    # ------------------------------------------------------------------ season
    def season(self, year: int) -> dict:
        schedule = self.jp.schedule(year)
        results = self.jp.results(year)
        winners = {int(r["round"]): r["Results"][0] for r in results if r.get("Results")}
        meetings = self._meetings_by_round(year, schedule)
        today = date.today()
        calendar = []
        for race in schedule:
            rnd = int(race["round"])
            race_day = date.fromisoformat(race["date"])
            w = winners.get(rnd)
            calendar.append(
                {
                    "round": rnd,
                    "name": race["raceName"],
                    "circuit": race["Circuit"]["circuitName"],
                    "country": race["Circuit"]["Location"]["country"],
                    "date": race_day,
                    "done": race_day < today,
                    "winner": (
                        {
                            "code": w["Driver"].get("code", ""),
                            "name": f"{w['Driver']['givenName']} {w['Driver']['familyName']}",
                            "team": w["Constructor"]["name"],
                        }
                        if w
                        else None
                    ),
                    "sessions": meetings.get(rnd, []),
                    "sprint": "Sprint" in race,
                }
            )
        return {
            "year": year,
            "calendar": calendar,
            "drivers": self.driver_standings(year),
            "teams": self.team_standings(year),
            "has_sessions": year >= FIRST_OPENF1_YEAR,
        }

    def _meetings_by_round(self, year: int, schedule: list[dict]) -> dict[int, list[dict]]:
        if year < FIRST_OPENF1_YEAR:
            return {}
        meetings = [m for m in self.of.meetings(year) if "Testing" not in m["meeting_name"]]
        sessions = self.of.sessions(year=year)
        out: dict[int, list[dict]] = {}
        for race in schedule:
            race_day = race["date"]
            for m in meetings:
                if m["date_start"][:10] <= race_day <= m["date_end"][:10]:
                    rows = [
                        {"key": s["session_key"], "name": s["session_name"]}
                        for s in sessions
                        if s["meeting_key"] == m["meeting_key"] and not s.get("is_cancelled")
                    ]
                    rows.sort(key=lambda s: SESSION_ORDER.get(s["name"], 0))
                    out[int(race["round"])] = rows
                    break
        return out

    def driver_standings(self, year: int) -> list[dict]:
        return [
            {
                "position": int(s["position"]) if str(s.get("position", "")).isdigit() else None,
                "code": s["Driver"].get("code", ""),
                "name": f"{s['Driver']['givenName']} {s['Driver']['familyName']}",
                "team": s["Constructors"][-1]["name"] if s.get("Constructors") else "",
                "points": float(s["points"]),
                "wins": int(s["wins"]),
            }
            for s in self.jp.driver_standings(year)
        ]

    def team_standings(self, year: int) -> list[dict]:
        return [
            {
                "position": int(s["position"]) if str(s.get("position", "")).isdigit() else None,
                "name": s["Constructor"]["name"],
                "points": float(s["points"]),
                "wins": int(s["wins"]),
            }
            for s in self.jp.constructor_standings(year)
        ]

    def chart_progression(self, year: int, what: str, focus_raw: str | None) -> dict:
        prog = A.points_progression(self.jp.results(year), self.jp.sprint_results(year))
        table = prog["teams"] if what == "teams" else prog["drivers"]
        ranked = sorted(table, key=lambda k: table[k][-1] if table[k] else 0, reverse=True)
        focus = [k for k in (focus_raw or "").split(",") if k in table][:MAX_FOCUS] or ranked[:3]
        series = [
            {
                "driver": name,
                "code": name,
                "team": "",
                "slot": focus.index(name) + 1 if name in focus else None,
                "x": prog["rounds"],
                "y": table[name],
            }
            for name in ranked
        ]
        return {
            "kind": "lines",
            "title": "Constructor points by round" if what == "teams" else "Driver points by round",
            "x_title": "Round",
            "y_title": "Points",
            "series": series,
            "bands": [],
            "label_all_focus": True,
        }

    # -------------------------------------------------------------------- home
    def next_event(self, year: int) -> dict | None:
        now = datetime.now(UTC)
        for y in (year, year + 1):
            try:
                schedule = self.jp.schedule(y)
            except Exception:  # next season may not be published yet
                continue
            for race in schedule:
                entries = [(label, race.get(key)) for key, label in JOLPICA_SESSIONS]
                entries.append(("Race", {"date": race["date"], "time": race.get("time")}))
                for label, info in entries:
                    if not info or not info.get("time"):
                        continue
                    when = parse_ts(f"{info['date']}T{info['time']}")
                    if when and when > now:
                        return {
                            "race": race["raceName"],
                            "round": int(race["round"]),
                            "year": y,
                            "session": label,
                            "when": when,
                            "circuit": race["Circuit"]["circuitName"],
                        }
        return None

    def latest_race(self, year: int) -> dict | None:
        """The most recent race session with published results."""
        for y in (year, year - 1):
            if y < FIRST_OPENF1_YEAR:
                break
            races = [
                s
                for s in self.of.sessions(year=y)
                if s["session_name"] == "Race" and self.of.is_final(s)
            ]
            if races:
                return races[-1]
        return None

    def home(self) -> dict:
        year = datetime.now(UTC).year
        latest = self.latest_race(year)
        podium, meeting = [], None
        if latest:
            meeting = self.of.meeting(latest["meeting_key"], latest["year"])
            rows, _ = self.results(latest)
            podium = [r for r in rows if r["position"] in (1, 2, 3)]
        standings_year = year
        drivers = self.driver_standings(year)
        if not drivers:
            standings_year = year - 1
            drivers = self.driver_standings(standings_year)
        return {
            "next": self.next_event(year),
            "latest": latest,
            "latest_meeting": meeting,
            "podium": podium,
            "drivers": drivers[:5],
            "teams": self.team_standings(standings_year)[:5],
            "standings_year": standings_year,
            "latest_sessions": self.siblings(latest) if latest else [],
        }
