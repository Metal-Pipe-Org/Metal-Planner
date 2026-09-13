"""Testy aut car-sharingu NA MAPIE przepływów (punkt 15 kontraktu).

To jest coś innego niż tests/test_traficar.py: tamte pilnują propozycji
kończącej się jazdą autem, te - samego znacznika na mapie. Mapa o jeździe
autem nie mówi nic. Mówi tylko dwie rzeczy: o której da się być PRZY aucie
(dojazd z rozkładu plus dojście liczone tą samą regułą, co każde inne -
punkt 14) i ile stąd do celu w linii prostej.

Feed fioletowe.live jest tu zawsze podstawiony (patrz tests/conftest.py).
"""

from datetime import date, datetime, time

import gtfs
import planner
import traficar
from tests.gtfs_builder import make_day

# Auta stoją tam, gdzie stoją TERAZ, więc mapa pokazuje je wyłącznie przy
# pytaniu o dziś - stąd dzisiejsza data zamiast stałej z pozostałych testów.
# Północ, żeby dep_sec wyszło zerem i liczby dały się czytać w głowie.
WHEN = datetime.combine(date.today(), time())

# Trzy punkty na jednym południku: 0,01° szerokości to ~1113 m.
S = (51.10, 17.00)          # start
M = (51.12, 17.00)          # przystanek w połowie
E = (51.16, 17.00)          # cel

# Auto ~33 m od M - poniżej minimalnego czasu dojścia (gtfs.WALK_MIN_SEC).
CAR = {"lat": 51.1203, "lon": 17.00, "plate": "WE1AA11", "model": "Renault Clio",
       "where": "ul. Testowa", "fuel": 80, "range": 300, "ogarniam": []}


def _day():
    """Tramwaj S -> M -> E (1200 s) i wolny autobus S -> E (4000 s)."""
    trips = [
        {"trip_id": "SZYBKI", "label": "Tramwaj 1",
         "stops": [("S", 0, 0), ("M", 600, 600), ("E", 1200, 1200)]},
        {"trip_id": "WOLNY", "label": "Autobus 2",
         "stops": [("S", 0, 0), ("E", 4000, 4000)]},
    ]
    day = make_day(trips, names={"S": "Start", "M": "Środek", "E": "Cel"})
    day.stop_coords.update({"S": S, "M": M, "E": E})
    return day


def _cars(monkeypatch, cars):
    monkeypatch.setattr(traficar, "enabled", lambda: True)
    monkeypatch.setattr(traficar, "car_list", lambda: cars)


def _flow(install_day, monkeypatch, cars=(CAR,), day=None):
    install_day(day or _day())
    _cars(monkeypatch, list(cars))
    result = planner.plan_flow("Start", "Cel", WHEN)
    assert "error" not in result
    return result


# ------------------------------------------------ co w ogóle trafia na mapę ----

def test_auto_przy_narysowanym_przystanku_trafia_na_mape(install_day, monkeypatch):
    result = _flow(install_day, monkeypatch)

    assert len(result["cars"]) == 1
    car = result["cars"][0]
    assert car["plate"] == "WE1AA11"
    assert car["from"] == "Środek"


def test_auto_dalej_niz_jedno_dojscie_nie_istnieje(install_day, monkeypatch):
    """Promień jest ten sam, co przy przejściu między przystankami - auto nie
    ma własnej, hojniejszej miary (punkt 14: jedna zasada na cały system)."""
    daleko = {**CAR, "lat": 51.13}      # ~1,1 km od M, powyżej gtfs.WALK_M
    assert _flow(install_day, monkeypatch, [daleko])["cars"] == []


def test_auto_przy_starcie_widac_od_razu(install_day, monkeypatch):
    """Do auta stojącego pod nosem idzie się bez wsiadania w cokolwiek -
    godzina przy nim to sama godzina wyjazdu plus marsz."""
    przy_starcie = {**CAR, "lat": 51.1003}

    car = _flow(install_day, monkeypatch, [przy_starcie])["cars"][0]

    assert car["from"] == "Start"
    assert car["at"] == car["walk_sec"]      # dep_sec = 0


def test_liczy_sie_to_co_mapa_RYSUJE_a_nie_cale_miasto(monkeypatch):
    """Zasięg podaje mapa (planner._drawn_reach). Przystanek, którego na niej
    nie ma, nie stawia auta na mapie, choćby leżał tuż obok niego."""
    day = _day()
    _cars(monkeypatch, [CAR])
    assert traficar.map_cars(day, {"M": 600}, E) != []
    assert traficar.map_cars(day, {"S": 0}, E) == []


# --------------------------------------------------- co auto o sobie mówi ----

def test_godzina_przy_aucie_to_dojazd_plus_dojscie(install_day, monkeypatch):
    """Marsz liczony tą samą funkcją, co wszędzie indziej - nie osobną
    prędkością dla aut."""
    dalej = {**CAR, "lat": 51.1235}          # ~390 m od M

    car = _flow(install_day, monkeypatch, [dalej])["cars"][0]

    metry = gtfs._haversine_m(*M, dalej["lat"], dalej["lon"])
    assert car["walk_sec"] == gtfs.walk_time_sec(metry)
    assert car["at"] == 600 + car["walk_sec"]


