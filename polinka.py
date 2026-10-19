"""Polinka - kolej gondolowa Politechniki Wrocławskiej nad Odrą (zgłoszenie
#165). Stacje dokleja się do dnia tak samo jak kolej (patrz pkp.augment_day),
a sama przeprawa nie jest kursem, tylko przejściem o innym tempie, jak rower
na mapie (zgłoszenie #364, patrz bikes.map_places).

DLACZEGO RĘCZNIE. Polinki nie ma w żadnym rozkładzie: wrocławski GTFS jej nie
zna, a Politechnika, która ją prowadzi, publikuje tylko godziny otwarcia na
swojej stronie (SOURCE_URL). Nie ma też rozkładu w ścisłym sensie - dwie
kabiny kursują wahadłowo przez cały czas otwarcia. Wszystko, co tu stoi, jest
więc przepisane ręcznie i ma datę (UPDATED) - do sprawdzenia na stronie PWr
przy każdej zmianie.

DLACZEGO NIE KURS. Polinka staje z powodu pogody i jeździ na osobny bilet,
więc nie może zastąpić komunikacji - a jako kurs w rozkładzie wygrywała
o minutę i zostawała na mapie jedyną trasą (zgłoszenie #364). Jako przeprawa
dokłada się na gotową mapę komunikacji i niczego w niej nie przestawia.
Zostaje tylko wtedy, gdy mapa do niej dowozi, a po drugiej stronie rzeki
zdąży się jeszcze wsiąść w coś, co mapa rysuje, albo dojść do celu - ta sama
reguła co przy rowerze (bikes.map_places).

PRZEPRAWA. Kabiny jeżdżą w kółko, więc nie ma na co czekać: przeprawa trwa
RIDE_SEC w godzinach otwarcia. PWr podaje „poniżej trzech minut", więc trzy
minuty mylą się w bezpieczną stronę (zasada 2 w docs/PRINCIPLES.md).

KIEDY. Według PWr: od poniedziałku do piątku 7:00-18:00, z przerwą
13:30-14:00; w weekendy i święta nieczynna. Przestoje z powodu pogody (burza,
śnieg, silny wiatr, mgła) i przeglądy co pół roku są nie do przewidzenia.
Bilety MPK ani Urbancard nie obowiązują (osobny bilet z biletomatu, 4,60 zł).
"""

from datetime import date, timedelta

# Kiedy ostatnio sprawdzono dane z tego pliku na stronie PWr. Przy każdej
# zmianie czegokolwiek niżej: nowa data.
UPDATED = date(2026, 10, 12)
SOURCE_URL = "https://pwr.edu.pl/en/polinka"

ID_PREFIX = "POLINKA:"

# Współrzędne z OpenStreetMap (węzły aerialway=station 2395966361
# i 2395966364). Nazwy w nawiasie po stronie rzeki, jak „Na Grobli
# (Hydropolis)" w GTFS - dwie różne nazwy, więc dwa różne miejsca, a nie
# jeden plac z mostem pieszym przez Odrę (patrz gtfs._build_places).
STATIONS = {
    ID_PREFIX + "WW": ("Polinka (Wybrzeże Wyspiańskiego)", 51.1073039, 17.0583802),
    ID_PREFIX + "NG": ("Polinka (Na Grobli)", 51.1059580, 17.0533566),
}

OPEN_SEC = 7 * 3600
CLOSE_SEC = 18 * 3600
BREAK_SEC = (13 * 3600 + 30 * 60, 14 * 3600)
RIDE_SEC = 3 * 60

# Dymek przeprawy na mapie: to, czego pasażer nie zobaczy nigdzie indziej -
# godziny otwarcia, bilet i data sprawdzenia danych. Wiersz po wierszu, jak
# dodatkowe informacje w dymku auta (app.js: carTooltipHtml).
NOTES = [
    "Czynna pn–pt 7:00–18:00, przerwa 13:30–14:00",
    "W weekendy i święta nieczynna",
    "Osobny bilet, nie MPK",
    f"Godziny wpisane ręcznie, stan na {UPDATED:%d.%m.%Y}",
]


