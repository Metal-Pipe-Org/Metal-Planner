"""Składanie rozkładu Siechnic z odpowiedzi API kiedyPrzyjedzie.

Testujemy warstwę przekształceń, nie sieć: wejściem są takie same struktury,
jakie oddaje API (kształt sprawdzony na żywym serwerze, patrz siechnice.py),
wyjściem - wiersze w kształcie tabel z update_gtfs.SCHEMA.
"""

import sqlite3
from datetime import date

import pytest

import siechnice
import update_gtfs


# --- odczyt słupków --------------------------------------------------------

def test_parse_stops_odwraca_kolejnosc_i_skaluje_wspolrzedne():
    # API oddaje mikrostopnie i (lon, lat) - odwrotnie niż reszta projektu.
    rows = [["30320:54875", 121014, "Bardzka", 17049301, 51083475, 0, 0, 0]]

    stops = siechnice.parse_stops(rows)

    assert stops == [{
        "designator": "30320:54875",
        "name": "Bardzka",
        "lat": pytest.approx(51.083475),
        "lon": pytest.approx(17.049301),
    }]


# --- numer linii kursu -----------------------------------------------------

def test_resolve_line_bierze_linie_wspolna_dla_wszystkich_slupkow():
    lines_by_stop = {"a": {"800+80", "89+890"}, "b": {"800+80"}, "c": {"800+80", "810+81"}}

    assert siechnice.resolve_line(["a", "b", "c"], lines_by_stop) == "800+80"


def test_resolve_line_zwraca_none_gdy_zostaje_kilka_kandydatek():
    # Dwie linie o identycznym zbiorze przystanków - nie zgadujemy, która.
    lines_by_stop = {"a": {"800+80", "810+81"}, "b": {"800+80", "810+81"}}

    assert siechnice.resolve_line(["a", "b"], lines_by_stop) is None


def test_resolve_line_zwraca_none_gdy_zaden_slupek_nie_ma_linii():
    assert siechnice.resolve_line(["a", "b"], {"a": set(), "b": set()}) is None


def test_resolve_line_pomija_slupki_bez_wpisu_zamiast_zerowac_przeciecie():
    # Słupek bez wpisu w /api/directions nie może skasować kandydatek -
    # inaczej jedna dziura w danych wywala cały kurs.
    lines_by_stop = {"a": {"860+86"}, "b": set()}

    assert siechnice.resolve_line(["a", "b"], lines_by_stop) == "860+86"


# --- czasy przez północ ----------------------------------------------------

def test_make_times_monotonic_zostawia_rosnace_bez_zmian():
    assert siechnice.make_times_monotonic([21780, 22000, 23580]) == [21780, 22000, 23580]


def test_make_times_monotonic_dolicza_dobe_po_polnocy():
    # Kurs 23:50 -> 00:05: API pokazuje drugi odjazd jako 300 s przy dacie
    # następnego dnia; GTFS zapisuje go jako 24:05, czyli 86700.
    assert siechnice.make_times_monotonic([85800, 300]) == [85800, 86700]


def test_make_times_monotonic_nie_cofa_sie_drugi_raz_w_tej_samej_dobie():
    times = siechnice.make_times_monotonic([85800, 300, 600])

    assert times == [85800, 86700, 87000]


# --- składanie kursów ------------------------------------------------------

def _departures(*items):
    return [{"departure": dep, "trip_id": trip, "index": idx} for trip, idx, dep in items]


def test_build_trips_skleja_odjazdy_o_tym_samym_trip_id_wg_index():
    departures_by_stop = {
        # Celowo w kolejności "od końca": porządek ma pochodzić z `index`,
        # a nie z tego, w jakiej kolejności odpytaliśmy słupki.
        "c": _departures((7, 3, 21000)),
        "a": _departures((7, 1, 20000)),
        "b": _departures((7, 2, 20500)),
    }
    lines_by_stop = {"a": {"800+80"}, "b": {"800+80"}, "c": {"800+80"}}

    trips, stats = siechnice.build_trips(departures_by_stop, lines_by_stop)

    assert len(trips) == 1
    assert trips[0]["line"] == "800+80"
    assert trips[0]["stops"] == [("a", 20000), ("b", 20500), ("c", 21000)]
    assert stats == {"skipped_short": 0, "skipped_no_line": 0}


def test_build_trips_odrzuca_kurs_widziany_z_jednego_slupka():
    departures_by_stop = {"a": _departures((7, 1, 20000))}

    trips, stats = siechnice.build_trips(departures_by_stop, {"a": {"800+80"}})

    assert trips == []
    assert stats["skipped_short"] == 1


