"""Składanie rozkładu Siechnic z odpowiedzi API kiedyPrzyjedzie.

Testujemy warstwę przekształceń, nie sieć: wejściem są takie same struktury,
jakie oddaje API (kształt sprawdzony na żywym serwerze, patrz siechnice.py),
wyjściem - wiersze w kształcie tabel z update_gtfs.SCHEMA.
"""

import ast
import io
import json
import os
import sqlite3
from datetime import date, timedelta
from pathlib import Path

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

def test_parse_stops_ujednolica_polpauze_do_lacznika():
    # Źródło pisze "Radwanice - Szkoła" i "Radwanice – Szkoła" jako dwa słupki;
    # po ujednoliceniu autouzupełnianie widzi jedną nazwę.
    rows = [
        ["30333:54962", 1, "Radwanice - Szkoła", 17100000, 51100000, 0, 0, 0],
        ["1432006:1494566", 2, "Radwanice – Szkoła", 17100200, 51100000, 0, 0, 0],
    ]

    names = {stop["name"] for stop in siechnice.parse_stops(rows)}

    assert names == {"Radwanice - Szkoła"}


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

WINDOW = ("20261001", "20261031")


def _feed():
    stops = [
        {"designator": "a", "name": "Siechnice - Rynek", "lat": 51.03, "lon": 17.14},
        {"designator": "b", "name": "Sucha", "lat": 51.0968, "lon": 17.0382},
    ]
    trips = [{"trip_id": 4403, "line": "800+80", "stops": [("a", 20000), ("b", 21000)]}]
    return stops, [(date(2026, 8, 28), trips)]   # piątek


def test_to_gtfs_rows_nie_wystawia_slupka_ktory_juz_jest_w_bazie():
    stops, days = _feed()
    stop_id_map = {"a": "SIE:a", "b": "5632"}   # 'b' sklejone z wrocławskim

    rows = siechnice.to_gtfs_rows(days, stops, stop_id_map, WINDOW)

    assert [r[0] for r in rows["stops"]] == ["SIE:a"]
    assert [r[2] for r in rows["stop_times"]] == ["SIE:a", "5632"]


def test_to_gtfs_rows_powtarza_dzien_kopii_co_tydzien_w_oknie():
    # Kopia to jeden tydzień, a ma działać tygodniami: piątek z kopii jeździ
    # w każdy piątek okna, sobota w każdą sobotę.
    stops, days = _feed()
    days = days + [(date(2026, 8, 29), days[0][1])]
    stop_id_map = {"a": "SIE:a", "b": "SIE:b"}

    rows = siechnice.to_gtfs_rows(days, stops, stop_id_map, WINDOW)

    assert rows["calendar"] == [
        ("SIE:friday", 0, 0, 0, 0, 1, 0, 0, "20261001", "20261031"),
        ("SIE:saturday", 0, 0, 0, 0, 0, 1, 0, "20261001", "20261031"),
    ]
    assert {t[2] for t in rows["trips"]} == {"SIE:friday", "SIE:saturday"}
    # Ten sam trip_id w dwóch dniach to dwa różne kursy - identyfikatory
    # muszą się różnić, inaczej drugi dzień nadpisze pierwszy.
    assert len({t[0] for t in rows["trips"]}) == 2


def test_to_gtfs_rows_ustawia_kierunek_na_ostatni_przystanek():
    stops, days = _feed()

    rows = siechnice.to_gtfs_rows(days, stops, {"a": "SIE:a", "b": "SIE:b"}, WINDOW)

    assert rows["trips"][0][3] == "Sucha"


def test_to_gtfs_rows_daje_linii_typ_autobusowy():
    stops, days = _feed()

    rows = siechnice.to_gtfs_rows(days, stops, {"a": "SIE:a", "b": "SIE:b"}, WINDOW)

    assert rows["routes"] == [("SIE:800+80", "800+80", "Siechnice - linia 800+80", 3)]


# --- wejście do bazy -------------------------------------------------------

def _wroclaw_db(tmp_path):
    """Baza o schemacie z update_gtfs z wrocławskim kalendarzem na październik."""
    db_path = tmp_path / "gtfs.sqlite"
    db = sqlite3.connect(db_path)
    db.executescript(update_gtfs.SCHEMA)
    db.execute("INSERT INTO calendar VALUES ('s1', 1, 1, 1, 1, 1, 0, 0, '20261001', '20261025')")
    db.execute("INSERT INTO calendar_dates VALUES ('s2', '20261031', 1)")
    db.commit()
    db.close()
    return db_path


