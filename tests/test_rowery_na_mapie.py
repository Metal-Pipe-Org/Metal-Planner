"""Testy roweru miejskiego NA MAPIE przepływów.

To jest coś innego niż tests/test_bikes.py i tests/test_bike_transfer.py:
tamte pilnują propozycji z etapem rowerowym, te - kropki na mapie.

Rower jest tu przejściem o innym tempie, nie kursem. Mapa mówi o nim dwie
rzeczy: o której da się być PRZY nim (dojazd z rozkładu plus jedno dojście -
punkt 14) i dokąd stąd warto dojechać. "Warto" znaczy dokładnie tyle, co
wszędzie indziej na tej mapie: po zsiadaniu wciąż mieści się w oknie, które
mapa i tak rysuje. NIE znaczy "szybciej niż tramwajem" - ktoś może chcieć
jechać rowerem dlatego, że woli rower.

Kanał GBFS operatora jest tu zawsze podstawiony (patrz tests/conftest.py).
"""

from datetime import date, datetime, time

import bikes
import gtfs
import planner
from tests.gtfs_builder import make_day

# Stan stojaków jest z tej chwili, więc pytamy o dziś - inaczej warstwa
# przełącza się w tryb "stacje stoją, ale nie wiadomo, ile w nich rowerów".
WHEN = datetime.combine(date.today(), time())

# Punkty na jednym południku: 0,001° szerokości to ~111 m.
S = (51.100, 17.00)         # start
M = (51.120, 17.00)         # przystanek w połowie
E = (51.160, 17.00)         # cel
X = (51.129, 17.00)         # słupek, który otwiera przejazd rowerem

# Stacja dokładnie przy M i druga dokładnie przy X - ~1000 m od siebie, czyli
# powyżej dolnego progu przejazdu (bikes.MIN_RIDE_M) i głęboko poniżej sufitu.
A = {"id": "A", "name": "Stacja A", "lat": M[0], "lon": M[1], "bikes": 7,
     "electric": 2, "docks": 9, "renting": True, "returning": True}
B = {"id": "B", "name": "Stacja B", "lat": X[0], "lon": X[1], "bikes": 3,
     "electric": 0, "docks": 12, "renting": True, "returning": True}


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
    day.stop_names["X"] = "Otwarty"
    day.stop_coords["X"] = X
    return day


def _feed(monkeypatch, stations=(A, B), loose=()):
    monkeypatch.setattr(bikes, "enabled", lambda: True)
    monkeypatch.setattr(bikes, "stations", lambda: [dict(s) for s in stations])
    monkeypatch.setattr(bikes, "free_bikes", lambda: [dict(b) for b in loose])


# Mapa dowozi do M o 600, a na X pozwala wsiąść jeszcze długo - czyli przejazd
# A -> B do czegoś prowadzi i ma prawo być na mapie.
REACH = {"M": 600}
BOARD = {"X": 9000, "E": 9000}


# ------------------------------------------ co w ogóle trafia na mapę ----

def test_stacja_z_ktorej_da_sie_dojechac_trafia_na_mape(monkeypatch):
    _feed(monkeypatch)

    places = bikes.map_places(_day(), REACH, BOARD)

    assert [p["id"] for p in places] == ["A"]
    assert [ride["id"] for ride in places[0]["rides"]] == ["B"]
    assert places[0]["from"] == "Środek"


def test_kropke_dostaje_tylko_to_na_czym_mozna_SIASC(monkeypatch):
    """Drugi koniec przejazdu własnej kropki nie dostaje - pokazuje się razem
    ze strzałką, pod kursorem. Mapa stawia kropkę tam, gdzie da się WSIĄŚĆ
    na rower, a nie wszędzie, gdzie rower dojedzie."""
    _feed(monkeypatch)

    places = bikes.map_places(_day(), REACH, BOARD)

    assert [p["id"] for p in places] == ["A"]
    assert [ride["id"] for ride in places[0]["rides"]] == ["B"]


def test_ta_sama_stacja_ma_wlasne_przejazdy_choc_jest_czyims_celem(monkeypatch):
    """Gdy komunikacja dowozi do drugiego końca, ten koniec staje się zwykłym
    miejscem do wsiadania - z własną godziną i własnymi przejazdami."""
    _feed(monkeypatch)

    places = bikes.map_places(_day(), {"M": 600, "X": 600},
                              {**BOARD, "M": 9000})
    obie = {p["id"]: p for p in places}

    assert set(obie) == {"A", "B"}
    assert obie["B"]["at"] is not None
    assert [ride["id"] for ride in obie["B"]["rides"]] == ["A"]


