"""Rozkłady: kursy linii i tablica odjazdów z przystanku (timetables.py).

Co tu jest naprawdę sprawdzane - czyli co w tym module może pójść nie tak,
a nie widać tego z ekranu:

  * kursy jednej linii NIE są jedną trasą. Linia ma dwa kierunki i do tego
    kursy skrócone; wariant to ciąg przystanków, nie headsign, bo dwa różne
    przebiegi z tym samym napisem na czole to dwie różne listy przystanków.
    Wariant niesie LICZBĘ swoich kursów - po niej odróżnia się kierunek
    jeżdżący cały dzień od zjazdu do zajezdni raz na dobę;
  * odjazd z przystanku to połączenie, które się w nim ZACZYNA - ostatni
    przystanek kursu nie jest odjazdem, bo nie da się tam wsiąść;
  * tablica dnia D obejmuje ogon doby D-1 (autobus nocny o 00:20 należy do
    kalendarza soboty, ale odjeżdża w niedzielę) - to samo, co gwarantuje
    test_service_day.py dla wyszukiwarki, tylko widziane od strony tablicy;
  * godziny po północy pokazujemy na tarczy zegara: 24:20 to 00:20.

Baza jest prawdziwym SQLite (jak w test_service_day.py), bo rozkład linii
czyta z niej wprost - syntetyczny DayData by tego nie pokrył.
"""

import datetime
import sqlite3

import pytest

import gtfs
import timetables
import update_gtfs

SATURDAY = datetime.date(2026, 8, 22)
SUNDAY = datetime.date(2026, 8, 23)

# Dwa słupki jednego miejsca ("RYNEK") - tablica ma je scalić w jedną.
STOPS = {
    "R1": ("RYNEK", 51.10, 17.00),
    "R2": ("RYNEK", 51.1001, 17.0001),
    "B": ("BROCHÓW", 51.12, 17.02),
    "C": ("CENTRUM", 51.14, 17.04),
    "Z": ("Zajezdnia GAJ", 51.16, 17.06),
}

# (trip_id, route_id, service_id, headsign, [(stop, sekundy), ...])
TRIPS = [
    # Kierunek podstawowy linii 17, dwa kursy - wariant "pełny".
    ("t17_a", "R17", "CODZIENNIE", "CENTRUM",
     [("R1", 21600), ("B", 22200), ("C", 22800)]),
    ("t17_b", "R17", "CODZIENNIE", "CENTRUM",
     [("R1", 25200), ("B", 25800), ("C", 26400)]),
    # Ten sam napis na czole, ale inny przebieg (bez BROCHOWA) - osobny wariant.
    ("t17_skrot", "R17", "CODZIENNIE", "CENTRUM",
     [("R1", 27000), ("C", 27900)]),
    # Kurs zjazdowy do zajezdni, wyjeżdżający po północy: 24:20 -> 24:50.
    ("t17_noc", "R17", "SOBOTA", "Zajezdnia GAJ",
     [("R2", 87600), ("Z", 89400)]),
    # Druga linia z tego samego słupka - tablica ma ją mieć obok 17.
    ("t9", "R9", "CODZIENNIE", "BROCHÓW",
     [("R2", 21900), ("B", 22500)]),
]


@pytest.fixture
def feed(tmp_path, monkeypatch):
    path = tmp_path / "gtfs.sqlite"
    db = sqlite3.connect(path)
    db.executescript(update_gtfs.SCHEMA)
    db.executemany(
        "INSERT INTO stops VALUES (?,?,?,?)",
        [(sid, name, lat, lon) for sid, (name, lat, lon) in STOPS.items()],
    )
    db.executemany(
        "INSERT INTO routes VALUES (?,?,'',?)",
        [("R17", "17", 0), ("R9", "9", 3)],
    )
    db.executemany(
        "INSERT INTO calendar VALUES (?,?,?,?,?,?,?,?,'20260801','20260930')",
        [
            ("CODZIENNIE", 1, 1, 1, 1, 1, 1, 1),
            ("SOBOTA", 0, 0, 0, 0, 0, 1, 0),
        ],
    )
    for trip_id, route_id, service_id, headsign, stops in TRIPS:
        db.execute("INSERT INTO trips VALUES (?,?,?,?,NULL)",
                   (trip_id, route_id, service_id, headsign))
        db.executemany(
            "INSERT INTO stop_times VALUES (?,?,?,?,?)",
            [(trip_id, i, stop, sec, sec) for i, (stop, sec) in enumerate(stops)],
        )
    db.commit()
    db.close()

    monkeypatch.setattr(gtfs, "DB_PATH", path)
    gtfs._day_cache.clear()
    yield path
    gtfs._day_cache.clear()


def _o_godzinie(tablica, godzina):
    """Jeden odjazd tablicy po godzinie - czytelniej niż indeks, bo kolejność
    zaczyna się od kursów nocnych z doby poprzedniej."""
    return next(d for d in tablica["departures"] if d["t"] == godzina)


