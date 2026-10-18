"""Pure transforms from OpenF1 tables to view models and chart data.

Nothing here does I/O, so every function can be tested on small fixture frames.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

RACE_TYPES = {"Race"}
QUALI_TYPES = {"Qualifying"}
FALLBACK_COLOUR = "#898781"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def fmt_laptime(seconds: float | None) -> str:
    if seconds is None or (isinstance(seconds, float) and math.isnan(seconds)):
        return ""
    minutes, sec = divmod(float(seconds), 60)
    return f"{int(minutes)}:{sec:06.3f}" if minutes else f"{sec:.3f}"


def fmt_gap(seconds: float | None) -> str:
    if seconds is None or (isinstance(seconds, float) and math.isnan(seconds)):
        return ""
    return f"+{float(seconds):.3f}"


def fmt_racetime(seconds: float | None) -> str:
    if seconds is None or (isinstance(seconds, float) and math.isnan(seconds)):
        return ""
    hours, rem = divmod(float(seconds), 3600)
    minutes, sec = divmod(rem, 60)
    return f"{int(hours)}:{int(minutes):02d}:{sec:06.3f}"


def _num(value):
    """NaN/None -> None, numpy scalars -> python."""
    if value is None:
        return None
    if isinstance(value, float | np.floating) and math.isnan(value):
        return None
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    return value


def is_race(session: dict) -> bool:
    return session.get("session_type") in RACE_TYPES


def is_quali(session: dict) -> bool:
    return session.get("session_type") in QUALI_TYPES


# ---------------------------------------------------------------------------
# drivers
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Driver:
    number: int
    code: str
    name: str
    team: str
    colour: str
    headshot: str | None = None


def driver_map(drivers: pd.DataFrame) -> dict[int, Driver]:
    out: dict[int, Driver] = {}
    if drivers.empty:
        return out
    for row in drivers.drop_duplicates("driver_number", keep="last").itertuples():
        colour = getattr(row, "team_colour", None)
        number = int(row.driver_number)
        out[number] = Driver(
            number=number,
            code=getattr(row, "name_acronym", None) or str(number),
            name=(getattr(row, "full_name", None) or "").title(),
            team=getattr(row, "team_name", None) or "",
            colour=f"#{colour}" if isinstance(colour, str) and colour else FALLBACK_COLOUR,
            headshot=getattr(row, "headshot_url", None),
        )
    return out


# ---------------------------------------------------------------------------
# laps
# ---------------------------------------------------------------------------
def lap_end_times(laps: pd.DataFrame) -> pd.DataFrame:
    """Add ``date_end`` to each lap.

    The next lap's start is the most reliable end marker; the last lap falls back
    to start + duration.
    """
    if laps.empty:
        return laps.assign(date_end=pd.Series(dtype="datetime64[ns, UTC]"))
    df = laps.sort_values(["driver_number", "lap_number"]).copy()
    nxt = df.groupby("driver_number")["date_start"].shift(-1)
    dur = pd.to_timedelta(df["lap_duration"], unit="s")
    df["date_end"] = nxt.where(nxt.notna(), df["date_start"] + dur)
    return df


def best_laps(laps: pd.DataFrame) -> dict[int, dict]:
    """Fastest valid lap per driver: {driver: {lap, time}}."""
    valid = laps.dropna(subset=["lap_duration"])
    if valid.empty:
        return {}
    idx = valid.groupby("driver_number")["lap_duration"].idxmin()
    return {
        int(r.driver_number): {"lap": int(r.lap_number), "time": float(r.lap_duration)}
        for r in valid.loc[idx].itertuples()
    }


def quick_laps(laps: pd.DataFrame, threshold: float = 1.07) -> pd.DataFrame:
    """Representative laps: no pit-out laps, no lap 1, within 107% of the best lap."""
    df = laps.dropna(subset=["lap_duration"])
    if df.empty:
        return df
    df = df[(df["lap_number"] > 1) & ~df["is_pit_out_lap"].fillna(False).astype(bool)]
    if df.empty:
        return df
    return df[df["lap_duration"] <= df["lap_duration"].min() * threshold]


def lap_time_series(laps: pd.DataFrame, order: list[int], quick: bool = True) -> list[dict]:
    """Per-driver lap times for the distribution chart, in finishing order."""
    df = quick_laps(laps) if quick else laps.dropna(subset=["lap_duration"])
    out = []
    for number in order:
        d = df[df["driver_number"] == number].sort_values("lap_number")
        if d.empty:
            continue
        out.append(
            {
                "driver": number,
                "laps": d["lap_number"].astype(int).tolist(),
                "times": d["lap_duration"].round(3).tolist(),
            }
        )
    return out


# ---------------------------------------------------------------------------
# race progression
# ---------------------------------------------------------------------------
def gap_to_leader_by_lap(laps: pd.DataFrame) -> pd.DataFrame:
    """Seconds behind the leader at the end of each lap.

    The leader of lap N is whoever completed lap N first, so the gap is simply the
    difference between crossing times. Returns columns driver_number, lap_number, gap.
    """
    df = lap_end_times(laps).dropna(subset=["date_end"])
    if df.empty:
        return pd.DataFrame(columns=["driver_number", "lap_number", "gap"])
    first = df.groupby("lap_number")["date_end"].transform("min")
    df["gap"] = (df["date_end"] - first).dt.total_seconds().round(3)
    return df[["driver_number", "lap_number", "gap"]]


def position_by_lap(laps: pd.DataFrame, positions: pd.DataFrame) -> pd.DataFrame:
    """Each driver's running position at the end of every lap they completed."""
    ends = lap_end_times(laps).dropna(subset=["date_end"])
    if ends.empty or positions.empty:
        return pd.DataFrame(columns=["driver_number", "lap_number", "position"])
    out = []
    pos = positions.sort_values("date")
    for number, d in ends.groupby("driver_number"):
        p = pos[pos["driver_number"] == number][["date", "position"]]
        if p.empty:
            continue
        merged = pd.merge_asof(
            d.sort_values("date_end")[["lap_number", "date_end"]],
            p,
            left_on="date_end",
            right_on="date",
            direction="backward",
        )
        merged["driver_number"] = number
        out.append(merged[["driver_number", "lap_number", "position"]])
    if not out:
        return pd.DataFrame(columns=["driver_number", "lap_number", "position"])
    res = pd.concat(out).dropna(subset=["position"])
    res["position"] = res["position"].astype(int)
    return res


