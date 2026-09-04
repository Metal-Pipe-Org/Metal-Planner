"""Testy "kontraktu mapy przepływów" - listy gwarancji zachowania mapy
uzgodnionej z użytkownikiem 2026-08-11 (patrz docs/FLOW_MAP_CONTRACT.md dla
pełnej listy punktów, docs/ROUTING_ALGORITHM.md dla opisu samego algorytmu).
Dotyczy WYŁĄCZNIE rysowania mapy (plan_flow / segments) - lista propozycji
tras (journeys) jest świadomie poza zakresem.

Numeracja testów odpowiada numeracji punktów kontraktu:
  1. cały wachlarz opcji naraz, jasność ciągła (nie próg pokaż/ukryj)
  2. okno czasowe liczone względem najszybszej trasy
  3. jasność W KA�ŻDYM PUNKCIE kursu - kurs może wyjść na mapie jako kilka
     kawałków o różnej jasności, cięte dokładnie tam, gdzie mijamy realną,
     lepszą przesiadkę
  4. brak wiszących w powietrzu gałęzi - każdy segment zakotwiczony z obu stron
  (punkt 5, "twarda gwarancja najszybszej trasy", został świadomie USUNIĘTY
  z kontraktu przez użytkownika - nie jest tu testowany jako gwarancja)
  6. geometria po realnych ulicach/torach, z fallbackiem na łamaną po
     przystankach, gdy nie ma dostępnego shape'u
  7. zawsze wiadomo, co tam jedzie - gdy kilka linii dzieli dokładnie ten
     sam odcinek (te same, kolejne przystanki), leżą na mapie jedna na
     drugiej; backend nie rusza geometrii, tylko podaje SKŁAD korytarza
     (planner._corridor_lines, pole `corridor`), z którego front robi grupkę
     numerów i przełączanie linii pod kursorem
  9. pełny zakres jasności zawsze wykorzystany - liczony względem
     najgorszej FAKTYCZNIE pokazanej opcji, nie względem pełnej szerokości
     okna czasowego (poszerzanie okna nie rozjaśnia już pokazanych opcji)
 11. węzeł przesiadkowy mówi, co się tu dzieje z każdą linią - wsiadasz tu
     pierwszy raz, jedziesz dalej czymś, czym można już jechać, czy właśnie
     tym przyjechałeś (planner._transfer_nodes, pole `flow`)
"""

import datetime
import math
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

def test_deadline_scales_with_best_route_duration():
    # 150% -> 50% czasu trasy jako naddatek
    d = planner._deadline(1000, 0, extra_pct=150, extra_floor_sec=0, extra_cap_sec=99999)
    assert d == 1000 + 500


def test_deadline_floor_protects_very_short_routes():
    # 3-minutowa trasa (180 s) przy 110% to tylko 18 s naddatku - floor ma to podnieść
    d = planner._deadline(180, 0, extra_pct=110, extra_floor_sec=600, extra_cap_sec=99999)
    assert d == 180 + 600


def test_deadline_cap_limits_very_long_routes():
    d = planner._deadline(10_000, 0, extra_pct=200, extra_floor_sec=0, extra_cap_sec=3600)
    assert d == 10_000 + 3600


# ------------------------------------------------------------------- 1+2 ---

def _three_tier_fan_day():
    """Cztery kursy: najlepszy, dwa pośrednie (jeden dalej, jeden poza
    oknem), do sprawdzenia zarówno ciągłości jasności, jak i odcięcia okna
    i pełnego wykorzystania skali (patrz punkt 9)."""
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


def test_whole_fan_shown_with_continuous_brightness_and_window_cutoff(install_day):
    """Cały sensowny wachlarz (nie tylko najszybsza trasa) jest pokazany,
    jasność jest ciągła (nie 0/1), a coś poza oknem czasowym w ogóle się
    nie pojawia."""
    day = _three_tier_fan_day()
    install_day(day)

    # extra_pct ma sufit 200% (MAX_EXTRA_PCT), więc szerokość okna podbijamy
    # przez extra_floor_sec (naddatek min. 1800 s -> deadline 2400) -
    # "slower" (1400) w oknie, "excluded" (5000) wciąż poza nim.
    result = planner.plan_flow(
        "Start", "Cel", when=WHEN,
        extra_pct=110, extra_floor_sec=1800, extra_cap_sec=999999,
    )
    assert "error" not in result

    fast = _segs_by_num(result, "1", "tram")
    mid = _segs_by_num(result, "2", "bus")
    slower = _segs_by_num(result, "4", "bus")
    excluded = _segs_by_num(result, "9", "tram")

    assert len(fast) == 1 and fast[0]["w"] == 1.0          # najszybsza = pełna jasność
    assert len(mid) == 1                                    # też pokazana, nie tylko najszybsza
    assert 0.0 < mid[0]["w"] < 1.0                          # ciągła jasność, nie 0/1
    assert len(slower) == 1 and slower[0]["w"] == 0.0       # najgorsza POKAZANA = dolny kraniec skali
    assert excluded == []                                   # poza oknem - nie pokazana wcale


# ----------------------------------------------------------------------- 3 -

def _skip_a_better_transfer_day():
    """Autobus 7: S -> M -> E (wolno, cały czas tym samym pojazdem).
    Tramwaj 3: M -> E (szybko) - odjeżdża z M tuż po tym, jak dojeżdża tam
    autobus, więc jest to realna, złapana kontynuacja.
    Najlepsza trasa to Autobus->M->przesiadka->Tramwaj (720 s), NIE sam
    autobus do końca (1200 s)."""
    trips = [
        {"trip_id": "bus", "label": "Autobus 7",
         "stops": [("S", 0, 0), ("M", 400, 420), ("E", 1200, 1200)]},
        {"trip_id": "tram", "label": "Tramwaj 3",
         "stops": [("M", 540, 540), ("E", 700, 700)]},
    ]
    return make_day(trips, names={"S": "Start", "M": "Srodek", "E": "Cel"})


