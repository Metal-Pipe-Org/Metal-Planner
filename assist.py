"""Asystent podróży - "co mam TERAZ zrobić", odpowiedziane wokół pytającego.

Mapa przepływów odpowiada na pytanie "jak się tam dostać" i odpowiada na nie
w świecie: linia narysowana po swoich torach mówi, w którą stronę jedzie, bez
tłumaczenia. Lista propozycji odpowiada na to samo pytanie wierszami. Asystent
pyta o coś węższego - nie "jak dojechać", tylko "co zrobić w ciągu najbliższej
minuty" - i musi odpowiedzieć tak samo jak mapa, czyli WOKÓŁ CZŁOWIEKA:

    145 · za 3 min · 12:41

zmusza, żeby symbole z powrotem przykleić do rzeczywistości - która krawędź,
w którą stronę, który z dwóch stojących tu pojazdów. Kreska wychodząca
z konkretnej krawędzi w prawdziwym kierunku, z numerem postawionym przy tej
krawędzi, nie zmusza do niczego.

Nie ma tu żadnego nowego algorytmu i ma nie być. Wszystko, czego asystent
potrzebuje - opcje, ich godziny, ich geometria - policzyła już mapa
przepływów (planner.plan_flow). Ten moduł robi z tego jedną rzecz: sprowadza
propozycje do PIERWSZYCH RUCHÓW.

CO TO JEST OPCJA. Nie trasa, tylko pierwszy ruch: "dojdź na tę krawędź
i wsiądź w tę linię o tej godzinie". Dwie różne trasy, które zaczynają się
tym samym wsiadaniem, są dla stojącego na chodniku jedną decyzją - różnią się
dopiero za pół godziny, a do tego czasu robi się dokładnie to samo. Dlatego
propozycje grupujemy po (linia, kierunek, krawędź), a z każdej grupy zostaje
ta, która dowozi NAJWCZEŚNIEJ - reszta jest tym samym ruchem, tylko gorzej
rozegranym później.

JEDNA MIARA I JEDEN PRÓG (zasada 1 z docs/PRINCIPLES.md). Miarą jest
wyłącznie: **o ile później niż najszybciej jest się w celu**. Próg tej miary
ustawia pokrętło pod zębatką i nic poza nim nie decyduje o tym, co widać -
nie ma tu ani limitu "pokaż pięć najlepszych", ani wyjątków "ta opcja jest
dziwna, schowaj ją". Próg działa też GŁĘBIEJ niż na wyniku: idzie wprost do
plan_flow jako okno (`window_sec`), więc opcje nie są wybierane z mapy
policzonej pod inną miarę - cała odpowiedź powstaje pod tą jedną.

GODZINY SĄ Z ROZKŁADU (zasada 2). Ani jedna liczba tutaj nie jest liczona
z prędkości: odjazdy, przyjazdy i godzina w celu przychodzą z etapów, które
złożył planner. Jedyny szacunek to przejście pieszo - liczone hojnie
i zawsze w tę samą stronę (gtfs.walk_time_sec), tak samo jak wszędzie indziej.
"""

import math
from datetime import datetime

import gtfs
import onboard
import planner
import sidenum

# Domyślny próg: o ile minut później niż najszybciej wolno być w celu.
# Kwadrans - zmierzone na pięciu relacjach (2026-09-20): przy dziesięciu
# minutach połowa z nich dawała JEDNĄ opcję, czyli asystent wybierał za
# człowieka zamiast pokazać wachlarz; przy kwadransie wychodzi od dwóch do
# dziesięciu, a przy półgodzinie dokładają się przejazdy w stronę przeciwną
# do celu, byle dojechać dwa razy dłużej. Pokrętło pod zębatką, nigdy
# w .env: to ustawienie wyglądu odpowiedzi, nie sekret.
DEFAULT_WINDOW_MIN = 15
MIN_WINDOW_MIN = 1
MAX_WINDOW_MIN = 60          # sufit pokrętła - pilnowany tutaj, przy odczycie

