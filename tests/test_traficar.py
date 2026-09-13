"""Testy propozycji kończących się Traficarem (traficar.py + planner).

Feed fioletowe.live jest tu ZAWSZE podstawiony - żaden z tych testów nie
dotyka sieci (patrz tests/conftest.py, `_traficar_disabled_by_default`).
Sprawdzamy dwie rzeczy osobno:

1. samo szukanie auta (`traficar.car_options`) - które pary "przystanek
   wysiadania + auto" w ogóle są propozycją, a które nie;
2. złożenie tego w trasę (`planner.plan_flow`) - czy godziny etapów
   zazębiają się co do sekundy i czy lista dostaje pozycję z autem, nie
   tracąc żadnej ze swoich.
"""

import datetime

import planner
import traficar
from tests.gtfs_builder import make_day

WHEN = datetime.datetime(2026, 1, 5, 0, 0, 0)   # dep_sec = 0, dla czytelnych liczb

# Trzy punkty na jednym południku, żeby odległości dało się policzyć w głowie:
# 0,01° szerokości to ~1113 m.
S = (51.10, 17.00)          # start
M = (51.12, 17.00)          # węzeł w połowie - przy nim stoi auto
E = (51.16, 17.00)          # cel, ~4,45 km od M

# Auto tuż przy M (~33 m) i drugie, tej samej klasy, przy nim.
CAR = {"lat": 51.1203, "lon": 17.00, "plate": "WE1AA11", "model": "Renault Clio",
       "where": "Wrocław, ul. Testowa", "fuel": 80, "range": 300}


def _day():
    """S -> M szybko (600 s), S -> E w całości wolno (4000 s).

    Auto stoi przy M, więc ma być czym pobić wolny dojazd komunikacją: to
    cały sens tej funkcji - ostatni kawałek, na który nie ma dobrej linii.
    """
    trips = [
        {"trip_id": "SZYBKI", "label": "Tramwaj 1",
         "stops": [("S", 0, 0), ("M", 600, 600)]},
        {"trip_id": "WOLNY", "label": "Autobus 2",
         "stops": [("S", 0, 0), ("E", 4000, 4000)]},
    ]
    day = make_day(trips)
    day.stop_coords.update({"S": S, "M": M, "E": E})
    return day


def _cars(monkeypatch, cars):
    monkeypatch.setattr(traficar, "enabled", lambda: True)
    monkeypatch.setattr(traficar, "car_list", lambda: cars)


# --------------------------------------------------------- samo szukanie ----

def test_auto_przy_przystanku_jest_propozycja(monkeypatch):
    day = _day()
    _cars(monkeypatch, [CAR])

    options = traficar.car_options(day, {"M": 600}, E)

    assert len(options) == 1
    option = options[0]
    assert option["stop"] == "M"
    assert option["car"]["plate"] == "WE1AA11"
    # Dojście krótsze niż minuta liczymy jako minutę (WALK_MIN_SEC).
    assert option["walk_sec"] == traficar.WALK_MIN_SEC
    assert option["start_sec"] == traficar.START_SEC
    # ~4,42 km w linii prostej razy zmierzona krętość (patrz DRIVE_DETOUR).
    assert 5600 < option["drive_m"] < 5900
    assert option["arrival"] == (
        600 + option["walk_sec"] + traficar.START_SEC + option["drive_sec"])


def test_slupek_startowy_nie_jest_wysiadaniem(monkeypatch):
    """`reachable` dostaje wyłącznie słupki, na które komunikacja DOWOZI -
    to planner odsiewa start (patrz _reached_times). Tu pilnujemy drugiej
    strony umowy: pusta mapa dojazdów to pusta lista, a nie wyjątek."""
    _cars(monkeypatch, [CAR])
    assert traficar.car_options(_day(), {}, E) == []


def test_auto_za_daleko_od_przystanku_odpada(monkeypatch):
    day = _day()
    # ~1,1 km na północ od M - dalej niż WALK_TO_CAR_M.
    _cars(monkeypatch, [{**CAR, "lat": 51.13}])
    assert traficar.car_options(day, {"M": 600}, E) == []


def test_auto_pod_samym_celem_nie_ma_czego_zalatwic(monkeypatch):
    """Cel przesunięty tuż obok auta: zostaje kilkaset metrów jazdy, czyli
    mniej, niż trwa samo odpalenie. To nie jest opcja, tylko koszt."""
    day = _day()
    _cars(monkeypatch, [CAR])
    tuz_obok = (51.1250, 17.00)     # ~520 m od auta, poniżej MIN_DRIVE_M
    assert traficar.car_options(day, {"M": 600}, tuz_obok) == []


def test_auto_bez_zasiegu_na_te_trase_odpada(monkeypatch):
    """Feed podaje zasięg w kilometrach i bywa on niski. Auto, które dojedzie
    "na styk", nie jest propozycją - patrz RANGE_RESERVE_M."""
    day = _day()
    _cars(monkeypatch, [{**CAR, "fuel": 4, "range": 6}])   # ~6 km jazdy, zasięg 6 km
    assert traficar.car_options(day, {"M": 600}, E) == []


def test_warianty_tego_samego_to_nie_rozne_propozycje(monkeypatch):
    """Dwa auta na tym samym parkingu przy tym samym przystanku to jedna
    propozycja, nie dwie - inaczej "różne opcje" byłyby listą numerów
    rejestracyjnych."""
    day = _day()
    _cars(monkeypatch, [CAR, {**CAR, "plate": "WE2BB22", "lat": 51.1204}])

    options = traficar.car_options(day, {"M": 600}, E)

    assert len(options) == 1