def grid_from_positions(positions: pd.DataFrame, session: dict) -> dict[int, int]:
    """Starting order: the last position published before the session starts."""
    if positions.empty:
        return {}
    start = pd.Timestamp(session["date_start"])
    pre = positions[positions["date"] <= start]
    if pre.empty:
        pre = positions
    latest = pre.sort_values("date").groupby("driver_number").tail(1)
    return {int(r.driver_number): int(r.position) for r in latest.itertuples()}


def neutralisations(race_control: pd.DataFrame, last_lap: int | None = None) -> list[dict]:
    """Safety car, VSC and red-flag periods as lap ranges."""
    if race_control.empty:
        return []
    rc = race_control.sort_values("date")
    periods: list[dict] = []
    open_: dict[str, int] = {}

    def close(kind: str, lap):
        if kind in open_:
            start = open_.pop(kind)
            periods.append({"kind": kind, "start": start, "end": int(lap or start)})

    for row in rc.itertuples():
        msg = (row.message or "").upper()
        lap = _num(row.lap_number)
        flag = (row.flag or "").upper() if isinstance(row.flag, str) else ""
        if lap is None:
            continue
        if "VSC DEPLOYED" in msg or "VIRTUAL SAFETY CAR DEPLOYED" in msg:
            open_.setdefault("VSC", int(lap))
        elif "VSC ENDING" in msg or "VIRTUAL SAFETY CAR ENDING" in msg:
            close("VSC", lap)
        elif "SAFETY CAR DEPLOYED" in msg:
            open_.setdefault("SC", int(lap))
        elif "SAFETY CAR IN THIS LAP" in msg:
            close("SC", lap)
        elif flag == "RED" and row.category == "Flag" and (row.scope or "") != "Sector":
            open_.setdefault("Red flag", int(lap))
        elif flag == "GREEN" and "Red flag" in open_:
            close("Red flag", lap)
    for kind in list(open_):
        close(kind, last_lap)
    return sorted(periods, key=lambda p: p["start"])


# ---------------------------------------------------------------------------
# tyres
# ---------------------------------------------------------------------------
COMPOUNDS = ["SOFT", "MEDIUM", "HARD", "INTERMEDIATE", "WET"]


def stint_rows(stints: pd.DataFrame, order: list[int], laps: pd.DataFrame) -> list[dict]:
    """Tyre stints per driver, in finishing order, with the last lap filled in."""
    if stints.empty:
        return []
    last_lap = laps.groupby("driver_number")["lap_number"].max().to_dict() if not laps.empty else {}
    out = []
    for number in order:
        d = stints[stints["driver_number"] == number].sort_values("stint_number")
        rows = []
        for r in d.itertuples():
            start = _num(r.lap_start)
            end = _num(r.lap_end) or last_lap.get(number)
            if start is None or end is None:
                continue
            compound = (r.compound or "UNKNOWN").upper()
            rows.append(
                {
                    "stint": int(r.stint_number),
                    "compound": compound,
                    "start": int(start),
                    "end": int(end),
                    "laps": int(end) - int(start) + 1,
                    "age_start": _num(r.tyre_age_at_start),
                }
            )
        if rows:
            out.append({"driver": number, "stints": rows})
    return out


