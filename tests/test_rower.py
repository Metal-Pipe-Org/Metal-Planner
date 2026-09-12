"""Testy warstwy rowerowej (bikes.py + sekcja ROWER MIEJSKI w planner.py).

Bez sieci i bez SQLite: stacje wstrzykujemy zamiast `bikes.stations_quiet`,
rozkład buduje tests/gtfs_builder tak jak w pozostałych testach. Chodzi
o zachowanie ALGORYTMU, nie o to, co akurat stoi w stojakach we Wrocławiu.

Cztery rzeczy, które muszą być prawdą:

1. rower wolno wstawić w DOWOLNE miejsce trasy - na początek, na koniec,
   w środek i jako całą trasę;
2. czas etapu to dojście + odblokowanie + jazda + zwrot + dojście, i to
   wszystko widać w godzinach na karcie, a nie tylko w opisie;
3. rower, który niczego nie wygrywa, nie trafia na listę;
4. cokolwiek się stanie z cudzym serwerem, zwykłe wyszukiwanie ma działać
   tak samo jak bez tej warstwy.
"""

import datetime

import bikes
import planner
import pytest
from tests.gtfs_builder import make_day

WHEN = datetime.datetime(2026, 1, 5, 0, 0, 0)   # dep_sec = 0, dla czytelnych liczb
SZEROKIE_OKNO = dict(extra_pct=200, extra_floor_sec=0, extra_cap_sec=999999)

# Współrzędne trzymamy w jednej kolumnie (ten sam południk), więc odległość
# to wprost różnica szerokości: 0,001° ≈ 111 m.
LON = 17.0


def _stacja(sid, name, lat, bikes_n=5, docks_n=5, renting=True, returning=True):
    return {"id": sid, "name": name, "lat": lat, "lon": LON,
            "bikes": bikes_n, "docks": docks_n,
            "renting": renting, "returning": returning}


@pytest.fixture(autouse=True)
def _dzien_pytania_jest_dzisiaj(monkeypatch):
    """Rower liczy się tylko przy pytaniu o DZIŚ (stan stojaków jest żywy,
    nie rozkładowy - patrz planner.plan_flow). Testy pytają o stałą datę,
    więc to ona musi być „dzisiaj" - inaczej cała warstwa milczałaby z
    powodu, którego żaden z tych testów nie bada."""
    class _Dzis(datetime.date):
        @classmethod
        def today(cls):
            return WHEN.date()
    monkeypatch.setattr(planner, "date", _Dzis)


@pytest.fixture
def install_stations(monkeypatch):
    """install_stations([...]) podstawia listę stacji zamiast kanału GBFS."""
    def _install(stations):
        monkeypatch.setattr(bikes, "stations_quiet", lambda: list(stations))
    return _install


def _siec_z_luka():
    """S --(1)--> M ... luka ... P --(2)--> E, plus wolny objazd M --(3)--> P.

    Rozkład jest tak ułożony, że objazd linią 3 (na P dopiero o 1800) gubi
    wcześniejszy kurs linii 2 i podróż kończy się o 2700. Rower z okolicy M
    do okolicy P tę lukę przeskakuje - na P o 1440, czyli z zapasem na kurs
    o 1600, czyli przyjazd o 1900.
    """
    trips = [
        {"trip_id": "T1", "label": "Tramwaj 1", "stops": [("S", 0, 0), ("M", 600, 600)]},
        {"trip_id": "T3", "label": "Autobus 3", "stops": [("M", 700, 800), ("P", 1800, 1800)]},
        {"trip_id": "T2a", "label": "Tramwaj 2", "stops": [("P", 1600, 1600), ("E", 1900, 1900)]},
        {"trip_id": "T2b", "label": "Tramwaj 2", "stops": [("P", 2400, 2400), ("E", 2700, 2700)]},
    ]
    day = make_day(trips)
    day.stop_coords.update({
        "S": (51.100, LON),
        "M": (51.110, LON),
        "P": (51.120, LON),     # 1113 m od M - za daleko, żeby dojść pieszo
        "E": (51.130, LON),
    })
    return day


# Stacje po 100 m od węzłów M i P; między sobą 913 m, czyli powyżej progu
# bikes.MIN_RIDE_M (poniżej niego szybciej jest po prostu przejść).
STACJA_M = _stacja("a", "Stacja przy M", 51.1109)
STACJA_P = _stacja("b", "Stacja przy P", 51.1191)