def test_merge_into_wpisuje_kursy_do_bazy_o_schemacie_z_update_gtfs(tmp_path):
    db_path = _wroclaw_db(tmp_path)
    db = sqlite3.connect(db_path)
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


def test_merge_into_bierze_okno_dat_z_rozkladu_wroclawia(tmp_path):
    # Po końcu wrocławskiej paczki aplikacja ma mówić, że rozkładu jeszcze
    # nie ma - Siechnice nie mogą tej daty przesunąć dalej.
    db_path = _wroclaw_db(tmp_path)

    stops, days = _feed()
    siechnice.merge_into(db_path, stops, days, log=lambda *_: None)

    db = sqlite3.connect(db_path)
    assert db.execute(
        "SELECT start_date, end_date FROM calendar WHERE service_id = 'SIE:friday'"
    ).fetchone() == ("20261001", "20261031")
    db.close()


def test_merge_into_daje_sie_powtorzyc_bez_zdublowania_slupkow(tmp_path):
    # Aktualizacja leci codziennie na świeżo zbudowanej bazie, ale gdyby
    # kiedyś poszła dwa razy na tej samej, słupki i linie mają się nadpisać.
    db_path = _wroclaw_db(tmp_path)

    stops, days = _feed()
    siechnice.merge_into(db_path, stops, days, log=lambda *_: None)
    siechnice.merge_into(db_path, stops, days, log=lambda *_: None)

    db = sqlite3.connect(db_path)
    assert db.execute("SELECT count(*) FROM stops").fetchone()[0] == 2
    assert db.execute("SELECT count(*) FROM routes").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM trips").fetchone()[0] == 1
    assert db.execute(
        "SELECT count(*) FROM calendar WHERE service_id LIKE 'SIE:%'").fetchone()[0] == 1
    # stop_times nie ma klucza głównego - bez czyszczenia drugi przebieg
    # dołożyłby drugi komplet wierszy zamiast je nadpisać.
    assert db.execute("SELECT count(*) FROM stop_times").fetchone()[0] == 2
    db.close()


def test_merge_into_nie_rusza_danych_wroclawskich(tmp_path):
    db_path = _wroclaw_db(tmp_path)
    db = sqlite3.connect(db_path)
    db.execute("INSERT INTO stops VALUES ('1', 'Rynek', 51.11, 17.03)")
    db.execute("INSERT INTO routes VALUES ('r1', '33', 'tramwaj', 0)")
    db.execute("INSERT INTO trips VALUES ('t1', 'r1', 's1', 'Rynek', '')")
    db.execute("INSERT INTO stop_times VALUES ('t1', 0, '1', 100, 100)")
    db.commit()
    db.close()

    stops, days = _feed()
    siechnice.merge_into(db_path, stops, days, log=lambda *_: None)

    db = sqlite3.connect(db_path)
    assert db.execute("SELECT count(*) FROM trips WHERE trip_id = 't1'").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM routes WHERE route_id = 'r1'").fetchone()[0] == 1
    assert db.execute(
        "SELECT count(*) FROM stop_times WHERE trip_id = 't1'").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM calendar WHERE service_id = 's1'").fetchone()[0] == 1
    db.close()


# --- statyczna kopia -------------------------------------------------------

def _snapshot(days=7, start=date(2026, 10, 12)):
    """Kopia w formacie wgrywanego pliku: dwa słupki, jeden kurs dziennie."""
    return {
        "source": "test",
        "stops": [
            ["a", 1, "Siechnice - Rynek", 17140000, 51030000, 0],
            ["b", 2, "Sucha", 17038200, 51096800, 0],
        ],
        "lines_by_stop": {"a": ["800+80"], "b": ["800+80"]},
        "days": [
            {"date": (start + timedelta(days=offset)).isoformat(),
             "departures_by_stop": {
                 "a": [{"departure": 20000, "trip_id": 4403, "index": 1}],
                 "b": [{"departure": 21000, "trip_id": 4403, "index": 2}],
             }}
            for offset in range(days)
        ],
    }


