"""Testy "kontraktu mapy przepływów" - listy gwarancji zachowania mapy
uzgodnionej z użytkownikiem 2026-08-11 (patrz docs/FLOW_MAP_CONTRACT.md dla
pełnej listy punktów, docs/ROUTING_ALGORITHM.md dla opisu samego algorytmu).
Dotyczy WYŁĄCZNIE rysowania mapy (plan_flow / segments) - lista propozycji
tras (journeys) jest świadomie poza zakresem.

Sekcje odpowiadają numeracji punktów kontraktu; który test pilnuje którego
punktu: docs/FLOW_MAP_NOTES.md, sekcja "Testy".
"""

import datetime
import sqlite3

import gtfs
import planner
from tests.gtfs_builder import make_day

WHEN = datetime.datetime(2026, 1, 5, 0, 0, 0)   # dep_sec = 0, dla czytelnych liczb


def _coords_of(day, stop_ids):
    return [[round(lat, 5), round(lon, 5)] for lat, lon in
            (day.stop_coords[s] for s in stop_ids)]


def _segs_by_num(result, num, kind):
    return [s for s in result["segments"] if s["num"] == num and s["kind"] == kind]


# --------------------------------------------------------------------- 2 ---

def _narysowane(*przebiegi):
    """Narysowane kursy dla _corridor_km: każdy przebieg w całości."""
    kept = [{"stops": list(p)} for p in przebiegi]
    return kept, {id(seg): (0, len(seg["stops"])) for seg in kept}


def test_lines_lying_on_each_other_count_once_towards_density():
    """Dwadzieścia numerów jednym korytarzem to w oku jedna kreska - druga
    linia tymi samymi przystankami nie dokłada ani metra, inny korytarz tak."""
    day = make_day([
        {"trip_id": "a", "label": "Tramwaj 1",
         "stops": [("S", 0, 0), ("M", 60, 60), ("E", 120, 120)]},
        {"trip_id": "b", "label": "Tramwaj 2",
         "stops": [("S", 0, 0), ("X", 60, 60), ("E", 120, 120)]},
    ])
    jedna = planner._corridor_km(day, *_narysowane(["S", "M", "E"]))
    na_sobie = planner._corridor_km(day, *_narysowane(["S", "M", "E"], ["S", "M", "E"]))
    obok = planner._corridor_km(day, *_narysowane(["S", "M", "E"], ["S", "X", "E"]))
    assert jedna > 0
    assert na_sobie == jedna
    assert obok > jedna


def test_two_platforms_of_one_place_are_one_corridor():
    """Tramwaj i autobus stają na osobnych słupkach tego samego placu - to
    wciąż jedna ulica, nie dwie."""
    day = make_day([
        {"trip_id": "t", "label": "Tramwaj 1", "stops": [("S_t", 0, 0), ("E_t", 60, 60)]},
        {"trip_id": "b", "label": "Autobus 2", "stops": [("S_b", 0, 0), ("E_b", 60, 60)]},
    ], names={"S_t": "Plac", "S_b": "Plac", "E_t": "Cel", "E_b": "Cel"})
    tramwaj = planner._corridor_km(day, *_narysowane(["S_t", "E_t"]))
    oba = planner._corridor_km(day, *_narysowane(["S_t", "E_t"], ["S_b", "E_b"]))
    assert oba == tramwaj


def test_the_frame_is_never_narrower_than_a_kilometre():
    day = make_day([{"trip_id": "a", "label": "Tramwaj 1",
                     "stops": [("S", 0, 0), ("E", 60, 60)]}])
    day.stop_coords["E"] = day.stop_coords["S"]      # relacja w jednym punkcie
    assert planner._frame_km2(day, [], ["S", "E"]) == planner.MIN_FRAME_SIDE_KM ** 2


def test_density_is_measured_per_side_of_the_frame_as_on_screen():
    """Każdy kadr wpasowany jest w to samo okno, a kreska ma stałą grubość:
    kadr dwa razy szerszy (cztery razy większy) mieści na ekranie dwa razy
    więcej korytarza, nie cztery."""
    assert planner._map_density(2.0, 1.0) == planner._map_density(4.0, 4.0)


def _jeden_korytarz_i_objazd_day():
    """Trzy linie jednym korytarzem S -> E (w celu o 600, 900 i 1400) i jedna
    innym, przez X (w celu o 1000). Sam korytarz S-E ma gęstość ~1,5 km/√km²
    w swoim kadrze, z objazdem ~6,0."""
    return make_day([
        {"trip_id": "fast", "label": "Tramwaj 1",
         "stops": [("S", 0, 0), ("E", 600, 600)]},
        {"trip_id": "mid", "label": "Autobus 2",
         "stops": [("S", 0, 0), ("E", 900, 900)]},
        {"trip_id": "slower", "label": "Autobus 4",
         "stops": [("S", 0, 0), ("E", 1400, 1400)]},
        {"trip_id": "obok", "label": "Autobus 7",
         "stops": [("S", 0, 0), ("X", 500, 500), ("E", 1000, 1000)]},
    ], names={"S": "Start", "E": "Cel", "X": "Objazd"})


def _linie(result):
    return sorted({s["num"] for s in result["segments"]}, key=int)


def test_the_density_slider_has_a_server_side_ceiling(install_day):
    install_day(_jeden_korytarz_i_objazd_day())
    wynik = planner.plan_flow("Start", "Cel", when=WHEN, density=999)
    assert wynik["density"] == planner.MAX_MAP_DENSITY


def _three_tier_fan_day():
    """Cztery kursy jednym korytarzem: najszybszy, dwa wolniejsze i jeden
    bardzo wolny."""
    trips = [
        {"trip_id": "fast", "label": "Tramwaj 1",
         "stops": [("S", 0, 0), ("E", 600, 600)]},
        {"trip_id": "mid", "label": "Autobus 2",
         "stops": [("S", 0, 0), ("E", 900, 900)]},
        {"trip_id": "slower", "label": "Autobus 4",
         "stops": [("S", 0, 0), ("E", 1400, 1400)]},
        {"trip_id": "excluded", "label": "Tramwaj 9",
         "stops": [("S", 0, 0), ("E", 5000, 5000)]},
    ]
    return make_day(trips, names={"S": "Start", "E": "Cel"})


