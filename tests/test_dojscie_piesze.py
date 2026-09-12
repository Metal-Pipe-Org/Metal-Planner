"""Przejście pieszo między RÓŻNYMI przystankami (2026-09-10).

Do tej zmiany pieszo dawało się przejść wyłącznie między słupkami o tej samej
nazwie, więc dwa przystanki po dwóch stronach skrzyżowania były dla
wyszukiwarki punktami niepołączonymi, a stacja kolejowa łączyła się z miastem
tylko tam, gdzie nazwy przypadkiem się pokryły. Tu pilnujemy trzech rzeczy:
że krawędź piesza w ogóle powstaje z ODLEGŁOŚCI, że niesie własny czas
przejścia, i że pasażer dowiaduje się, DOKĄD ma pójść.
"""

from datetime import datetime

import gtfs
import planner
from gtfs_builder import make_day

WHEN = datetime(2026, 1, 5, 0, 0, 0)   # dep_sec = 0, dla czytelnych liczb


# Trzy punkty na jednej linii południka: A-B ok. 111 m, A-C ok. 555 m.
A = (51.1000, 17.0300)
B = (51.1010, 17.0300)
C = (51.1050, 17.0300)


def _o_metrow(od, metrow):
    """Punkt oddalony o `metrow` na północ od `od` - dojście z krańca relacji
    liczy się z ODLEGŁOŚCI (gtfs.walk_reach), więc scenariusz musi ustawić
    słupki naprawdę tam, gdzie mają być, a nie tylko zadeklarować sąsiedztwo."""
    return (od[0] + metrow / 111_320, od[1])


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
    day.stop_coords["START"] = _o_metrow(day.stop_coords["OBOK"], 250)
    day.place_of["START"] = "wojszyce"
    day.stops_by_place["wojszyce"] = ["START"]
    return day


def _dojscie(day):
    """Ile naprawdę trwa dojście START -> OBOK w tym dniu. Liczone z dnia,
    a nie z nominalnych 250 m: _o_metrow przesuwa po samej szerokości, więc
    haversine i tak wyjdzie o kilka metrów inny - a etap ma podać dokładnie
    tę sekundę, którą policzył skan."""
    return gtfs.walk_seconds(day, "START", "OBOK")


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
    assert legs[0]["to"] == "Wrocław Wojszyce"


def test_the_opening_walk_reports_when_to_leave_not_when_the_train_goes():
    """Karta ma podać godzinę WYJŚCIA. Odjazd pierwszego pojazdu zostawiłby
    pasażera kilkaset metrów od peronu dokładnie wtedy, gdy pociąg rusza."""
    day = _day_z_dojsciem_ze_startu(odjazd=600)
    stop, _, journey = planner._scan(day, ["START"], ["CEL"], 0)
    legs = planner._reconstruct(day, journey, stop)
    assert legs[0]["dep_sec"] == 600 - _dojscie(day)


def test_walking_out_of_the_origin_takes_only_one_step():
    """Jeden krok, tak samo jak przy przesiadce - inaczej zasięg startu
    rósłby wielokrotnością promienia."""
    day = _day_z_dojsciem_ze_startu()
    # DALEJ stoi tuż za OBOK, ale ze STARTU jest już poza promieniem dojścia:
    # łańcuchem "przejdź, przejdź" byłby osiągalny, jednym krokiem nie jest.
    day.stop_names["DALEJ"] = "Dalej"
    day.stop_coords["DALEJ"] = _o_metrow(day.stop_coords["OBOK"],
                                         -(gtfs.WALK_M - 100))
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
    day.stop_coords["PERON2"] = _o_metrow(day.stop_coords["START"], 120)
    assert "PERON2" not in planner._origin_walk(day, ["START", "PERON2"])


# ---- dojście pieszo DO celu ---------------------------------------------

def _day_z_celem_za_dojsciem():
    """Kurs dowozi pod STACJA, a celem jest CEL - inny przystanek, trzy
    minuty pieszo stamtąd. Nic nie dojeżdża pod sam CEL."""
    day = make_day([
        {"trip_id": "T1", "label": "Pociąg KD 1",
         "stops": [("START", 0, 0), ("STACJA", 600, 600)]},
    ], names={"STACJA": "Wrocław Główny"})
    day.stop_names["CEL"] = "DWORZEC GŁÓWNY"
    day.stop_coords["CEL"] = _o_metrow(day.stop_coords["STACJA"], 150)
    day.place_of["CEL"] = "dworzec główny"
    day.stops_by_place["dworzec główny"] = ["CEL"]
    return day