def test_single_course_splits_brightness_at_a_real_skipped_transfer(install_day):
    """To jest sedno punktu 3 kontraktu: jadąc autobusem, mijamy w Środku
    realną, szybszą przesiadkę na tramwaj (ta przesiadka to najlepsza trasa
    w ogóle). Dopóki jedziemy do Środka, jasność = jasność najlepszej trasy
    (moglibyśmy tam wysiąść i się przesiąść). Jadąc dalej BEZ przesiadki,
    jasność ma spaść - jeden fizyczny kurs autobusu wychodzi na mapie jako
    DWA kawałki o różnej jasności, nie jeden płaski odcinek."""
    day = _skip_a_better_transfer_day()
    install_day(day)

    result = planner.plan_flow(
        "Start", "Cel", when=WHEN,
        extra_pct=200, extra_floor_sec=0, extra_cap_sec=999999,
    )
    assert "error" not in result
    assert result["best_arrival"] == planner._fmt_time(700)

    bus_pieces = _segs_by_num(result, "7", "bus")
    tram_pieces = _segs_by_num(result, "3", "tram")

    assert len(tram_pieces) == 1
    assert tram_pieces[0]["w"] == 1.0     # druga połowa najlepszej trasy - pełna jasność

    assert len(bus_pieces) == 2, (
        "autobus powinien wyjść na mapie jako DWA kawałki: jasny do Środka, "
        "ciemniejszy dalej (tam gdzie mija się przesiadkę na tramwaj)"
    )
    # kawałek Start->Środek: tak samo jasny jak najlepsza trasa
    start_to_mid = next(s for s in bus_pieces if s["path"] == _coords_of(day, ["S", "M"]))
    mid_to_end = next(s for s in bus_pieces if s is not start_to_mid)
    assert start_to_mid["w"] == 1.0
    assert mid_to_end["w"] < start_to_mid["w"]
    # W tym scenariuszu mid_to_end (przyjazd samym autobusem, bez
    # przesiadki) to zarazem najgorsza wartość widoczna GDZIEKOLWIEK na
    # mapie, więc zgodnie z punktem 9 (pełny zakres jasności zawsze
    # wykorzystany) ląduje dokładnie na dolnym krańcu skali, nie tuż nad
    # nim - to nie znika, tylko dolny kraniec skali (patrz punkt 9) ma
    # zagwarantowaną widoczność na samej mapie (`static/app.js`), nie w
    # tej surowej wartości.
    assert mid_to_end["w"] == 0.0

    # kawałki tego samego fizycznego kursu stykają się dokładnie w Środku -
    # żadnej dziury ani zakładki na mapie.
    assert start_to_mid["path"][-1] == mid_to_end["path"][0]

    # fallback geometrii (punkt 6): bez shape_id, kawałek bez współdzielonego
    # odcinka to nadal łamana po rzeczywistych współrzędnych przystanków.
    assert start_to_mid["path"] == _coords_of(day, ["S", "M"])

    # mid_to_end NATOMIAST dzieli odcinek Środek->Cel z Tramwajem 3 (oba
    # kursy zatrzymują się na tych samych dwóch, kolejnych słupkach) - to
    # sytuacja z punktu 7. Geometria obu zostaje ta sama i prawdziwa, więc
    # na mapie leżą jedna na drugiej; rozróżnia je skład korytarza (patrz
    # sekcja "7" niżej i planner._corridor_lines).
    assert mid_to_end["path"] == _coords_of(day, ["M", "E"])
    assert tram_pieces[0]["path"] == _coords_of(day, ["M", "E"])
    assert mid_to_end["corridor"] == tram_pieces[0]["corridor"] == [
        {"num": "3", "kind": "tram"}, {"num": "7", "kind": "bus"},
    ]
    # kawałek Start->Środek jedzie sam, więc składu nie dostaje wcale - i to
    # jest właśnie powód, dla którego kurs musi być tu POCIĘTY także po
    # zmianie składu, nie tylko po jasności
    assert "corridor" not in start_to_mid


def test_no_flicker_without_a_real_alternative_to_skip(install_day):
    """Bez konkurencyjnej przesiadki po drodze kurs ma jedną, stałą jasność
    na całej narysowanej długości - punkt 3 nie ma tworzyć podziałów tam,
    gdzie nic naprawdę się nie zmienia."""
    trips = [
        {"trip_id": "bus", "label": "Autobus 7",
         "stops": [("S", 0, 0), ("M", 400, 420), ("E", 1200, 1200)]},
    ]
    day = make_day(trips, names={"S": "Start", "M": "Srodek", "E": "Cel"})
    install_day(day)

    result = planner.plan_flow(
        "Start", "Cel", when=WHEN,
        extra_pct=200, extra_floor_sec=0, extra_cap_sec=999999,
    )
    assert "error" not in result
    bus_pieces = _segs_by_num(result, "7", "bus")
    assert len(bus_pieces) == 1
    assert bus_pieces[0]["path"] == _coords_of(day, ["S", "M", "E"])
    assert bus_pieces[0]["w"] == 1.0   # jedyna trasa = najlepsza trasa


