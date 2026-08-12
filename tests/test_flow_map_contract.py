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
  9. pełny zakres jasności zawsze wykorzystany - liczony względem
     najgorszej FAKTYCZNIE pokazanej opcji, nie względem pełnej szerokości
     okna czasowego (poszerzanie okna nie rozjaśnia już pokazanych opcji)
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

def test_deadline_scales_with_best_route_duration():
    # 150% domyślnie -> 50% czasu trasy jako naddatek
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
    mid_to_end = next(s for s in bus_pieces if s["path"] == _coords_of(day, ["M", "E"]))
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

    # fallback geometrii (punkt 6): bez shape_id, ścieżka to łamana po
    # rzeczywistych współrzędnych przystanków.
    assert start_to_mid["path"] == _coords_of(day, ["S", "M"])
    assert mid_to_end["path"] == _coords_of(day, ["M", "E"])


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
        latest, origin_latest, {"E"}, 0,
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
