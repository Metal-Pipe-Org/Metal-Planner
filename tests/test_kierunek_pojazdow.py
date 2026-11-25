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


class _Clock(datetime.datetime):
    sec = NOON_SEC

    @classmethod
    def now(cls, tz=None):
        return cls(2026, 9, 14, tzinfo=tz) + datetime.timedelta(seconds=cls.sec)


def _trip(db, trip_id, route, stops, start_sec, shape=None):
    db.execute("INSERT INTO trips VALUES (?,?,'ALL','Koniec',?)", (trip_id, route, shape))
    db.executemany(
        "INSERT INTO stop_times VALUES (?,?,?,?,?)",
        [(trip_id, i, s, start_sec + i * 600, start_sec + i * 600) for i, s in enumerate(stops)],
    )


def _clear_caches():
    for cache in (vehicles._window_cache, vehicles._stops_cache, vehicles._path_cache, vehicles._anchors):
        cache.clear()
    vehicles._anchors_db[0] = None
    _Clock.sec = NOON_SEC


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
    monkeypatch.setattr(vehicles, "datetime", _Clock)
    _clear_caches()
    yield path
    _clear_caches()


def _vehicle(feed_path, n, key=None, late_sec=0):
    """Pojazd kursu 3_n tam, gdzie kurs ma być teraz wg rozkładu (albo
    late_sec wcześniej - spóźniony)."""
    db = sqlite3.connect(feed_path)
    rows = db.execute(
        "SELECT s.stop_lat, st.departure_sec FROM stop_times st JOIN stops s "
        "ON s.stop_id = st.stop_id WHERE st.trip_id = ? ORDER BY st.stop_sequence",
        (f"3_{n}",)).fetchall()
    db.close()
    sec = _Clock.sec - late_sec
    lat = next(la + (lb - la) * (sec - a) / (b - a)
               for (la, a), (lb, b) in zip(rows, rows[1:]) if a <= sec <= b)
    return {"line": "D", "kind": "bus", "lat": lat, "lon": 17.0,
            "key": n + OFFSET if key is None else key, "steps": [], "still_sec": 0}


def _seen_twice(feed_path, plan):
    """Dwa odczyty kanału dwie minuty od siebie - pojazdy (klucz -> kurs) jadą
    po swoich kursach, więc ich stałe zdążą się potwierdzić. Zwraca pojazdy
    z drugiego odczytu."""
    for sec in (NOON_SEC - 120, NOON_SEC):
        _Clock.sec = sec
        pojazdy = [_vehicle(feed_path, n, key) for key, n in plan.items()]
        vehicles._add_courses(pojazdy)
    return pojazdy


def test_letter_lines_from_the_feed_are_upper_case():
    """Kanał oddaje „d", rozkład ma „D" - bez zrównania linia literowa nie
    zgadza się z kursem ani z mapą."""
    raw = [{"type": "bus", "name": "d", "x": 51.1, "y": 17.0, "k": 1},
           {"type": "bus", "name": " 146 ", "x": 51.1, "y": 17.0, "k": 2},
           {"type": "bus", "name": "61a", "x": 51.1, "y": 17.0, "k": 3}]
    assert [v["line"] for v in vehicles._parse(raw)] == ["D", "146", "61A"]


def test_a_lettered_line_binds_to_its_course_by_key(feed):
    pojazdy = _seen_twice(feed, {n + OFFSET: n for n in range(1, 6)})
    vehicles._add_headings(pojazdy)
    assert all(v["source"] == "trip" for v in pojazdy)
    assert all(v["heading"] is not None and v["to"] for v in pojazdy)


def test_a_course_seen_once_binds_nothing(feed):
    """Raz stać tam, gdzie kurs ma być, to jeszcze przypadek (np. spóźniony
    poprzedni kurs) - kurs przychodzi dopiero z drugim odczytem."""
    pojazdy = [_vehicle(feed, n) for n in range(1, 6)]
    vehicles._add_courses(pojazdy)
    assert not any("course" in v for v in pojazdy)


def test_a_lone_key_binds_by_itself_once_it_follows_its_course(feed):
    """Tramwaje mają klucze rzadko i bez sąsiadów - wystarcza, że kropka po
    raz drugi, kawałek dalej, jest tam, gdzie kurs."""
    sam = _seen_twice(feed, {1 + OFFSET: 1})[0]
    assert sam["course"][0]["id"] == "3_1"


def test_a_late_vehicle_gets_the_constant_of_its_neighbours(feed):
    """Spóźniony pojazd nie stoi tam, gdzie jego kurs - nie nauczy się sam,
    ale jego klucz leży w bloku sąsiadów, którzy się zgadzają."""
    _seen_twice(feed, {OFFSET + 1: 1, OFFSET + 2: 2, OFFSET + 3: 3})
    spozniony = _vehicle(feed, 5, late_sec=300)
    vehicles._add_courses([spozniony])
    assert spozniony["course"][0]["id"] == "3_5"


def test_neighbours_that_disagree_lend_nothing(feed):
    """Na granicy bloków najbliżsi sąsiedzi mają różne stałe - lepiej nic niż
    zgadywać, do którego bloku należy klucz."""
    _seen_twice(feed, {OFFSET + 1: 1, OFFSET + 2: 2, OFFSET + 3: 1})
    spozniony = _vehicle(feed, 5, late_sec=300)
    vehicles._add_courses([spozniony])
    assert "course" not in spozniony