def _propozycja_z_rowerem(wynik):
    for journey in wynik["journeys"]:
        if any(leg["kind"] == "bike" for leg in journey["legs"]):
            return journey
    return None


def _kinds(journey):
    return [leg["kind"] for leg in journey["legs"]]


# --------------------------------------------------- rower w dowolnym miejscu ----

def test_rower_w_srodku_trasy_przeskakuje_luke(install_day, install_stations):
    """Sedno całej funkcji: rower stoi POŚRODKU, między dwoma przejazdami.

    Nie ma tu żadnego osobnego algorytmu "trasa z rowerem w środku" - wychodzi
    to ze złożenia skanu w przód (dojazd do M) z profilem dojazdu do celu
    (co da się złapać z P), patrz planner._bike_candidates.
    """
    install_day(_siec_z_luka())
    install_stations([STACJA_M, STACJA_P])
    wynik = planner.plan_flow("S", "E", when=WHEN, use_bikes=True, **SZEROKIE_OKNO)

    rower = _propozycja_z_rowerem(wynik)
    assert rower is not None, "rower miał przeskoczyć lukę M -> P"
    assert _kinds(rower) == ["ride", "walk", "bike", "walk", "ride"]
    assert rower["arrival_sec"] == 1900
    # ...i ma stać PRZED trasą bez roweru, która dojeżdża dopiero o 2700.
    assert wynik["journeys"][0] is rower
    assert wynik["bikes"] == {"journeys": 1, "stations": 2, "live": True}


def test_rower_na_poczatku_i_na_koncu(install_day, install_stations):
    """Ten sam mechanizm z jednym końcem relacji zamiast przystanku."""
    day = _siec_z_luka()
    install_day(day)

    # Na KOŃCU: relacja S -> P, stacja tuż przy celu. Rower z okolicy M
    # dowozi pod sam P na 1440, więc objazd linią 3 (na miejscu o 1800)
    # przestaje być potrzebny.
    install_stations([STACJA_M, STACJA_P])
    wynik = planner.plan_flow("S", "P", when=WHEN, use_bikes=True, **SZEROKIE_OKNO)
    rower = _propozycja_z_rowerem(wynik)
    assert rower is not None
    assert _kinds(rower) == ["ride", "walk", "bike", "walk"]

    # Na POCZĄTKU: relacja M -> E. Do stacji przy M dochodzi się wprost
    # z punktu startu, bez żadnego przejazdu przed rowerem.
    wynik = planner.plan_flow("M", "E", when=WHEN, use_bikes=True, **SZEROKIE_OKNO)
    rower = _propozycja_z_rowerem(wynik)
    assert rower is not None
    assert _kinds(rower) == ["walk", "bike", "walk", "ride"]


def test_sam_rower_bez_zadnego_przejazdu(install_day, install_stations):
    """Relacja M -> P: rozkład oferuje tylko wolny objazd (na miejscu o 1800),
    rower dowozi na 840. Wychodzi trasa złożona z samego dojścia i przejazdu."""
    install_day(_siec_z_luka())
    install_stations([STACJA_M, STACJA_P])
    wynik = planner.plan_flow("M", "P", when=WHEN, use_bikes=True, **SZEROKIE_OKNO)

    rower = _propozycja_z_rowerem(wynik)
    assert rower is not None
    assert _kinds(rower) == ["walk", "bike", "walk"]
    assert rower["arrival_sec"] == 840


# ------------------------------------------------------------- czas i marginesy ----

def test_godziny_skladaja_sie_z_dojscia_odblokowania_jazdy_i_zwrotu(
        install_day, install_stations):
    """Każdy składnik jest w godzinach, nie tylko w opisie.

    M o 600 -> dojście 120 s -> odblokowanie 180 s -> jazda 360 s ->
    zwrot 60 s -> dojście 120 s = 1440 na przystanku P.
    """
    install_day(_siec_z_luka())
    install_stations([STACJA_M, STACJA_P])
    wynik = planner.plan_flow("S", "E", when=WHEN, use_bikes=True, **SZEROKIE_OKNO)
    legs = _propozycja_z_rowerem(wynik)["legs"]
    dojscie, przejazd, zejscie = legs[1], legs[2], legs[3]

    assert dojscie["dep_sec"] == 600 and dojscie["minutes"] == 2
    # Etap zaczyna się PO dojściu, a kończy po zwrocie roweru.
    assert przejazd["dep_sec"] == 720
    assert przejazd["arr_sec"] == 720 + bikes.UNLOCK_SEC + 360 + bikes.DOCK_SEC
    assert zejscie["dep_sec"] == przejazd["arr_sec"]
    # Trzy części sumują się dokładnie do długości etapu - liczby stoją na
    # karcie obok siebie i nie mogą się nie zgadzać.
    assert (przejazd["unlock_minutes"] + przejazd["ride_minutes"]
            + przejazd["dock_minutes"]) == przejazd["minutes"]
    assert przejazd["unlock_minutes"] == bikes.UNLOCK_SEC // 60