def test_build_trips_odrzuca_kurs_o_nierozstrzygalnej_linii():
    departures_by_stop = {
        "a": _departures((7, 1, 20000)),
        "b": _departures((7, 2, 20500)),
    }
    lines_by_stop = {"a": {"800+80", "810+81"}, "b": {"800+80", "810+81"}}

    trips, stats = siechnice.build_trips(departures_by_stop, lines_by_stop)

    assert trips == []
    assert stats["skipped_no_line"] == 1


def test_build_trips_normalizuje_kurs_przez_polnoc():
    departures_by_stop = {
        "a": _departures((7, 1, 85800)),
        "b": _departures((7, 2, 300)),
    }

    trips, _ = siechnice.build_trips(departures_by_stop, {"a": {"89+890"}, "b": {"89+890"}})

    assert trips[0]["stops"] == [("a", 85800), ("b", 86700)]


# --- sklejanie ze słupkami wrocławskimi ------------------------------------

def test_match_existing_stops_laczy_te_same_slupki_mimo_innej_pisowni():
    # Wrocławski GTFS pisze 'SUCHA', kiedyPrzyjedzie 'Sucha'.
    stops = [{"designator": "d1", "name": "Sucha", "lat": 51.09686, "lon": 17.03828}]
    existing = [("5632", "SUCHA", 51.09686508, 17.03828127)]

    assert siechnice.match_existing_stops(stops, existing) == {"d1": "5632"}


def test_match_existing_stops_nie_laczy_odleglych_imiennikow():
    # Ta sama nazwa po drugiej stronie aglomeracji to nie ten sam słupek -
    # sklejenie zrobiłoby z nich nieistniejącą przesiadkę.
    stops = [{"designator": "d1", "name": "Kolejowa", "lat": 51.01, "lon": 17.17}]
    existing = [("999", "Kolejowa", 51.12, 16.92)]

    assert siechnice.match_existing_stops(stops, existing) == {}


def test_match_existing_stops_wybiera_najblizszy_z_imiennikow():
    stops = [{"designator": "d1", "name": "Bardzka", "lat": 51.0834, "lon": 17.0493}]
    existing = [
        ("far", "Bardzka", 51.0845, 17.0493),
        ("near", "Bardzka", 51.08341, 17.04931),
    ]

    assert siechnice.match_existing_stops(stops, existing) == {"d1": "near"}


# --- wiersze GTFS ----------------------------------------------------------

def _feed():
    stops = [
        {"designator": "a", "name": "Siechnice - Rynek", "lat": 51.03, "lon": 17.14},
        {"designator": "b", "name": "Sucha", "lat": 51.0968, "lon": 17.0382},
    ]
    trips = [{"trip_id": 4403, "line": "800+80", "stops": [("a", 20000), ("b", 21000)]}]
    return stops, [(date(2026, 8, 28), trips)]


def test_to_gtfs_rows_nie_wystawia_slupka_ktory_juz_jest_w_bazie():
    stops, days = _feed()
    stop_id_map = {"a": "SIE:a", "b": "5632"}   # 'b' sklejone z wrocławskim

    rows = siechnice.to_gtfs_rows(days, stops, stop_id_map)

    assert [r[0] for r in rows["stops"]] == ["SIE:a"]
    assert [r[2] for r in rows["stop_times"]] == ["SIE:a", "5632"]


def test_to_gtfs_rows_daje_kazdej_dacie_wlasny_service_id():
    stops, days = _feed()
    days = days + [(date(2026, 8, 29), days[0][1])]
    stop_id_map = {"a": "SIE:a", "b": "SIE:b"}

    rows = siechnice.to_gtfs_rows(days, stops, stop_id_map)

    assert rows["calendar_dates"] == [
        ("SIE:20260828", "20260828", 1),
        ("SIE:20260829", "20260829", 1),
    ]
    assert {t[2] for t in rows["trips"]} == {"SIE:20260828", "SIE:20260829"}
    # Ten sam trip_id w dwóch dniach to dwa różne kursy - identyfikatory
    # muszą się różnić, inaczej drugi dzień nadpisze pierwszy.
    assert len({t[0] for t in rows["trips"]}) == 2


def test_to_gtfs_rows_ustawia_kierunek_na_ostatni_przystanek():
    stops, days = _feed()

    rows = siechnice.to_gtfs_rows(days, stops, {"a": "SIE:a", "b": "SIE:b"})

    assert rows["trips"][0][3] == "Sucha"