# ----------------------------------------------------------------------- 4 -

def test_dead_end_branch_never_appears(install_day):
    """Kurs prowadzący do przystanku, z którego nie da się już dojechać do
    celu w oknie czasowym, nie ma prawa pojawić się na mapie w ogóle -
    "nie mam fizycznie jak tam być" w sensie użytecznym."""
    trips = [
        {"trip_id": "feeder_ok", "label": "Autobus 1",
         "stops": [("S", 0, 0), ("M", 200, 200)]},
        {"trip_id": "good_onward", "label": "Tramwaj 2",
         "stops": [("M", 320, 320), ("E", 500, 500)]},
        {"trip_id": "dead_end", "label": "Autobus 9",
         "stops": [("S", 0, 0), ("X", 9999, 9999)]},   # X nie prowadzi nigdzie dalej
    ]
    day = make_day(trips, names={"S": "Start", "M": "Srodek", "E": "Cel", "X": "Donikad"})
    install_day(day)

    result = planner.plan_flow(
        "Start", "Cel", when=WHEN,
    )
    assert "error" not in result
    assert result["best_arrival"] == planner._fmt_time(500)

    assert _segs_by_num(result, "9", "bus") == []          # ślepa gałąź - nigdy nie narysowana
    assert len(_segs_by_num(result, "1", "bus")) == 1       # ...ale realna przesiadka jest
    assert len(_segs_by_num(result, "2", "tram")) == 1


def test_tail_onto_a_terminus_loop_is_not_anchored_by_the_way_back(install_day):
    """Zgłoszone przez użytkownika 2026-08-15 ("co to za odnoga?"): ogon
    wjeżdżający na pętlę końcową tylko po to, żeby zaraz z niej wrócić.

    Tramwaj 1 mija Srodek (skąd realnie jedzie się do celu) i jedzie dalej
    na PETLA. Na PETLA stoi zdążalny, jasny Tramwaj 2 - ale jedzie z
    powrotem przez Srodek, czyli tam, skąd właśnie przyjechaliśmy. To nie
    kontynuacja, tylko droga powrotna, więc odcinek Srodek -> PETLA nie ma
    prawa się narysować (punkt 4), mimo że technicznie da się tam
    "przesiąść"."""
    trips = [
        {"trip_id": "into_loop", "label": "Tramwaj 1",
         "stops": [("S", 0, 0), ("M", 100, 100), ("L", 200, 200)]},
        {"trip_id": "back_out", "label": "Tramwaj 2",
         "stops": [("L", 400, 400), ("M", 500, 500), ("E", 600, 600)]},
        {"trip_id": "onward", "label": "Autobus 3",
         "stops": [("M", 300, 300), ("E", 450, 450)]},
    ]
    day = make_day(trips, names={"S": "Start", "M": "Srodek", "L": "PETLA", "E": "Cel"})
    install_day(day)

    result = planner.plan_flow(
        "Start", "Cel", when=WHEN,
    )
    assert "error" not in result

    # kontrola scenariusza: przesiadka Srodek -> Cel faktycznie jest widoczna
    assert len(_segs_by_num(result, "3", "bus")) == 1

    for seg in _segs_by_num(result, "1", "tram"):
        assert _coords_of(day, ["L"])[0] not in seg["path"], (
            "ogon Tramwaju 1 nie ma prawa sięgać pętli - jedyne, co z niej "
            "odjeżdża, zawraca tam, skąd właśnie przyjechał"
        )


def test_tail_is_not_anchored_by_a_course_turning_back_further_up_the_line(install_day):
    """Zawrócenie liczy się względem CAŁEJ przejechanej drogi, nie tylko
    poprzedniego przystanku.

    Tramwaj 1 jedzie Start -> Wezel -> Srodek -> PETLA. Z pętli odjeżdża
    Tramwaj 15, ale wraca przez Wezel - przystanek, przez który już
    przejechaliśmy (Srodek pomija, bo jedzie inną ulicą). Poprzednia wersja
    reguły patrzyła tylko jeden przystanek wstecz ("czy wraca na Srodek?"),
    więc uznawała to za kontynuację i rysowała ogon aż na pętlę. Realna
    przesiadka jest na Wezle i tam ogon ma się kończyć."""
    trips = [
        {"trip_id": "into_loop", "label": "Tramwaj 1",
         "stops": [("S", 0, 0), ("A", 100, 100), ("B", 200, 200), ("L", 300, 300)]},
        {"trip_id": "back_out", "label": "Tramwaj 15",
         "stops": [("L", 500, 500), ("A", 600, 600), ("E", 700, 700)]},
    ]
    day = make_day(trips, names={"S": "Start", "A": "Wezel", "B": "Srodek",
                                 "L": "PETLA", "E": "Cel"})
    install_day(day)

    result = planner.plan_flow(
        "Start", "Cel", when=WHEN,
    )
    assert "error" not in result

    # kontrola scenariusza: przesiadka na Wezle jest widoczna, cel osiągalny
    assert result["best_arrival"] == planner._fmt_time(700)
    assert len(_segs_by_num(result, "15", "tram")) == 1

    for seg in _segs_by_num(result, "1", "tram"):
        for stop in ("B", "L"):
            assert _coords_of(day, [stop])[0] not in seg["path"], (
                f"ogon Tramwaju 1 nie ma prawa sięgać {day.stop_names[stop]!r} - "
                "jedyne, co z pętli odjeżdża, zawraca po naszych własnych śladach"
            )