# Ile propozycji każemy plannerowi poskładać. Wysoko i na sztywno, bo z nich
# dopiero powstają opcje: kilka propozycji potrafi zwinąć się do jednego
# pierwszego ruchu, więc szukanie "sześciu tras" dałoby czasem dwie opcje.
# To NIE jest drugi próg - o tym, co widać, rozstrzyga wyłącznie okno wyżej.
JOURNEY_LIMIT = planner.MAX_JOURNEY_LIMIT


def window_sec(minutes):
    """Pokrętło (w minutach) -> okno w sekundach, z sufitem."""
    if minutes is None:
        minutes = DEFAULT_WINDOW_MIN
    return int(max(MIN_WINDOW_MIN, min(MAX_WINDOW_MIN, minutes))) * 60


# Jak długa jest KRESKA opcji. Nie cały pierwszy przejazd - dobrane do
# KADRU, a nie do trasy. Kadr asystenta obejmuje mnie i krawędzie w zasięgu
# dojścia pieszo (gtfs.WALK_M, 600 m), czyli coś rzędu półtora kilometra;
# kreska długości ośmiuset metrów wychodziła w nim poza ekran razem z grotem,
# więc kierunku - jedynej rzeczy, o którą ten widok pyta - nie było widać
# wcale. Trzysta pięćdziesiąt metrów mieści się w kadrze w całości i pokazuje
# wyjazd z krawędzi razem z pierwszym skrętem. Dalszy przebieg dorysowuje się
# po dotknięciu numeru i dotyczy wtedy JEDNEJ opcji.
HEAD_M = 350


def _metres(a, b):
    """Odległość w linii prostej - do mierzenia długości kreski, nie do
    liczenia czegokolwiek, co zobaczy użytkownik jako czas."""
    lat = math.radians((a[0] + b[0]) / 2)
    return math.hypot((b[0] - a[0]) * 111320,
                      (b[1] - a[1]) * 111320 * math.cos(lat))


def _trim(path, metres):
    """Początek łamanej o zadanej długości, z ostatnim punktem dociętym
    dokładnie na tej długości (a nie na najbliższym wierzchołku).

    Geometria zostaje PRAWDZIWA (punkt 6 kontraktu) - to jest ten sam
    przebieg po ulicach i torach, tylko krótszy. Nic tu nie jest prostowane
    ani przesuwane.
    """
    out = [path[0]]
    left = metres
    for before, after in zip(path, path[1:]):
        span = _metres(before, after)
        if span >= left:
            f = left / span if span else 0
            out.append([round(before[0] + (after[0] - before[0]) * f, 5),
                        round(before[1] + (after[1] - before[1]) * f, 5)])
            return out
        out.append(after)
        left -= span
    return out


def _first_ride(legs):
    """Pierwszy etap PRZEJAZDU propozycji - to on jest pierwszym ruchem."""
    for leg in legs:
        if leg.get("kind") == "ride":
            return leg
    return None


def _rents_a_car(legs):
    """Czy ta propozycja kończy się wynajętym autem (patrz planner._car_drive_leg).

    Asystent odpowiada na pytanie „czym stąd pojechać", zadane przez kogoś,
    kto stoi na chodniku albo siedzi w tramwaju - i nie ma tu żadnego
    przełącznika, którym ten ktoś prosiłby o car-sharing. Mapa przepływów ma
    na auta osobną warstwę i osobne pokrętło; dopóki asystent nie ma swojego,
    milcząco dokładane auto zmieniałoby godzinę w celu przy numerze linii na
    taką, której tą linią się nie osiąga.

    To nie jest ukrywanie opcji według naszego widzimisię (zasada 1) - to
    zakres środków, o które w ogóle pytamy, taki sam dla wszystkich opcji.
    Godziny pozostałych nie zmienia o sekundę.
    """
    return any(leg.get("kind") == "drive" for leg in legs)