def test_stacja_dalej_niz_jedno_dojscie_nie_istnieje(monkeypatch):
    """Promień jest ten sam, co przy przejściu między przystankami - rower nie
    dostaje własnej, hojniejszej miary (punkt 14: jedna zasada na system)."""
    daleko = {**A, "lat": M[0] + 0.007}      # ~780 m od M, powyżej gtfs.WALK_M

    _feed(monkeypatch, stations=(daleko, B))

    assert bikes.map_places(_day(), REACH, BOARD) == []


def test_przejazd_po_ktorym_sie_nie_zdazy_znika(monkeypatch):
    """Cała reguła sensu w jednym teście: gdy na X trzeba być wcześniej, niż
    da się tam dojechać rowerem, przejazdu nie ma - a bez przejazdu nie ma
    po co pokazywać stacji."""
    _feed(monkeypatch)

    assert bikes.map_places(_day(), REACH, {"X": 700, "E": 700}) == []


def test_przystanek_bez_narysowanego_odjazdu_niczego_nie_otwiera(monkeypatch):
    """Najważniejsza poprawka tej warstwy. Liczy się to, co mapa RYSUJE:
    przystanek, na który da się dojść, ale z którego mapa nie pokazuje ani
    jednego odjazdu, nie jest powodem, żeby tam jechać. Wcześniej brało się
    to ze skanu wstecz, który zna pół miasta - i rower proponował przejazd
    "pod przystanek, z którego nic nie jedzie"."""
    _feed(monkeypatch)

    # X jest tuż przy stacji B i da się tam być na czas - ale mapa nie rysuje
    # stamtąd żadnego odjazdu, więc nie ma go w `board`.
    assert bikes.map_places(_day(), REACH, {"E": 9000}) == []


def test_przejazd_nie_musi_byc_szybszy_niz_tramwaj(monkeypatch):
    """Rower zostaje na mapie także wtedy, gdy komunikacja dowozi na ten sam
    słupek WCZEŚNIEJ. To nie jest wyścig - to druga możliwość."""
    _feed(monkeypatch)
    day = _day()
    at = bikes.map_places(day, REACH, BOARD)[0]["rides"][0]["opens_at"]

    # Mapa jest na X dużo wcześniej, niż byłby tam rowerzysta...
    szybka_mapa = {p["id"]: p
                   for p in bikes.map_places(day, {"M": 600, "X": at - 3600},
                                             BOARD)}

    # ...a przejazd i tak zostaje, bo mieści się w oknie.
    assert [ride["id"] for ride in szybka_mapa["A"]["rides"]] == ["B"]


def test_stacja_bez_rowerow_nie_jest_poczatkiem(monkeypatch):
    _feed(monkeypatch, stations=({**A, "bikes": 0}, B))

    assert bikes.map_places(_day(), REACH, BOARD) == []


# --------------------------------------------- ile to trwa i skąd to wiemy ----

def test_godzina_przy_rowerze_to_dojazd_plus_dojscie(monkeypatch):
    """Marsz liczony tą samą funkcją, co wszędzie indziej."""
    dalej = {**A, "lat": M[0] + 0.003}       # ~334 m od M
    _feed(monkeypatch, stations=(dalej, B))

    place = bikes.map_places(_day(), REACH, BOARD)[0]

    metry = gtfs._haversine_m(*M, dalej["lat"], dalej["lon"])
    assert place["walk_sec"] == gtfs.walk_time_sec(metry)
    assert place["at"] == 600 + place["walk_sec"]


def test_czas_przejazdu_to_narzut_plus_odleglosc(monkeypatch):
    """Jedyna liczba, jaką mapa o rowerze ZGADUJE - jedną prędkością liczoną
    po linii prostej, plus stały narzut na wypożyczenie i oddanie."""
    _feed(monkeypatch)

    ride = bikes.map_places(_day(), REACH, BOARD)[0]["rides"][0]

    metry = gtfs._haversine_m(*M, *X)
    assert ride["m"] == round(metry)
    assert ride["sec"] == bikes.ride_time_sec(metry)
    assert ride["sec"] > bikes.MAP_OVERHEAD_SEC
    assert ride["at"] == 600 + gtfs.WALK_MIN_SEC + ride["sec"]