# ------------------------------------------------------- rozkład linii ----

def test_linie_sa_wypisane_tramwaje_przed_autobusami(feed):
    assert timetables.all_lines() == [
        {"num": "17", "mode": "tram", "label": "Tramwaj 17"},
        {"num": "9", "mode": "bus", "label": "Autobus 9"},
    ]


def test_kurs_skrocony_to_osobny_wariant(feed):
    """Ten sam headsign, inny ciąg przystanków - dwa różne rozkłady. Zlanie
    ich w jeden dałoby tablicę godzin podpisaną przystankiem, przez który
    połowa tych kursów nie przejeżdża."""
    rozklad = timetables.line_timetable("17", SUNDAY)
    warianty = rozklad["variants"]
    assert [w["trips"] for w in warianty] == [2, 1]           # pełny przed skróconym
    assert [len(w["stops"]) for w in warianty] == [3, 2]
    assert all(w["headsign"] == "CENTRUM" for w in warianty)


def test_wariant_niesie_pelna_liste_przystankow_bez_godzin(feed):
    """Rozkład linii odpowiada na "którędy jedzie", a nie "o której" - godziny
    wiszą na słupku (stop_board) i to on odpowiada na to drugie. Bez tego
    odpowiedź na jedną linię to kilkaset kursów razy kilkadziesiąt godzin,
    z których front nie rysuje ani jednej."""
    pelny = timetables.line_timetable("17", SUNDAY)["variants"][0]
    assert [s["name"] for s in pelny["stops"]] == ["RYNEK", "BROCHÓW", "CENTRUM"]
    assert pelny["trips"] == 2
    assert not any(isinstance(w.get("trips"), list)
                   for w in timetables.line_timetable("17", SUNDAY)["variants"])


def test_przystanek_wariantu_niesie_swoj_slupek(feed):
    """Rozkład linii i tablica przystanku muszą mówić o tym samym słupku -
    na tym stoi skok "odjazdy tej linii stąd": front bierze `id` z wariantu
    i podstawia je jako wybrany słupek tablicy. Nazwa by nie wystarczyła,
    bo oba słupki RYNKU nazywają się tak samo, a linia jedzie jednym z nich."""
    wariant = timetables.line_timetable("17", SUNDAY)["variants"][0]
    assert [s["id"] for s in wariant["stops"]] == ["R1", "B", "C"]

    tablica = timetables.stop_board("RYNEK", SUNDAY)
    assert wariant["stops"][0]["id"] in [p["id"] for p in tablica["points"]]


def test_kurs_po_polnocy_nalezy_do_doby_ktora_wyjechal(feed):
    """Zjazd o 24:20 siedzi w kalendarzu SOBOTY i w sobotnim rozkładzie linii
    ma być - jednostką jest tu doba rozkładowa, a nie kalendarzowa (inaczej
    niż na tablicy przystanku, patrz test niżej)."""
    sobota = timetables.line_timetable("17", SATURDAY)
    zjazd = [w for w in sobota["variants"] if w["to"] == "Zajezdnia GAJ"]
    assert len(zjazd) == 1 and zjazd[0]["trips"] == 1
    assert [s["name"] for s in zjazd[0]["stops"]] == ["RYNEK", "Zajezdnia GAJ"]


def test_linia_bez_kursow_tego_dnia_nie_jest_bledem(feed):
    """Rozkład na dzień, w którym linia nie jeździ, ma być pustą tablicą
    z wyjaśnieniem - nie komunikatem "nie ma takiej linii"."""
    tylko_sobotnia = timetables.line_timetable("17", SUNDAY)
    assert all(w["to"] != "Zajezdnia GAJ" for w in tylko_sobotnia["variants"])

    nieznana = timetables.line_timetable("123", SUNDAY)
    assert "error" in nieznana and not nieznana.get("variants")


# --------------------------------------------------- tablica przystanku ----

def test_tablica_laczy_slupki_jednego_miejsca(feed):
    """Wpisanie "RYNEK" ma dać odjazdy z OBU słupków tego placu - tak samo,
    jak wyszukiwarka wsiada na dowolnym z nich (patrz gtfs._build_places)."""
    tablica = timetables.stop_board("RYNEK", SUNDAY)
    assert tablica["stop"] == "RYNEK"
    # 00:20 to zjazd sobotni - w niedzielę odjeżdża pierwszy (patrz niżej)
    assert [d["t"] for d in tablica["departures"]] == [
        "00:20", "06:00", "06:05", "07:00", "07:30",
    ]
    assert {d["stop"] for d in tablica["departures"]} == {"R1", "R2"}