def _raw(snapshot):
    return json.dumps(snapshot).encode("utf-8")


def test_feed_from_snapshot_sklada_kursy_po_przejsciu_przez_json():
    # Kopia leży na dysku jako JSON - po drodze zbiory stają się listami,
    # a klucze napisami. Złożenie kursów ma to przeżyć.
    snapshot = json.loads(_raw(_snapshot(days=1, start=date(2026, 10, 13))))

    stops, days = siechnice.feed_from_snapshot(snapshot, log=lambda *_: None)

    assert [s["designator"] for s in stops] == ["a", "b"]
    assert days == [(date(2026, 10, 13), [
        {"trip_id": 4403, "line": "800+80", "stops": [("a", 20000), ("b", 21000)]},
    ])]


def test_update_bez_kopii_nie_dokleja_niczego(tmp_path):
    db_path = _wroclaw_db(tmp_path)

    merged = siechnice.update(db_path, tmp_path / "brak.json", log=lambda *_: None)

    assert merged is False
    db = sqlite3.connect(db_path)
    assert db.execute("SELECT count(*) FROM trips").fetchone()[0] == 0
    db.close()


def test_update_dokleja_kopie_z_pliku(tmp_path):
    db_path = _wroclaw_db(tmp_path)
    snapshot_path = tmp_path / "rozklad.json"
    snapshot_path.write_bytes(_raw(_snapshot()))

    merged = siechnice.update(db_path, snapshot_path, log=lambda *_: None)

    assert merged is True
    db = sqlite3.connect(db_path)
    assert db.execute(
        "SELECT count(*) FROM calendar WHERE service_id LIKE 'SIE:%'").fetchone()[0] == 7
    assert db.execute("SELECT count(*) FROM trips").fetchone()[0] == 7
    db.close()


# --- sprawdzenie wgranego pliku -------------------------------------------

def test_validate_snapshot_przyjmuje_pelny_tydzien():
    assert siechnice.validate_snapshot(_raw(_snapshot()))["lines_by_stop"]["a"] == ["800+80"]


def _broken(change):
    snapshot = _snapshot()
    change(snapshot)
    return _raw(snapshot)


@pytest.mark.parametrize("raw, fragment", [
    (b"\x89PNG\r\n", "nie jest plik JSON"),
    (b"[1, 2, 3]", "nie jest obiektem JSON"),
    (_raw({"stops": []}), "Brak pól: lines_by_stop, days"),
    (_broken(lambda s: s.update(stops=[])), "niepustą listą"),
    (_broken(lambda s: s["stops"].append(["c", 3, "X", "17", 51])), "Zły słupek"),
    (_broken(lambda s: s.update(lines_by_stop={"a": "800"})), "lines_by_stop"),
    (_broken(lambda s: s["days"][0].update(date="wczoraj")), "Zła data"),
    (_broken(lambda s: s["days"].append(s["days"][0])), "powtarza się"),
    (_broken(lambda s: s["days"].pop()), "6 z 7 dni"),
    (_broken(lambda s: s["days"][0]["departures_by_stop"].update(z=[])), "nieznanego słupka"),
    (_broken(lambda s: s["days"][0]["departures_by_stop"]["a"][0].pop("index")),
     "departure, trip_id i index"),
    (_broken(lambda s: s.update(lines_by_stop={})), "ani jednego kursu"),
])
def test_validate_snapshot_odrzuca_zly_plik_z_powodem(raw, fragment):
    with pytest.raises(siechnice.InvalidSnapshot, match=fragment):
        siechnice.validate_snapshot(raw)


def test_save_snapshot_zapisuje_tylko_dobry_plik(tmp_path):
    path = tmp_path / "wolumen" / "rozklad.json"
    siechnice.save_snapshot(_raw(_snapshot()), path)
    good = path.read_bytes()

    with pytest.raises(siechnice.InvalidSnapshot):
        siechnice.save_snapshot(b"{}", path)

    assert path.read_bytes() == good
    assert [p.name for p in path.parent.iterdir()] == ["rozklad.json"]


# --- wgranie przez ⚙ -------------------------------------------------------