def test_przejazd_krotszy_niz_prog_odpada(monkeypatch):
    """Poniżej pół kilometra samo wypożyczenie trwa dłużej niż marsz."""
    blisko = {**B, "lat": M[0] + 0.003}      # ~334 m od A
    _feed(monkeypatch, stations=(A, blisko))

    assert bikes.map_places(_day(), REACH, BOARD) == []


def test_przejazd_dluzszy_niz_sufit_odpada(monkeypatch):
    """Pół godziny pedałowania to osobna wycieczka, nie dojazd do tramwaju."""
    daleko = {**B, "lat": M[0] + 0.06, "lon": M[1]}    # ~6,7 km od A
    day = _day()
    day.stop_coords["X"] = (daleko["lat"], daleko["lon"])
    _feed(monkeypatch, stations=(A, daleko))

    assert bikes.map_places(day, REACH, BOARD) == []


def test_otwiera_najblizszy_slupek_na_ktory_sie_zdazy(monkeypatch):
    """Po zsiadaniu idzie się do najbliższego - dalszy, na który też by się
    zdążyło, nie jest przez ten przejazd otwarty bardziej."""
    day = _day()
    day.stop_names["DALEKI"] = "Daleki"
    day.stop_coords["DALEKI"] = (X[0] + 0.004, X[1])     # ~445 m od B
    _feed(monkeypatch)

    ride = bikes.map_places(day, REACH,
                            {**BOARD, "DALEKI": 9000})[0]["rides"][0]

    assert ride["opens"] == "Otwarty"
    assert ride["opens_walk_sec"] == gtfs.WALK_MIN_SEC


def test_pomija_slupek_na_ktory_sie_nie_zdazy(monkeypatch):
    """...ale najbliższy, na który się NIE zdąży, nie zasłania dalszego,
    na który się jeszcze zdąży."""
    day = _day()
    day.stop_names["DALEKI"] = "Daleki"
    day.stop_coords["DALEKI"] = (X[0] + 0.004, X[1])
    _feed(monkeypatch)

    ride = bikes.map_places(day, REACH, {"X": 700, "DALEKI": 9000,
                                         "E": 9000})[0]["rides"][0]

    assert ride["opens"] == "Daleki"


# --------------------------------------------------- rowery stojące luzem ----

def test_rower_luzem_jest_poczatkiem_przejazdu(monkeypatch):
    """Wypożycza się go tak samo jak ten ze stojaka, więc jako początek liczy
    się na równi ze stacją."""
    luzem = {"id": "L1", "lat": M[0], "lon": M[1], "electric": True}
    _feed(monkeypatch, stations=(B,), loose=(luzem,))

    places = bikes.map_places(_day(), REACH, BOARD)

    assert [p["id"] for p in places] == ["L1"]
    assert places[0]["loose"] is True
    assert places[0]["bikes"] == 1
    assert places[0]["name"] is None


def test_rower_luzem_nie_jest_koncem_przejazdu(monkeypatch):
    """Zostawienie roweru poza stacją kosztuje u operatora osobno i dużo -
    przejazd kończy się wyłącznie na stacji."""
    luzem = {"id": "L2", "lat": X[0], "lon": X[1], "electric": False}
    _feed(monkeypatch, stations=(A,), loose=(luzem,))

    assert bikes.map_places(_day(), REACH, BOARD) == []


# ------------------------------------------- pytanie o inny dzień ----

def test_inny_dzien_zostawia_kropki_ale_bez_rowerow_luzem(monkeypatch):
    """Stacja stoi tam zawsze, więc kropka zostaje. Rower leżący dziś na
    chodniku - nie."""
    luzem = {"id": "L3", "lat": M[0], "lon": M[1], "electric": False}
    _feed(monkeypatch, stations=({**A, "bikes": 0}, B), loose=(luzem,))

    places = bikes.map_places(_day(), REACH, BOARD, live=False)

    # Pusty dziś stojak zostaje: "nie wiadomo, ile tam będzie" to nie to samo,
    # co "nie będzie nic". Rower z chodnika znika - jutro go tam nie ma.
    assert [p["id"] for p in places] == ["A"]