def test_the_rest_of_the_course_tells_which_way_it_goes(feed):
    """Dalsza trasa (od następnego przystanku) to to, po czym front odróżnia
    „jedzie tam, dokąd mapa" od „jedzie w przeciwną stronę"."""
    pojazdy = _seen_twice(feed, {n + OFFSET: n for n in range(1, 6)})
    tam, wstecz = pojazdy[0], pojazdy[4]
    assert tam["rest"][-1] == [round(LAT["S5"], 5), 17.0]
    assert wstecz["rest"][-1] == [round(LAT["S0"], 5), 17.0]
    assert [round(LAT["S0"], 5), 17.0] not in tam["rest"]


def test_a_vehicle_with_an_unknown_course_carries_no_rest(feed):
    _seen_twice(feed, {n + OFFSET: n for n in range(1, 6)})
    obcy = {"line": "D", "kind": "bus", "lat": 51.5, "lon": 17.5, "key": 999999, "steps": [], "still_sec": 0}
    vehicles._add_courses([obcy])
    assert "rest" not in obcy and "course" not in obcy


def test_a_vetoed_course_leaves_nothing_behind(feed):
    """Ruch przeczący kursowi (strzałka by kłamała) kasuje całe dopasowanie,
    także dalszą trasę - po niej front odsiewa pojazdy."""
    pojazdy = _seen_twice(feed, {n + OFFSET: n for n in range(1, 6)})
    pojazdy[0]["steps"] = [180]
    assert "rest" in pojazdy[0]
    vehicles._add_headings(pojazdy)
    assert pojazdy[0]["heading"] is None and pojazdy[0]["source"] is None
    assert "rest" not in pojazdy[0] and "to" not in pojazdy[0]


def _moving(lat, lon, bearing=0):
    """Pojazd bez kursu, który dwa razy przesunął się w tę samą stronę."""
    return {"line": "D", "kind": "bus", "lat": lat, "lon": lon, "key": 999999, "steps": [bearing, bearing],
            "still_sec": 0}


def test_just_past_a_stop_the_route_still_has_one_next_stop(feed):
    """Tuż za przystankiem kropka pasuje i do odcinka przed nim, i za nim - to
    wciąż jedno miejsce trasy, nie rozjazd."""
    za_s2 = _moving(LAT["S2"] + 0.0004, 17.0)
    vehicles._add_courses([za_s2])
    vehicles._add_headings([za_s2])
    assert za_s2["source"] == "route" and za_s2["next"] == "S3"


@pytest.fixture
def bend(feed):
    """Linia E, której trasa między S1 a S2 wygina się ~350 m na wschód."""
    db = sqlite3.connect(feed)
    db.execute("INSERT INTO routes VALUES ('RE','E','',3)")
    mid = (LAT["S1"] + LAT["S2"]) / 2
    shape = [(LAT["S0"], 17.0), (LAT["S1"], 17.0), (LAT["S1"] + 0.004, 17.005),
             (mid, 17.005), (LAT["S2"] - 0.004, 17.005), (LAT["S2"], 17.0), (LAT["S3"], 17.0)]
    db.executemany("INSERT INTO shapes VALUES ('SE',?,?,?)", [(i, la, lo) for i, (la, lo) in enumerate(shape)])
    _trip(db, "3_9", "RE", ["S0", "S1", "S2", "S3"], NOON_SEC - 900, shape="SE")
    db.commit()
    db.close()
    return mid


def test_the_route_is_matched_along_its_bend_not_the_straight_line(bend):
    """Na łuku kropka jest daleko od prostej między przystankami, ale na trasie."""
    na_luku = {**_moving(bend, 17.005), "line": "E"}
    vehicles._add_courses([na_luku])
    vehicles._add_headings([na_luku])
    assert na_luku["source"] == "route" and na_luku["next"] == "S2"


def test_a_vehicle_the_feed_froze_on_the_route_loses_its_arrow(feed):
    """Kanał od ponad STALE_SEC podaje pojazd w tym samym miejscu w trasie:
    strzałka wskazywałaby z miejsca, w którym go już nie ma."""
    pojazdy = _seen_twice(feed, {n + OFFSET: n for n in range(1, 6)})
    zamrozony = pojazdy[0]
    zamrozony["still_sec"] = vehicles.STALE_SEC + 1
    vehicles._add_courses([zamrozony])
    vehicles._add_headings([zamrozony])
    assert zamrozony["stale"] and zamrozony["heading"] is None and "rest" not in zamrozony


def test_a_vehicle_waiting_at_its_terminus_is_not_stale(feed):
    na_petli = {**_moving(LAT["S0"], 17.0), "still_sec": 600}
    vehicles._add_courses([na_petli])
    assert na_petli["stale"] is False


def test_the_feed_tells_how_long_ago_a_vehicle_last_moved(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(vehicles, "time", type("T", (), {"monotonic": staticmethod(lambda: clock[0])}))
    monkeypatch.setattr(vehicles, "_tracks", {})
    for sec, lat in ((100, 51.1), (110, 51.1), (130, 51.1), (140, 51.101), (150, 51.101)):
        clock[0] = sec
        pojazd = {"lat": lat, "lon": 17.0, "key": 7}
        vehicles._track_motion([pojazd])
    assert pojazd["still_sec"] == 10