def test_exit_brightness_is_non_increasing_along_a_course(install_day):
    """Białoskrzynkowa własność, na której opiera się punkt 3: to, co jeszcze
    jest osiągalne z danego miejsca kursu, może się z czasem tylko pogarszać
    (albo zostać bez zmian), nigdy poprawić - inaczej podział na kawałki
    tworzyłby fałszywe polepszenia zamiast realnych, mijanych okazji."""
    day = _skip_a_better_transfer_day()
    install_day(day)
    dep_sec = 0
    best_stop, best_arr, _ = planner._scan(day, {"S"}, {"E"}, dep_sec)
    deadline = planner._deadline(best_arr, dep_sec, extra_pct=200, extra_floor_sec=0, extra_cap_sec=999999)
    earliest, arrived_by, trip_board = planner._forward(day, {"S"}, dep_sec, deadline)
    latest = planner._backward(day, {"E"}, dep_sec, deadline)
    origin_latest = max(latest[s] for s in {"S"} if s in latest)
    segs = planner._discover_segments(
        day, dep_sec, deadline, earliest, arrived_by, trip_board,
        latest, origin_latest, {"E"},
    )
    profile = planner._target_profile(day, {"E"}, dep_sec, deadline)
    planner._refine_brightness(day, segs, {"E"}, deadline, best_arr, profile)

    for seg in segs:
        qs = seg["exit_q"]
        assert all(qs[i] >= qs[i + 1] - 1e-9 for i in range(len(qs) - 1)), (
            f"{seg['label']}: exit_q spada niemonotonicznie: {qs}"
        )


# ----------------------------------------------------------------------- 9 -

def test_brightness_uses_full_range_regardless_of_window_width(install_day):
    """Poszerzenie suwaka okna czasowego nie ma prawa rozjaśniać już
    pokazanych opcji - jasność jest liczona względem najgorszej opcji,
    która FAKTYCZNIE się pokazuje, nie względem pełnej (dowolnie szerokiej)
    szerokości okna. Ten sam wachlarz tras (fast/mid/slower), dwa różne,
    dużo różniące się szerokością okna (naddatek podbity przez
    extra_floor_sec, bo sam suwak % ma sufit 200% - patrz MAX_EXTRA_PCT) -
    obie wciąż mieszczące "slower", a nie mieszczące "excluded" - mają dać
    IDENTYCZNĄ jasność."""
    day = _three_tier_fan_day()
    install_day(day)

    narrow = planner.plan_flow(
        "Start", "Cel", when=WHEN,
        extra_pct=110, extra_floor_sec=900, extra_cap_sec=999999,    # deadline 1500
    )
    wide = planner.plan_flow(
        "Start", "Cel", when=WHEN,
        extra_pct=110, extra_floor_sec=1800, extra_cap_sec=999999,   # deadline 2400
    )
    assert "error" not in narrow and "error" not in wide

    # membership niezmieniona w obu oknach (excluded=5000 dalej poza obydwoma)
    for result in (narrow, wide):
        assert _segs_by_num(result, "9", "tram") == []

    for num, kind in [("1", "tram"), ("2", "bus"), ("4", "bus")]:
        w_narrow = _segs_by_num(narrow, num, kind)[0]["w"]
        w_wide = _segs_by_num(wide, num, kind)[0]["w"]
        assert w_narrow == w_wide, (
            f"linia {num}: {w_narrow} (wąskie okno) != {w_wide} (szerokie okno) - "
            "poszerzenie okna nie powinno zmieniać jasności już pokazanej opcji"
        )


def test_previously_worst_option_brightens_when_a_new_worse_one_appears(install_day):
    """Druga strona punktu 9: gdy poszerzenie okna FAKTYCZNIE wprowadza nową,
    gorszą opcję, to dół skali przesuwa się niżej - opcja, która wcześniej
    była najgorsza pokazana (i świeciła na 0,0), ma się realnie rozjaśnić,
    bo już nie jest na dole. To NIE jest błąd - to jest ta sama zasada
    "pełny zakres zawsze wykorzystany" z poprzedniego testu, tylko
    zadziałana w drugą stronę."""
    trips = [
        {"trip_id": "fast", "label": "Tramwaj 1",
         "stops": [("S", 0, 0), ("E", 600, 600)]},
        {"trip_id": "mid", "label": "Autobus 2",
         "stops": [("S", 0, 0), ("E", 900, 900)]},
        {"trip_id": "slower", "label": "Autobus 4",
         "stops": [("S", 0, 0), ("E", 1400, 1400)]},
        {"trip_id": "newly_worse", "label": "Tramwaj 9",
         "stops": [("S", 0, 0), ("E", 2300, 2300)]},
    ]
    day = make_day(trips, names={"S": "Start", "E": "Cel"})
    install_day(day)

    narrow = planner.plan_flow(
        "Start", "Cel", when=WHEN,
        extra_pct=110, extra_floor_sec=900, extra_cap_sec=999999,    # deadline 1500
    )
    wide = planner.plan_flow(
        "Start", "Cel", when=WHEN,
        extra_pct=110, extra_floor_sec=1800, extra_cap_sec=999999,   # deadline 2400
    )
    assert "error" not in narrow and "error" not in wide

    assert _segs_by_num(narrow, "9", "tram") == []          # 2300 poza wąskim oknem
    newly_worse_wide = _segs_by_num(wide, "9", "tram")
    assert len(newly_worse_wide) == 1                        # ...ale w szerokim się mieści

    slower_narrow = _segs_by_num(narrow, "4", "bus")[0]["w"]
    slower_wide = _segs_by_num(wide, "4", "bus")[0]["w"]
    mid_narrow = _segs_by_num(narrow, "2", "bus")[0]["w"]
    mid_wide = _segs_by_num(wide, "2", "bus")[0]["w"]

    assert slower_narrow == 0.0                              # w wąskim oknie - dół skali
    assert slower_wide > slower_narrow                        # w szerokim - już nie dół, więc jaśniej
    assert mid_wide > mid_narrow                              # to samo dla środkowej opcji
    assert newly_worse_wide[0]["w"] == 0.0                    # nowy dół skali to teraz TA opcja