# ---------------------------------------------------------------------------
# results table
# ---------------------------------------------------------------------------
def results_rows(
    session: dict,
    results: pd.DataFrame,
    drivers: dict[int, Driver],
    laps: pd.DataFrame,
    grid: dict[int, int] | None = None,
    pits: pd.DataFrame | None = None,
) -> list[dict]:
    best = best_laps(laps)
    overall_best = min((b["time"] for b in best.values()), default=None)
    pit_counts = (
        pits.dropna(subset=["lap_number"]).groupby("driver_number").size().to_dict()
        if pits is not None and not pits.empty
        else {}
    )
    rows = []
    if results.empty:
        # Fall back to best laps (e.g. data still being published).
        ranked = sorted(best.items(), key=lambda kv: kv[1]["time"])
        results = pd.DataFrame(
            [{"driver_number": n, "position": i + 1} for i, (n, _) in enumerate(ranked)]
        )

    # Positions are numbers, except for markers such as "RT" (retired).
    raw_position = results["position"]
    results = results.assign(position=pd.to_numeric(raw_position, errors="coerce"))
    results["position_label"] = [
        p if isinstance(p, str) and not p.isdigit() else None for p in raw_position
    ]
    for r in results.sort_values("position", na_position="last").itertuples():
        number = int(r.driver_number)
        drv = drivers.get(number) or Driver(number, str(number), "", "", FALLBACK_COLOUR)
        status = "Finished"
        if r.position_label:
            status = {"RT": "DNF", "NC": "NC", "DQ": "DSQ"}.get(r.position_label, r.position_label)
        if getattr(r, "dsq", False):
            status = "DSQ"
        elif getattr(r, "dns", False):
            status = "DNS"
        elif getattr(r, "dnf", False):
            status = "DNF"
        position = _num(r.position)
        row = {
            "position": int(position) if position is not None else None,
            "driver": number,
            "code": drv.code,
            "name": drv.name,
            "team": drv.team,
            "colour": drv.colour,
            "laps": _num(getattr(r, "number_of_laps", None)),
            "status": status,
            "best_lap": fmt_laptime(best.get(number, {}).get("time")),
            "best_lap_is_fastest": bool(
                overall_best and best.get(number, {}).get("time") == overall_best
            ),
        }
        duration = getattr(r, "duration", None)
        gap = getattr(r, "gap_to_leader", None)
        if is_quali(session):
            parts = duration if isinstance(duration, list) else [duration]
            parts = (parts + [None, None, None])[:3]
            row["q"] = [fmt_laptime(_num(p)) for p in parts]
        elif is_race(session):
            row["grid"] = (grid or {}).get(number)
            row["points"] = _num(getattr(r, "points", None))
            row["pits"] = pit_counts.get(number, 0)
            if status != "Finished":
                row["time"] = status
            elif row["position"] == 1:
                row["time"] = fmt_racetime(_num(duration))
            elif isinstance(gap, str):
                row["time"] = gap  # e.g. "+1 LAP"
            else:
                row["time"] = fmt_gap(_num(gap))
            if row["grid"] and row["position"]:
                row["delta"] = row["grid"] - row["position"]
        else:
            t = best.get(number, {}).get("time")
            row["time"] = fmt_laptime(t)
            row["gap"] = (
                fmt_gap(t - overall_best) if t and overall_best and t != overall_best else ""
            )
        rows.append(row)
    return rows


def default_focus(results: list[dict], n: int = 3) -> list[int]:
    return [r["driver"] for r in results if r.get("position")][:n]


def finishing_order(results: list[dict], drivers: dict[int, Driver]) -> list[int]:
    order = [r["driver"] for r in results]
    order += [n for n in drivers if n not in order]
    return order


# ---------------------------------------------------------------------------
# telemetry
# ---------------------------------------------------------------------------
def add_distance(car: pd.DataFrame) -> pd.DataFrame:
    """Integrate speed (km/h) over time to get distance (m) along the lap."""
    df = car.sort_values("date").reset_index(drop=True).copy()
    t = (df["date"] - df["date"].iloc[0]).dt.total_seconds().to_numpy()
    v = df["speed"].astype(float).to_numpy() / 3.6
    dist = np.concatenate([[0.0], np.cumsum(np.diff(t) * (v[1:] + v[:-1]) / 2)])
    df["t"] = t
    df["distance"] = dist
    return df


def telemetry_trace(car: pd.DataFrame) -> dict:
    df = add_distance(car)
    return {
        "distance": df["distance"].round(1).tolist(),
        "speed": df["speed"].astype(int).tolist(),
        "throttle": df["throttle"].clip(0, 100).astype(int).tolist(),
        "brake": (df["brake"] > 0).astype(int).mul(100).tolist(),
        "gear": df["n_gear"].astype(int).tolist(),
        "rpm": df["rpm"].astype(int).tolist(),
    }