def test_to_gtfs_rows_daje_linii_typ_autobusowy():
    stops, days = _feed()

    rows = siechnice.to_gtfs_rows(days, stops, {"a": "SIE:a", "b": "SIE:b"})

    assert rows["routes"] == [("SIE:800+80", "800+80", "Siechnice - linia 800+80", 3)]


# --- wejście do bazy -------------------------------------------------------

def test_merge_into_wpisuje_kursy_do_bazy_o_schemacie_z_update_gtfs(tmp_path):
    db_path = tmp_path / "gtfs.sqlite"
    db = sqlite3.connect(db_path)
    db.executescript(update_gtfs.SCHEMA)
    db.execute("INSERT INTO stops VALUES ('5632', 'SUCHA', 51.09686508, 17.03828127)")
    db.commit()
    db.close()

    stops, days = _feed()
    siechnice.merge_into(db_path, stops, days, log=lambda *_: None)

    db = sqlite3.connect(db_path)
    # Słupek 'Sucha' był już w bazie - kurs ma na niego wskazywać, zamiast
    # dokładać drugi marker w tym samym miejscu.
    assert db.execute("SELECT count(*) FROM stops").fetchone()[0] == 2
    assert [r[0] for r in db.execute(
        "SELECT stop_id FROM stop_times ORDER BY stop_sequence")] == ["SIE:a", "5632"]
    assert db.execute("SELECT count(*) FROM trips").fetchone()[0] == 1
    db.close()


def test_merge_into_daje_sie_powtorzyc_bez_zdublowania_slupkow(tmp_path):
    # Aktualizacja leci codziennie na świeżo zbudowanej bazie, ale gdyby
    # kiedyś poszła dwa razy na tej samej, słupki i linie mają się nadpisać.
    db_path = tmp_path / "gtfs.sqlite"
    db = sqlite3.connect(db_path)
    db.executescript(update_gtfs.SCHEMA)
    db.commit()
    db.close()

    stops, days = _feed()
    siechnice.merge_into(db_path, stops, days, log=lambda *_: None)
    siechnice.merge_into(db_path, stops, days, log=lambda *_: None)

    db = sqlite3.connect(db_path)
    assert db.execute("SELECT count(*) FROM stops").fetchone()[0] == 2
    assert db.execute("SELECT count(*) FROM routes").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM trips").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM calendar_dates").fetchone()[0] == 1
    # stop_times nie ma klucza głównego - bez czyszczenia drugi przebieg
    # dołożyłby drugi komplet wierszy zamiast je nadpisać.
    assert db.execute("SELECT count(*) FROM stop_times").fetchone()[0] == 2
    db.close()


def test_merge_into_nie_rusza_danych_wroclawskich(tmp_path):
    db_path = tmp_path / "gtfs.sqlite"
    db = sqlite3.connect(db_path)
    db.executescript(update_gtfs.SCHEMA)
    db.execute("INSERT INTO stops VALUES ('1', 'Rynek', 51.11, 17.03)")
    db.execute("INSERT INTO routes VALUES ('r1', '33', 'tramwaj', 0)")
    db.execute("INSERT INTO trips VALUES ('t1', 'r1', 's1', 'Rynek', '')")
    db.execute("INSERT INTO stop_times VALUES ('t1', 0, '1', 100, 100)")
    db.execute("INSERT INTO calendar_dates VALUES ('s1', '20260828', 1)")
    db.commit()
    db.close()

    stops, days = _feed()
    siechnice.merge_into(db_path, stops, days, log=lambda *_: None)

    db = sqlite3.connect(db_path)
    assert db.execute("SELECT count(*) FROM trips WHERE trip_id = 't1'").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM routes WHERE route_id = 'r1'").fetchone()[0] == 1
    assert db.execute(
        "SELECT count(*) FROM stop_times WHERE trip_id = 't1'").fetchone()[0] == 1
    db.close()


# --- wyłącznik -------------------------------------------------------------

def test_update_nie_rusza_sieci_gdy_wylaczone(monkeypatch, tmp_path):
    monkeypatch.delenv("SIECHNICE_ENABLED", raising=False)

    def _explode(*args, **kwargs):
        raise AssertionError("wyłączone źródło nie ma prawa odpytywać API")

    monkeypatch.setattr(siechnice, "fetch_feed", _explode)

    assert siechnice.update(tmp_path / "gtfs.sqlite", log=lambda *_: None) is False


def test_enabled_reaguje_na_zmienna(monkeypatch):
    monkeypatch.setenv("SIECHNICE_ENABLED", "on")
    assert siechnice.enabled() is True

    monkeypatch.setenv("SIECHNICE_ENABLED", "off")
    assert siechnice.enabled() is False
