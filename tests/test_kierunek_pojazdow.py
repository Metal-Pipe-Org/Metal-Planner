"""Kierunek pojazdu na mapie (#256): nazwa linii z kanału, wiązanie z kursem po
kluczu i to, co pojazd niesie dla frontu (strzałka, cel, dalsza trasa)."""

import datetime
import sqlite3

import pytest

import gtfs
import update_gtfs
import vehicles

NOON_SEC = 12 * 3600
OFFSET = 1000                      # stała: klucz z kanału = numer kursu + OFFSET
STOPS = ["S0", "S1", "S2", "S3", "S4", "S5"]
LAT = {name: 51.10 + i * 0.02 for i, name in enumerate(STOPS)}


class _Noon(datetime.datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 9, 14, 12, 0, 0, tzinfo=tz)


def _trip(db, trip_id, route, stops, start_sec):
    db.execute("INSERT INTO trips VALUES (?,?,'ALL','Koniec',NULL)", (trip_id, route))
    db.executemany(
        "INSERT INTO stop_times VALUES (?,?,?,?,?)",
        [(trip_id, i, s, start_sec + i * 600, start_sec + i * 600) for i, s in enumerate(stops)],
    )


def _clear_caches():
    for cache in (vehicles._window_cache, vehicles._stops_cache, vehicles._anchors):
        cache.clear()
    vehicles._anchors_db[0] = None


@pytest.fixture
def feed(tmp_path, monkeypatch):
    path = tmp_path / "gtfs.sqlite"
    db = sqlite3.connect(path)
    db.executescript(update_gtfs.SCHEMA)
    db.executemany("INSERT INTO stops VALUES (?,?,?,?)", [(s, s, LAT[s], 17.0) for s in STOPS])
    db.execute("INSERT INTO routes VALUES ('RD','D','',3)")
    db.execute("INSERT INTO calendar VALUES ('ALL',1,1,1,1,1,1,1,'20260901','20261031')")
    # Cztery kursy w tę samą stronę, co kwadrans pod rząd (o 12:00 stoją
    # kilka km od siebie), plus jeden w przeciwną.
    for n in range(1, 5):
        _trip(db, f"3_{n}", "RD", STOPS, NOON_SEC - 900 * (n - 1) - 300)
    _trip(db, "3_5", "RD", STOPS[::-1], NOON_SEC - 300)
    db.commit()
    db.close()
    monkeypatch.setattr(gtfs, "DB_PATH", path)
    monkeypatch.setattr(vehicles, "datetime", _Noon)
    _clear_caches()
    yield path
    _clear_caches()


def _vehicle(feed_path, n):
    """Pojazd stojący dokładnie tam, gdzie kurs ma być o 12:00 wg rozkładu."""
    db = sqlite3.connect(feed_path)
    rows = db.execute(
        "SELECT s.stop_lat, st.departure_sec FROM stop_times st JOIN stops s "
        "ON s.stop_id = st.stop_id WHERE st.trip_id = ? ORDER BY st.stop_sequence",
        (f"3_{n}",)).fetchall()
    db.close()
    lat = next(la + (lb - la) * (NOON_SEC - a) / (b - a)
               for (la, a), (lb, b) in zip(rows, rows[1:]) if a <= NOON_SEC <= b)
    return {"line": "D", "kind": "bus", "lat": lat, "lon": 17.0,
            "key": n + OFFSET, "steps": []}


def test_letter_lines_from_the_feed_are_upper_case():
    """Kanał oddaje „d", rozkład ma „D" - bez zrównania linia literowa nie
    zgadza się z kursem ani z mapą."""
    raw = [{"type": "bus", "name": "d", "x": 51.1, "y": 17.0, "k": 1},
           {"type": "bus", "name": " 146 ", "x": 51.1, "y": 17.0, "k": 2},
           {"type": "bus", "name": "61a", "x": 51.1, "y": 17.0, "k": 3}]
    assert [v["line"] for v in vehicles._parse(raw)] == ["D", "146", "61A"]


def test_a_lettered_line_binds_to_its_course_by_key(feed):
    pojazdy = [_vehicle(feed, n) for n in range(1, 6)]
    vehicles._add_courses(pojazdy)
    vehicles._add_headings(pojazdy)
    assert all(v["source"] == "trip" for v in pojazdy)
    assert all(v["heading"] is not None and v["to"] for v in pojazdy)


def test_the_rest_of_the_course_tells_which_way_it_goes(feed):
    """Dalsza trasa (od następnego przystanku) to to, po czym front odróżnia
    „jedzie tam, dokąd mapa" od „jedzie w przeciwną stronę"."""
    pojazdy = [_vehicle(feed, n) for n in range(1, 6)]
    vehicles._add_courses(pojazdy)
    tam, wstecz = pojazdy[0], pojazdy[4]
    assert tam["rest"][-1] == [round(LAT["S5"], 5), 17.0]
    assert wstecz["rest"][-1] == [round(LAT["S0"], 5), 17.0]
    assert [round(LAT["S0"], 5), 17.0] not in tam["rest"]


def test_a_vehicle_with_an_unknown_course_carries_no_rest(feed):
    pojazdy = [_vehicle(feed, n) for n in range(1, 6)]
    obcy = {"line": "D", "kind": "bus", "lat": 51.5, "lon": 17.5, "key": 999999, "steps": []}
    vehicles._add_courses(pojazdy + [obcy])
    assert "rest" not in obcy and "course" not in obcy


def test_a_vetoed_course_leaves_nothing_behind(feed):
    """Ruch przeczący kursowi (strzałka by kłamała) kasuje całe dopasowanie,
    także dalszą trasę - po niej front odsiewa pojazdy."""
    pojazdy = [_vehicle(feed, n) for n in range(1, 6)]
    pojazdy[0]["steps"] = [180]
    vehicles._add_courses(pojazdy)
    assert "rest" in pojazdy[0]
    vehicles._add_headings(pojazdy)
    assert pojazdy[0]["heading"] is None and pojazdy[0]["source"] is None
    assert "rest" not in pojazdy[0] and "to" not in pojazdy[0]
