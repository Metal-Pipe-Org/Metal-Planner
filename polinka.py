"""Polinka - kolej gondolowa Politechniki Wrocławskiej nad Odrą (zgłoszenie
#165), doklejana do dnia tak samo jak kolej (patrz pkp.augment_day).

DLACZEGO RĘCZNIE. Polinki nie ma w żadnym rozkładzie: wrocławski GTFS jej nie
zna, a Politechnika, która ją prowadzi, publikuje tylko godziny otwarcia na
swojej stronie (SOURCE_URL). Nie ma też rozkładu w ścisłym sensie - dwie
kabiny kursują wahadłowo przez cały czas otwarcia. Wszystko, co tu stoi, jest
więc przepisane ręcznie i ma datę (UPDATED), a front mówi to wprost przy
każdym przejeździe Polinką (patrz NOTE).

KURSY. Bez rozkładu liczymy ostrożnie (zasada 2 w docs/PRINCIPLES.md - szacunek
myli się w bezpieczną stronę): odjazd co HEADWAY_SEC w obie strony, przejazd
RIDE_SEC. PWr podaje „poniżej trzech minut" i do 370 osób na godzinę w każdą
stronę przy kabinie na 15 osób, więc naprawdę jedzie częściej - ale na odjazd
co 5 minut zdąży się zawsze.

KIEDY. Według PWr: od poniedziałku do piątku 7:00-18:00, z przerwą
13:30-14:00; w weekendy i święta nieczynna. Przestoje z powodu pogody (burza,
śnieg, silny wiatr, mgła) i przeglądy co pół roku są nie do przewidzenia - tego
nie udajemy, mówi o tym notka.

Bilety MPK ani Urbancard nie obowiązują (osobny bilet z biletomatu), więc
notka mówi też o cenie: to jedyny odcinek w wyszukiwarce, za który płaci się
osobno, a pasażer z biletem miejskim musi o tym wiedzieć, zanim wsiądzie.
"""

from datetime import date, timedelta

# Kiedy ostatnio sprawdzono dane z tego pliku na stronie PWr - front pokazuje
# tę datę w notce. Przy każdej zmianie czegokolwiek niżej: nowa data.
UPDATED = date(2026, 10, 12)
SOURCE_URL = "https://pwr.edu.pl/en/polinka"

ID_PREFIX = "POLINKA:"
# Etykieta jak „Tramwaj 17": rodzaj i numer (patrz planner._line_parts) - na
# plakietce staje „Polinka", a rodzaj `cable` daje jej własny kolor.
LABEL = "Kolejka Polinka"

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
HEADWAY_SEC = 5 * 60
RIDE_SEC = 3 * 60

FARE = "4,60 zł, ulgowy 2,30 zł"

NOTE = (f"Rozkład wpisany ręcznie — Polinka nie publikuje go online. "
        f"Stan na {UPDATED:%d.%m.%Y}: pn–pt 7:00–18:00, przerwa 13:30–14:00, "
        f"w weekendy i święta nieczynna; przy złej pogodzie bywa zamknięta. "
        f"Bilety MPK nie obowiązują ({FARE}).")


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


def departures():
    """Godziny odjazdów z każdej stacji - te same w obie strony."""
    out = []
    for dep in range(OPEN_SEC, CLOSE_SEC - RIDE_SEC + 1, HEADWAY_SEC):
        # Kabina, która nie zdąży dojechać przed przerwą, nie rusza wcale.
        if dep + RIDE_SEC > BREAK_SEC[0] and dep < BREAK_SEC[1]:
            continue
        out.append(dep)
    return out


def augment_day(data, day):
    """Dokleja stacje (zawsze) i przejazdy (w dni kursowania) do dnia.

    Wołane z gtfs.load_day PRZED budowaniem miejsc i mostów pieszych, tak jak
    kolej - stacja dostaje przejścia do przystanków obok tym samym mechanizmem
    co każdy słupek. Stacje stoją także w dni bez kursów, żeby dało się do
    nich dojść i żeby wyszukiwanie z nazwą stacji nie przestawało działać
    w sobotę.
    """
    for stop_id, (name, lat, lon) in STATIONS.items():
        data.stop_names[stop_id] = name
        data.stop_coords[stop_id] = (lat, lon)
    if not runs_on(day):
        return
    (a, _), (b, _) = STATIONS.items()
    for from_s, to_s in ((a, b), (b, a)):
        headsign = STATIONS[to_s][0]
        for dep in departures():
            trip = trip_id(from_s, to_s, dep)
            data.trip_info[trip] = (LABEL, headsign)
            data.conns.append((dep, dep + RIDE_SEC, from_s, to_s, trip))


def trip_id(from_s, to_s, dep):
    """Każdy przejazd to osobny kurs: kabina jedzie z jednej stacji na drugą
    i tyle. Identyfikator niesie wszystko, co o nim wiadomo (patrz
    trip_rows) - nie ma go w żadnej bazie."""
    return f"{from_s}>{to_s[len(ID_PREFIX):]}>{dep}"


def trip_rows(trip):
    """Cały przejazd jako [(stop_id, przyjazd, odjazd), ...] - odpowiednik
    stop_times dla gtfs.trip_path i timetables."""
    from_s, to_code, dep = trip.split(">")
    dep = int(dep)
    return [(from_s, dep, dep),
            (ID_PREFIX + to_code, dep + RIDE_SEC, dep + RIDE_SEC)]


def stations_geo():
    """Stacje do narysowania na mapie (/api/stops), jak pkp.all_stations_geo."""
    return [{"name": name, "lat": lat, "lon": lon}
            for name, lat, lon in STATIONS.values()]