def test_margines_na_odblokowanie_naprawde_przesuwa_godzine(
        install_day, install_stations, monkeypatch):
    """Podniesienie marginesu gubi kurs o 1600 - i propozycja ma to pokazać,
    a nie udawać, że zdąży. To jedyny etap trasy, na którym pasażer stoi
    przed maszyną, więc margines musi być realnym czasem, nie ozdobą."""
    install_day(_siec_z_luka())
    install_stations([STACJA_M, STACJA_P])
    # 1440 + TRANSFER_SEC = 1560, czyli kurs o 1600 jest do złapania z zapasem
    # 40 s. Margines dłuższy o minutę ten zapas kasuje.
    monkeypatch.setattr(bikes, "UNLOCK_SEC", bikes.UNLOCK_SEC + 60)
    wynik = planner.plan_flow("S", "E", when=WHEN, use_bikes=True, **SZEROKIE_OKNO)

    # Kurs o 1600 przepadł, zostaje ten o 2400 - i tę PRAWDZIWĄ godzinę
    # pokazuje karta, zamiast obiecywać 1900, na które już się nie zdąży.
    assert _propozycja_z_rowerem(wynik)["arrival_sec"] == 2700


def test_za_krotki_przejazd_nie_jest_proponowany(install_day, install_stations):
    """Poniżej bikes.MIN_RIDE_M odblokowanie i zwrot zjadają całą oszczędność -
    szybciej jest przejść, więc takiej pary stacji w ogóle nie rozważamy."""
    install_day(_siec_z_luka())
    blisko = _stacja("c", "Stacja tuż obok", 51.1112)   # 33 m od stacji przy M
    install_stations([STACJA_M, blisko])
    wynik = planner.plan_flow("S", "E", when=WHEN, use_bikes=True, **SZEROKIE_OKNO)

    assert _propozycja_z_rowerem(wynik) is None


# ------------------------------------------------------------- stan stojaków ----

def test_pusta_stacja_i_pelna_stacja_sa_pomijane(install_day, install_stations):
    """Stacja bez rowerów nie jest miejscem, z którego da się wyjechać,
    a stacja bez wolnego miejsca - takim, w którym da się rower oddać."""
    install_day(_siec_z_luka())

    install_stations([_stacja("a", "Pusta", 51.1109, bikes_n=0), STACJA_P])
    assert _propozycja_z_rowerem(
        planner.plan_flow("S", "E", when=WHEN, use_bikes=True, **SZEROKIE_OKNO)) is None

    install_stations([STACJA_M, _stacja("b", "Pełna", 51.1191, docks_n=0)])
    assert _propozycja_z_rowerem(
        planner.plan_flow("S", "E", when=WHEN, use_bikes=True, **SZEROKIE_OKNO)) is None

    # Stacja zdjęta z ulicy (is_renting=false) tak samo - rowery w niej stoją,
    # ale nikt ich stamtąd nie wypożyczy.
    install_stations([_stacja("a", "Wyłączona", 51.1109, renting=False), STACJA_P])
    assert _propozycja_z_rowerem(
        planner.plan_flow("S", "E", when=WHEN, use_bikes=True, **SZEROKIE_OKNO)) is None


def test_propozycja_niesie_stan_obu_stojakow(install_day, install_stations):
    """„6 rowerów / 3 wolne miejsca" na karcie - bez tego propozycja każe iść
    pod stojak w ciemno."""
    install_day(_siec_z_luka())
    install_stations([_stacja("a", "Stacja przy M", 51.1109, bikes_n=6),
                      _stacja("b", "Stacja przy P", 51.1191, docks_n=3)])
    wynik = planner.plan_flow("S", "E", when=WHEN, use_bikes=True, **SZEROKIE_OKNO)
    przejazd = next(leg for leg in _propozycja_z_rowerem(wynik)["legs"]
                    if leg["kind"] == "bike")

    assert przejazd["bikes_available"] == 6
    assert przejazd["docks_available"] == 3
    assert przejazd["from"] == "Stacja przy M"
    assert przejazd["to"] == "Stacja przy P"


