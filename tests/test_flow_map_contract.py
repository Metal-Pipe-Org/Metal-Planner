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
    planner._refine_brightness(day, segs, {"E"}, deadline, best_arr)

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