def test_the_target_is_reachable_on_foot_from_a_nearby_stop():
    """_target_reach zna nie tylko słupki celu, ale i te o jedno przejście
    od niego - z czasem dojścia i wskazaniem, DO KTÓREGO słupka celu."""
    day = _day_z_celem_za_dojsciem()
    reach = planner._target_reach(day, {"CEL"})
    assert reach["CEL"] == (0, "CEL")
    assert reach["STACJA"] == (gtfs.walk_seconds(day, "STACJA", "CEL"), "CEL")


def test_the_backward_scan_seeds_the_walk_to_the_target():
    """Skan wstecz cofa się POŁĄCZENIAMI, więc bez zasiewu nie wie, że stojąc
    trzy minuty od celu jest się właściwie na miejscu. Skutek był cichy:
    `latest` na takim przystanku brało się z przypadkowego objazdu, a reguła
    cofnięcia kasowała przez to cały dowożący tam kurs."""
    day = _day_z_celem_za_dojsciem()
    deadline = 3600
    latest = planner._backward(day, {"CEL"}, 0, deadline)
    assert latest["CEL"] == deadline
    assert latest["STACJA"] == deadline - gtfs.walk_seconds(day, "STACJA", "CEL"), \
        "dojście do celu nie zasiane"


def test_a_journey_may_END_with_a_walk_to_the_target():
    """Trasa dowożąca pod przystanek obok celu jest gotowa po dojściu - lista
    nie ma doklejać kolejnego przejazdu tylko po to, żeby skończyć na słupku
    celu (patrz _target_reach)."""
    day = _day_z_celem_za_dojsciem()
    stop, arr, journey = planner._scan(day, ["START"], ["CEL"], 0)
    assert stop == "CEL"
    assert arr == 600 + gtfs.walk_seconds(day, "STACJA", "CEL"), \
        "przyjazd liczy się DO CELU, razem z dojściem"
    legs = planner._reconstruct(day, journey, stop)
    assert [leg["kind"] for leg in legs] == ["ride", "walk"]
    assert legs[-1]["to"] == "DWORZEC GŁÓWNY"


# ---- przejście musi coś OTWIERAĆ ----------------------------------------

def _day_z_kursem_przez_start():
    """Kurs staje najpierw na OBOK (pięć minut marszu od startu), a zaraz
    potem na samym STARCIE - dokładnie układ z Wojszyc: 112 jest na
    Parafialnej o 18:13, a na Wojszycach o 18:14."""
    day = make_day([
        {"trip_id": "T1", "label": "Autobus 112",
         "stops": [("OBOK", 780, 780), ("START", 840, 840), ("CEL", 2280, 2280)]},
    ], names={"OBOK": "Parafialna"})
    day.stop_names["START"] = "Wojszyce"
    day.stop_coords["START"] = _o_metrow(day.stop_coords["OBOK"], 250)
    day.place_of["START"] = "wojszyce"
    day.stops_by_place["wojszyce"] = ["START"]
    day.stops_by_key["wojszyce"] = ["START"]       # żeby dało się o nią zapytać z nazwy
    day.display_name["wojszyce"] = "Wojszyce"
    return day


def test_no_walk_to_catch_a_course_that_stops_at_the_origin_anyway():
    """Przejście ma sens tylko wtedy, gdy OTWIERA kurs, którego inaczej nie
    złapiemy. Kurs, który i tak zatrzyma się tam, gdzie stoimy, taki nie jest -
    a skan kazał iść po niego wstecz, bo wcześniejsze wsiadanie widział
    pierwsze. Godzina w celu jest w obu wersjach ta sama, więc marsz był
    czystą stratą (zgłoszone na żywo: Wojszyce -> DWORZEC GŁÓWNY, 18:08)."""
    day = _day_z_kursem_przez_start()
    stop, arr, journey = planner._scan(day, ["START"], ["CEL"], 0)
    assert (stop, arr) == ("CEL", 2280)
    legs = planner._reconstruct(day, journey, stop)
    assert [leg["kind"] for leg in legs] == ["ride"], "marsz po własny autobus"
    assert legs[0]["from"] == "Wojszyce"


def test_the_map_draws_such_a_course_from_the_origin_stop(install_day):
    """To samo na mapie: kurs zatrzymujący się na przystanku startowym ma być
    rysowany OD NIEGO. Inaczej mapa zaczyna się o przystanek wcześniej, obok
    wskazanego startu, a na samym starcie nie ma nawet kropki - bo linia tylko
    tamtędy "przejeżdża" (patrz _transfer_nodes)."""
    day = _day_z_kursem_przez_start()
    install_day(day)
    flow = planner.plan_flow("Wojszyce", "CEL", when=WHEN)
    assert flow["segments"], "mapa pusta"
    start_point = planner._round_path([day.stop_coords["START"]])[0]
    assert all(seg["path"][0] == start_point for seg in flow["segments"]), \
        "kawałek zaczyna się przed przystankiem startowym"
    assert any(node["name"] == "Wojszyce" for node in flow["nodes"]), \
        "brak kropki na przystanku startowym"