def test_wylacznik_gasi_propozycje(monkeypatch):
    day = _day()
    _cars(monkeypatch, [CAR])
    monkeypatch.setattr(traficar, "enabled", lambda: False)
    assert traficar.car_options(day, {"M": 600}, E) == []


def test_padniety_feed_to_brak_auta_a_nie_blad(monkeypatch):
    """Traficar jest dodatkiem. Gdy fioletowe.live nie odpowiada, ma zniknąć
    propozycja z autem - nie odpowiedź na pytanie "jak tam dojadę"."""
    day = _day()
    monkeypatch.setattr(traficar, "enabled", lambda: True)

    def padnij():
        raise traficar.TraficarDataError("brak sieci")

    monkeypatch.setattr(traficar, "car_list", padnij)
    assert traficar.car_options(day, {"M": 600}, E) == []


def test_opis_postoju_bez_powtarzania_miasta():
    """Adres z feedu niesie miasto, a cała aplikacja jest o Wrocławiu."""
    assert traficar._spot_label("Wrocław, ul. Łagiewnicka") == "ul. Łagiewnicka"
    # Poza Wrocławiem miasto jest już informacją, nie szumem.
    assert traficar._spot_label("Siechnice, ul. Polna") == "Siechnice, ul. Polna"
    # Samo miasto to nie jest adres - lepszy brak niż "Wrocław", po którym
    # nikt auta nie znajdzie (co z brakiem zrobić, decyduje planner).
    assert traficar._spot_label("Wrocław") == ""
    assert traficar._spot_label(None) == ""


# ------------------------------------------------------ złożona propozycja ----

def _traficar_journey(install_day, monkeypatch, cars=(CAR,)):
    install_day(_day())
    _cars(monkeypatch, list(cars))
    result = planner.plan_flow("S", "E", WHEN)
    assert "error" not in result
    return result


def test_propozycja_z_autem_trafia_na_liste(install_day, monkeypatch):
    result = _traficar_journey(install_day, monkeypatch)

    z_autem = [j for j in result["journeys"] if j.get("traficar")]
    assert len(z_autem) == 1
    kinds = [leg["kind"] for leg in z_autem[0]["legs"]]
    assert kinds == ["ride", "walk", "drive"]
    assert z_autem[0]["legs"][1]["to_car"] is True
    # Zwykłe propozycje zostają na liście - auto jest DODATKOWĄ opcją.
    assert any(not j.get("traficar") for j in result["journeys"])


def test_godziny_etapow_zazebiaja_sie(install_day, monkeypatch):
    result = _traficar_journey(install_day, monkeypatch)
    journey = next(j for j in result["journeys"] if j.get("traficar"))
    ride, walk, drive = journey["legs"]

    # Tramwaj dowozi na M o 600, potem dojście, potem pięć minut na start -
    # i dopiero wtedy auto rusza.
    assert ride["arr_sec"] == 600
    assert drive["dep_sec"] == 600 + walk["minutes"] * 60 + drive["start_min"] * 60
    assert drive["arr_sec"] == drive["dep_sec"] + drive["minutes"] * 60
    assert journey["arrival"] == drive["to_time"]
    assert journey["duration_min"] == round((drive["arr_sec"] - ride["dep_sec"]) / 60)


def test_auto_liczy_sie_jako_zmiana_pojazdu(install_day, monkeypatch):
    """Z tramwaju do auta wysiada się i wsiada tak samo jak z tramwaju do
    autobusu - karta ma to policzyć, choć auto nie ma rozkładu."""
    result = _traficar_journey(install_day, monkeypatch)
    journey = next(j for j in result["journeys"] if j.get("traficar"))
    assert journey["transfers"] == 1


def test_etap_jazdy_niesie_czym_i_ile(install_day, monkeypatch):
    result = _traficar_journey(install_day, monkeypatch)
    drive = next(j for j in result["journeys"] if j.get("traficar"))["legs"][2]

    assert drive["mode"] == "car"          # front bierze z tego kolor plakietki
    assert drive["plate"] == "WE1AA11"
    assert drive["model"] == "Renault Clio"
    assert drive["estimated"] is True      # godziny policzone, nie odczytane
    assert drive["km"] > 0
    assert len(drive["path"]) == 2         # odcinek prosty auto -> cel


def test_bez_auta_lista_jest_dokladnie_taka_jak_byla(install_day, monkeypatch):
    """Włączenie Traficara nie ma prawa przestawić niczego w propozycjach
    komunikacji miejskiej - ma tylko dołożyć swoje."""
    install_day(_day())
    monkeypatch.setattr(traficar, "enabled", lambda: False)
    bez = planner.plan_flow("S", "E", WHEN)["journeys"]

    _cars(monkeypatch, [CAR])
    z_autem = planner.plan_flow("S", "E", WHEN)["journeys"]

    assert [j for j in z_autem if not j.get("traficar")] == bez


def test_prywatne_pola_etapow_nie_wyciekaja(install_day, monkeypatch):
    """Etapy z _reconstruct niosą pola robocze (_trip, _stops_t...), które do
    odpowiedzi nie trafiają - tak samo jak wszędzie indziej."""
    result = _traficar_journey(install_day, monkeypatch)
    journey = next(j for j in result["journeys"] if j.get("traficar"))

    assert not [key for leg in journey["legs"] for key in leg if key.startswith("_")]