def test_two_tails_propping_each_other_up_are_both_cut_back(install_day):
    """Kontynuacja musi sama być narysowana DALEJ, nie tylko jechać dalej
    w rozkładzie.

    Tramwaj 1 i Tramwaj 7 jadą tym samym korytarzem na PETLA. Obu ogony
    kończą się na Ostatnim, bo z pętli wraca tylko droga powrotna (Tramwaj
    15). Każdy z nich "widzi" tam drugiego jako zdążalną kontynuację, która
    fizycznie jedzie dalej - i tak wzajemnie się podpierały, zostawiając na
    mapie dwa kikuty kończące się w tym samym miejscu. Kontynuacja liczy
    się tylko wtedy, gdy sama jest narysowana poza ten przystanek."""
    # Dwa kursy każdej linii, żeby dało się przesiąść z jednej w drugą w OBIE
    # strony - bez tego "wzajemne podpieranie się" nie powstaje.
    trips = [
        {"trip_id": "t1_a", "label": "Tramwaj 1",
         "stops": [("S", 0, 0), ("M", 100, 100), ("B", 200, 200), ("K", 300, 300)]},
        {"trip_id": "t1_b", "label": "Tramwaj 1",
         "stops": [("S", 400, 400), ("M", 500, 500), ("B", 600, 600), ("K", 700, 700)]},
        {"trip_id": "t7_a", "label": "Tramwaj 7",
         "stops": [("S", 60, 60), ("M", 160, 160), ("B", 260, 260), ("K", 360, 360)]},
        {"trip_id": "t7_b", "label": "Tramwaj 7",
         "stops": [("S", 440, 440), ("M", 540, 540), ("B", 640, 640), ("K", 740, 740)]},
        {"trip_id": "back_out", "label": "Tramwaj 15",
         "stops": [("K", 420, 420), ("B", 500, 500), ("M", 580, 580),
                   ("E", 660, 660)]},
        {"trip_id": "onward", "label": "Autobus 3",
         "stops": [("M", 300, 300), ("E", 500, 500)]},
    ]
    day = make_day(trips, names={"S": "Start", "M": "Wezel", "B": "Ostatni",
                                 "K": "PETLA", "E": "Cel"})
    install_day(day)

    result = planner.plan_flow(
        "Start", "Cel", when=WHEN,
    )
    assert "error" not in result

    # kontrola scenariusza: realna droga do celu (przez Wezel) jest na mapie,
    # a oba tramwaje dowożą do tej przesiadki
    assert result["best_arrival"] == planner._fmt_time(500)
    assert len(_segs_by_num(result, "3", "bus")) == 1
    assert _segs_by_num(result, "1", "tram") != []

    for seg in result["segments"]:
        for stop in ("B", "K"):
            assert _coords_of(day, [stop])[0] not in seg["path"], (
                f"nic nie ma prawa dojeżdżać do {day.stop_names[stop]!r}: "
                "stamtąd nie da się pojechać dalej, można tylko wrócić"
            )


def _origin_passed_after_a_terminus_loop_day():
    """Realny układ z Sosnowieckiej (zmierzony 2026-08-27): przystanek
    startowy ma dwa słupki, a linia obsługuje go PO drodze z pętli końcowej.

    Autobus 124 dowozi ze Startu (słupek "w stronę pętli") na PETLA i tam
    kończy. Z pętli wyjeżdża Autobus 134 i wraca tą samą ulicą - przez
    Srodek i przez DRUGI słupek Startu - a dopiero potem jedzie w miasto do
    celu. Pasażer nie ma po co jechać na pętlę: wsiada na drugim słupku
    Startu, obok. Ale najwcześniejsze możliwe wsiadanie do 134 (a tym samym
    początek jego segmentu) wypada na PETLI, bo da się tam dojechać
    124-tką."""
    trips = [
        {"trip_id": "into_loop", "label": "Autobus 124",
         "stops": [("S_in", 0, 0), ("M_in", 60, 60), ("L", 120, 120)]},
        {"trip_id": "out_of_loop", "label": "Autobus 134",
         "stops": [("L", 300, 300), ("M_out", 360, 360), ("S_out", 420, 420),
                   ("P", 700, 700), ("E", 1000, 1000)]},
        {"trip_id": "alt", "label": "Tramwaj 5",
         "stops": [("P", 820, 820), ("Q", 950, 950), ("E", 1200, 1200)]},
    ]
    return make_day(
        trips,
        names={"S_in": "Start", "S_out": "Start", "M_in": "Srodek",
               "M_out": "Srodek", "L": "PETLA", "P": "Wezel", "Q": "Objazd",
               "E": "Cel"},
        siblings={"S_in": ("S_out",), "S_out": ("S_in",),
                  "M_in": ("M_out",), "M_out": ("M_in",)},
    )


def test_course_passing_the_origin_after_a_loop_is_anchored_at_the_origin(install_day):
    """Kotwica początku pyta "czy da się TU wsiąść", nie "czy kurs się tu
    zaczyna".

    Segment Autobusu 134 zaczyna się na PETLI (tam wypada najwcześniejsze
    wsiadanie), ale mija przystanek startowy w swoim środku - i to właśnie
    tam pasażer do niego wsiada. Gdyby kotwica początku patrzyła tylko na
    PIERWSZY przystanek segmentu, 134 mógłby się zakotwiczyć wyłącznie o
    124-tkę jadącą na pętlę - a ta słusznie ginie (punkt 4: z pętli wraca się
    po własnych śladach). Wtedy ginie 134 i mapa schodzi do trybu awaryjnego
    (Sosnowiecka -> Wojszyce, 15:37)."""
    day = _origin_passed_after_a_terminus_loop_day()
    install_day(day)

    result = planner.plan_flow(
        "Start", "Cel", when=WHEN,
    )
    assert "error" not in result
    assert result["best_arrival"] == planner._fmt_time(1000)

    # 134 musi być na mapie i musi być narysowany OD przystanku startowego -
    # stamtąd się w niego wsiada.
    onward = _segs_by_num(result, "134", "bus")
    assert onward != [], "kurs mijający start w środku musi się zakotwiczyć na starcie"
    assert any(_coords_of(day, ["S_out"])[0] in seg["path"] for seg in onward)

    assert result["degraded"] is False

    # ...ale ogon na pętlę nadal się nie rysuje (punkt 4).
    for seg in _segs_by_num(result, "124", "bus"):
        assert _coords_of(day, ["L"])[0] not in seg["path"]


def test_a_normal_map_is_not_marked_as_a_fallback(install_day):
    """Odwrotna strona tego samego znacznika: zwykła mapa nie ma prawa go
    podnosić, inaczej stałby się bezużyteczny."""
    day = _three_tier_fan_day()
    install_day(day)

    result = planner.plan_flow(
        "Start", "Cel", when=WHEN,
    )
    assert "error" not in result
    assert result["degraded"] is False


