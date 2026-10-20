import numpy as np
import pandas as pd
import pytest

from f1dash.data import analysis as A
from f1dash.services import parse_focus, parse_int

T0 = pd.Timestamp("2026-01-01T12:00:00Z")


def laps_frame(rows):
    """rows: (driver, lap, start_offset_s, duration_s)"""
    return pd.DataFrame(
        [
            {
                "driver_number": d,
                "lap_number": lap,
                "date_start": T0 + pd.Timedelta(seconds=start),
                "lap_duration": dur,
                "is_pit_out_lap": False,
            }
            for d, lap, start, dur in rows
        ]
    )


def test_formatting():
    assert A.fmt_laptime(95.202) == "1:35.202"
    assert A.fmt_laptime(59.5) == "59.500"
    assert A.fmt_laptime(None) == ""
    assert A.fmt_gap(7.3354) == "+7.335"
    assert A.fmt_racetime(6120.785) == "1:42:00.785"


def test_lap_end_uses_next_lap_start():
    laps = laps_frame([(1, 1, 0, 90), (1, 2, 91, 89)])
    ends = A.lap_end_times(laps)
    assert ends.iloc[0]["date_end"] == T0 + pd.Timedelta(seconds=91)
    assert ends.iloc[1]["date_end"] == T0 + pd.Timedelta(seconds=180)


def test_gap_to_leader_is_line_crossing_difference():
    laps = laps_frame([(1, 1, 0, 90), (1, 2, 90, 90), (2, 1, 0, 92), (2, 2, 92, 91)])
    gaps = A.gap_to_leader_by_lap(laps).set_index(["driver_number", "lap_number"])["gap"]
    assert gaps[(1, 1)] == 0
    assert gaps[(2, 1)] == pytest.approx(2.0)
    assert gaps[(2, 2)] == pytest.approx(3.0)


def test_position_by_lap_takes_latest_position_before_line():
    laps = laps_frame([(1, 1, 0, 90), (2, 1, 0, 91)])
    positions = pd.DataFrame(
        [
            {"driver_number": 1, "date": T0, "position": 2},
            {"driver_number": 2, "date": T0, "position": 1},
            {"driver_number": 1, "date": T0 + pd.Timedelta(seconds=60), "position": 1},
            {"driver_number": 2, "date": T0 + pd.Timedelta(seconds=60), "position": 2},
            {"driver_number": 1, "date": T0 + pd.Timedelta(seconds=200), "position": 2},
        ]
    )
    res = A.position_by_lap(laps, positions).set_index("driver_number")["position"]
    assert res[1] == 1
    assert res[2] == 2


def test_quick_laps_drop_outliers_lap_one_and_out_laps():
    laps = laps_frame([(1, 1, 0, 100), (1, 2, 0, 90), (1, 3, 0, 120), (1, 4, 0, 91)])
    laps.loc[laps.lap_number == 4, "is_pit_out_lap"] = True
    assert A.quick_laps(laps)["lap_number"].tolist() == [2]


def test_neutralisations_from_race_control():
    rc = pd.DataFrame(
        [
            {
                "date": T0,
                "lap_number": 8,
                "category": "SafetyCar",
                "flag": None,
                "scope": None,
                "message": "VSC DEPLOYED",
            },
            {
                "date": T0 + pd.Timedelta(minutes=2),
                "lap_number": 9,
                "category": "SafetyCar",
                "flag": None,
                "scope": None,
                "message": "VSC ENDING",
            },
            {
                "date": T0 + pd.Timedelta(minutes=30),
                "lap_number": 30,
                "category": "SafetyCar",
                "flag": None,
                "scope": None,
                "message": "SAFETY CAR DEPLOYED",
            },
        ]
    )
    assert A.neutralisations(rc, last_lap=50) == [
        {"kind": "VSC", "start": 8, "end": 9},
        {"kind": "SC", "start": 30, "end": 50},
    ]


def test_stints_fill_missing_end_lap():
    stints = pd.DataFrame(
        [
            {
                "driver_number": 1,
                "stint_number": 1,
                "compound": "SOFT",
                "lap_start": 1,
                "lap_end": 20,
                "tyre_age_at_start": 0,
            },
            {
                "driver_number": 1,
                "stint_number": 2,
                "compound": "hard",
                "lap_start": 21,
                "lap_end": None,
                "tyre_age_at_start": 3,
            },
        ]
    )
    laps = laps_frame([(1, n, n * 90, 90) for n in range(1, 41)])
    [row] = A.stint_rows(stints, [1], laps)
    assert row["stints"][1] == {
        "stint": 2,
        "compound": "HARD",
        "start": 21,
        "end": 40,
        "laps": 20,
        "age_start": 3,
    }


