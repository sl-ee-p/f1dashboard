"""Record a small, real data set for the offline test suite.

Usage: python scripts/record_fixtures.py  (needs network; ~60 requests)
Writes tests/fixtures/upstream.json.gz: {"<url>?<sorted params>": payload}.
"""

import gzip
import json
from pathlib import Path

import pandas as pd

from f1dash.data.http import Fetcher

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "upstream.json.gz"
OF = "https://api.openf1.org/v1"
JP = "https://api.jolpi.ca/ergast/f1"
MV = "https://api.multiviewer.app/api/v1"
RACE, QUALI, MEETING, YEAR = 11388, 11384, 1296, 2026
DRIVERS = [3, 16, 12, 81]


def key(url, params=None):
    if not params:
        return url
    return url + "?" + "&".join(f"{k}={params[k]}" for k in sorted(params))


def main():
    f = Fetcher(Path("/tmp/f1dash-record-cache"), min_interval=0.6)
    data = {}

    def get(url, params=None, keep=None):
        try:
            payload = f.get_json(url, params=params)
        except Exception as exc:  # noqa: BLE001 - record 404s as empty lists
            print("  !", url, params, exc)
            payload = []
        if keep and isinstance(payload, list):
            payload = [r for r in payload if keep(r)]
        data[key(url, params)] = payload
        return payload

    drivers_only = lambda r: r.get("driver_number") in DRIVERS  # noqa: E731
    get(f"{OF}/meetings", {"year": YEAR}, keep=lambda r: r["meeting_key"] == MEETING)
    get(f"{OF}/sessions", {"year": YEAR}, keep=lambda r: r["meeting_key"] == MEETING)
    get(f"{OF}/sessions", {"meeting_key": MEETING})
    for sk in (RACE, QUALI):
        get(f"{OF}/sessions", {"session_key": sk})
        for ep in ("drivers", "session_result", "laps", "stints", "pit", "position", "intervals"):
            get(f"{OF}/{ep}", {"session_key": sk}, keep=drivers_only)
        for ep in ("starting_grid", "race_control", "weather"):
            get(f"{OF}/{ep}", {"session_key": sk})
    # Telemetry for each driver's fastest race lap.
    laps = pd.DataFrame(data[key(f"{OF}/laps", {"session_key": RACE})]).dropna(
        subset=["lap_duration"]
    )
    for drv in DRIVERS[:2]:
        lap = laps[laps.driver_number == drv].sort_values("lap_duration").iloc[0]
        start = pd.Timestamp(lap.date_start).tz_convert("UTC")
        end = start + pd.Timedelta(seconds=float(lap.lap_duration))
        fmt = "%Y-%m-%dT%H:%M:%S.%f"
        params = {
            "session_key": RACE,
            "driver_number": drv,
            "date>": start.strftime(fmt),
            "date<": end.strftime(fmt),
        }
        for ep in ("car_data", "location"):
            get(f"{OF}/{ep}", params)
    get(f"{MV}/circuits/61/{YEAR}")
    get(f"{JP}/{YEAR}.json")
    get(f"{JP}/{YEAR}/driverstandings.json")
    get(f"{JP}/{YEAR}/constructorstandings.json")
    for path in ("results", "sprint"):
        offset = 0
        while True:
            page = get(f"{JP}/{YEAR}/{path}.json", {"limit": 100, "offset": offset})
            offset += 100
            if offset >= int(page["MRData"]["total"]):
                break
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(gzip.compress(json.dumps(data, separators=(",", ":")).encode()))
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KiB, {len(data)} responses)")


if __name__ == "__main__":
    main()