# ----------------------------------------------------------------------- 6 -

def test_shape_slice_uses_real_street_geometry_when_available():
    """Bez zmian w tej pracy, ale to jeden z sześciu punktów kontraktu -
    pilnujemy, żeby dalej działał: gdy jest dostępny shape (realne ulice/tory),
    ścieżka jest wycinana z niego, nie z prostej łamanej po przystankach."""
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE shapes (shape_id TEXT, seq INTEGER, lat REAL, lon REAL)")
    # Realny kształt "zakrzywiony" w bok, żeby dawał się odróżnić od prostej
    # linii między przystankami.
    shape_points = [
        (51.100, 17.000), (51.101, 17.002), (51.100, 17.004),
        (51.102, 17.006), (51.100, 17.008),
    ]
    for i, (lat, lon) in enumerate(shape_points):
        db.execute("INSERT INTO shapes VALUES (?, ?, ?, ?)", ("shp-1", i, lat, lon))
    db.commit()

    stop_coords = [(51.100, 17.000), (51.100, 17.008)]   # start i koniec shape'u
    path = gtfs.shape_slice("shp-1", stop_coords, db)

    assert path[0] == list(stop_coords[0]) or tuple(path[0]) == stop_coords[0]
    assert len(path) > 2, "wycinek realnej geometrii ma więcej punktów niż prosta łamana"


def test_shape_slice_falls_back_to_stop_polyline_without_a_shape():
    stop_coords = [(51.10, 17.00), (51.11, 17.01)]
    assert gtfs.shape_slice(None, stop_coords, db=None) == stop_coords


# ----------------------------------------------------------------------- 7 -

def _fully_overlapping_lines_day():
    """Tramwaj 1 i Autobus 2 jadą DOKŁADNIE tym samym korytarzem od startu do
    celu (te same, kolejne przystanki) - Tramwaj szybszy (najlepsza trasa),
    Autobus wolniejszy, ale wciąż w oknie. Najprostszy możliwy przypadek
    punktu 7: dwie linie leżące jedna na drugiej na całej długości."""
    trips = [
        {"trip_id": "tram", "label": "Tramwaj 1",
         "stops": [("S", 0, 0), ("A", 100, 100), ("B", 200, 200),
                   ("C", 300, 300), ("E", 400, 400)]},
        {"trip_id": "bus", "label": "Autobus 2",
         "stops": [("S", 0, 0), ("A", 150, 150), ("B", 300, 300),
                   ("C", 450, 450), ("E", 600, 600)]},
    ]
    return make_day(trips, names={"S": "Start", "A": "A", "B": "B", "C": "C", "E": "Cel"})


def test_lines_sharing_a_corridor_each_carry_the_whole_corridor(install_day):
    """Sedno punktu 7: dwie linie na dokładnie tym samym korytarzu leżą na
    mapie JEDNA NA DRUGIEJ - i tak ma zostać. Geometria jest prawdziwa, po
    torach i ulicach (punkt 6), nikt jej nie rozsuwa; próby rozjeżdżania
    wiązki na pasma odpadły trzy razy z rzędu (patrz FLOW_MAP_NOTES.md).

    Rozpoznanie linii bierze się stąd, że KAŻDY kawałek niesie PEŁNY skład
    swojego korytarza - razem z sobą samym. Front stawia z tego jedną grupkę
    numerów na cały korytarz (zamiast rozrzucać po numerze na linię) i
    pozwala się między nimi przełączać pod kursorem.

    Skład jest liczony z ROZKŁADU (te same, kolejne przystanki), nie z
    odległości na ekranie - to drugie przy widoku całego miasta doliczało
    linie z sąsiednich ulic i wypisywało "13 linii" tam, gdzie jadą dwie."""
    day = _fully_overlapping_lines_day()
    install_day(day)

    result = planner.plan_flow(
        "Start", "Cel", when=WHEN,
    )
    assert "error" not in result

    tram = _segs_by_num(result, "1", "tram")
    bus = _segs_by_num(result, "2", "bus")
    assert len(tram) == 1 and len(bus) == 1

    raw = _coords_of(day, ["S", "A", "B", "C", "E"])
    assert tram[0]["path"] == raw and bus[0]["path"] == raw, (
        "geometria zostaje prawdziwa i identyczna dla obu linii - backend nie "
        "przesuwa niczego w bok"
    )

    corridor = [{"num": "1", "kind": "tram"}, {"num": "2", "kind": "bus"}]
    assert tram[0]["corridor"] == corridor
    assert bus[0]["corridor"] == corridor, (
        "obie linie muszą widzieć TEN SAM skład korytarza, razem z sobą samą - "
        "inaczej grupka numerów wyglądałaby inaczej w zależności od tego, "
        "którą linię akurat się wskazało, a przełączanie gubiłoby wskazaną"
    )


def _joining_line_day():
    """Tramwaj 1 i Tramwaj 4 jadą razem S->A->B; na B dołącza Tramwaj 2, który
    rusza ze startu później i inną drogą (przez X), i dalej, B->C->E, jadą we
    trzy. Skład korytarza zmienia się więc dokładnie w B."""
    trips = [
        {"trip_id": "t1", "label": "Tramwaj 1",
         "stops": [("S", 0, 0), ("A", 100, 100), ("B", 200, 200),
                   ("C", 300, 300), ("E", 400, 400)]},
        {"trip_id": "t4", "label": "Tramwaj 4",
         "stops": [("S", 0, 0), ("A", 130, 130), ("B", 260, 260),
                   ("C", 390, 390), ("E", 520, 520)]},
        {"trip_id": "t2", "label": "Tramwaj 2",
         "stops": [("S", 200, 200), ("X", 300, 300), ("B", 400, 400),
                   ("C", 480, 480), ("E", 560, 560)]},
    ]
    return make_day(trips, names={"S": "Start", "A": "A", "B": "B", "C": "C",
                                  "X": "X", "E": "Cel"})


def _corridor_covering(result, num, coords):
    """Skład korytarza z kawałka linii `num`, który obejmuje wszystkie podane
    współrzędne."""
    for seg in result["segments"]:
        if seg["num"] == num and all(c in seg["path"] for c in coords):
            return seg.get("corridor")
    return None