def _opening_walk(legs):
    """Przejście OTWIERAJĄCE propozycję, czyli dojście z miejsca, w którym
    stoję, do krawędzi - albo None, gdy wsiada się tam, gdzie się stoi.

    Tylko pierwszy etap, i tylko gdy jest przejściem: przejścia w środku
    propozycji to przesiadki, a te dzieją się za kwadrans, nie teraz.
    """
    first = legs[0] if legs else None
    return first if first and first.get("kind") == "walk" else None


def _option_key(ride):
    """Co czyni dwa pierwsze ruchy TYM SAMYM ruchem (patrz nagłówek modułu).

    Krawędź, nie nazwa przystanku: z dwóch słupków „Katedra" jedzie się
    w przeciwne strony i wsiada się w nie inaczej. Nazwa bierze się z etapu,
    a ten niesie ją po słupku, z którego faktycznie rusza kurs - plus punkt
    startowy geometrii, który rozróżnia dwie krawędzie o tej samej nazwie.
    """
    return (ride["num"], ride["mode"], ride["headsign"], ride["from"],
            tuple(ride["path"][0]))


def _option(journey, ride, best_arr):
    """Propozycja sprowadzona do pierwszego ruchu."""
    legs = journey["legs"]
    walk = _opening_walk(legs)
    return {
        # Co wsiąść i w którą stronę.
        "num": ride["num"],
        "kind": ride["mode"],
        "headsign": ride["headsign"],
        # Gdzie stoi krawędź - i to jest miejsce, w którym stanie numer.
        # Punkt z geometrii kursu, nie środek przystanku: numer ma stać na
        # SWOJEJ krawędzi, a nie pośrodku placu z sześcioma słupkami.
        "stop": ride["from"],
        "at": ride["path"][0],
        # Kiedy stąd rusza i ile idzie się na tę krawędź.
        "depart": ride["from_time"],
        "depart_sec": ride["dep_sec"],
        "walk_min": walk["minutes"] if walk else 0,
        "walk_path": walk["path"] if walk else None,
        # Kreska w prawdziwym kierunku: początek geometrii pierwszego
        # przejazdu, po torach i ulicach (patrz HEAD_M).
        "head": _trim(ride["path"], HEAD_M),
        # Cały dalszy przebieg - rysowany dopiero po dotknięciu numeru.
        "path": [point for leg in legs for point in (leg.get("path") or ())],
        # O której jest się w celu i O ILE PÓŹNIEJ niż najszybciej - to jest
        # ta jedna miara, którą się tnie (patrz nagłówek modułu).
        "arrive": journey["arrival"],
        "later_min": round((journey["arrival_sec"] - best_arr) / 60),
        "transfers": journey["transfers"],
        "legs": legs,
    }


def _options(journeys, best_arr, deadline, window):
    """Propozycje -> pierwsze ruchy, po jednym na (linia, kierunek, krawędź).

    Z grupy zostaje ta propozycja, która dowozi najwcześniej: przy tym samym
    wsiadaniu wszystko, co je różni, dzieje się dopiero po wyjściu z tego
    pojazdu, a wtedy i tak wybiera się na nowo.

    PUNKT ODNIESIENIA MIARY liczymy z tego, co faktycznie zostało pokazane,
    a nie wyłącznie z `best_arr` planera. Powód jest zmierzony, nie
    teoretyczny: `best_arr` to wynik skanu z SAMYCH przystanków startowych,
    a propozycja wolno zacząć się DOJŚCIEM na sąsiedni przystanek - i wtedy
    potrafi dowieźć wcześniej niż „najszybciej" (Świeradowska ->
    pl. Grunwaldzki, 16:22 wobec 16:24). Bez tego pierwsza opcja dostawała
    etykietę „+-2 min", czyli liczbę, która nie znaczy nic.

    Cięcie progiem robimy TUTAJ jeszcze raz, choć plan_flow dostał to samo
    okno: mapa gwarantuje próg na NARYSOWANEJ sieci, a propozycja składa się
    z jej kawałków i potrafi (przez czekanie na przesiadce) wyjść odrobinę
    poza nie. Nigdy natomiast nie tniemy SZERZEJ niż planner: dalej mapa
    przestaje być kompletna, więc „wszystko do tego progu" nie byłoby już
    prawdą.
    """
    best = {}
    for journey in journeys:
        legs = journey.get("legs") or []
        ride = _first_ride(legs)
        if ride is None or _rents_a_car(legs) or journey["arrival_sec"] > deadline:
            continue
        key = _option_key(ride)
        current = best.get(key)
        if current is None or journey["arrival_sec"] < current[0]:
            best[key] = (journey["arrival_sec"], journey, ride)

    best_arr = min([arr for arr, _, _ in best.values()] + [best_arr])
    cut = min(deadline, best_arr + window)
    out = [_option(journey, ride, best_arr)
           for arr, journey, ride in best.values() if arr <= cut]
    shown = {"best_sec": best_arr, "cut_sec": cut}
    # Kolejność jest tą samą miarą, którą się tnie - opcja dowożąca najbliżej
    # najszybszego przyjazdu stoi pierwsza. Układanie po godzinie odjazdu
    # kusi ("co jedzie najbliżej"), ale stawia na górze pierwszy lepszy kurs,
    # którym jedzie się dwa razy dłużej, a przy dwóch różnych porządkach -
    # jednym do cięcia, drugim do pokazywania - już nie wiadomo, co znaczy
    # „pierwsza pozycja".
    out.sort(key=lambda o: (o["later_min"], o["depart_sec"]))
    return {**shown, "options": out}