@pytest.fixture
def upload(tmp_path, monkeypatch):
    """Klient Flaska z kopią i bazą w tmp_path; przebudowa tylko zliczana."""
    import app as app_module

    monkeypatch.setattr(siechnice, "SNAPSHOT_PATH", tmp_path / "siechnice" / "rozklad.json")
    monkeypatch.setattr(update_gtfs, "DB_PATH", tmp_path / "gtfs.sqlite")
    monkeypatch.setattr(update_gtfs, "LOCK_PATH", tmp_path / "gtfs_update.lock")
    rebuilds = []
    monkeypatch.setattr(update_gtfs, "start_update_in_background",
                        lambda: rebuilds.append(True))
    monkeypatch.setenv("DEV_MODE", "1")

    client = app_module.app.test_client()
    client.rebuilds = rebuilds
    client.snapshot_path = siechnice.SNAPSHOT_PATH
    return client


def _post(client, raw):
    return client.post("/api/dev/siechnice",
                       data={"file": (io.BytesIO(raw), "siechnice.json")},
                       content_type="multipart/form-data")


def test_wgranie_zapisuje_plik_i_odpala_przebudowe(upload):
    response = _post(upload, _raw(_snapshot()))

    assert response.status_code == 200
    assert response.get_json()["uploaded_at"]
    assert upload.snapshot_path.read_bytes() == _raw(_snapshot())
    assert upload.rebuilds == [True]


def test_zly_plik_odrzucony_z_komunikatem_bez_przebudowy(upload):
    response = _post(upload, b"to nie jest rozklad")

    assert response.status_code == 400
    assert "JSON" in response.get_json()["error"]
    assert not upload.snapshot_path.exists()
    assert upload.rebuilds == []


def test_bez_trybu_deweloperskiego_wgrania_nie_ma(upload, monkeypatch):
    monkeypatch.setenv("DEV_MODE", "")

    assert _post(upload, _raw(_snapshot())).status_code == 404
    assert upload.get("/api/dev/siechnice").status_code == 404
    assert not upload.snapshot_path.exists()
    assert upload.rebuilds == []


def test_stan_bez_wgranej_kopii(upload):
    assert upload.get("/api/dev/siechnice").get_json() == {
        "uploaded_at": None, "rebuilding": False, "in_database": False,
    }


def test_stan_mowi_czy_baza_ma_juz_wgrana_kopie(upload):
    _post(upload, _raw(_snapshot()))
    assert upload.get("/api/dev/siechnice").get_json()["in_database"] is False

    update_gtfs.DB_PATH.write_bytes(b"")
    later = upload.snapshot_path.stat().st_mtime + 1
    os.utime(update_gtfs.DB_PATH, (later, later))

    assert upload.get("/api/dev/siechnice").get_json()["in_database"] is True


# --- jedna przebudowa naraz ------------------------------------------------

def test_przebudowa_trzyma_blokade_a_druga_na_nia_czeka(tmp_path, monkeypatch):
    monkeypatch.setattr(update_gtfs, "DATA_DIR", tmp_path)
    monkeypatch.setattr(update_gtfs, "LOCK_PATH", tmp_path / "gtfs_update.lock")
    seen = []
    monkeypatch.setattr(update_gtfs, "_run_update_locked",
                        lambda: seen.append(update_gtfs.update_running()) or True)

    assert update_gtfs.update_running() is False
    assert update_gtfs.run_update() is True
    assert seen == [True]
    assert update_gtfs.update_running() is False


def test_przebudowa_w_tle_wola_osobny_proces(monkeypatch):
    calls = []
    monkeypatch.setattr(update_gtfs.subprocess, "run",
                        lambda *args, **kwargs: calls.append(args[0]))

    update_gtfs.start_update_in_background().join(timeout=5)

    assert calls and calls[0][-1].endswith("update_gtfs.py")


# --- serwer nie odpytuje kiedyPrzyjedzie -----------------------------------

ROOT = Path(__file__).resolve().parent.parent


def test_modul_serwera_nie_ma_kodu_sieciowego():
    # siechnice.py jedzie w obrazie i woła go nocna aktualizacja - nie może
    # umieć niczego pobrać, nawet przez pomyłkę.
    tree = ast.parse((ROOT / "siechnice.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module.split(".")[0])

    assert not imported & {"http", "urllib", "socket", "ssl", "requests"}
