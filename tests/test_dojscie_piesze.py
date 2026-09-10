"""Przejście pieszo między RÓŻNYMI przystankami (2026-09-10).

Do tej zmiany pieszo dawało się przejść wyłącznie między słupkami o tej samej
nazwie, więc dwa przystanki po dwóch stronach skrzyżowania były dla
wyszukiwarki punktami niepołączonymi, a stacja kolejowa łączyła się z miastem
tylko tam, gdzie nazwy przypadkiem się pokryły. Tu pilnujemy trzech rzeczy:
że krawędź piesza w ogóle powstaje z ODLEGŁOŚCI, że niesie własny czas
przejścia, i że pasażer dowiaduje się, DOKĄD ma pójść.
"""

import gtfs
import planner
from gtfs_builder import make_day


# Trzy punkty na jednej linii południka: A-B ok. 111 m, A-C ok. 555 m.
A = (51.1000, 17.0300)
B = (51.1010, 17.0300)
C = (51.1050, 17.0300)


# ---- skąd biorą się krawędzie --------------------------------------------

def test_nearby_stops_are_bridged_regardless_of_name():
    """Sedno zmiany: o krawędzi decyduje odległość, nie nazwa. Słupki
    nazywają się tu zupełnie inaczej i mimo to mają się połączyć."""
    bridges = gtfs._nearby_bridges({"RYNEK": A, "DWORZEC": B}, max_m=300)
    assert bridges["RYNEK"].keys() == {"DWORZEC"}
    assert bridges["DWORZEC"].keys() == {"RYNEK"}


def test_a_stop_beyond_the_radius_is_not_bridged():
    """Promień jest granicą, a nie sugestią - inaczej "przejście pieszo"
    zaczęłoby oznaczać marsz przez pół miasta."""
    bridges = gtfs._nearby_bridges({"RYNEK": A, "DALEKI": C}, max_m=300)
    assert bridges == {}


def test_the_bridge_is_symmetric():
    """Skan wstecz (planner._backward) odejmuje czas przejścia po TEJ SAMEJ
    krawędzi, po której skan w przód go dodaje. Krawędź jednokierunkowa
    rozjechałaby oba skany."""
    bridges = gtfs._nearby_bridges({"A": A, "B": B, "C": C}, max_m=600)
    for stop, neighbors in bridges.items():
        for other, sec in neighbors.items():
            assert bridges[other][stop] == sec


def test_the_grid_finds_neighbours_across_cell_borders():
    """Siatka jest optymalizacją, nie zmianą reguły: wynik ma być taki sam,
    jak przy porównaniu każdego z każdym - także dla pary rozdzielonej
    granicą komórki. Kilkadziesiąt słupków wzdłuż południka przechodzi przez
    wiele granic naraz."""
    coords = {str(i): (51.1 + i * 0.0005, 17.03) for i in range(40)}
    bridges = gtfs._nearby_bridges(coords, max_m=300)
    wprost = {
        s: {
            o for o in coords
            if o != s and gtfs._haversine_m(*coords[s], *coords[o]) <= 300
        }
        for s in coords
    }
    assert {s: set(n) for s, n in bridges.items()} == {
        s: n for s, n in wprost.items() if n
    }


# ---- ile trwa przejście ---------------------------------------------------

def test_walking_time_grows_with_distance():
    """Czas przestał być stałą - to cały powód, dla którego krawędź niesie
    własny koszt zamiast wspólnego WALK_SEC."""
    assert gtfs.walk_time_sec(600) > gtfs.walk_time_sec(300)


def test_a_short_walk_never_costs_less_than_the_floor():
    """Podłoga (patrz gtfs.WALK_MIN_SEC) jest nośna: krawędź piesza jako
    jedyna nie dostaje bufora przesiadki, więc musi go w sobie mieścić."""
    assert gtfs.walk_time_sec(0) == gtfs.WALK_MIN_SEC
    assert gtfs.WALK_MIN_SEC > planner.TRANSFER_SEC


