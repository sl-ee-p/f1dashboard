"""Typed access to the upstream APIs.

* OpenF1 (https://openf1.org) gives session-level timing: laps, stints, positions,
  car telemetry, race control and weather. Historical data runs from 2023 on.
* Jolpica (the Ergast successor) gives the season calendar, results and standings.
* MultiViewer gives the circuit outline and corner positions.

Everything returns plain lists/dicts or pandas DataFrames; analysis lives in
``analysis.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd

from .http import DAY, NEVER, SHORT, Fetcher, NotFound

FIRST_OPENF1_YEAR = 2023


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class OpenF1:
    def __init__(self, fetcher: Fetcher, base_url: str) -> None:
        self.fetch = fetcher
        self.base = base_url.rstrip("/")

    def _get(self, endpoint: str, expire=SHORT, **params) -> list[dict]:
        try:
            return self.fetch.get_json(f"{self.base}/{endpoint}", params=params, expire=expire)
        except NotFound:
            # OpenF1 answers 404 when a valid query simply has no rows.
            return []

    # -- calendar ---------------------------------------------------------
    def meetings(self, year: int) -> list[dict]:
        return self._get("meetings", expire=DAY, year=year)

    def sessions(self, year: int | None = None, meeting_key: int | None = None) -> list[dict]:
        params = {}
        if year is not None:
            params["year"] = year
        if meeting_key is not None:
            params["meeting_key"] = meeting_key
        rows = self._get("sessions", expire=DAY, **params)
        return sorted(rows, key=lambda s: s["date_start"])

    def session(self, session_key: int) -> dict | None:
        rows = self._get("sessions", expire=DAY, session_key=session_key)
        return rows[0] if rows else None

    def meeting(self, meeting_key: int, year: int | None = None) -> dict | None:
        if year is not None:
            # Served from the cached season list: no extra request.
            return next((m for m in self.meetings(year) if m["meeting_key"] == meeting_key), None)
        rows = self._get("meetings", expire=DAY, meeting_key=meeting_key)
        return rows[0] if rows else None

    @staticmethod
    def is_final(session: dict) -> bool:
        """Data for a session is final a few hours after it ends."""
        end = parse_ts(session.get("date_end"))
        return bool(end and datetime.now(UTC) > end + timedelta(hours=6))

    def _expiry(self, session: dict):
        return NEVER if self.is_final(session) else SHORT

    # -- per-session tables ----------------------------------------------
    def table(self, endpoint: str, session: dict, **params) -> pd.DataFrame:
        rows = self._get(
            endpoint, expire=self._expiry(session), session_key=session["session_key"], **params
        )
        df = pd.DataFrame(rows)
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"], utc=True, format="ISO8601")
        if "date_start" in df.columns:
            df["date_start"] = pd.to_datetime(df["date_start"], utc=True, format="ISO8601")
        return df

    def drivers(self, session: dict) -> pd.DataFrame:
        return self.table("drivers", session)

    def results(self, session: dict) -> pd.DataFrame:
        return self.table("session_result", session)

    def starting_grid(self, session: dict) -> pd.DataFrame:
        return self.table("starting_grid", session)

    def laps(self, session: dict) -> pd.DataFrame:
        return self.table("laps", session)

    def stints(self, session: dict) -> pd.DataFrame:
        return self.table("stints", session)

    def pits(self, session: dict) -> pd.DataFrame:
        return self.table("pit", session)

    def positions(self, session: dict) -> pd.DataFrame:
        return self.table("position", session)

    def intervals(self, session: dict) -> pd.DataFrame:
        return self.table("intervals", session)

    def race_control(self, session: dict) -> pd.DataFrame:
        return self.table("race_control", session)

    def weather(self, session: dict) -> pd.DataFrame:
        return self.table("weather", session)

    def _window(self, endpoint, session, driver, start, end) -> pd.DataFrame:
        fmt = "%Y-%m-%dT%H:%M:%S.%f"
        return self.table(
            endpoint,
            session,
            driver_number=driver,
            **{
                "date>": start.tz_convert("UTC").strftime(fmt),
                "date<": end.tz_convert("UTC").strftime(fmt),
            },
        )

    def car_data(self, session, driver: int, start: pd.Timestamp, end: pd.Timestamp):
        return self._window("car_data", session, driver, start, end)

    def location(self, session, driver: int, start: pd.Timestamp, end: pd.Timestamp):
        return self._window("location", session, driver, start, end)


class Jolpica:
    def __init__(self, fetcher: Fetcher, base_url: str) -> None:
        self.fetch = fetcher
        self.base = base_url.rstrip("/")

    def _get(self, path: str, expire=DAY, **params) -> dict:
        return self.fetch.get_json(f"{self.base}/{path}", params=params or None, expire=expire)[
            "MRData"
        ]

    def _paged(self, path: str, key: str, expire) -> list[dict]:
        """Fetch every page of a result list and merge races split across pages."""
        races: dict[str, dict] = {}
        offset = 0
        while True:
            data = self._get(path, expire=expire, limit=100, offset=offset)
            for race in data["RaceTable"]["Races"]:
                merged = races.setdefault(race["round"], {**race, key: []})
                merged[key].extend(race.get(key, []))
            offset += int(data["limit"])
            if offset >= int(data["total"]):
                break
        return [races[r] for r in sorted(races, key=int)]

    def schedule(self, year: int) -> list[dict]:
        return self._get(f"{year}.json")["RaceTable"]["Races"]

    def results(self, year: int) -> list[dict]:
        return self._paged(f"{year}/results.json", "Results", expire=timedelta(hours=1))

    def sprint_results(self, year: int) -> list[dict]:
        return self._paged(f"{year}/sprint.json", "SprintResults", expire=timedelta(hours=1))

    def driver_standings(self, year: int) -> list[dict]:
        lists = self._get(f"{year}/driverstandings.json", expire=timedelta(hours=1))[
            "StandingsTable"
        ]["StandingsLists"]
        return lists[0]["DriverStandings"] if lists else []

    def constructor_standings(self, year: int) -> list[dict]:
        lists = self._get(f"{year}/constructorstandings.json", expire=timedelta(hours=1))[
            "StandingsTable"
        ]["StandingsLists"]
        return lists[0]["ConstructorStandings"] if lists else []


class MultiViewer:
    def __init__(self, fetcher: Fetcher, base_url: str) -> None:
        self.fetch = fetcher
        self.base = base_url.rstrip("/")

    def circuit(self, circuit_key: int, year: int) -> dict | None:
        try:
            return self.fetch.get_json(f"{self.base}/circuits/{circuit_key}/{year}", expire=NEVER)
        except NotFound:
            return None


class DataHub:
    """Bundles all sources; one instance per app."""

    def __init__(self, config, fetcher: Fetcher | None = None) -> None:
        self.fetcher = fetcher or Fetcher(
            config.cache_dir, min_interval=config.request_interval, offline=config.offline
        )
        self.openf1 = OpenF1(self.fetcher, config.openf1_url)
        self.jolpica = Jolpica(self.fetcher, config.jolpica_url)
        self.multiviewer = MultiViewer(self.fetcher, config.multiviewer_url)