def test_corridor_numbers_follow_one_global_order_everywhere(install_day):
    """Numery w grupce - a więc i kolejność przełączania pod kursorem - to
    zawsze obcięcie JEDNEGO, globalnego porządku linii
    (planner._line_sort_key) do linii obecnych na danym odcinku. Dzięki temu
    numer nie przeskakuje w grupce z miejsca na miejsce przy przejściu na
    sąsiedni odcinek, a "następna w kolejności" znaczy wszędzie to samo.

    Dosiadający się Tramwaj 2 wchodzi więc POMIĘDZY 1 a 4, a nie na koniec
    listy - a względna kolejność 1 przed 4 zostaje ta sama po obu stronach
    przystanku B."""
    day = _joining_line_day()
    install_day(day)

    result = planner.plan_flow("Start", "Cel", when=WHEN,
                               density=planner.MAX_MAP_DENSITY)
    assert "error" not in result

    before = _corridor_covering(result, "1", _coords_of(day, ["S", "A"]))
    after = _corridor_covering(result, "1", _coords_of(day, ["C", "E"]))
    assert before is not None and after is not None, (before, after)

    assert [line["num"] for line in before] == ["1", "4"]
    assert [line["num"] for line in after] == ["1", "2", "4"], (
        "Tramwaj 2 ma wejść do grupki na swoje miejsce w globalnym porządku, "
        "nie na koniec - inaczej numery przestawiałyby się z odcinka na odcinek"
    )


def test_a_piece_never_claims_a_corridor_it_has_already_left(install_day):
    """Kawałek niesie JEDEN skład korytarza na całej swojej długości, więc
    musi być pocięty dokładnie tam, gdzie ten skład się zmienia - obok
    cięcia po jasności (punkt 3), tym samym mechanizmem.

    Bez tego kawałek Tramwaju 1 ciągnący się przez B twierdziłby "tędy jadą
    1, 2 i 4" także PRZED B, gdzie Tramwaju 2 jeszcze nie ma - grupka numerów
    stanęłaby nad odcinkiem, którym połowa z nich nie jeździ."""
    day = _joining_line_day()
    install_day(day)

    result = planner.plan_flow("Start", "Cel", when=WHEN,
                               density=planner.MAX_MAP_DENSITY)
    assert "error" not in result

    start = _coords_of(day, ["S"])[0]
    end = _coords_of(day, ["E"])[0]
    for seg in _segs_by_num(result, "1", "tram"):
        assert not (start in seg["path"] and end in seg["path"]), (
            "kawałek Tramwaju 1 przeszedł przez B jednym kawałkiem, mimo że "
            "skład korytarza zmienia się właśnie tam"
        )


def _leaving_line_day():
    """Autobus 143 i Autobus 124 jadą razem S->A->B; w B 124 skręca do D,
    gdzie czeka szybszy Tramwaj 9 do celu, a 143 jedzie dalej B->C->E. Obie
    drogi się bronią (przesiadka za wcześniejszy przyjazd), więc obie są na
    mapie, a skład korytarza 143 zmienia się w B."""
    trips = [
        {"trip_id": "b143", "label": "Autobus 143",
         "stops": [("S", 0, 0), ("A", 100, 100), ("B", 200, 200),
                   ("C", 400, 400), ("E", 650, 650)]},
        {"trip_id": "b124", "label": "Autobus 124",
         "stops": [("S", 10, 10), ("A", 110, 110), ("B", 210, 210),
                   ("D", 300, 300)]},
        {"trip_id": "t9", "label": "Tramwaj 9",
         "stops": [("D", 420, 420), ("E", 600, 600)]},
    ]
    return make_day(trips, names={"S": "Start", "A": "A", "B": "B", "C": "C",
                                  "D": "D", "E": "Cel"})


def test_every_number_in_a_group_is_drawn_along_the_whole_piece(install_day):
    """Każda linia ze składu kawałka jest narysowana na KAŻDYM jego odcinku.
    Mapa ma mało wyjść, więc kawałek ciągnął się od wyjścia do wyjścia przez
    zmianę składu i grupka obiecywała linię, której dalej już nie ma, a
    najechanie na jej numer niczego nie wskazywało (#159: 124 przy Moście
    Grunwaldzkim)."""
    day = _leaving_line_day()
    install_day(day)

    result = planner.plan_flow("Start", "Cel", when=WHEN)
    assert "error" not in result

    hops = {}
    for seg in result["segments"]:
        path = [tuple(p) for p in seg["path"]]
        hops.setdefault((seg["kind"], seg["num"]), set()).update(zip(path, path[1:]))
    for seg in result["segments"]:
        path = [tuple(p) for p in seg["path"]]
        for line in seg.get("corridor") or []:
            missing = set(zip(path, path[1:])) - hops.get((line["kind"], line["num"]), set())
            assert not missing, (seg["num"], line["num"], missing)


def test_solo_line_never_gets_a_corridor_list(install_day):
    """Kontrolne: linia, która NIE dzieli żadnego odcinka z inną linią, nie
    dostaje składu korytarza wcale - front rysuje wtedy jej numer sam, a pod
    kursorem nie ma się co przełączać."""
    day = make_day(
        [{"trip_id": "bus", "label": "Autobus 7",
          "stops": [("S", 0, 0), ("M", 400, 420), ("E", 1200, 1200)]}],
        names={"S": "Start", "M": "Srodek", "E": "Cel"},
    )
    install_day(day)

    result = planner.plan_flow(
        "Start", "Cel", when=WHEN,
    )
    assert "error" not in result
    pieces = _segs_by_num(result, "7", "bus")
    assert len(pieces) == 1
    assert pieces[0]["path"] == _coords_of(day, ["S", "M", "E"])
    assert "corridor" not in pieces[0]


# ---------------------------------------------------------------------- 11 -