def _me(day, start_point, start_name, ride_stop):
    """Gdzie jestem - punkt, na którym centruje się mapa asystenta.

    Trzy przypadki, w kolejności pewności: wskazany punkt (moja lokalizacja
    albo klik w mapę), słupek, przy którym zaraz stanie mój pojazd, i wreszcie
    nazwa przystanku, w którym stoję.
    """
    if start_point:
        return [round(start_point[0], 5), round(start_point[1], 5)]
    if ride_stop and ride_stop in day.stop_coords:
        return [round(c, 5) for c in day.stop_coords[ride_stop]]
    _name, stops, _hints = gtfs.match_stop(start_name, day)
    points = [day.stop_coords[s] for s in (stops or []) if s in day.stop_coords]
    if not points:
        return None
    # Środek WSZYSTKICH słupków miejsca, nie pierwszy z brzegu: stojąc na
    # placu z sześcioma krawędziami stoi się pośrodku niego, a nie na tej,
    # która akurat wyszła pierwsza z dopasowania nazwy.
    return [round(sum(c) / len(points), 5) for c in zip(*points)]


def plan(end_query, when=None, start_query="", start_point=None, end_point=None,
         side=None, in_vehicle=None, window_min=None, transfer_gain_sec=None):
    """Odpowiedź asystenta - {"me", "options", ...} albo {"error"}.

    `side` to czterocyfrowy numer boczny; gdy go podano, zastępuje i „skąd",
    i trzy pola „jestem w pojeździe" - rozpoznany kurs zamienia się na
    dokładnie te trzy rzeczy i dalej jedzie już istniejącą ścieżką
    (patrz onboard.find_by_side i planner.plan_flow, parametr in_vehicle).
    """
    when = when or datetime.now()
    try:
        day = gtfs.load_day(when.date())
    except FileNotFoundError as e:
        return {"error": str(e)}

    asked_sec = when.hour * 3600 + when.minute * 60 + when.second

    # Numer boczny i trzy pola to dwie odpowiedzi na to samo pytanie, więc
    # przychodzą razem i znaczą co innego w zależności od tego, co jeszcze
    # jest podane:
    #
    #   sam numer      -> rozpoznaj z niego kurs (zwykła, szybka droga);
    #   numer + pola   -> numer się nie udał i ktoś dokończył ręcznie, więc
    #                     to ON uczy nas, co znaczy - patrz niżej;
    #   same pola      -> zwykły start z pokładu, numeru nikt nie podawał.
    #
    # Rozwiązujemy to PRZED wyszukiwaniem, żeby nierozpoznany numer nie
    # wyglądał jak brak połączenia: to dwa różne zdania i tylko jedno z nich
    # prowadzi do trzech pól.
    recognised = None
    if side and not in_vehicle:
        recognised = onboard.find_by_side(day, when.date(), side, asked_sec)
        if "error" in recognised:
            return recognised
        in_vehicle = {k: recognised[k] for k in ("num", "mode", "headsign",
                                                 "stop", "trip")}
    elif side and in_vehicle:
        # Czyjeś trzy pola są dla nas jedyną wiedzą o tym wozie, jakiej kanał
        # nie miał - a kosztowały go kilkanaście sekund. Zapamiętujemy parę
        # na dziś, żeby następny pytający o ten sam wóz dostał odpowiedź od
        # ręki. Tylko na dziś i tylko dopóki kanał milczy: oficjalna paczka
        # zawsze bije to, czego się domyśliliśmy (patrz sidenum.brigade_of).
        ride = onboard.find_ride(day, in_vehicle["num"], in_vehicle["mode"],
                                 in_vehicle["stop"], asked_sec,
                                 in_vehicle["headsign"])
        if "error" not in ride:
            in_vehicle = {**in_vehicle, "trip": ride["trip"]}
            duty = onboard.brigade_of_trip(when.date(), ride["trip"])
            if duty:
                sidenum.remember(side, duty["num"], duty["brigade"], when.date())

    data = planner.plan_flow(
        start_query, end_query, when, start_point, end_point,
        journey_limit=JOURNEY_LIMIT,
        transfer_gain_sec=transfer_gain_sec,
        in_vehicle=in_vehicle,
        window_sec=window_sec(window_min),
        fastest_journey=True,
    )
    if "error" in data:
        return data

    # Obie granice czytamy z odpowiedzi, a nie odtwarzamy z pokrętła: planner
    # przycina okno własnym sufitem, więc próg, pod którym naprawdę liczono
    # mapę, potrafi być węższy niż ten, o który poprosiliśmy.
    deadline = data["deadline_sec"]
    best_arr = data["best_arrival_sec"]
    window = window_sec(window_min)

    shown = _options(
        # Najszybsza trasa staje w jednym rzędzie z propozycjami, bo listy
        # propozycji nie da się czytać jako kompletu opcji: potrafi nie
        # zawierać akurat tej trasy (patrz plan_flow, fastest_journey). Bez
        # niej asystent zapewniałby, że najszybciej jest się w celu o 16:18,
        # i nie pokazywał ani jednego sposobu, żeby to zrobić. Jeśli ta trasa
        # i tak jest już na liście, zgrupuje się z nią - to ten sam pierwszy
        # ruch, więc nie zrobi się z tego duplikat.
        ([data["fastest_journey"]] if "fastest_journey" in data else [])
        + data["journeys"], best_arr, deadline, window)

    ride_stop = in_vehicle.get("stop") if in_vehicle else None
    return {
        "start": data["start"],
        "end": data["end"],
        "now": data["departure"],
        "me": _me(day, start_point, data["start"], ride_stop),
        # Obie liczby opisują to, CO WIDAĆ, a nie to, o co poprosiliśmy:
        # najwcześniejszy przyjazd spośród pokazanych opcji i próg faktycznie
        # użyty do cięcia (planner ma własny sufit okna - patrz plan_flow).
        # Inaczej nagłówek obiecywałby godzinę, której nie osiąga ani jedna
        # pokazana opcja.
        "best_arrival": planner._fmt_time(shown["best_sec"]),
        "window_min": round((shown["cut_sec"] - shown["best_sec"]) / 60),
        "options": shown["options"],
        # Rozpoznany pojazd - zdanie do potwierdzenia przez człowieka. Front
        # ma je pokazać zawsze, gdy jest: to jedyne miejsce, w którym widać
        # pomyłkę rozpoznania, zanim pomyłką stanie się cała podróż.
        **({"onboard": {**data["onboard"],
                        **({"side": str(side)} if side else {}),
                        "learned": bool(recognised and recognised["learned"])}}
           if "onboard" in data else {}),
    }