def test_tablica_wymienia_slupki_z_ktorych_cos_odjezdza(feed):
    """Scalone miejsce trzeba dać się rozłożyć z powrotem na słupki - to one
    są grupami kierunków ("z tego jedzie się do centrum, z tamtego na pętlę").
    Front filtruje tablicę po `departures[].stop`, więc serwer dokłada do
    słupka tylko to, czego z odjazdów nie policzy: nazwę i współrzędne."""
    tablica = timetables.stop_board("RYNEK", SUNDAY)
    assert [p["id"] for p in tablica["points"]] == ["R1", "R2"]
    assert {p["name"] for p in tablica["points"]} == {"RYNEK"}
    assert tablica["points"][0]["lat"] == 51.1
    # Każdy odjazd wskazuje swój słupek - inaczej nie dałoby się zawęzić.
    assert {d["stop"] for d in tablica["departures"]} <= {p["id"] for p in tablica["points"]}


def test_slupek_bez_odjazdow_nie_wchodzi_do_wyboru(feed):
    """CENTRUM jest końcem trasy - nie da się tam wsiąść, więc nie ma czego
    wybierać (i pusta pozycja nie ma się z czego wziąć)."""
    assert timetables.stop_board("CENTRUM", SUNDAY)["points"] == []


def test_ostatni_przystanek_kursu_nie_jest_odjazdem(feed):
    """Do kursu, który się tu kończy, nie da się wsiąść - i nie ma go na
    tablicy odjazdów."""
    assert not timetables.stop_board("CENTRUM", SUNDAY)["departures"]
    assert timetables.stop_board("BROCHÓW", SUNDAY)["departures"]


def test_odjazdy_wskazuja_swoja_linie_z_kierunkiem(feed):
    tablica = timetables.stop_board("RYNEK", SUNDAY)
    linie = tablica["lines"]
    # Tramwaje przed autobusami, w obrębie linii po kierunku - ten sam
    # porządek, co w grupkach numerów na mapie przepływów.
    assert [(l["mode"], l["num"], l["headsign"]) for l in linie] == [
        ("tram", "17", "CENTRUM"),
        ("tram", "17", "Zajezdnia GAJ"),
        ("bus", "9", "BROCHÓW"),
    ]
    o_szostej = _o_godzinie(tablica, "06:00")
    assert linie[o_szostej["line"]]["num"] == "17"
    assert linie[o_szostej["line"]]["count"] == 3


def test_ogon_nocy_wchodzi_na_tablice_nastepnego_dnia(feed):
    """Zjazd sobotni odjeżdża o 00:20 - czyli w niedzielę, i tam ma być
    widoczny (patrz gtfs.PREV_DAY_SEC)."""
    niedziela = timetables.stop_board("RYNEK", SUNDAY)
    nocne = [d for d in niedziela["departures"] if d["t"] == "00:20"]
    assert len(nocne) == 1
    assert nocne[0]["trip"].startswith(gtfs.PREV_DAY_PREFIX)
    assert nocne[0]["sec"] == 1200          # oś doby zaczyna się o północy


# ------------------------------------------------------------- jeden kurs ----

def test_kurs_zza_polnocy_nie_stoi_na_tablicy_dwa_razy(feed):
    """Zjazd o 24:20 należy do kalendarza SOBOTY, ale odjeżdża w niedzielę -
    i tylko na tablicy niedzieli ma prawo stać. Oś doby sięga poza 24 h dla
    wyszukiwarki (patrz gtfs.load_day), więc bez odcięcia ten sam kurs byłby
    na obu tablicach: raz jako własna nadwyżka soboty, raz jako ogon nocy
    w niedzielę."""
    sobota = timetables.stop_board("RYNEK", SATURDAY)
    assert all(d["sec"] < gtfs.PREV_DAY_SEC for d in sobota["departures"])
    assert "00:20" not in [d["t"] for d in sobota["departures"]]

    niedziela = timetables.stop_board("RYNEK", SUNDAY)
    assert [d["t"] for d in niedziela["departures"]].count("00:20") == 1


def test_kurs_pokazuje_przebieg_od_wskazanego_slupka(feed):
    tablica = timetables.stop_board("RYNEK", SUNDAY)
    odjazd = _o_godzinie(tablica, "06:00")            # linia 17 z R1
    kurs = timetables.trip_detail(odjazd["trip"], SUNDAY, odjazd["stop"], odjazd["sec"])

    assert kurs["num"] == "17" and kurs["mode"] == "tram"
    assert [s["t"] for s in kurs["stops"]] == ["06:00", "06:10", "06:20"]
    assert kurs["board_index"] == 0
    assert len(kurs["tail"]) == len(kurs["path"]) == 3


def test_kurs_wsiadany_w_srodku_ma_krotszy_ogon(feed):
    """`tail` to odpowiedź na "dokąd stąd pojedzie" - a nie na to, którędy
    ten pojazd już przejechał."""
    brochow = timetables.stop_board("BROCHÓW", SUNDAY)["departures"][0]
    kurs = timetables.trip_detail(brochow["trip"], SUNDAY, brochow["stop"], brochow["sec"])
    assert kurs["board_index"] == 1
    assert len(kurs["path"]) == 3
    assert len(kurs["tail"]) == 2


def test_nieznany_kurs_to_blad_a_nie_wyjatek(feed):
    assert "error" in timetables.trip_detail("nie_ma_takiego", SUNDAY)