def test_plan_flow_mowi_czy_liczby_sa_z_tej_chwili(install_day, monkeypatch):
    install_day(_day())
    _feed(monkeypatch)

    dzis = planner.plan_flow("Start", "Cel", WHEN)
    kiedys = planner.plan_flow("Start", "Cel", datetime(2026, 1, 5, 0, 0, 0))

    assert dzis["bike_places_live"] is True
    assert kiedys["bike_places_live"] is False


# ------------------------------------------------ czego rower nie rusza ----

def test_rower_nie_jest_kursem_na_mapie(install_day, monkeypatch):
    """Kropka roweru niczego w wachlarzu nie przestawia: te same kawałki,
    te same jasności, te same węzły, co bez rowerów."""
    install_day(_day())
    _feed(monkeypatch)
    z_rowerem = planner.plan_flow("Start", "Cel", WHEN)

    monkeypatch.setattr(bikes, "enabled", lambda: False)
    bez_roweru = planner.plan_flow("Start", "Cel", WHEN)

    assert bez_roweru["bike_places"] == []
    assert z_rowerem["segments"] == bez_roweru["segments"]
    assert z_rowerem["nodes"] == bez_roweru["nodes"]


def test_padniety_kanal_to_brak_kropek_a_nie_blad(install_day, monkeypatch):
    """Rower jest dodatkiem. Milczący kanał ma zabrać kropki, nie odpowiedź
    na pytanie "jak tam dojadę"."""
    install_day(_day())
    monkeypatch.setattr(bikes, "enabled", lambda: True)

    def padnij():
        raise OSError("brak sieci")

    monkeypatch.setattr(bikes, "stations", padnij)

    result = planner.plan_flow("Start", "Cel", WHEN)
    assert result["bike_places"] == []
    assert result["segments"]


def test_wylacznik_gasi_rowery_na_mapie(install_day, monkeypatch):
    install_day(_day())
    monkeypatch.setattr(bikes, "enabled", lambda: False)

    assert planner.plan_flow("Start", "Cel", WHEN)["bike_places"] == []


# ------------------------------------------------------- czytanie kanału ----

def test_elektryki_czytamy_z_rodzaju_napedu_a_nie_z_nazwy(monkeypatch):
    """Nazwy modeli ("E-Bike", "e-SMARTbike 2.0 RFID") to marketing operatora
    i lista rośnie z każdą dostawą - rozstrzyga `propulsion_type`."""
    types = {"data": {"vehicle_types": [
        {"vehicle_type_id": "71", "name": "Rower standardowy",
         "propulsion_type": "human"},
        {"vehicle_type_id": "183", "name": "E-Bike",
         "propulsion_type": "electric_assist"},
    ]}}
    info = {"data": {"stations": [
        {"station_id": "1", "name": "Stacja", "lat": 51.1, "lon": 17.0},
    ]}}
    status = {"data": {"stations": [
        {"station_id": "1", "num_bikes_available": 10, "num_docks_available": 6,
         "vehicle_types_available": [{"vehicle_type_id": "71", "count": 9},
                                     {"vehicle_type_id": "183", "count": 1}]},
    ]}}

    station = bikes._stations_from(info, status, bikes._electric_ids(types))[0]

    assert station["bikes"] == 10
    assert station["electric"] == 1


def test_rower_przypisany_do_stacji_nie_jest_luzem(monkeypatch):
    """Kanał wolnych rowerów wylicza TAKŻE te stojące w stojakach. Wpuszczone,
    byłyby na mapie drugi raz - już policzone przez stan stacji."""
    feed = {"data": {"bikes": [
        {"bike_id": "w-stojaku", "lat": 51.1, "lon": 17.0,
         "vehicle_type_id": "71", "station_id": "12497516"},
        {"bike_id": "na-chodniku", "lat": 51.2, "lon": 17.1,
         "vehicle_type_id": "183"},
        {"bike_id": "zepsuty", "lat": 51.2, "lon": 17.1,
         "vehicle_type_id": "71", "is_disabled": True},
    ]}}
    types = {"data": {"vehicle_types": [
        {"vehicle_type_id": "183", "propulsion_type": "electric_assist"},
    ]}}
    monkeypatch.setattr(bikes, "enabled", lambda: True)
    monkeypatch.setattr(bikes, "_cached",
                        lambda key, url, age: feed if key == "free" else types)

    loose = bikes.free_bikes()

    assert [b["id"] for b in loose] == ["na-chodniku"]
    assert loose[0]["electric"] is True