def _easter(year):
    """Niedziela Wielkanocna (algorytm Meeusa/Jonesa/Butchera)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def holidays(year):
    """Dni ustawowo wolne od pracy w Polsce (Wigilia od 2025 r.). Te, które
    wypadają w weekend, i tak nic tu nie zmieniają."""
    easter = _easter(year)
    fixed = [(1, 1), (1, 6), (5, 1), (5, 3), (8, 15), (11, 1), (11, 11),
             (12, 25), (12, 26)]
    days = {date(year, m, d) for m, d in fixed}
    if year >= 2025:
        days.add(date(year, 12, 24))
    days |= {easter, easter + timedelta(days=1),          # Wielkanoc
             easter + timedelta(days=49),                  # Zielone Świątki
             easter + timedelta(days=60)}                  # Boże Ciało
    return days


def runs_on(day):
    """Czy Polinka jeździ tego dnia (patrz nagłówek modułu)."""
    return day.weekday() < 5 and day not in holidays(day.year)


def boarding_at(sec):
    """Najwcześniej, o której da się wsiąść, stojąc na stacji o `sec` -
    None, gdy tego dnia już się nie zdąży. Kabina, która nie dojechałaby
    przed przerwą albo przed zamknięciem, nie rusza."""
    sec = max(sec, OPEN_SEC)
    if sec + RIDE_SEC > BREAK_SEC[0] and sec < BREAK_SEC[1]:
        sec = BREAK_SEC[1]
    return sec if sec + RIDE_SEC <= CLOSE_SEC else None


def augment_day(data, day):
    """Dokleja stacje do dnia - zawsze, bez względu na dzień.

    Wołane z gtfs.load_day PRZED budowaniem miejsc i mostów pieszych, tak jak
    kolej - stacja dostaje przejścia do przystanków obok tym samym mechanizmem
    co każdy słupek, i da się jej szukać po nazwie. Przejazdów nie ma: przeprawa
    nie jest kursem (patrz nagłówek modułu i map_crossings).
    """
    for stop_id, (name, lat, lon) in STATIONS.items():
        data.stop_names[stop_id] = name
        data.stop_coords[stop_id] = (lat, lon)


def map_crossings(day, service_day, arrivals, onward, target_set):
    """Przeprawy Polinką na mapie przepływów - po jednej na kierunek, który
    przeszedł regułę sensu - w kształcie narysowanego kawałka
    (planner._finalize_segments), więc front rysuje je jak każdą linię:
    plakietka „Polinka", kolor Polinki, godziny pod kursorem. Kawałek niesie
    przeprawę, po której najwcześniej jest się w celu, a w `tip` - własny
    dymek: każdy pokazany kierunek z godziną przy stacji, czasem przeprawy
    i przyjazdem do celu (bez godzin samej przeprawy) i NOTES (patrz app.js:
    segTipHtml).

    Ta sama reguła i te same dane co przy rowerze (bikes.map_places):
    `arrivals()` mówi, o której mapa dowozi w pobliże stacji,
    `onward()` - w co narysowanego da się wsiąść po drugiej stronie i o której
    jest się wtedy w celu. Przeprawa, do której mapa nie dowozi albo po której
    nie ma w co wsiąść ani jak dojść do celu, nie istnieje. Inaczej niż przy
    rowerze niczego się tu nie wybiera - kierunki są dwa.
    """
    if not runs_on(service_day):
        return []
    # Tu, a nie na górze: bikes ciągnie gtfs, a gtfs - ten moduł.
    import bikes

    reach = arrivals()
    reach_cells = bikes._cells(day, reach)
    ahead = onward()
    ahead_cells = bikes._cells(day, set(ahead) | set(target_set))
    out, rides = [], []
    for from_id, to_id in ((a, b) for a in STATIONS for b in STATIONS if a != b):
        from_name, from_lat, from_lon = STATIONS[from_id]
        to_name, to_lat, to_lon = STATIONS[to_id]
        before = bikes._pareto(
            (board, rides)
            for at, rides in bikes._before_bike(
                reach_cells, reach, {"lat": from_lat, "lon": from_lon}, day.walk_mps)
            if (board := boarding_at(at)) is not None)
        after = bikes._after_bike(ahead_cells, ahead, target_set,
                                  {"lat": to_lat, "lon": to_lon}, day.walk_mps)
        options = bikes._journeys(before, RIDE_SEC, after)
        if options:
            best = min(options, key=lambda o: (o["arrival"], o["bike_at"]))
            ends = [[round(from_lat, 5), round(from_lon, 5)],
                    [round(to_lat, 5), round(to_lon, 5)]]
            out.append({
                "path": ends,
                "num": "Polinka",
                "kind": "cable",
                "w": 1.0,
                "stops_t": [[*ends[0], best["bike_at"]],
                            [*ends[1], best["bike_at"] + RIDE_SEC]],
                "stops_n": [from_name, to_name],
                "arrive": best["arrival"],
            })
            # O której jest się przy stacji i skąd się do niej dochodzi - jak
            # „jesteś przy nim" w dymku auta (bikes._arrival_at, to samo co
            # przy kropce roweru).
            near = bikes._arrival_at(day, reach_cells, reach,
                                     {"lat": from_lat, "lon": from_lon})
            rides.append({"from": _side(from_name), "to": _side(to_name),
                          "at": near["at"], "walk_sec": near["walk_sec"],
                          "walk_from": near["from"], "sec": RIDE_SEC,
                          "arrival": best["arrival"]})
    tip = {"title": "Polinka", "rides": rides, "lines": NOTES}
    for seg in out:
        seg["tip"] = tip
    return out


def _side(name):
    """„Polinka (Na Grobli)" -> „Na Grobli" - strona rzeki, krótko do dymka."""
    return name.partition("(")[2].rstrip(")")


def stations_geo():
    """Stacje do narysowania na mapie (/api/stops), jak pkp.all_stations_geo."""
    return [{"name": name, "lat": lat, "lon": lon}
            for name, lat, lon in STATIONS.values()]