def _three_flows_at_one_node_day():
    """Węzeł X, na którym dzieją się wszystkie trzy rzeczy naraz.

    Tramwaj 1 wiezie z S przez X do celu (wolno, ale w oknie) - w X można
    w niego wsiąść, ale można też już nim jechać: PRZEJAZD.
    Autobus 2 zaczyna się w X i dowozi najszybciej - WSIADANIE.
    Autobus 3 dowozi z S do X i tam się kończy - PRZYJAZD: mapa nim dalej
    nie wiezie, ale to nim najwcześniej da się tu być.
    """
    return make_day([
        {"trip_id": "tA", "label": "Tramwaj 1", "headsign": "CEL",
         "stops": [("S", 0, 0), ("X", 600, 600), ("T", 1500, 1500)]},
        {"trip_id": "tB", "label": "Autobus 2", "headsign": "CEL",
         "stops": [("X", 900, 900), ("T", 1200, 1200)]},
        {"trip_id": "tD", "label": "Autobus 3", "headsign": "WEZEL",
         "stops": [("S", 0, 0), ("X", 500, 500)]},
    ])


def _node_named(result, name):
    for node in result["nodes"]:
        if node["name"] == name:
            return node
    raise AssertionError(
        f"brak węzła {name!r}; są: {[n['name'] for n in result['nodes']]}")


def _flow_of(node, num):
    for line in node["lines"]:
        if line["num"] == num:
            return line
    raise AssertionError(
        f"węzeł {node['name']!r} nie wymienia linii {num!r}; ma: "
        + str([f"{l['num']}/{l['flow']}" for l in node["lines"]]))


def test_a_node_says_which_of_three_things_happens_with_each_line(install_day):
    """Punkt 11: przesiadka to nie tylko "w co tu wsiąść". Ta sama kropka
    odpowiada na trzy różne pytania i przy każdej linii mówi, o które chodzi -
    inaczej pojazd, którym się tu właśnie przyjechało, w ogóle nie istnieje."""
    install_day(_three_flows_at_one_node_day())
    result = planner.plan_flow("S", "T", WHEN)
    wezel = _node_named(result, "X")

    assert _flow_of(wezel, "1")["flow"] == "through"
    assert _flow_of(wezel, "2")["flow"] == "start"
    assert _flow_of(wezel, "3")["flow"] == "end"


def test_only_boardable_lines_carry_a_deadline_and_only_arrivals_a_time(install_day):
    """Te dwie liczby odpowiadają na różne pytania i nie mają prawa się
    pomylić: `depart_by` to "którym ostatnim odjazdem jeszcze zdążę"
    (tylko dla linii do wsiadania), `arrive` to "o której tu tą linią jestem"
    (tylko dla przyjazdu - w tablicy odjazdów przystanku tej godziny NIE MA,
    bo przyjazd nie jest odjazdem)."""
    install_day(_three_flows_at_one_node_day())
    wezel = _node_named(planner.plan_flow("S", "T", WHEN), "X")

    for num in ("1", "2"):
        assert "depart_by" in _flow_of(wezel, num)
        assert "arrive" not in _flow_of(wezel, num)

    przyjazd = _flow_of(wezel, "3")
    assert "depart_by" not in przyjazd
    # 500 to godzina z rozkładu TEGO kursu, którym narysowano kawałek -
    # nie najbliższy kurs tej linii i nie godzina węzła "tak w ogóle".
    assert przyjazd["arrive"] == 500


def test_the_node_hour_is_the_earliest_you_can_be_here(install_day):
    """Od tej godziny liczy się "co stąd jeszcze odjedzie" i "za ile" w każdym
    wierszu - także w wierszu przyjazdu. Najwcześniej da się tu być
    autobusem 3 (500), nie tramwajem 1 (600)."""
    install_day(_three_flows_at_one_node_day())
    assert _node_named(planner.plan_flow("S", "T", WHEN), "X")["sec"] == 500


def test_a_place_where_you_only_get_off_is_still_not_a_transfer(install_day):
    """Dokładanie przyjazdów NIE rozsiewa kropek po mapie: miejsce, z którego
    nie da się już nigdzie pojechać, nie jest przesiadką i kropki nie dostaje.
    Zmienia się to, co mówi kropka, a nie to, gdzie stoi."""
    install_day(_three_flows_at_one_node_day())
    result = planner.plan_flow("S", "T", WHEN)

    assert "T" not in [n["name"] for n in result["nodes"]]
    # ...a węzeł, na którym da się wsiąść, zostaje - razem z przyjazdem.
    assert {"S", "X"} == {n["name"] for n in result["nodes"]}


def _passing_line_day():
    """Odwzorowanie zgłoszenia z 2026-08-31 (Galeria Dominikańska -> pl. Grunwaldzki).

    Autobus 10 jedzie B -> K -> T jednym, NIEPRZECIĘTYM kawałkiem, więc przez K
    tylko PRZEJEŻDŻA - tak jak tramwaj 10 i autobus 111 przez Katedrę. Tramwaj 5
    i tramwaj 3 na K się kończą - tak jak tam 5 i N. Na K nie zaczyna się nic.
    """
    return make_day([
        {"trip_id": "t5", "label": "Tramwaj 5", "headsign": "PETLA",
         "stops": [("S", 0, 0), ("U", 300, 300), ("K", 500, 500)]},
        {"trip_id": "t7", "label": "Autobus 7", "headsign": "B",
         "stops": [("S", 0, 0), ("B", 200, 200)]},
        {"trip_id": "t10", "label": "Autobus 10", "headsign": "CEL",
         "stops": [("B", 400, 400), ("K", 900, 900), ("T", 1200, 1200)]},
        {"trip_id": "t3", "label": "Tramwaj 3", "headsign": "CEL",
         "stops": [("U", 500, 500), ("K", 750, 750)]},
    ])


def test_a_line_passing_through_the_middle_of_a_piece_is_still_listed(install_day):
    """Zgłoszone 2026-08-31: przez Urząd Wojewódzki mapa rysowała autobus N,
    ale kropka go nie widziała - kawałek N miał tam swój ŚRODEK, a węzeł czytał
    wyłącznie końce kawałków. Linia rysowana przez przystanek jest przy nim
    opcją i ma być wypisana."""
    install_day(_passing_line_day())
    wezel = _node_named(planner.plan_flow("S", "T", WHEN), "K")

    assert _flow_of(wezel, "10")["flow"] == "through"