def test_the_cheaper_edge_wins_when_two_providers_disagree():
    """Dostawcy mostów odpowiadają na to samo pytanie, więc przy rozbieżności
    wierzymy temu, który zna krótszą drogę (patrz gtfs._merge_bridges)."""
    merged = gtfs._merge_bridges({"A": {"B": 600}}, {"A": {"B": 240}})
    assert merged == {"A": {"B": 240}}


def test_a_place_stays_walkable_even_below_the_radius():
    """Miejsce ma być spójne pieszo Z DEFINICJI - dwa perony jednego
    przystanku zostają połączone także wtedy, gdy promień marszu jest
    krótszy niż dzielący je dystans."""
    coords = {"PERON_A": A, "PERON_C": C}
    bridges = gtfs._merge_bridges(
        gtfs._walking_bridges([["PERON_A", "PERON_C"]], coords),
        gtfs._nearby_bridges(coords, max_m=100),
    )
    assert "PERON_C" in bridges["PERON_A"]


# ---- co widzi pasażer -----------------------------------------------------

def _day_z_przejsciem_miedzy_przystankami():
    """Dwa niepowiązane kursy; jedyne połączenie między nimi to przejście
    pieszo z PRZYSTANEK_X na STACJA (różne nazwy, różne miejsca)."""
    day = make_day([
        {"trip_id": "T1", "label": "Autobus 1",
         "stops": [("START", 0, 0), ("PRZYSTANEK_X", 600, 600)]},
        {"trip_id": "T2", "label": "Pociąg KD 1",
         "stops": [("STACJA", 1200, 1200), ("CEL", 1800, 1800)]},
    ], names={"PRZYSTANEK_X": "Rynek", "STACJA": "Wrocław Główny"})
    day.siblings = {
        "PRZYSTANEK_X": {"STACJA": 240},
        "STACJA": {"PRZYSTANEK_X": 240},
    }
    return day


def test_a_walk_between_two_different_stops_completes_a_journey():
    """Autobus i pociąg nie mają wspólnego przystanku - łączy je wyłącznie
    przejście pieszo. Dokładnie ta relacja była wcześniej niemożliwa."""
    stop, arr, journey = planner._scan(
        day := _day_z_przejsciem_miedzy_przystankami(), ["START"], ["CEL"], 0)
    assert stop == "CEL"
    assert arr == 1800
    kinds = [leg["kind"] for leg in planner._reconstruct(day, journey, stop)]
    assert kinds == ["ride", "walk", "ride"]


def test_a_walk_to_another_stop_says_where_to_go():
    """"Zmiana stanowiska" to instrukcja wykonalna tylko w obrębie jednego
    przystanku. Przy marszu na inny przystanek bez nazwy celu etap jest do
    niczego - stąd `same_place` i inna treść."""
    day = _day_z_przejsciem_miedzy_przystankami()
    leg = planner._walk_leg(day, "PRZYSTANEK_X", "STACJA")
    assert leg["same_place"] is False
    assert "Wrocław Główny" in leg["text"]
    assert leg["minutes"] == 4


def test_a_platform_change_still_reads_as_a_platform_change():
    """Druga strona tego samego rozróżnienia: w obrębie jednego miejsca
    komunikat ma zostać taki, jaki był."""
    day = make_day([
        {"trip_id": "T1", "label": "Tramwaj 1",
         "stops": [("A", 0, 0), ("B", 60, 60)]},
    ], names={"A": "Rynek", "B": "Rynek"})
    leg = planner._walk_leg(day, "A", "B")
    assert leg["same_place"] is True
    assert "stanowisk" in leg["text"]


# ---- "byłem tu" to co innego niż "dojdę tu" -------------------------------

def test_having_been_somewhere_is_measured_by_place_not_by_walking_range():
    """Reguła zawracania (planner._leads_onward) pyta, czy kurs wraca na
    przystanek JUŻ MINIĘTY. Rozwijana zasięgiem marszu uznawała za minięte
    wszystko w promieniu od trasy i kasowała z mapy dobre kontynuacje -
    stąd osobne _same_place_stops obok _sibling_places."""
    day = make_day([
        {"trip_id": "T1", "label": "Tramwaj 1",
         "stops": [("PERON_A", 0, 0), ("OBOK", 60, 60)]},
    ], names={"PERON_A": "Rynek", "OBOK": "Kwiska"})
    day.siblings = {"PERON_A": {"OBOK": 180}, "OBOK": {"PERON_A": 180}}

    assert "OBOK" in planner._sibling_places(day, "PERON_A")
    assert "OBOK" not in planner._same_place_stops(day, "PERON_A")