def time_delta(car_a: pd.DataFrame, car_b: pd.DataFrame, points: int = 400) -> dict:
    """Running time difference B minus A over a common distance grid.

    Positive means B is behind A at that point of the lap.
    """
    a, b = add_distance(car_a), add_distance(car_b)
    length = min(a["distance"].iloc[-1], b["distance"].iloc[-1])
    grid = np.linspace(0, length, points)
    ta = np.interp(grid, a["distance"], a["t"])
    tb = np.interp(grid, b["distance"], b["t"])
    return {"distance": grid.round(1).tolist(), "delta": (tb - ta).round(3).tolist()}


def track_points(loc: pd.DataFrame, car: pd.DataFrame) -> pd.DataFrame:
    """Join each location sample with the nearest speed sample and lap distance."""
    loc = loc[(loc["x"] != 0) | (loc["y"] != 0)].sort_values("date")
    car = add_distance(car)[["date", "speed", "distance"]]
    return pd.merge_asof(loc, car, on="date", direction="nearest")


def rotate(x, y, degrees: float):
    rad = math.radians(degrees)
    c, s = math.cos(rad), math.sin(rad)
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    return x * c - y * s, x * s + y * c


def minisector_dominance(
    track_a: pd.DataFrame, track_b: pd.DataFrame, sectors: int = 25
) -> list[int]:
    """For each mini-sector, 0 if driver A was quicker through it, else 1."""
    out = []
    la, lb = track_a["distance"].max(), track_b["distance"].max()
    for i in range(sectors):
        fa = (track_a["distance"] >= la * i / sectors) & (
            track_a["distance"] < la * (i + 1) / sectors
        )
        fb = (track_b["distance"] >= lb * i / sectors) & (
            track_b["distance"] < lb * (i + 1) / sectors
        )
        va = track_a.loc[fa, "speed"].mean()
        vb = track_b.loc[fb, "speed"].mean()
        out.append(0 if (pd.isna(vb) or (not pd.isna(va) and va >= vb)) else 1)
    return out


# ---------------------------------------------------------------------------
# weather
# ---------------------------------------------------------------------------
def clean_weather(weather: pd.DataFrame) -> pd.DataFrame:
    """Drop sensor dropouts, which OpenF1 reports as 0 degrees."""
    if weather.empty:
        return weather
    w = weather[(weather["air_temperature"] > 0) & (weather["track_temperature"] > 0)]
    return w.sort_values("date")


def weather_summary(weather: pd.DataFrame) -> dict | None:
    w = clean_weather(weather)
    if w.empty:
        return None
    return {
        "air": [float(w["air_temperature"].min()), float(w["air_temperature"].max())],
        "track": [float(w["track_temperature"].min()), float(w["track_temperature"].max())],
        "humidity": round(float(w["humidity"].mean())),
        "wind": round(float(w["wind_speed"].mean()), 1),
        "rain": bool(w["rainfall"].fillna(0).astype(float).gt(0).any()),
    }


def weather_series(weather: pd.DataFrame, start: pd.Timestamp) -> dict:
    w = clean_weather(weather)
    minutes = ((w["date"] - start).dt.total_seconds() / 60).round(1)
    return {
        "minute": minutes.tolist(),
        "air": w["air_temperature"].tolist(),
        "track": w["track_temperature"].tolist(),
        "rain": w["rainfall"].fillna(0).astype(float).gt(0).astype(int).tolist(),
    }


# ---------------------------------------------------------------------------
# season
# ---------------------------------------------------------------------------
def points_progression(results: list[dict], sprints: list[dict]) -> dict:
    """Cumulative driver and constructor points after each round."""
    by_round: dict[int, list[tuple[str, str, str, float]]] = {}
    for race in results + sprints:
        rnd = int(race["round"])
        for r in race.get("Results", []) + race.get("SprintResults", []):
            by_round.setdefault(rnd, []).append(
                (
                    r["Driver"].get("code") or r["Driver"]["familyName"][:3].upper(),
                    r["Constructor"]["name"],
                    r["Driver"]["driverId"],
                    float(r.get("points", 0)),
                )
            )
    rounds = sorted(by_round)
    drivers: dict[str, list[float]] = {}
    teams: dict[str, list[float]] = {}
    for i, rnd in enumerate(rounds):
        for code, team, _id, pts in by_round[rnd]:
            drivers.setdefault(code, [0.0] * len(rounds))
            teams.setdefault(team, [0.0] * len(rounds))
            drivers[code][i] += pts
            teams[team][i] += pts

    def cumulate(table):
        return {k: np.cumsum(v).round(1).tolist() for k, v in table.items()}

    return {"rounds": rounds, "drivers": cumulate(drivers), "teams": cumulate(teams)}