def test_a_stop_you_change_at_gets_a_dot_even_if_nothing_starts_there(install_day):
    """Zgłoszone 2026-08-31: koło Katedry nie było kropki, choć dojeżdża się tam
    piątką wyłącznie po to, żeby przesiąść się dalej. Kończyły się tam kawałki
    5 i N, a 10 i 111 tylko tamtędy PRZEJEŻDŻAŁY - więc "nic się tu nie
    zaczyna" kasowało kropkę razem z całą przesiadką."""
    install_day(_passing_line_day())
    result = planner.plan_flow("S", "T", WHEN)
    wezel = _node_named(result, "K")

    assert _flow_of(wezel, "5")["flow"] == "end"
    # ...i to przejeżdżająca dziesiątka jest tym, po co się tu wysiada
    assert any(l["flow"] != "end" for l in wezel["lines"])


def _drawing_seam_day():
    """Kawałki tramwaju 10 i 20 stykają się na K, bo zmienia się tam wartość
    jazdy dalej - ale ŻADNA z tych linii się na K nie zaczyna ani nie kończy:
    obie tamtędy przejeżdżają. Tak wygląda Urząd Wojewódzki (Impart), gdzie D
    i 146 dostały szew od zmiany składu korytarza."""
    return make_day([
        {"trip_id": "t7", "label": "Autobus 7", "headsign": "B",
         "stops": [("S", 0, 0), ("B", 100, 100)]},
        {"trip_id": "t8", "label": "Autobus 8", "headsign": "C",
         "stops": [("S", 0, 0), ("C", 100, 100)]},
        {"trip_id": "t10", "label": "Tramwaj 10", "headsign": "CEL",
         "stops": [("B", 300, 300), ("K", 600, 600), ("T", 1250, 1250)]},
        {"trip_id": "t20", "label": "Tramwaj 20", "headsign": "CEL",
         "stops": [("C", 300, 300), ("K", 800, 800), ("T", 1000, 1000)]},
    ])


def test_a_seam_between_two_pieces_is_not_a_transfer(install_day):
    """Zgłoszone 2026-08-31: kropka stała na Urzędzie Wojewódzkim (Impart)
    i nie miała nic do powiedzenia - D i 146 tylko tamtędy przejeżdżały.
    Kawałki tnie także zmiana składu korytarza (punkt 7), czyli sprawa czysto
    rysunkowa, a kropka dziedziczyła ten szew. Miejsce, przez które wszystko
    tylko przejeżdża, nie jest przesiadką."""
    install_day(_drawing_seam_day())
    result = planner.plan_flow("S", "T", WHEN, density=planner.MAX_MAP_DENSITY)

    # Szew NAPRAWDĘ tam jest - inaczej test przechodziłby na pusto.
    assert len(_segs_by_num(result, "10", "tram")) > 1
    assert "K" not in [n["name"] for n in result["nodes"]]


def _penultimate_stop_day():
    """Na K da się być najwcześniej o 3600 - i o 3600 da się też być U CELU
    (autobusem 9). Tramwaj 10 jedzie przez K DO celu, a autobus 4 na K się
    kończy, więc kropka na K ma prawo stać."""
    return make_day([
        {"trip_id": "t9", "label": "Autobus 9", "headsign": "CEL",
         "stops": [("S", 0, 0), ("T", 3600, 3600)]},
        {"trip_id": "t4", "label": "Autobus 4", "headsign": "K",
         "stops": [("S", 0, 0), ("K", 3600, 3600)]},
        {"trip_id": "t7", "label": "Autobus 7", "headsign": "B",
         "stops": [("S", 0, 0), ("B", 200, 200)]},
        {"trip_id": "t10", "label": "Tramwaj 10", "headsign": "CEL",
         "stops": [("B", 400, 400), ("K", 3800, 3800), ("T", 4100, 4100)]},
    ])


def test_the_stop_before_the_target_does_not_claim_the_line_ends_there(install_day):
    """O to, czy kawałek wiezie Z POWROTEM, pytamy o niego JAKO CAŁOŚĆ.

    `_rides_back` uznaje za cofnięcie także RÓWNE godziny, a tuż przed celem
    "najwcześniej tutaj" i "najwcześniej u celu" bywają identyczne. Pytany
    o drogę OD TEGO przystanku orzekłby, że tramwaj 10 kończy się na K - choć
    jedzie stamtąd jeszcze przystanek do celu - i skasowałby całą kropkę,
    bo z K nie zostałoby już nic, czym da się jechać dalej (Reja, 2026-08-31).
    """
    install_day(_penultimate_stop_day())
    wezel = _node_named(planner.plan_flow("S", "T", WHEN,
                                          density=planner.MAX_MAP_DENSITY), "K")

    assert _flow_of(wezel, "10")["flow"] == "through"
    assert _flow_of(wezel, "4")["flow"] == "end"


# ---- 13 - zawsze jakaś trasa, choćby za godzinę --------------------------

def _dzien_z_jednym_kursem(odjazd, przyjazd):
    """START -> CEL jednym autobusem, o zadanej godzinie i tylko o niej."""
    return make_day([{
        "trip_id": "T1", "label": "Autobus 1",
        "stops": [("START", odjazd, odjazd), ("CEL", przyjazd, przyjazd)],
    }])


def test_the_departure_is_the_vehicle_not_the_question():
    """Godzina czekania nie jest podróżą: wyjazd trasy to odjazd pojazdu,
    nie godzina, o którą pytano."""
    day = _dzien_z_jednym_kursem(12 * 3600, 12 * 3600 + 1800)
    stop, _, journey = planner._scan(day, ["START"], ["CEL"], 10 * 3600)
    assert stop == "CEL"
    wyjazd = planner._journey_start(day, journey, stop)
    assert wyjazd == 12 * 3600, "odczytany ma być odjazd pojazdu, nie godzina pytania"