def test_no_relative_progress_gate_at_boarding_stop(install_day):
    """Regresja (znaleziona 2026-08-12, zgłoszona przez użytkownika jako
    "trasy przez Gaj znikają przy poszerzeniu okna"). _discover_segments
    miała kiedyś DODATKOWY, WZGLĘDNY filtr ("regułę postępu") porównujący
    wyjścia kursu do NAJLEPSZEGO 'latest' osiągalnego W MIEJSCU WSIADANIA
    przez JAKĄKOLWIEK inną, niepowiązaną przesiadkę. Gdy w miejscu wsiadania
    istniała osobna, szybka trasa (tu: Tramwaj 9, Srodek->Cel wprost), jej
    sama OBECNOŚĆ zawyżała punkt odniesienia i kasowała realne, pośrednie
    wyjście na tym samym, wolniejszym kursie (Autobus 7, Srodek->Posrodku->
    Cel) - mimo że fizycznie nie mają z sobą nic wspólnego. Naprawa
    2026-08-12 (druga tura, po zgłoszeniu użytkownika że "Tolerancja
    regresji" ma zostać na zawsze 0): ten filtr USUNIĘTY CAŁKOWICIE, nie
    tylko poprawiony - _discover_segments zostawia teraz tylko absolutny
    test "czy to jeszcze mieści się w oknie", a właściwą, stabilną jasność
    per pozycja liczy _refine_brightness (suffix-min). Tramwaj 9 wsiada się
    dopiero w szerokim oknie (odjazd 650) - test dowodzi, że jego obecność
    i tak nie wpływa na to, co _discover_segments w ogóle zwraca dla
    Autobusu 7."""
    trips = [
        {"trip_id": "feeder", "label": "Autobus 1",
         "stops": [("S", 0, 0), ("F", 10, 10)]},
        {"trip_id": "slow", "label": "Autobus 7",
         "stops": [("M", 300, 300), ("P", 400, 420), ("E", 700, 700)]},
        {"trip_id": "escape", "label": "Tramwaj 9",
         "stops": [("M", 650, 650), ("E", 680, 680)]},
    ]
    day = make_day(
        trips,
        names={"S": "Start", "F": "Feeder", "M": "Srodek", "P": "Posrodku", "E": "Cel"},
        siblings={"F": ("M",)},   # dojście piechotą z F do Srodka -> wsiadanie w Srodku to nie origin
    )
    install_day(day)
    dep_sec = 0
    deadline = 900   # szerokie okno - obejmuje też Tramwaj 9 (odjazd 650, przyjazd 680)

    earliest, arrived_by, trip_board = planner._forward(day, {"S"}, dep_sec, deadline)
    latest = planner._backward(day, {"E"}, dep_sec, deadline)
    assert latest.get("M") == 650, (
        "kontrola scenariusza: latest['M'] ma być zdominowane przez Tramwaj 9"
    )

    segs = planner._discover_segments(
        day, dep_sec, deadline, earliest, arrived_by, trip_board,
        latest, None, {"E"},   # origin_latest=None - izolacja od reguły cofnięcia
    )
    slow = next(s for s in segs if s["trip_id"] == "slow")
    exit_stops = [e[3] for e in slow["exits"]]
    assert "P" in exit_stops, (
        "wyjście w Posrodku (realny, pośredni przystanek TEGO kursu) zniknęło, "
        "mimo że w _discover_segments nie ma już żadnego filtra, który mógłby "
        "je odrzucić z powodu niepowiązanej, szybkiej trasy w miejscu wsiadania"
    )


def _origin_latest_scenario_day():
    """Start->Cel: 'Tramwaj 1' to najszybsza (bezpośrednia) trasa - definiuje
    best_arr, nie ma nic wspólnego z resztą scenariusza. 'Autobus 2'+
    'Autobus 3': realna, wolniejsza alternatywa przez Srodek i Posrodku -
    wsiada się w Srodku (NIE na własnym przystanku startowym), więc
    podlega regule cofnięcia. 'Tramwaj 9': osobna, niepowiązana trasa wprost
    ze Startu, wolniejsza niż najszybsza, ale szybsza niż alternatywa przez
    Srodek - istnieje WYŁĄCZNIE po to, by przy szerokim oknie stać się
    złapalna i zawyżyć punkt odniesienia reguły cofnięcia (origin_latest),
    mimo że fizycznie nie ma nic wspólnego z korytarzem przez Srodek."""
    trips = [
        {"trip_id": "best", "label": "Tramwaj 1",
         "stops": [("S", 0, 0), ("E", 200, 200)]},
        {"trip_id": "feeder", "label": "Autobus 2",
         "stops": [("S", 0, 0), ("M", 50, 50)]},
        {"trip_id": "slow_continue", "label": "Autobus 3",
         "stops": [("M", 200, 200), ("P", 300, 320), ("E", 500, 500)]},
        {"trip_id": "fast_direct", "label": "Tramwaj 9",
         "stops": [("S", 600, 600), ("E", 650, 650)]},
    ]
    return make_day(trips, names={"S": "Start", "M": "Srodek", "P": "Posrodku", "E": "Cel"})