def test_najkrotsze_dojscie_wygrywa(install_day, monkeypatch):
    """Auto w zasięgu dwóch narysowanych przystanków opisuje się tym, z
    którego jest się przy nim NAJWCZEŚNIEJ."""
    day = _day()
    day.stop_coords["S"] = (51.1190, 17.00)   # start ~145 m od auta, M ~33 m

    car = _flow(install_day, monkeypatch, day=day)["cars"][0]

    # Ze startu wychodzi się o zerowej godzinie, więc mimo dłuższego dojścia
    # jest się przy aucie wcześniej niż tramwajem o 600.
    assert car["from"] == "Start"
    assert car["at"] == car["walk_sec"]


def test_do_celu_tylko_w_linii_prostej(install_day, monkeypatch):
    """Czasu jazdy autem nie ma skąd wziąć i mapa go nie zgaduje - podaje samą
    odległość, resztę zostawia pasażerowi."""
    car = _flow(install_day, monkeypatch)["cars"][0]

    assert car["to_dest_m"] == round(
        gtfs._haversine_m(CAR["lat"], CAR["lon"], *E))
    assert "drive_sec" not in car
    assert "drive_m" not in car


def test_auto_nie_jest_kursem_na_mapie(install_day, monkeypatch):
    """Znacznik auta niczego w wachlarzu nie przestawia: te same kawałki,
    te same jasności, te same węzły, co bez aut."""
    z_autem = _flow(install_day, monkeypatch)
    bez_auta = _flow(install_day, monkeypatch, cars=[])

    assert bez_auta["cars"] == []
    assert z_autem["segments"] == bez_auta["segments"]
    assert z_autem["nodes"] == bez_auta["nodes"]


# ------------------------------------------------------------- Ogarniam ----

def test_co_jest_do_wziecia_dojezdza_do_mapy(install_day, monkeypatch):
    """Program "Ogarniam": przy niektórych autach Traficar płaci za zajęcie
    się nimi. Feed to podaje, więc mapa ma to przekazać bez zmian - z kwotą
    i z tym, za co."""
    do_wziecia = {**CAR, "ogarniam": [{"co": "Sprzątanie", "ile": 30},
                                      {"co": "Tankowanie", "ile": 15}]}

    car = _flow(install_day, monkeypatch, [do_wziecia])["cars"][0]

    assert car["ogarniam"] == [{"co": "Sprzątanie", "ile": 30},
                               {"co": "Tankowanie", "ile": 15}]


def test_auto_bez_ogarniania_mowi_to_pusta_lista(install_day, monkeypatch):
    """Pusta lista, nie brak pola: "nic tu nie ma" i "nie wiadomo" to dwie
    różne odpowiedzi, a front pisze w dymku jedną z nich."""
    assert _flow(install_day, monkeypatch)["cars"][0]["ogarniam"] == []


def test_ogarniam_czytamy_z_feedu_takie_jakie_jest(monkeypatch):
    """Jedyne miejsce, w którym zamieniamy kształt źródła na własny: w feedzie
    to `discounts` z `name`/`amount` (patrz CarDiscountV1). Auto bez nagrody
    ma tam `null`, nie pustą listę."""
    feed = {"cars": [
        {"lat": "51.12", "lng": "17.00", "regPlate": "WE1AA11", "modelId": 1,
         "location": "Wrocław, ul. Testowa", "fuel": 80.0, "range": 300,
         "available": True,
         "discounts": [{"name": "Relokacja", "amount": 30}]},
        {"lat": "51.13", "lng": "17.00", "regPlate": "WE2BB22", "modelId": 1,
         "location": "Wrocław", "fuel": 50.0, "range": 200,
         "available": True, "discounts": None},
    ]}
    monkeypatch.setattr(traficar, "_fetch", lambda url: feed)
    monkeypatch.setattr(traficar, "_models", lambda: {1: "Renault Clio"})
    monkeypatch.setattr(traficar, "_cars_cache",
                        {"at": 0.0, "cars": [], "generation": 0})

    z_nagroda, bez_nagrody = traficar.car_list()

    assert z_nagroda["ogarniam"] == [{"co": "Relokacja", "ile": 30}]
    assert bez_nagrody["ogarniam"] == []


# ------------------------------------------------ kiedy aut nie ma w ogóle ----

def test_pytanie_o_inny_dzien_nie_pokazuje_aut(install_day, monkeypatch):
    """Auto stoi tam, gdzie stoi teraz - nie tam, gdzie będzie stało we
    wtorek. Godzina "będziesz przy nim" byłaby wtedy zgadywaniem podanym
    jako fakt (ta sama zasada, co przy stojakach rowerowych)."""
    install_day(_day())
    _cars(monkeypatch, [CAR])

    result = planner.plan_flow("Start", "Cel", datetime(2026, 1, 5, 0, 0, 0))

    assert result["cars"] == []


def test_wylacznik_gasi_auta_na_mapie(install_day, monkeypatch):
    install_day(_day())
    _cars(monkeypatch, [CAR])
    monkeypatch.setattr(traficar, "enabled", lambda: False)

    assert planner.plan_flow("Start", "Cel", WHEN)["cars"] == []


def test_padniety_feed_to_brak_aut_a_nie_blad(install_day, monkeypatch):
    """Traficar jest dodatkiem. Milczący feed ma zabrać znaczniki, nie
    odpowiedź na pytanie "jak tam dojadę"."""
    install_day(_day())
    monkeypatch.setattr(traficar, "enabled", lambda: True)

    def padnij():
        raise traficar.TraficarDataError("brak sieci")

    monkeypatch.setattr(traficar, "car_list", padnij)

    result = planner.plan_flow("Start", "Cel", WHEN)
    assert result["cars"] == []
    assert result["segments"]