# ---- wyjście pieszo ze STARTU -------------------------------------------

def _day_z_dojsciem_ze_startu(odjazd=600):
    """Ze START-u nie odjeżdża NIC. Jedyny kurs rusza z OBOK, oddalonego
    o cztery minuty marszu - trzeba tam najpierw dojść."""
    day = make_day([
        {"trip_id": "T1", "label": "Pociąg KD 1",
         "stops": [("OBOK", odjazd, odjazd), ("CEL", odjazd + 600, odjazd + 600)]},
    ], names={"OBOK": "Wrocław Wojszyce"})
    day.stop_names["START"] = "Wojszyce"
    day.stop_coords["START"] = (51.1, 17.03)
    day.place_of["START"] = "wojszyce"
    day.stops_by_place["wojszyce"] = ["START"]
    day.siblings = {"START": {"OBOK": 240}, "OBOK": {"START": 240}}
    return day


def test_a_ride_reachable_only_by_walking_from_the_origin_is_found():
    """Sedno drugiej połowy zmiany. Chodzenie było wyłącznie przesiadką -
    relaksowało się tylko po WYSIADANIU - więc z przystanku startowego nie
    dawało się nigdzie wyjść i taki kurs był niewidoczny."""
    day = _day_z_dojsciem_ze_startu()
    stop, arr, journey = planner._scan(day, ["START"], ["CEL"], 0)
    assert stop == "CEL"
    assert arr == 1200
    legs = planner._reconstruct(day, journey, stop)
    assert [leg["kind"] for leg in legs] == ["walk", "ride"]


def test_the_opening_walk_reports_when_to_leave_not_when_the_train_goes():
    """Karta ma podać godzinę WYJŚCIA. Odjazd pierwszego pojazdu zostawiłby
    pasażera kilkaset metrów od peronu dokładnie wtedy, gdy pociąg rusza."""
    day = _day_z_dojsciem_ze_startu(odjazd=600)
    stop, _, journey = planner._scan(day, ["START"], ["CEL"], 0)
    legs = planner._reconstruct(day, journey, stop)
    assert legs[0]["dep_sec"] == 600 - 240


def test_walking_out_of_the_origin_takes_only_one_step():
    """Jeden krok, tak samo jak przy przesiadce - inaczej zasięg startu
    rósłby wielokrotnością promienia."""
    day = _day_z_dojsciem_ze_startu()
    day.stop_names["DALEJ"] = "Dalej"
    day.stop_coords["DALEJ"] = (51.102, 17.03)
    day.siblings["OBOK"]["DALEJ"] = 240
    day.siblings["DALEJ"] = {"OBOK": 240}
    assert set(planner._origin_walk(day, ["START"])) == {"OBOK"}


def test_just_walking_there_is_never_a_proposed_journey():
    """Ta wyszukiwarka planuje PRZEJAZDY. Poza tym trasa bez ani jednego
    przejazdu nie ma godziny wyjazdu, na której opiera się okno mapy."""
    day = _day_z_dojsciem_ze_startu()
    stop, _arr, _journey = planner._scan(day, ["START"], ["OBOK"], 0)
    assert stop is None, "samo dojście pieszo ogłoszone jako trasa"


def test_the_origin_itself_is_never_delayed_by_a_walk():
    """Na słupkach startowych stoi się od razu - całe miejsce jest startem
    naraz, więc dokładanie im czasu przejścia mogłoby tylko opóźnić wyjazd."""
    day = _day_z_dojsciem_ze_startu()
    day.stop_names["PERON2"] = "Wojszyce"
    day.stop_coords["PERON2"] = (51.1001, 17.03)
    day.siblings["START"]["PERON2"] = 180
    day.siblings["PERON2"] = {"START": 180}
    assert "PERON2" not in planner._origin_walk(day, ["START", "PERON2"])