def test_backtrack_reference_ignores_unrelated_faster_option_from_origin(install_day):
    """Regresja (znaleziona 2026-08-12, ta sama zgłoszona przez użytkownika
    "trasy znikają przy poszerzeniu okna" - druga, niezależna przyczyna).
    origin_latest (punkt odniesienia reguły cofnięcia przy wyborze miejsca
    wsiadania w _discover_segments) był liczony względem AKTUALNEGO
    deadline, nie względem stałego best_arr - gdy suwak okna poszerzał się
    na tyle, by złapać gdziekolwiek w mieście zupełnie niepowiązaną, szybszą
    trasę z przystanku startowego, origin_latest skakał w górę i kasował
    realnych kandydatów na zupełnie innych, wolniejszych korytarzach (tu:
    Autobus 3 przez Srodek), mimo że fizycznie nie mają z tamtą trasą nic
    wspólnego."""
    day = _origin_latest_scenario_day()
    install_day(day)

    narrow = planner.plan_flow(
        "Start", "Cel", when=WHEN,
        extra_pct=110, extra_floor_sec=320, extra_cap_sec=999999,   # deadline 520 - bez Tramwaju 9 (odjazd 600)
    )
    wide = planner.plan_flow(
        "Start", "Cel", when=WHEN,
        extra_pct=110, extra_floor_sec=500, extra_cap_sec=999999,   # deadline 700 - Tramwaj 9 złapany
    )
    assert "error" not in narrow and "error" not in wide
    assert narrow["best_arrival"] == planner._fmt_time(200)
    assert wide["best_arrival"] == planner._fmt_time(200)

    # kontrola scenariusza: Tramwaj 9 faktycznie widoczny dopiero w szerokim oknie
    assert _segs_by_num(narrow, "9", "tram") == []
    assert len(_segs_by_num(wide, "9", "tram")) == 1

    # kontrola scenariusza: alternatywa przez Srodek widoczna w wąskim oknie
    assert len(_segs_by_num(narrow, "3", "bus")) >= 1

    # sedno testu: poszerzenie suwaka nie ma prawa jej skasować
    assert len(_segs_by_num(wide, "3", "bus")) >= 1, (
        "alternatywa przez Srodek zniknęła po poszerzeniu okna - origin_latest "
        "został zawyżony przez niepowiązaną, szybszą trasę z przystanku "
        "startowego"
    )


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
        extra_pct=300, extra_floor_sec=0, extra_cap_sec=999999,
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
        extra_pct=300, extra_floor_sec=0, extra_cap_sec=999999,
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
        extra_pct=300, extra_floor_sec=0, extra_cap_sec=999999,
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
        extra_pct=300, extra_floor_sec=0, extra_cap_sec=999999,
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
    124-tkę jadącą na pętlę - a ta słusznie ginie na kotwicy końca (punkt 4:
    z pętli wraca się po własnych śladach). Wtedy ginie 134, za nim wszystko,
    co się o niego opierało, i cała mapa schodzi do jednej trasy.

    Zmierzone na żywych danych (Sosnowiecka -> Wojszyce, 15:37): 31
    kandydatów, 0 zatrzymanych - dokładnie ten układ."""
    day = _origin_passed_after_a_terminus_loop_day()
    install_day(day)

    result = planner.plan_flow(
        "Start", "Cel", when=WHEN,
        extra_pct=300, extra_floor_sec=0, extra_cap_sec=999999,
    )
    assert "error" not in result
    assert result["best_arrival"] == planner._fmt_time(1000)

    # 134 musi być na mapie i musi być narysowany OD przystanku startowego -
    # stamtąd się w niego wsiada.
    onward = _segs_by_num(result, "134", "bus")
    assert onward != [], "kurs mijający start w środku musi się zakotwiczyć na starcie"
    assert any(_coords_of(day, ["S_out"])[0] in seg["path"] for seg in onward)

    # ...a skoro sieć się nie rozpadła, widać też przesiadkę na Wezle, czyli
    # cały wachlarz, a nie samą najszybszą trasę (punkt 1).
    assert _segs_by_num(result, "5", "tram") != [], (
        "mapa zeszła do jednej trasy - kotwiczenie przycięło wszystko do zera"
    )
    assert result["degraded"] is False

    # ...ale ogon na pętlę nadal się nie rysuje (punkt 4).
    for seg in _segs_by_num(result, "124", "bus"):
        assert _coords_of(day, ["L"])[0] not in seg["path"]


def test_fallback_map_admits_that_it_is_a_fallback(install_day):
    """Tryb awaryjny plan_flow (kotwiczenie przycięło wszystko do zera)
    rysuje JEDNĄ trasę z jasnościami wpisanymi na sztywno - łamie punkty 1,
    2 i 9 kontraktu. Skoro zostaje jako zabezpieczenie, to musi się do tego
    przyznawać, żeby rzadka mapa nie wyglądała identycznie jak "tędy
    naprawdę nic nie jedzie".

    Układ: Autobus 1 dowozi na Zawrotkę, a jedyne, co stamtąd odjeżdża,
    wraca przez Pośrednią - czyli tam, skąd właśnie przyjechaliśmy - więc
    ogon 1 nie ma się o co zakotwiczyć (punkt 4). Przesiadka piętro niżej,
    na samej Pośredniej, też nie ratuje sprawy: Autobus 2 pojawia się tam
    dopiero po ponad 20 minutach czekania (WAIT_CAP_SEC), a tyle nie łączy
    już dwóch segmentów w jedną widoczną drogę. Autobus 2 traci więc swoją
    jedyną kotwicę początku i sieć schodzi do zera. Przystanek startowy nie
    leży po drodze żadnego z tych kursów, więc nie ratuje ich też kotwica
    "da się tu wsiąść"."""
    trips = [
        {"trip_id": "r1", "label": "Autobus 1",
         "stops": [("S", 0, 0), ("A", 100, 100), ("M", 200, 200)]},
        {"trip_id": "r2", "label": "Autobus 2",
         "stops": [("M", 1400, 1400), ("A", 1500, 1500), ("E", 1600, 1600)]},
    ]
    day = make_day(trips, names={"S": "Start", "A": "Posrednia",
                                 "M": "Zawrotka", "E": "Cel"})
    install_day(day)

    result = planner.plan_flow(
        "Start", "Cel", when=WHEN,
        extra_pct=300, extra_floor_sec=0, extra_cap_sec=999999,
    )
    assert "error" not in result
    assert result["segments"] != []          # coś jednak pokazujemy
    assert result["degraded"] is True, (
        "mapa zeszła do samej najszybszej trasy i musi to powiedzieć wprost"
    )


def test_a_normal_map_is_not_marked_as_a_fallback(install_day):
    """Odwrotna strona tego samego znacznika: zwykła mapa nie ma prawa go
    podnosić, inaczej stałby się bezużyteczny."""
    day = _three_tier_fan_day()
    install_day(day)

    result = planner.plan_flow(
        "Start", "Cel", when=WHEN,
        extra_pct=110, extra_floor_sec=1800, extra_cap_sec=999999,
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
        extra_pct=200, extra_floor_sec=0, extra_cap_sec=999999,
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
    """Tramwaj 1 i Tramwaj 4 jadą razem S->A->B; na B dosiada się Tramwaj 2
    (odjazd na tyle późny, że da się na niego przesiąść z Tramwaju 1) i dalej,
    B->C->E, jadą we trzy. Skład korytarza zmienia się więc dokładnie w B."""
    trips = [
        {"trip_id": "t1", "label": "Tramwaj 1",
         "stops": [("S", 0, 0), ("A", 100, 100), ("B", 200, 200),
                   ("C", 300, 300), ("E", 400, 400)]},
        {"trip_id": "t4", "label": "Tramwaj 4",
         "stops": [("S", 0, 0), ("A", 130, 130), ("B", 260, 260),
                   ("C", 390, 390), ("E", 520, 520)]},
        {"trip_id": "t2", "label": "Tramwaj 2",
         "stops": [("B", 400, 400), ("C", 480, 480), ("E", 560, 560)]},
    ]
    return make_day(trips, names={"S": "Start", "A": "A", "B": "B", "C": "C", "E": "Cel"})


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

    result = planner.plan_flow(
        "Start", "Cel", when=WHEN,
        extra_pct=200, extra_floor_sec=0, extra_cap_sec=999999,
    )
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

    result = planner.plan_flow(
        "Start", "Cel", when=WHEN,
        extra_pct=200, extra_floor_sec=0, extra_cap_sec=999999,
    )
    assert "error" not in result

    start = _coords_of(day, ["S"])[0]
    end = _coords_of(day, ["E"])[0]
    for seg in _segs_by_num(result, "1", "tram"):
        assert not (start in seg["path"] and end in seg["path"]), (
            "kawałek Tramwaju 1 przeszedł przez B jednym kawałkiem, mimo że "
            "skład korytarza zmienia się właśnie tam"
        )


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
        extra_pct=200, extra_floor_sec=0, extra_cap_sec=999999,
    )
    assert "error" not in result
    pieces = _segs_by_num(result, "7", "bus")
    assert len(pieces) == 1
    assert pieces[0]["path"] == _coords_of(day, ["S", "M", "E"])
    assert "corridor" not in pieces[0]


# ----------------------------------------------------------------------- 4 -

def _turning_loop_day():
    """Trzy kursy, które na poziomie MIEJSC wyglądają podobnie, a są czym innym.

    „loop"    S -> Rondo -> Pętla -> Rondo -> Cel — zahacza o pętlę PO DRODZE
              i jedzie dalej, do celu
    „koncowa" S -> Kamieńskiego -> Rondo -> Pętla -> Rondo — pętla jest jej
              KOŃCEM: po powrocie nie wiezie już nigdzie nowego
    „prosty"  S -> Kamieńskiego -> Kamieńskiego -> Cel — dwa sąsiednie słupki
              jednego miejsca, minięte jeden po drugim, jadąc prosto
    """
    trips = [
        {"trip_id": "loop", "label": "Autobus 102",
         "stops": [("S", 0, 0), ("R1", 300, 300), ("P", 420, 420),
                   ("R2", 540, 540), ("E", 900, 900)]},
        {"trip_id": "koncowa", "label": "Autobus 103",
         "stops": [("S", 60, 60), ("K1", 150, 150), ("R1", 400, 400),
                   ("P", 520, 520), ("R2", 640, 640)]},
        {"trip_id": "prosty", "label": "Tramwaj 7",
         "stops": [("S", 0, 0), ("K1", 300, 300), ("K2", 360, 360), ("E", 900, 900)]},
    ]
    return make_day(trips, names={
        "R1": "Rondo", "R2": "Rondo",
        "K1": "Kamieńskiego", "K2": "Kamieńskiego",
        "P": "Petla", "S": "Start", "E": "Cel",
    })


def _discovered(day, dep_sec=0):
    best_stop, best_arr, _ = planner._scan(day, {"S"}, {"E"}, dep_sec)
    deadline = planner._deadline(best_arr, dep_sec, extra_pct=200,
                                 extra_floor_sec=0, extra_cap_sec=999999)
    earliest, arrived_by, trip_board = planner._forward(day, {"S"}, dep_sec, deadline)
    latest = planner._backward(day, {"E"}, dep_sec, deadline)
    origin_latest = max(latest[s] for s in {"S"} if s in latest)
    segs = planner._discover_segments(
        day, dep_sec, deadline, earliest, arrived_by, trip_board,
        latest, origin_latest, {"E"},
    )
    return {seg["label"]: seg["stops"] for seg in segs}


def test_a_drawn_course_stops_where_the_loop_ends_it(install_day):
    """Kurs, dla którego pętla jest KOŃCEM, urywa się na niej. Mapa nie ma
    rysować wjazdu na pętlę końcową i natychmiastowego powrotu tą samą ulicą
    (punkt 4; zgłoszone 2026-08-29 na autobusie 102 pod Kosmonautów)."""
    day = _turning_loop_day()
    install_day(day)
    stops = _discovered(day)
    assert stops["Autobus 103"] == ["S", "K1", "R1", "P"], stops["Autobus 103"]


def test_a_course_that_rides_on_past_a_loop_is_drawn_whole(install_day):
    """...ale kurs, który zahacza o pętelkę PO DRODZE i wiezie dalej, jedzie
    dalej także na mapie.

    Odwrócone 2026-09-04. Reguła zawracania ucinała na PIERWSZYM powrocie do
    minionego miejsca, więc trafiała też w kursy jadące dalej. Zgłoszone na
    Bielany Wrocławskie - PKP -> Wojszyce o 13:29: Autobus 612 obsługuje
    osiedlową pętelkę i dopiero potem jedzie na Partynice, gdzie jest jedyna
    przesiadka w stronę celu. Cięcie zabierało JEDYNE wyjście ze startu -
    mapa rysowała 24 kawałki, ani jednego przy przystanku startowym, i wpadała
    w tryb awaryjny.

    Intencja punktu 4 zostaje (żadnych kikutów na pętli końcowej), zmienia się
    miara: pętla kończy kurs tylko wtedy, gdy po powrocie nie ma już ani
    jednego NOWEGO miejsca. Zmierzone na 24 prawdziwych relacjach: 22 bez
    żadnej zmiany, 2 wychodzą z trybu awaryjnego, zero regresji."""
    day = _turning_loop_day()
    install_day(day)
    stops = _discovered(day)
    assert stops["Autobus 102"] == ["S", "R1", "P", "R2", "E"], stops["Autobus 102"]


def test_two_stops_of_one_place_in_a_row_are_not_a_turn_back(install_day):
    """Miejsce bywa grubsze od słupka: linia potrafi minąć dwa jego przystanki
    jeden po drugim, jadąc PROSTO. Ucięcie takiego kursu odbierało jedynemu
    dojazdowi do celu jego wyjście i cała relacja spadała do trybu awaryjnego
    (POŚWIĘTNE -> KLECINA)."""
    day = _turning_loop_day()
    install_day(day)
    stops = _discovered(day)
    assert stops["Tramwaj 7"] == ["S", "K1", "K2", "E"], stops["Tramwaj 7"]


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

    assert _flow_of(wezel, "3")["flow"] == "end"
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
    result = planner.plan_flow("S", "T", WHEN)

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
    wezel = _node_named(planner.plan_flow("S", "T", WHEN), "K")

    assert _flow_of(wezel, "10")["flow"] == "through"
    assert _flow_of(wezel, "4")["flow"] == "end"


# ---- 13 - zawsze jakaś trasa, choćby za godzinę --------------------------

def _dzien_z_jednym_kursem(odjazd, przyjazd):
    """START -> CEL jednym autobusem, o zadanej godzinie i tylko o niej."""
    return make_day([{
        "trip_id": "T1", "label": "Autobus 1",
        "stops": [("START", odjazd, odjazd), ("CEL", przyjazd, przyjazd)],
    }])


def test_the_window_is_measured_from_the_departure_not_the_question():
    """Godzina czekania nie jest podróżą i nie ma rozdymać wachlarza.
    Pytanie o 10:00 i wyjazd o 12:00 dają dokładnie to samo okno, co pytanie
    zadane tuż przed wyjazdem - bo trasa trwa tyle samo."""
    day = _dzien_z_jednym_kursem(12 * 3600, 12 * 3600 + 1800)
    stop, arr, journey = planner._scan(day, ["START"], ["CEL"], 10 * 3600)
    assert stop == "CEL"
    wyjazd = planner._journey_start(day, journey, stop)
    assert wyjazd == 12 * 3600, "odczytany ma być odjazd pojazdu, nie godzina pytania"
    od_wyjazdu = planner._deadline(arr, wyjazd)
    od_pytania = planner._deadline(arr, 10 * 3600)
    assert od_wyjazdu < od_pytania, "czekanie rozdmuchało okno mapy"


def test_a_journey_that_starts_with_a_walk_still_reports_its_departure():
    """Odjazdem trasy jest odjazd PIERWSZEGO PRZEJAZDU, nie moment wyjścia
    z domu - przejście na sąsiedni słupek nie ma godziny w rozkładzie."""
    day = _dzien_z_jednym_kursem(12 * 3600, 12 * 3600 + 1800)
    day.stop_names["OBOK"] = "OBOK"
    day.stop_coords["OBOK"] = (51.11, 17.03)
    day.siblings = {"OBOK": ("START",), "START": ("OBOK",)}
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


def test_the_map_window_itself_starts_at_the_departure(monkeypatch):
    """To samo, ale przez całą ścieżkę: okno RYSOWANEJ mapy ma być policzone
    od wyjazdu. Autobus 12:00 -> 12:30 przy pytaniu o 10:00 daje naddatek
    z trzydziestu minut jazdy (7,5 min), a nie ze stu pięćdziesięciu minut
    czekania i jazdy razem (wtedy naddatek dobiłby do sufitu)."""
    day = _dzien_z_jednym_kursem(12 * 3600, 12 * 3600 + 1800)
    monkeypatch.setattr(gtfs, "load_day", lambda d: day)
    wynik = planner.plan_flow("START", "CEL",
                              datetime.datetime(2026, 8, 31, 10, 0))
    assert "error" not in wynik, wynik.get("error")
    assert wynik["starts_sec"] == 12 * 3600
    assert wynik["deadline_sec"] == 12 * 3600 + 1800 + 450


def test_a_relation_with_no_service_at_all_still_says_so():
    """Pusta mapa z komunikatem należy się relacji, której nie da się
    przejechać w ogóle - obietnica "zawsze jakaś trasa" nie może zmienić się
    w zmyślanie połączeń, których nie ma."""
    day = _dzien_z_jednym_kursem(8 * 3600, 8 * 3600 + 1800)
    stop, _, _ = planner._scan(day, ["CEL"], ["START"], 0)
    assert stop is None


# ----------------------------------------------- ręczne przedłużenie okna ---

def test_manual_horizon_widens_the_window_but_never_narrows_it(install_day):
    """Przycisk „+X min" nad mapą prosi o KONKRETNĄ szerokość okna
    (horizon_sec): dokłada kursy, które przy oknie z suwaków były już poza
    granicą. Węższa prośba nie może okna przyciąć - to zostaje domeną
    suwaków."""
    install_day(_three_tier_fan_day())
    waskie = dict(extra_pct=110, extra_floor_sec=0, extra_cap_sec=600)

    z_suwakow = planner.plan_flow("Start", "Cel", when=WHEN, **waskie)
    # 600 s trasy + 10% -> okno do 660 s: "excluded" (5000 s) daleko poza nim
    assert z_suwakow["limit_sec"] == 660
    assert _segs_by_num(z_suwakow, "9", "tram") == []

    przedluzone = planner.plan_flow("Start", "Cel", when=WHEN,
                                    horizon_sec=6000, **waskie)
    assert przedluzone["limit_sec"] == 6000
    assert len(_segs_by_num(przedluzone, "9", "tram")) == 1

    wezsze = planner.plan_flow("Start", "Cel", when=WHEN,
                               horizon_sec=60, **waskie)
    assert wezsze["limit_sec"] == z_suwakow["limit_sec"]


def test_manual_horizon_has_a_hard_ceiling(install_day):
    """Sufit stoi po stronie serwera, nie frontu: szerokość okna to wprost
    koszt skanu, więc żądanie z zewnątrz nie może go podnieść ponad
    MAX_HORIZON_SEC."""
    install_day(_three_tier_fan_day())
    wynik = planner.plan_flow("Start", "Cel", when=WHEN, horizon_sec=99_999,
                              extra_pct=110, extra_floor_sec=0, extra_cap_sec=600)
    assert wynik["limit_sec"] == planner.MAX_HORIZON_SEC == 2 * 3600


def test_a_node_weighs_as_much_as_what_lies_next_to_it(install_day):
    """Kropka niczego nie rusza (punkt 11), więc jasność BIERZE, a nie nadaje:
    węzeł waży tyle, co najlepszy kawałek, który go dotyka - tą samą,
    przeskalowaną miarą co linie (punkt 9). Bez tego setka kropek w bladej
    okolicy niosła ciężar obrazka zamiast korytarza, który prowadzi do celu
    (zgłoszone 2026-09-04). Najlepszy, a nie najgorszy: miejsce jest tak
    dobre, jak najlepsza rzecz, którą się z niego jedzie - minimum gasiłoby
    węzeł na najszybszej trasie, ilekroć mija go cokolwiek bladego."""
    install_day(_three_flows_at_one_node_day())
    wynik = planner.plan_flow("S", "T", WHEN)

    assert wynik["nodes"], "bez węzłów nie ma czego mierzyć"
    for wezel in wynik["nodes"]:
        obok = [
            seg["w"] for seg in wynik["segments"]
            if any(punkt[0] == wezel["lat"] and punkt[1] == wezel["lon"]
                   for punkt in seg.get("stops_t", ()))
        ]
        assert obok, f"węzeł {wezel['name']!r} nie leży przy żadnym kawałku"
        assert wezel["w"] == max(obok), (
            f"węzeł {wezel['name']!r} ma {wezel['w']}, a najjaśniejszy kawałek "
            f"przy nim {max(obok)}")


def _fast_and_slow_day():
    """Korytarz i objazd, oba w oknie. Tramwaj 1 wiezie S -> T wprost i
    najszybciej. Autobus 2 wozi S -> D, autobus 3 dowozi D -> T - ta sama
    podróż objazdem, wolniej, ale wciąż na czas. Węzeł D leży wyłącznie przy
    objeździe, więc ma być wyraźnie bledszy od startu."""
    return make_day([
        {"trip_id": "szybki", "label": "Tramwaj 1", "headsign": "CEL",
         "stops": [("S", 0, 0), ("T", 600, 600)]},
        {"trip_id": "objazd1", "label": "Autobus 2", "headsign": "OBJAZD",
         "stops": [("S", 0, 0), ("D", 300, 300)]},
        {"trip_id": "objazd2", "label": "Autobus 3", "headsign": "CEL",
         "stops": [("D", 420, 420), ("T", 840, 840)]},
    ])


def test_a_node_by_a_detour_is_paler_than_one_on_the_fast_route(install_day):
    """Druga połowa tej samej obietnicy: skoro kropka bierze jasność z tego,
    co przy niej leży, to węzeł stojący wyłącznie przy wolnym objeździe MUSI
    być bledszy niż ten na najszybszej trasie. Bez tego cała zmiana byłaby
    pustym polem - wszystkie kropki wychodziłyby na jedynkę."""
    install_day(_fast_and_slow_day())
    wezly = {w["name"]: w["w"] for w in planner.plan_flow("S", "T", WHEN)["nodes"]}

    assert wezly["S"] == 1.0
    assert wezly["D"] < wezly["S"]
