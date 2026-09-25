"""Testy roweru miejskiego NA MAPIE przepływów (punkt 16 kontraktu).

To jest coś innego niż tests/test_bikes.py i tests/test_bike_transfer.py:
tamte pilnują propozycji z etapem rowerowym, te - kropki na mapie.

Rower jest tu przejściem o innym tempie, nie kursem. Kandydatem jest
PRZEJAZD, oceniany w całej podróży trzema liczbami z tej samej drogi: jak
o której jest się przy rowerze, o której w celu i iloma pojazdami. Dojazd do
roweru i dalsza droga po nim idą tym, co mapa RYSUJE. Przejazd NIE musi być
szybszy niż tramwaj - ktoś może chcieć jechać rowerem dlatego, że woli rower.

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
X = (51.129, 17.00)         # słupek, z którego mapa wiezie dalej

# Stacja dokładnie przy M i druga dokładnie przy X - ~1000 m od siebie.
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
    for stop, name, coords in (("X", "Otwarty", X), ("Y", "Daleki", (51.150, 17.00)),
                               ("Z", "Pośredni", (51.140, 17.00))):
        day.stop_names[stop] = name
        day.stop_coords[stop] = coords
    return day


def _feed(monkeypatch, stations=(A, B), loose=()):
    monkeypatch.setattr(bikes, "enabled", lambda: True)
    monkeypatch.setattr(bikes, "stations", lambda: [dict(s) for s in stations])
    monkeypatch.setattr(bikes, "free_bikes", lambda: [dict(b) for b in loose])


# Mapa dowozi do M o 600 jednym pojazdem, a z X wiezie dalej: wsiadając tam
# o 9000, jest się w celu o 9600, też jednym pojazdem.
ARRIVE = {"M": [(600, 1)]}
ONWARD = {"X": [(9000, 9600, 1)]}
TARGETS = {"E"}


def _map(day=None, arrive=ARRIVE, onward=ONWARD, limit=30, live=True, **opcje):
    return bikes.map_places(day or _day(), lambda: arrive, lambda: onward,
                            TARGETS, limit, live=live, **opcje)


# ------------------------------------------ co w ogóle trafia na mapę ----

def test_stacja_z_ktorej_da_sie_dojechac_trafia_na_mape(monkeypatch):
    _feed(monkeypatch)

    places = _map()

    assert [p["id"] for p in places] == ["A"]
    assert [ride["id"] for ride in places[0]["rides"]] == ["B"]
    assert places[0]["from"] == "Środek"
    # Pojazd przed rowerem i pojazd po nim.
    assert _cel(places[0]["rides"][0]) == [{"arrival": 9600, "vehicles": 2}]


def test_kropke_dostaje_tylko_to_na_czym_mozna_SIASC(monkeypatch):
    """Drugi koniec przejazdu własnej kropki nie dostaje - pokazuje się razem
    z kreską. Mapa stawia kropkę tam, gdzie da się WSIĄŚĆ na rower, a nie
    wszędzie, gdzie rower dojedzie."""
    _feed(monkeypatch)

    places = _map()

    assert [p["id"] for p in places] == ["A"]
    assert [ride["id"] for ride in places[0]["rides"]] == ["B"]


def test_ta_sama_stacja_ma_wlasne_przejazdy_choc_jest_czyims_celem(monkeypatch):
    """Gdy komunikacja dowozi do drugiego końca, ten koniec staje się zwykłym
    miejscem do wsiadania - z własną godziną i własnymi przejazdami."""
    _feed(monkeypatch)

    places = _map(arrive={"M": [(600, 1)], "X": [(600, 1)]},
                  onward={**ONWARD, "M": [(9000, 9600, 1)]})
    obie = {p["id"]: p for p in places}

    assert set(obie) == {"A", "B"}
    assert [ride["id"] for ride in obie["B"]["rides"]] == ["A"]


def test_stacja_dalej_niz_jedno_dojscie_nie_istnieje(monkeypatch):
    """Promień jest ten sam, co przy przejściu między przystankami - rower nie
    dostaje własnej, hojniejszej miary (punkt 14: jedna zasada na system)."""
    daleko = {**A, "lat": M[0] + 0.007}      # ~780 m od M, powyżej gtfs.WALK_M
    _feed(monkeypatch, stations=(daleko, B))

    assert _map() == []


def test_przejazd_po_ktorym_sie_nie_zdazy_znika(monkeypatch):
    """Gdy z X trzeba odjechać wcześniej, niż da się tam dojechać rowerem,
    przejazdu nie ma - a bez przejazdu nie ma po co pokazywać stacji."""
    _feed(monkeypatch)

    assert _map(onward={"X": [(700, 800, 1)]}) == []


def test_przystanek_bez_narysowanego_odjazdu_niczego_nie_otwiera(monkeypatch):
    """Liczy się to, co mapa RYSUJE: przystanek, na który da się dojść, ale
    z którego mapa nie pokazuje ani jednego odjazdu, nie jest powodem, żeby
    tam jechać."""
    _feed(monkeypatch)

    assert _map(onward={}) == []


def test_przejazd_nie_musi_byc_szybszy_niz_tramwaj(monkeypatch):
    """Rower zostaje na mapie także wtedy, gdy komunikacja dowozi na ten sam
    słupek dużo WCZEŚNIEJ. To nie jest wyścig - to druga możliwość."""
    _feed(monkeypatch)

    places = _map(arrive={"M": [(600, 1)], "X": [(0, 1)]})
    obie = {p["id"]: p for p in places}

    assert [ride["id"] for ride in obie["A"]["rides"]] == ["B"]


def test_stacja_bez_rowerow_nie_jest_poczatkiem(monkeypatch):
    _feed(monkeypatch, stations=({**A, "bikes": 0}, B))

    assert _map() == []


def test_przejazd_po_progu_mapy_zostaje(monkeypatch):
    """Próg mapy jest progiem kursów z rozkładem, nie rowerów: przejazd, który
    dowozi do celu dopiero po nim, dalej konkuruje swoimi trzema liczbami."""
    _feed(monkeypatch)
    pozno = 10 * 3600

    ride = _map(onward={"X": [(9000, pozno, 1)]})[0]["rides"][0]

    assert _cel(ride) == [{"arrival": pozno, "vehicles": 2}]


# --------------------------------------------- ile to trwa i skąd to wiemy ----

def test_godzina_przy_rowerze_to_dojazd_plus_dojscie(monkeypatch):
    """Marsz liczony tą samą funkcją, co wszędzie indziej."""
    dalej = {**A, "lat": M[0] + 0.003}       # ~334 m od M
    _feed(monkeypatch, stations=(dalej, B))

    place = _map()[0]

    metry = gtfs._haversine_m(*M, dalej["lat"], dalej["lon"])
    assert place["walk_sec"] == gtfs.walk_time_sec(metry)
    assert place["at"] == 600 + place["walk_sec"]


def test_czas_przejazdu_to_narzut_plus_odleglosc(monkeypatch):
    """Jedyna liczba, jaką mapa o rowerze ZGADUJE - jedną prędkością liczoną
    po linii prostej, plus stały narzut na wypożyczenie i oddanie."""
    _feed(monkeypatch)

    ride = _map()[0]["rides"][0]

    metry = gtfs._haversine_m(*M, *X)
    assert ride["m"] == round(metry)
    assert ride["sec"] == bikes.ride_time_sec(metry)
    assert ride["sec"] > bikes.MAP_OVERHEAD_SEC
    assert ride["at"] == 600 + gtfs.WALK_MIN_SEC + ride["sec"]


def test_krotki_przejazd_nie_ma_progu(monkeypatch):
    """Dolnego progu długości nie ma: krótki przejazd konkuruje swoimi trzema
    liczbami jak każdy inny."""
    blisko = {**B, "lat": M[0] + 0.003}      # ~334 m od A
    day = _day()
    day.stop_coords["X"] = (blisko["lat"], blisko["lon"])
    _feed(monkeypatch, stations=(A, blisko))

    assert [ride["m"] for ride in _map(day)[0]["rides"]] == [
        round(bikes.haversine_m(A["lat"], A["lon"], blisko["lat"], blisko["lon"]))]


def test_dlugi_przejazd_nie_ma_sufitu(monkeypatch):
    """Górnego progu długości nie ma: przejazd dłuższy niż pół godziny
    pedałowania zostaje jak każdy inny."""
    daleko = {**B, "lat": M[0] + 0.06, "lon": M[1]}    # ~6,7 km od A
    day = _day()
    day.stop_coords["X"] = (daleko["lat"], daleko["lon"])
    _feed(monkeypatch, stations=(A, daleko))

    assert [ride["m"] for ride in _map(day)[0]["rides"]] == [
        round(bikes.haversine_m(A["lat"], A["lon"], daleko["lat"], daleko["lon"]))]


def test_dojechac_rowerem_pod_sam_cel_to_zero_pojazdow_po_rowerze(monkeypatch):
    """Stacja przy celu kończy podróż dojściem - bez żadnego odjazdu stamtąd."""
    przy_celu = {**B, "lat": E[0], "lon": E[1]}
    _feed(monkeypatch, stations=(A, przy_celu))

    ride = _map(onward={})[0]["rides"][0]

    assert _cel(ride) == [
        {"arrival": ride["at"] + gtfs.WALK_MIN_SEC, "vehicles": 1}]


# ------------------------------------------ uczciwe pary godziny i pojazdów ----

def test_kazda_para_to_jedna_prawdziwa_droga(monkeypatch):
    """Z X da się dojechać szybciej dwoma pojazdami albo później jednym. Obie
    drogi zostają, każda ze swoją liczbą - nie ma pary „szybciej i jednym",
    bo takiej drogi nie ma."""
    _feed(monkeypatch)

    ride = _map(onward={"X": [(9000, 9600, 1), (9000, 9300, 2)]})[0]["rides"][0]

    assert _cel(ride) == [{"arrival": 9300, "vehicles": 3},
                               {"arrival": 9600, "vehicles": 2}]


def test_dojazd_do_roweru_tez_liczy_pojazdy(monkeypatch):
    """Do M da się dojechać wcześniej dwoma pojazdami albo później jednym.
    W celu i tak jest się o tej samej godzinie, więc na stacji zostaje droga
    z mniejszą liczbą pojazdów - godzina przy rowerze mówi o stacji, a nie
    o tym, który przejazd z niej pokazać."""
    _feed(monkeypatch)

    ride = _map(arrive={"M": [(600, 2), (900, 1)]})[0]["rides"][0]

    assert _cel(ride) == [{"arrival": 9600, "vehicles": 2}]


# ------------------------------------------------ które przejazdy pokazać ----

# Druga stacja startowa G ~334 m od M: mapa dowozi do obu tym samym pojazdem
# do M, ale przy A jest się od razu, a do G idzie się jeszcze 8 minut.
# K stoi przy samym starcie - dochodzi się do niej pieszo, bez pojazdu.
G = {**A, "id": "G", "name": "Stacja G", "lat": M[0] - 0.003}
K = {**A, "id": "K", "name": "Stacja K", "lat": S[0]}


def _cel(ride):
    """Opcje przejazdu bez godziny przy rowerze i podglądu - tylko to, o co
    pyta test: o której w celu i iloma pojazdami."""
    return [{"arrival": o["arrival"], "vehicles": o["vehicles"]}
            for o in ride["options"]]


def _przejazdy(places):
    return sorted((p["id"], ride["id"]) for p in places for ride in p["rides"])


def test_rower_osiagalny_wczesniej_bije_pozniejszy(monkeypatch):
    """A->B i G->B łapią ten sam odjazd z X, więc w celu są o tej samej
    godzinie i tymi samymi pojazdami. Przy A jest się wcześniej, więc A bije
    G: rower, do którego najpierw trzeba dojść w bok, niczego tu nie dodaje."""
    _feed(monkeypatch, stations=(A, B, G))

    places = _map(onward={"X": [(9000, 9300, 1)]}, limit=1)

    assert _przejazdy(places) == [("A", "B")]


def test_pozniej_osiagalny_zostaje_gdy_oszczedza_pojazd(monkeypatch):
    """Do K idzie się ze startu pieszo - później niż do A, ale bez pojazdu.
    K->B dowozi o tej samej godzinie co A->B, o jeden pojazd taniej, więc
    każdy z nich jest najlepszy w czymś innym i oba są na mapie."""
    _feed(monkeypatch, stations=(A, B, K))

    places = _map(arrive={"M": [(600, 1)], "S": [(900, 0)]},
                  onward={"X": [(9000, 9300, 1)]}, limit=1)

    assert _przejazdy(places) == [("A", "B"), ("K", "B")]


def test_stacja_po_drodze_zostaje_gdy_jest_najszybsza_przy_swoich_pojazdach(
        monkeypatch):
    """Obawa z 2026-09-25: rower w środku trasy zawsze przegrywa z rowerem
    przy starcie - tamten jest osiągalny wcześniej i bez pojazdu. Ale stacja
    odpada dopiero wtedy, gdy inna daje KAŻDĄ jej podróż co najmniej tak samo
    dobrze. Z K (przy starcie, bez pojazdu) rowerem pod sam cel jest się
    o 2940; z A (po jednym pojeździe) - o 2700. K jest osiągalna wcześniej
    i bez pojazdu, ale podróży tak szybkiej jak z A nie daje, więc A zostaje,
    a z nią obie - każda w czymś najlepsza."""
    day = _day()
    blisko_celu = {**B, "id": "C", "name": "Stacja przy celu", "lat": E[0]}
    _feed(monkeypatch, stations=(A, K, blisko_celu))

    places = _map(day, arrive={"M": [(600, 1)], "S": [(0, 0)]},
                  onward={"X": [(9000, 9300, 1)]}, limit=1)

    assert {p["id"] for p in places} == {"A", "K"}
    assert {p["id"]: p["why"]["beaten"] for p in places} == {"A": 0, "K": 0}


def test_kropka_tylko_przy_wybranym_przejezdzie(monkeypatch):
    """Z G prowadzi przejazd, tylko nie przeszedł wyboru - więc G nie ma
    kropki. Suwak o jeden wyżej wpuszcza kolejny poziom, a z nim G."""
    _feed(monkeypatch, stations=(A, B, G))

    jeden = _map(onward={"X": [(9000, 9300, 1)]}, limit=1)
    dwa = _map(onward={"X": [(9000, 9300, 1)]}, limit=2)

    assert "G" not in {p["id"] for p in jeden}
    assert _przejazdy(dwa) == [("A", "B"), ("G", "B")]


def test_podglad_mowi_dlaczego_rower_jest_na_mapie(monkeypatch):
    """Podgląd pod zębatką (Debug): przy każdej wybranej stacji - w czym
    jest najlepsza, ile stacji ją bije i które."""
    _feed(monkeypatch, stations=(A, B, G))

    places = {p["id"]: p for p in _map(onward={"X": [(9000, 9300, 1)]}, limit=2)}
    a = places["A"]["why"]
    g = places["G"]["why"]

    assert a["beaten"] == 0 and "najwcześniej przy rowerze" in a["records"]
    assert g["beaten"] == 1 and g["beaten_by"] == ["Stacja A"]
    assert "najwcześniej przy rowerze" not in g["records"]


def test_bez_kandydatow_nie_liczy_sie_dalsza_droga(monkeypatch):
    """Dalsza droga to przejście po całej mapie - gdy do żadnego roweru mapa
    nie dowozi, nie odpala się wcale."""
    _feed(monkeypatch)

    def nie_wolno():
        raise AssertionError("dalsza droga liczona bez kandydatów")

    assert bikes.map_places(_day(), lambda: {}, nie_wolno, TARGETS, 30) == []


# --------------------------------------------------- rowery stojące luzem ----

def test_rower_luzem_jest_poczatkiem_przejazdu(monkeypatch):
    """Wypożycza się go tak samo jak ten ze stojaka, więc jako początek liczy
    się na równi ze stacją."""
    luzem = {"id": "L1", "lat": M[0], "lon": M[1], "electric": True}
    _feed(monkeypatch, stations=(B,), loose=(luzem,))

    places = _map()

    assert [p["id"] for p in places] == ["L1"]
    assert places[0]["loose"] is True
    assert places[0]["bikes"] == 1
    assert places[0]["name"] is None


def test_rower_luzem_nie_jest_koncem_przejazdu(monkeypatch):
    """Zostawienie roweru poza stacją kosztuje u operatora osobno i dużo -
    przejazd kończy się wyłącznie na stacji."""
    luzem = {"id": "L2", "lat": X[0], "lon": X[1], "electric": False}
    _feed(monkeypatch, stations=(A,), loose=(luzem,))

    assert _map() == []


# ------------------------------------------- pytanie o inny dzień ----

def test_inny_dzien_zostawia_kropki_ale_bez_rowerow_luzem(monkeypatch):
    """Stacja stoi tam zawsze, więc kropka zostaje. Rower leżący dziś na
    chodniku - nie."""
    luzem = {"id": "L3", "lat": M[0], "lon": M[1], "electric": False}
    _feed(monkeypatch, stations=({**A, "bikes": 0}, B), loose=(luzem,))

    places = _map(live=False)

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


# ------------------------------- dojazd i dalsza droga z narysowanej mapy ----

def _podsluch(monkeypatch):
    """Co planner podaje do wyboru rowerów - policzone już obie połowy."""
    seen = {}

    def spy(day, arrivals, onward, target_set, limit, **opcje):
        seen.update(arrive=arrivals(), onward=onward(), limit=limit, **opcje)
        return []

    monkeypatch.setattr(bikes, "map_places", spy)
    return seen


def test_przesiadki_licza_sie_z_rozkladu_po_narysowanej_mapie(install_day,
                                                            monkeypatch):
    """Tramwaj S -> M i autobus M -> E z przesiadką: do celu dojeżdża się
    dwoma pojazdami, a z M - jednym. Godziny są odczytane z rozkładu."""
    trips = [
        {"trip_id": "T1", "label": "Tramwaj 1",
         "stops": [("S", 0, 0), ("M", 600, 600)]},
        {"trip_id": "B2", "label": "Autobus 2",
         "stops": [("M", 900, 900), ("E", 1500, 1500)]},
    ]
    day = make_day(trips, names={"S": "Start", "M": "Środek", "E": "Cel"})
    day.stop_coords.update({"S": S, "M": M, "E": E})
    install_day(day)
    seen = _podsluch(monkeypatch)

    planner.plan_flow("Start", "Cel", WHEN)

    assert seen["arrive"]["M"] == [(600, 1)]
    assert seen["arrive"]["E"] == [(1500, 2)]
    assert (900, 1500, 1) in seen["onward"]["M"]
    assert (0, 1500, 2) in seen["onward"]["S"]


def test_suwak_i_pokaz_wiecej_mnoza_liczbe_przejazdow(install_day, monkeypatch):
    install_day(_day())
    seen = _podsluch(monkeypatch)

    planner.plan_flow("Start", "Cel", WHEN)
    assert seen["limit"] == planner.DEFAULT_MAP_BIKES == 4

    planner.plan_flow("Start", "Cel", WHEN, bike_count=2, more=1)
    assert seen["limit"] == 4


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


def test_pokaz_wiecej_luzuje_regule_przejazdow_o_poziom():
    """To samo co przy autach (zgłoszenie #141): gdy pierwszy poziom mieści
    się już w suwaku, podniesienie samej liczby nic nie dokłada. Kliknięcie
    schodzi więc o poziom niżej - tu z jednej podróży na dwie."""
    # Trzy stacje, których nic nie bije, i czwarta pobita dokładnie raz.
    pobite = [0, 0, 0, 1]

    assert bikes._skyband(pobite, 3) == {0, 1, 2}
    assert bikes._skyband(pobite, 3, min_level=1) == {0, 1, 2, 3}


# ------------------------------------------- rodzaj roweru (#147) --------

# Dwa miejsca obok siebie: w jednym stoją same elektryki, w drugim same
# zwykłe. Mapa dowozi do obu, więc bez odsiewu widać oba.
ELEKTRYCZNA = {**A, "bikes": 2, "electric": 2}
ZWYKLA = B                                  # 3 rowery, ani jednego elektryka
OBA_STARTY = {"M": [(600, 1)], "X": [(600, 1)]}
OBA_DALEJ = {**ONWARD, "M": [(9000, 9600, 1)]}


def _rodzaje(monkeypatch, **opcje):
    _feed(monkeypatch, stations=(ELEKTRYCZNA, ZWYKLA))
    places = _map(arrive=OBA_STARTY, onward=OBA_DALEJ, **opcje)
    return sorted(place["id"] for place in places)


def test_rodzaj_roweru_to_osobny_wybor(monkeypatch):
    """Zgłoszenie #147: kto chce elektryka, nie weźmie zwykłego. Miejsce
    zostaje na mapie, gdy stoi w nim choć jeden rower włączonego rodzaju -
    oba rodzaje są domyślnie włączone, więc bez ruszania czegokolwiek mapa
    wygląda tak, jak wyglądała."""
    assert _rodzaje(monkeypatch) == ["A", "B"]
    assert _rodzaje(monkeypatch, regular=False) == ["A"]
    assert _rodzaje(monkeypatch, electric=False) == ["B"]


def test_bez_zadnego_rodzaju_roweru_nie_ma_wcale(monkeypatch):
    """Odhaczenie obu rodzajów znaczy to samo, co zgaszony rower: nie ma na
    czym wsiąść."""
    assert _rodzaje(monkeypatch, electric=False, regular=False) == []


def test_przy_nieznanym_stanie_stojakow_rodzaju_nie_zgadujemy(monkeypatch):
    """Pytanie o inny dzień: ile i jakich rowerów tam stoi, wiadomo tylko
    z tej chwili. Odsiew po rodzaju milczy wtedy zamiast udawać wiedzę -
    kropki zostają, a to, że stanu nie znamy, mapa mówi osobno."""
    assert _rodzaje(monkeypatch, live=False, regular=False) == ["A", "B"]


def test_odsiew_rodzaju_dotyczy_wsiadania_nie_oddawania(monkeypatch):
    """Rodzaj odsiewa miejsca, w których się WSIADA. Stacja z samymi
    elektrykami przy odhaczonym elektryku przestaje być początkiem przejazdu,
    ale zostaje jego końcem: oddaje się rower tam, gdzie jest wolny stojak,
    bez względu na to, co w nim akurat stoi."""
    _feed(monkeypatch, stations=(ELEKTRYCZNA, ZWYKLA))

    places = _map(arrive=OBA_STARTY, onward=OBA_DALEJ, electric=False)

    assert [place["id"] for place in places] == ["B"]
    assert [ride["id"] for ride in places[0]["rides"]] == ["A"]