# --------------------------------------------- rower nie psuje reszty aplikacji ----

def test_bez_wlacznika_odpowiedz_jest_identyczna(install_day, install_stations):
    """Rower jest wyborem pasażera: nieproszony nie zmienia w odpowiedzi
    dosłownie nic - ani listy, ani mapy, ani jednego pola."""
    install_day(_siec_z_luka())
    install_stations([STACJA_M, STACJA_P])

    bez = planner.plan_flow("S", "E", when=WHEN, **SZEROKIE_OKNO)
    assert "bikes" not in bez
    assert all(leg["kind"] != "bike"
               for journey in bez["journeys"] for leg in journey["legs"])


def test_awaria_kanalu_gbfs_nie_psuje_wyszukiwania(install_day, install_stations):
    """Cudzy serwer nie odpowiada -> lista tras jest dokładnie taka jak bez
    warstwy rowerowej, a nie komunikat o błędzie."""
    install_day(_siec_z_luka())
    install_stations([])      # tyle oddaje bikes.stations_quiet po awarii

    bez = planner.plan_flow("S", "E", when=WHEN, **SZEROKIE_OKNO)
    z_rowerem = planner.plan_flow("S", "E", when=WHEN, use_bikes=True, **SZEROKIE_OKNO)

    assert z_rowerem["bikes"] == {"journeys": 0, "stations": 0, "live": True}
    assert z_rowerem["journeys"] == bez["journeys"]
    assert z_rowerem["segments"] == bez["segments"]


def _siec_z_szybkim_tramwajem():
    """Bezpośredni tramwaj S -> E o 900; rowerem z okolicy M ta sama trasa
    zajmuje do 1440, czyli jest GORSZA, ale wciąż sensowna."""
    trips = [
        {"trip_id": "T1", "label": "Tramwaj 1", "stops": [("S", 0, 0), ("E", 900, 900)]},
        {"trip_id": "T2", "label": "Autobus 2", "stops": [("S", 0, 0), ("M", 600, 600)]},
    ]
    day = make_day(trips)
    day.stop_coords.update({"S": (51.100, LON), "M": (51.110, LON), "E": (51.120, LON)})
    return day


def test_gorszy_rower_zostaje_na_liscie_ale_na_dole(install_day, install_stations):
    """Rower podlega tej samej regule co reszta listy: mieści się w oknie
    czasowym mapy - jest, i staje tam, gdzie mu wypada.

    Osobnego progu („rower wchodzi tylko wtedy, gdy WYGRYWA") tu świadomie
    nie ma: zgoda na koszt roweru padła już przy odhaczeniu 🚲, a próg
    sprawdzany na danych zmieniających się co minutę (stan stojaków) dawałby
    funkcję, która przy dwóch wyszukaniach tej samej relacji raz pokazuje
    rower, a raz nie. Patrz planner._merge_journeys."""
    install_day(_siec_z_szybkim_tramwajem())
    install_stations([STACJA_M, _stacja("b", "Stacja przy E", 51.1191)])

    wynik = planner.plan_flow("S", "E", when=WHEN, use_bikes=True, **SZEROKIE_OKNO)

    rower = _propozycja_z_rowerem(wynik)
    assert rower is not None and rower["arrival_sec"] == 1440
    assert wynik["journeys"][0]["arrival_sec"] == 900      # tramwaj dalej pierwszy
    assert wynik["journeys"][-1] is rower                  # rower na końcu
    assert wynik["bikes"]["journeys"] == 1


def test_rower_poza_oknem_czasowym_nie_wchodzi(install_day, install_stations):
    """Jedyna granica, jaka roweru dotyczy, to okno czasowe mapy - to samo,
    które odsiewa zbyt wolne objazdy komunikacją."""
    install_day(_siec_z_szybkim_tramwajem())
    install_stations([STACJA_M, _stacja("b", "Stacja przy E", 51.1191)])

    # Okno = 900 + 10% = 990 s, a rowerem jest się dopiero o 1440.
    wynik = planner.plan_flow("S", "E", when=WHEN, use_bikes=True,
                              extra_pct=110, extra_floor_sec=0, extra_cap_sec=600)

    assert _propozycja_z_rowerem(wynik) is None
    assert wynik["bikes"]["journeys"] == 0
    # Same stacje były - to nie jest ten sam przypadek co awaria kanału.
    assert wynik["bikes"]["stations"] == 2