def test_a_journey_that_starts_with_a_walk_still_reports_its_departure():
    """Odjazdem trasy jest odjazd PIERWSZEGO PRZEJAZDU, nie moment wyjścia
    z domu - przejście na sąsiedni słupek nie ma godziny w rozkładzie."""
    day = _dzien_z_jednym_kursem(12 * 3600, 12 * 3600 + 1800)
    day.stop_names["OBOK"] = "OBOK"
    day.stop_coords["OBOK"] = (51.11, 17.03)
    day.siblings = {
        "OBOK": {"START": gtfs.WALK_MIN_SEC},
        "START": {"OBOK": gtfs.WALK_MIN_SEC},
    }
    stop, _, journey = planner._scan(day, ["START"], ["CEL"], 10 * 3600)
    assert planner._journey_start(day, journey, stop) == 12 * 3600


def test_nothing_today_is_answered_with_tomorrow(monkeypatch):
    """"Nie znaleziono połączenia" nie jest odpowiedzią na pytanie "jak tam
    dojadę". Gdy o podaną godzinę nic już nie jedzie, odpowiedzią jest
    najbliższy wyjazd - choćby dopiero rano następnego dnia."""
    dzis = _dzien_z_jednym_kursem(8 * 3600, 8 * 3600 + 1800)     # było o 8:00
    jutro = _dzien_z_jednym_kursem(6 * 3600, 6 * 3600 + 1800)    # jest o 6:00
    dni = {datetime.date(2026, 8, 31): dzis, datetime.date(2026, 9, 1): jutro}
    monkeypatch.setattr(gtfs, "load_day", lambda d: dni[d])

    wynik = planner.plan_flow("START", "CEL",
                              datetime.datetime(2026, 8, 31, 22, 0))
    assert "error" not in wynik, wynik.get("error")
    assert wynik["day_offset"] == 1, "odpowiedź ma sięgnąć następnej doby"
    assert wynik["starts"] == "06:00"
    # Czekanie liczone od pytania, przez granicę doby: 22:00 -> 06:00 nazajutrz
    # to osiem godzin, a nie sześć (tyle wyszłoby na osi samej nowej doby).
    assert wynik["waits_sec"] == 8 * 3600


def test_the_wait_does_not_move_the_map_threshold(monkeypatch):
    """Okno liczy się od wyjazdu, nie od pytania (punkt 13): próg mapy stoi
    względem najszybszego PRZYJAZDU, więc pytanie o 10:00 i o 11:59 o ten sam
    autobus o 12:00 daje mapę do tej samej godziny."""
    day = _dzien_z_jednym_kursem(12 * 3600, 12 * 3600 + 1800)
    monkeypatch.setattr(gtfs, "load_day", lambda d: day)
    wczesnie = planner.plan_flow("START", "CEL",
                                 datetime.datetime(2026, 8, 31, 10, 0))
    tuz_przed = planner.plan_flow("START", "CEL",
                                  datetime.datetime(2026, 8, 31, 11, 59))
    assert "error" not in wczesnie, wczesnie.get("error")
    assert wczesnie["starts_sec"] == 12 * 3600
    assert wczesnie["deadline_sec"] == tuz_przed["deadline_sec"]


def test_a_relation_with_no_service_at_all_still_says_so():
    """Pusta mapa z komunikatem należy się relacji, której nie da się
    przejechać w ogóle - obietnica "zawsze jakaś trasa" nie może zmienić się
    w zmyślanie połączeń, których nie ma."""
    day = _dzien_z_jednym_kursem(8 * 3600, 8 * 3600 + 1800)
    stop, _, _ = planner._scan(day, ["CEL"], ["START"], 0)
    assert stop is None


# ------------------------------ "wyjeżdżasz o" - najpóźniejszy wyjazd (#141) --

def _krazenie_day():
    """Tramwaj 1 rusza ze startu dopiero o 600 i jest w celu o 1200, mijając
    po drodze X o 900. Autobus 9 rusza od razu, ale dowozi tylko do X - skąd
    i tak wsiada się w tego samego Tramwaja 1. Wyjazd o 0 i wyjazd o 600 dają
    więc TĘ SAMĄ godzinę w celu."""
    return make_day([
        {"trip_id": "tram", "label": "Tramwaj 1",
         "stops": [("S", 600, 600), ("X", 900, 900), ("E", 1200, 1200)]},
        {"trip_id": "zabicie", "label": "Autobus 9",
         "stops": [("S", 0, 0), ("X", 300, 300)]},
    ], names={"S": "Start", "E": "Cel", "X": "Objazd"})


def test_the_map_accepts_a_walking_transfer_the_search_accepts():
    """Przesiadka pieszo kosztuje sam marsz - bez bufora przesiadki, tak jak
    w wyszukiwaniu. Autobus 100 jest na pętli o 600, marsz na przystanek
    tramwaju trwa 180, tramwaj odjeżdża o 780: wyszukiwanie ją uznaje, więc
    skan wstecz, z którego mapa bierze "najpóźniej trzeba tu być", też musi.
    Doliczał do marszu bufor i z mapy znikała wtedy sama najszybsza trasa
    (Sosnowiecka -> Wojszyce 22.09, setka na pętlę GAJ i osiemnastka
    z Morwowej)."""
    day = make_day([
        {"trip_id": "bus", "label": "Autobus 100",
         "stops": [("S", 0, 0), ("P", 600, 600)]},
        {"trip_id": "tram", "label": "Tramwaj 18",
         "stops": [("M", 780, 780), ("E", 1200, 1200)]},
    ], siblings={"P": {"M": 180}, "M": {"P": 180}})

    stop, arr, _ = planner._scan(day, ["S"], ["E"], 0)
    assert (stop, arr) == ("E", 1200)
    assert planner._backward(day, ["E"], 0, 1200)["S"] == 0


def test_the_headline_departure_is_the_latest_one(install_day):
    """"Wyjeżdżasz o" w pasku to najpóźniejszy wyjazd, który wciąż daje
    najszybszy przyjazd. Autobus 9 o 0 kazałby wyjść dziesięć minut wcześniej
    tylko po to, żeby czekać na X na tego samego Tramwaja 1."""
    install_day(_krazenie_day())

    wynik = planner.plan_flow("Start", "Cel", when=WHEN, density=15)

    assert wynik["starts_sec"] == 600
    assert wynik["ride_sec"] == 600          # 600 -> 1200, bez czekania
    assert wynik["best_sec"] == 1200         # "za ile" dalej od pytania
