"""Założenia czasowe pod zębatką (zgłoszenie #151).

Każdy pasażer ustawia sobie tempo marszu (trzy stałe tempa) oraz prędkość
roweru i narzut jego przejazdu. Pilnujemy tu trzech rzeczy: że domyślne
wartości to dokładnie dzisiejsze liczby (bez ruszania zębatki nic się nie
zmienia), że ustawienie naprawdę przesuwa godziny, i że sufity trzyma
serwer, a nie front.
"""

import datetime

import bikes
import gtfs
import planner
from tests.gtfs_builder import make_day

WHEN = datetime.datetime(2026, 1, 5, 0, 0, 0)   # dep_sec = 0, czytelne liczby

P1 = (51.1000, 17.0300)
P2 = (51.1000 + 300 / 111_320, 17.0300)   # ok. 300 m na północ od P1


def _dzien_z_przejsciem():
    """Autobus S -> P1 o 600, a z P2 (300 m od P1) tramwaje o 1020 i 1800.

    300 m to przy zwykłym tempie 8 minut (480 s) - na tramwaj o 1020 się
    nie zdąży. Szybkim tempem 6 minut i się zdąży; wolnym 10 minut i nie."""
    day = make_day([
        {"trip_id": "bus", "label": "Autobus 1",
         "stops": [("S", 0, 0), ("P1", 600, 600)]},
        {"trip_id": "tram1", "label": "Tramwaj 2",
         "stops": [("P2", 1020, 1020), ("E", 1500, 1500)]},
        {"trip_id": "tram2", "label": "Tramwaj 2",
         "stops": [("P2", 1800, 1800), ("E", 2400, 2400)]},
    ])
    day.stop_coords.update({"P1": P1, "P2": P2})
    day.siblings = gtfs._nearby_bridges(day.stop_coords)
    return day


# ------------------------------------------------------------- tempo marszu


def test_zwykle_tempo_to_ten_sam_dzien():
    """Domyślne tempo nie robi kopii - to dzień z load_day, co do obiektu."""
    day = _dzien_z_przejsciem()
    assert gtfs.with_pace(day, gtfs.DEFAULT_WALK_PACE) is day


def test_tempo_zmienia_cene_przejscia_a_nie_zasieg():
    day = _dzien_z_przejsciem()
    wolno = gtfs.with_pace(day, "wolno")
    szybko = gtfs.with_pace(day, "szybko")

    assert gtfs.walk_seconds(day, "P1", "P2") == 480
    assert gtfs.walk_seconds(szybko, "P1", "P2") == 360
    assert gtfs.walk_seconds(wolno, "P1", "P2") == 600
    assert szybko.siblings.keys() == day.siblings.keys()
    # Dzień bazowy zostaje nietknięty - dzielą go wszyscy pytający.
    assert day.walk_mps == gtfs.WALK_SPEED_MPS
    assert day.siblings["P1"]["P2"] == 480


def test_tempo_liczy_sie_raz_na_dzien():
    """Drugie pytanie tym samym tempem dostaje tę samą, już policzoną kopię."""
    day = _dzien_z_przejsciem()
    assert gtfs.with_pace(day, "szybko") is gtfs.with_pace(day, "szybko")


def test_szybkie_tempo_lapie_przesiadke(install_day):
    install_day(_dzien_z_przejsciem())

    zwykle = planner.plan_flow("S", "E", WHEN)
    szybko = planner.plan_flow("S", "E", WHEN, walk_pace="szybko")
    wolno = planner.plan_flow("S", "E", WHEN, walk_pace="wolno")

    assert zwykle["fastest"]["arrival"] == "00:40"
    assert szybko["fastest"]["arrival"] == "00:25"
    assert wolno["fastest"]["arrival"] == "00:40"


def test_nieznane_tempo_to_zwykle(install_day):
    """Z przeglądarki może przyjść cokolwiek - serwer bierze wtedy domyślne."""
    install_day(_dzien_z_przejsciem())
    wynik = planner.plan_flow("S", "E", WHEN, walk_pace="biegiem")
    assert wynik["fastest"]["arrival"] == "00:40"


# ------------------------------------------------------------------- rower


def test_domyslny_model_roweru_to_dzisiejsze_liczby():
    """1 km w linii prostej: 6 minut jazdy i 2 minuty narzutu."""
    assert bikes.ride_time_sec(1000) == 8 * 60
    assert bikes.ride_time_sec(1000, 20 / 3.6, 0) == 3 * 60


def test_suwaki_roweru_maja_sufit_na_serwerze(install_day, monkeypatch):
    """Wartości z localStorage bywają dowolne - planner je przycina."""
    install_day(_dzien_z_przejsciem())
    uzyte = {}

    def _zapamietaj(day, arrivals, onward, target_set, limit, **kwargs):
        uzyte.update(kwargs)
        return []
    monkeypatch.setattr(bikes, "map_places", _zapamietaj)

    planner.plan_flow("S", "E", WHEN, bike_kmh=500, bike_overhead_sec=-60)
    assert uzyte["ride_mps"] == planner.MAX_BIKE_KMH / 3.6
    assert uzyte["overhead_sec"] == 0

    planner.plan_flow("S", "E", WHEN, bike_kmh=1, bike_overhead_sec=99999)
    assert uzyte["ride_mps"] == planner.MIN_BIKE_KMH / 3.6
    assert uzyte["overhead_sec"] == planner.MAX_BIKE_OVERHEAD_SEC