def test_pytanie_o_inna_dobe_nie_dostaje_roweru(install_day, install_stations):
    """Stan stojaków mówi, ile rowerów stoi TERAZ - nie ile będzie stało
    jutro. Propozycja z rowerem na inny dzień byłaby zgadywaniem podanym
    jako fakt, więc jej nie ma; `live` mówi, że to inny powód niż milczący
    kanał operatora."""
    install_day(_siec_z_luka())
    install_stations([STACJA_M, STACJA_P])
    jutro = WHEN + datetime.timedelta(days=1)

    wynik = planner.plan_flow("S", "E", when=jutro, use_bikes=True, **SZEROKIE_OKNO)

    assert wynik["bikes"] == {"journeys": 0, "stations": 0, "live": False}
    assert _propozycja_z_rowerem(wynik) is None


# ------------------------------------------------------------------ bikes.py ----

def test_czasy_sa_w_pelnych_minutach_i_zaokraglane_w_gore():
    """Godziny na karcie mają dokładność minuty, więc czasy etapów też -
    inaczej „13:03 przyjazd, 1 min dojścia, 13:03 odjazd". W górę, bo każde
    z tych zaokrągleń jest marginesem (patrz bikes._whole_minutes)."""
    for metry in (1, 100, 300, 1200, 5000):
        assert bikes.walk_sec(metry) % 60 == 0
        assert bikes.ride_sec(metry) % 60 == 0
    assert bikes.walk_sec(1) == 60          # minimum to jedna minuta
    assert bikes.UNLOCK_SEC % 60 == 0 and bikes.DOCK_SEC % 60 == 0


def test_sufit_przejazdu_stoi_na_czasie_pedalowania():
    """MAX_RIDE_M jest wyliczony z MAX_RIDE_SEC, a nie wpisany osobno -
    inaczej zmiana prędkości roweru rozjechałaby jedno z drugim."""
    assert bikes.ride_sec(bikes.MAX_RIDE_M) == bikes.MAX_RIDE_SEC


def test_stacje_bez_pary_w_drugim_kanale_wypadaja():
    """station_information i station_status są dwoma osobnymi plikami i nie
    muszą być zgodne co do minuty. Stacja bez stanu to stacja, o której nie
    wiadomo, czy stoi w niej rower."""
    info = {"data": {"stations": [
        {"station_id": "1", "name": "Z pełną parą", "lat": 51.1, "lon": 17.0},
        {"station_id": "2", "name": "Bez stanu", "lat": 51.2, "lon": 17.0},
        {"station_id": "3", "name": "Bez współrzędnych", "lat": None, "lon": 17.0},
    ]}}
    status = {"data": {"stations": [
        {"station_id": "1", "num_bikes_available": 4, "num_docks_available": 7},
        {"station_id": "3", "num_bikes_available": 1, "num_docks_available": 1},
    ]}}
    stacje = bikes._stations_from(info, status)

    assert [s["id"] for s in stacje] == ["1"]
    assert stacje[0] == {"id": "1", "name": "Z pełną parą", "lat": 51.1, "lon": 17.0,
                         "bikes": 4, "electric": 0, "docks": 7,
                         "renting": True, "returning": True}


def test_stations_quiet_nie_rzuca_przy_awarii_sieci(monkeypatch):
    def boom():
        raise OSError("kanał nie odpowiada")
    monkeypatch.setattr(bikes, "stations", boom)

    assert bikes.stations_quiet() == []


def test_wylacznik_srodowiskowy(monkeypatch):
    """WRM_ENABLED=off wyłącza pytanie cudzego serwera w ogóle - bez niego
    nie dałoby się uruchomić aplikacji w sieci bez wyjścia na zewnątrz."""
    monkeypatch.setenv("WRM_ENABLED", "off")
    assert not bikes.enabled()
    assert bikes.stations() == []
    monkeypatch.setenv("WRM_ENABLED", "on")
    assert bikes.enabled()