# ---- punkt kliknięty na mapie -------------------------------------------

def test_a_point_on_the_map_pays_for_the_walk_like_everyone_else():
    """Kliknięty punkt to kraniec relacji jak każdy inny: słupki w zasięgu są
    osiągalne PIESZO, z czasem liczonym z odległości - a nie dostępne
    natychmiast, jak do 2026-09-12 (1000 m za darmo przy 5 minutach za 350 m
    w środku trasy)."""
    day = _day_z_dojsciem_ze_startu()
    lat, lon = _o_metrow(day.stop_coords["OBOK"], 200)
    z_punktem, punkt = gtfs.with_point(day, lat, lon, "start")
    assert z_punktem.siblings[punkt]["OBOK"] == gtfs.walk_time_sec(200)
    # Most jest dwukierunkowy - do punktu trzeba umieć DOJŚĆ, inaczej skan
    # wstecz i profil celu nie wiedzą, że stojąc obok jest się prawie u celu.
    assert z_punktem.siblings["OBOK"][punkt] == gtfs.walk_time_sec(200)
    assert "OBOK" not in day.siblings.get(punkt, {}), "dzień z cache'u zmieniony"


def _day_z_dwoma_dojsciami():
    """Ten sam kurs staje najpierw na DALEKIM, minutę później na BLISKIM.
    Z klikniętego punktu da się dojść do obu - do BLISKIEGO dwa razy bliżej.
    Układ z Radwanic: APK1 jest na Mickiewicza o 15:00 i na Skrajnej o 15:01,
    a ze wskazanego punktu na Mickiewicza idzie się 14 minut, na Skrajną 7."""
    day = make_day([
        {"trip_id": "T1", "label": "Autobus APK1",
         "stops": [("DALEKI", 900, 900), ("BLISKI", 960, 960), ("CEL", 2400, 2400)]},
    ], names={"DALEKI": "Mickiewicza", "BLISKI": "Skrajna"})
    day.stop_coords["DALEKI"] = _o_metrow(A, 500)
    day.stop_coords["BLISKI"] = _o_metrow(A, 200)
    return day


def test_of_two_stops_on_one_course_the_nearer_one_wins():
    """Wcześniejsza pozycja na trasie kursu nie jest warta ani metra
    nadłożonej drogi: to ten sam pojazd i ta sama godzina w celu. Skan
    wybierał dotąd pierwszy przystanek, na który zdążył - czyli ten dalszy,
    bo kurs mija go wcześniej."""
    day = _day_z_dwoma_dojsciami()
    z_punktem, punkt = gtfs.with_point(day, A[0], A[1], "start")
    stop, arr, journey = planner._scan(z_punktem, [punkt], ["CEL"], 0)
    assert (stop, arr) == ("CEL", 2400)
    legs = planner._reconstruct(z_punktem, journey, stop)
    assert [leg["kind"] for leg in legs] == ["walk", "ride"]
    assert legs[0]["to"] == "Skrajna", "marsz dalej po ten sam autobus"


def test_the_map_anchors_such_a_course_at_the_nearer_stop(install_day):
    """To samo na mapie: kurs ma być rysowany od przystanku, do którego jest
    bliżej — inaczej mapa pokazuje wsiadanie tam, dokąd nikt rozsądny nie
    pójdzie, skoro ten sam autobus zaraz podjeżdża bliżej."""
    day = _day_z_dwoma_dojsciami()
    install_day(day)
    flow = planner.plan_flow("", "CEL", when=WHEN, start_point=A)
    assert flow["segments"], "mapa pusta"
    blisko = planner._round_path([day.stop_coords["BLISKI"]])[0]
    assert all(seg["path"][0] == blisko for seg in flow["segments"])
    assert [n["name"] for n in flow["nodes"]] == ["Skrajna"]


def test_a_point_too_far_from_everything_is_an_honest_error():
    """Punkt bez ani jednego przystanku w promieniu marszu nie jest krańcem
    relacji - lepiej powiedzieć to wprost, niż liczyć trasę znikąd."""
    day = _day_z_dojsciem_ze_startu()
    daleko = _o_metrow(day.stop_coords["OBOK"], 5 * gtfs.WALK_M)
    z_punktem, punkt = gtfs.with_point(day, daleko[0], daleko[1], "start")
    assert z_punktem.siblings[punkt] == {}
