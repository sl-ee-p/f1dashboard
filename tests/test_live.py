import pandas as pd

from f1dash.data import analysis as A
from f1dash.data import live as L


def test_replay_reaches_the_real_finishing_order(hub, race):
    of = hub.openf1
    laps = of.laps(race)
    events = L.build_timeline(
        laps,
        of.positions(race),
        of.intervals(race),
        of.stints(race),
        of.pits(race),
        of.race_control(race),
    )
    state = L.TimingState(A.driver_map(of.drivers(race)), int(laps["lap_number"].max()))
    start = L.replay_start(laps, race, lap=1)
    replay = L.Replay(state, events, start)
    replay.advance_to(events[-1].date)
    assert replay.done
    tower = state.tower()
    assert [r["code"] for r in tower[:3]] == ["VER", "LEC", "ANT"]
    assert tower[0]["gap"] == "Leader" and tower[0]["interval"] == ""
    assert state.track_status == "Chequered"
    assert state.leader_lap == 62
    assert all(r["compound"] for r in tower)


def test_replay_start_from_lap():
    laps = pd.DataFrame(
        [
            {
                "driver_number": 1,
                "lap_number": n,
                "date_start": pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(minutes=n),
                "lap_duration": 60.0,
            }
            for n in range(1, 10)
        ]
    )
    start = L.replay_start(laps, {"date_start": "2026-01-01T00:00:00+00:00"}, lap=5)
    assert start == pd.Timestamp("2026-01-01T00:04:59", tz="UTC")


def test_safety_car_and_retirement_messages():
    state = L.TimingState({44: A.Driver(44, "HAM", "Lewis Hamilton", "Ferrari", "#ff0000")})
    t = pd.Timestamp("2026-01-01", tz="UTC")
    state.apply(L.Event(t, "rc", {"message": "SAFETY CAR DEPLOYED", "category": "SafetyCar"}))
    assert state.track_status == "Safety car"
    state.apply(L.Event(t, "rc", {"message": "CAR 44 (HAM) STOPPED", "category": "Other"}))
    assert state.cars[44].retired
    state.apply(
        L.Event(
            t, "rc", {"message": "WAVED BLUE FLAG FOR CAR 44", "category": "Flag", "flag": "BLUE"}
        )
    )
    assert all("BLUE" not in m["message"] for m in state.messages)