def test_whole_numbers_survive_missing_values():
    assert A._int(np.float64(57.0)) == 57 and isinstance(A._int(np.float64(57.0)), int)
    assert A._int(float("nan")) is None
    assert A._int(None) is None
    results = pd.DataFrame(
        [
            {"driver_number": 1, "position": 1, "number_of_laps": 57},
            {"driver_number": 2, "position": 2, "number_of_laps": None},
        ]
    )
    laps = laps_frame([(1, 1, 0, 90), (2, 1, 0, 91)])
    rows = A.results_rows({"session_type": "Practice"}, results, {}, laps)
    assert [r["laps"] for r in rows] == [57, None]
    assert isinstance(rows[0]["laps"], int)


def test_time_delta_of_identical_laps_is_zero():
    car = pd.DataFrame(
        {
            "date": [T0 + pd.Timedelta(seconds=s) for s in range(0, 60)],
            "speed": [200] * 60,
        }
    )
    delta = A.time_delta(car, car, points=50)
    assert max(abs(v) for v in delta["delta"]) < 1e-9
    assert delta["distance"][-1] == pytest.approx(200 / 3.6 * 59, rel=1e-3)


def test_points_progression_accumulates_races_and_sprints():
    def res(code, team, pts):
        return {
            "Driver": {"code": code, "driverId": code.lower(), "familyName": code},
            "Constructor": {"name": team},
            "points": str(pts),
        }

    results = [
        {"round": "1", "Results": [res("AAA", "T1", 25), res("BBB", "T2", 18)]},
        {"round": "2", "Results": [res("BBB", "T2", 25), res("AAA", "T1", 18)]},
    ]
    sprints = [{"round": "2", "SprintResults": [res("AAA", "T1", 8)]}]
    prog = A.points_progression(results, sprints)
    assert prog["rounds"] == [1, 2]
    assert prog["drivers"]["AAA"] == [25, 51]
    assert prog["teams"]["T2"] == [18, 43]


def test_unpublished_session_has_no_rows():
    # OpenF1 answers 404 for tables with no rows, which become frames without columns.
    empty = pd.DataFrame()
    session = {"session_type": "Race", "date_start": "2026-01-01T12:00:00+00:00"}
    assert A.best_laps(empty) == {}
    assert A.quick_laps(empty).empty
    assert A.lap_time_series(empty, [1, 16]) == []
    assert A.results_rows(session, empty, {}, empty, {}, empty) == []
    assert A.gap_to_leader_by_lap(empty).empty
    assert A.position_by_lap(empty, empty).empty
    assert A.stint_rows(empty, [1], empty) == []
    assert A.weather_summary(empty) is None


def test_parse_focus():
    valid = {1, 16, 44, 81}
    assert parse_focus("16,1", valid, [44]) == [16, 1]
    assert parse_focus("16,16,99,x", valid, [44]) == [16]
    assert parse_focus("", valid, [44, 1]) == [44, 1]
    assert parse_focus("1,16,44,81", valid, []) == [1, 16, 44]
    assert parse_focus("²,16", valid, []) == [16]


def test_parse_int_rejects_what_int_cannot_read():
    assert parse_int("16") == 16
    assert parse_int(" 7 ") == 7
    assert parse_int(None) is None
    assert parse_int("") is None
    assert parse_int("-1") is None
    assert parse_int("²") is None
    assert parse_int("١٦") is None
    assert parse_int("9" * 5000) is None


def test_race_results_from_real_data(hub, race):
    of = hub.openf1
    drivers = A.driver_map(of.drivers(race))
    rows = A.results_rows(race, of.results(race), drivers, of.laps(race))
    top = [(r["position"], r["code"]) for r in rows[:3]]
    assert top == [(1, "VER"), (2, "LEC"), (3, "ANT")]
    assert rows[0]["time"] == "1:42:00.785"
    assert rows[1]["time"] == "+7.335"


def test_quali_results_handle_non_numeric_positions(hub, quali):
    of = hub.openf1
    drivers = A.driver_map(of.drivers(quali))
    res = of.results(quali)
    res = pd.concat(
        [res, pd.DataFrame([{**res.iloc[0].to_dict(), "position": "RT", "driver_number": 99}])]
    )
    rows = A.results_rows(quali, res, drivers, of.laps(quali))
    assert rows[0]["position"] == 1
    assert len(rows[0]["q"]) == 3
    assert rows[-1]["position"] is None and rows[-1]["status"] == "DNF"
