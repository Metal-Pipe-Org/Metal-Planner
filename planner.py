"""Wyszukiwanie najszybszego połączenia algorytmem CSA (Connection Scan).

CSA nie buduje grafu: wszystkie połączenia dnia (przejazdy między sąsiednimi
przystankami) są posortowane po czasie odjazdu i skanowane raz, liniowo.
Połączenie jest "osiągalne", jeśli jesteśmy już w tym kursie albo zdążymy
na jego odjazd na przystanku startowym.
"""

from bisect import bisect_left, bisect_right
from collections import deque
from datetime import date, datetime, timedelta
from math import sqrt

import bikes
import gtfs
import onboard
import traficar

# Przesiadka na DOKŁADNIE tym samym słupku - bez marszu, więc nie wycenia się
# jej jak przejścia (punkt 14 kontraktu). Minuta, a nie zero: na mapie nie
# widać, ile czasu zostaje na przesiadkę, więc pasażer nie oceni tego sam.
# Decyzja użytkownika z 2026-09-24; wcześniej były tu dwie minuty.
TRANSFER_SEC = 60
# Czasu przejścia pieszo nie ma tu jako stałej: krawędź piesza niesie własny
# koszt, policzony z odległości przy budowie dnia (patrz gtfs.walk_seconds
# i gtfs._nearby_bridges). Bufora przesiadki krawędź piesza NIE dostaje -
# gtfs.WALK_MIN_SEC jest od niego większe, więc jest w nim zawarty.

# Ile MUSI oszczędzić przesiadka, żeby w ogóle warto ją było proponować.
# Nocne linie zjeżdżają się w węźle i ruszają z niego tą samą minutą tą samą
# ulicą, więc różnice na końcu bywają czystym zaokrągleniem dwóch rozkładów -
# a wysiadanie z pojazdu, który sam dowozi do celu, kosztuje przejście na
# inny peron i ryzyko utraty połączenia na całe pół godziny. Suwak w
# Ustawieniach Developerskich (transfer_gain_sec w API).
TRANSFER_GAIN_SEC = 600
# W jakim oknie w ogóle SZUKAMY wariantu bez przesiadki. Celowo niezależne
# od progu: suwak rozstrzyga, który wariant jest proponowany jako najlepszy,
# a nie który istnieje - przy progu 0 obie opcje mają dalej być widoczne.
# Tyle, ile wynosi górny koniec suwaka.
SEATED_HORIZON_SEC = 1800
INF = float("inf")


def plan_route(start_query, end_query, when=None, transfer_gain_sec=None):
    """Zwraca dict z trasą ('legs', czasy) albo z kluczem 'error'."""
    when = when or datetime.now()
    gain_sec = TRANSFER_GAIN_SEC if transfer_gain_sec is None else int(transfer_gain_sec)

    try:
        day = gtfs.load_day(when.date())
    except FileNotFoundError as e:
        return {"error": str(e)}

    start_name, source_stops, start_hints = gtfs.match_stop(start_query, day)
    if start_name is None:
        return _unknown_stop(start_query, start_hints)
    end_name, target_stops, end_hints = gtfs.match_stop(end_query, day)
    if end_name is None:
        return _unknown_stop(end_query, end_hints)
    if start_name == end_name:
        return {"error": "Przystanek początkowy i końcowy są takie same."}
    group_error = _city_group_error(day, start_name, source_stops, end_name, target_stops)
    if group_error:
        return group_error

    dep_sec = when.hour * 3600 + when.minute * 60 + when.second
    best_stop, best_arr, journey = _scan(day, source_stops, target_stops, dep_sec)

    if best_stop is None:
        return _no_connection(start_name, end_name, dep_sec, when)

    # /api/plan oddaje JEDNĄ trasę, więc bierzemy wariant proponowany jako
    # najlepszy przy obecnym progu (pełen wachlarz jest w /api/flow).
    legs = _variants(day, _reconstruct(day, journey, best_stop), gain_sec)[0]
    # Przyjazd bierzemy z samej trasy, nie z best_arr - w wariancie bez
    # przesiadki to może być świadomie oddana minuta czy dwie.
    arrival = _arrival_of(legs)
    first_dep = legs[0]["dep_sec"]
    _drop_private(legs)
    return {
        "start": start_name,
        "end": end_name,
        "departure": _fmt_time(first_dep),
        "arrival": _fmt_time(arrival),
        "travel_time": f"{round((arrival - first_dep) / 60)} min",
        "legs": legs,
    }


# Ile odjazdów pokazuje tablica przystanku pod kropką przesiadki. Tyle mieści
# się w dymku bez przewijania, a dalsze i tak są poza horyzontem decyzji
# "czym stąd pojechać".
TIMETABLE_LIMIT = 8
# Sufit dla `limit` z zapytania. Mapa przepływów prosi z zapasem, bo z tej listy
# zostawia potem tylko linie, w które sama pozwala tu wsiąść - a przy węźle,
# z którego odjeżdża pół miasta, te kilka właściwych bywa dopiero w trzeciej
# dziesiątce.
TIMETABLE_MAX = 60


def stop_timetable(stop_query, when=None, from_sec=None, limit=TIMETABLE_LIMIT,
                   point=None, until_sec=None):
    """Tablica odjazdów jednego przystanku - to, co widać po najechaniu na
    kropkę przesiadki na mapie.

    Przystanek wskazuje się NAZWĄ albo `point` = (lat, lon). Po współrzędnych
    pyta mapa przepływów: kropki stawia z geometrii kawałków, więc zna
    położenie słupka, a nie jego nazwę (patrz gtfs.stop_at).

    `when` wyznacza DOBĘ rozkładową, `from_sec` godzinę na jej osi. Osobno,
    bo etap trasy potrafi wypaść po północy (odjazd 24:40 to wciąż rozkład
    dnia poprzedniego) - a wtedy sama godzina "00:40" wskazywałaby dobę
    o jedną za daleko. Bez `from_sec` liczymy od godziny z `when`.

    Przystanek rozumiemy jako MIEJSCE, nie słupek (patrz gtfs.match_stop):
    najeżdżając na węzeł, pasażer pyta o wszystko, co z niego odjeżdża, a nie
    o jeden peron, przy którym akurat wysiadł.

    `until_sec` zamiast `limit`: wszystkie odjazdy do tej godziny. Tak pyta
    kropka mapy - do końca mapy - bo na ruchliwym węźle `limit` sztuk ze
    wszystkich linii kończył się, zanim nadeszła godzina, o której pasażer tu
    staje, i kursy linii z mapy wypadały przed odsiewem.
    """
    when = when or datetime.now()

    try:
        day = gtfs.load_day(when.date())
    except FileNotFoundError as e:
        return {"error": str(e)}

    if point is not None:
        name, stops = gtfs.stop_at(point[0], point[1], day)
        if name is None:
            return {"error": "W tym miejscu nie ma przystanku."}
    else:
        name, stops, hints = gtfs.match_stop(stop_query, day)
        if name is None:
            return _unknown_stop(stop_query, hints)

    if from_sec is None:
        from_sec = when.hour * 3600 + when.minute * 60 + when.second
    from_sec = int(from_sec)

    departures = []
    found = (gtfs.stop_departures(day, stops, from_sec, limit) if until_sec is None
             else gtfs.departures_between(day, stops, from_sec, int(until_sec)))
    seen = set()
    for dep_sec, trip, _stop_id in found:
        # Kurs, który w tym miejscu staje przy dwóch słupkach (310 na
        # Lutosławskiego: 5700 i 5706 w tej samej minucie), to jeden autobus
        # - wsiada się w pierwszy postój.
        if trip in seen:
            continue
        seen.add(trip)
        line, headsign = day.trip_info[trip]
        num, mode = _line_parts(line)
        departures.append({
            "time": _fmt_time(dep_sec),
            # Sekunda na osi doby - po niej front odsiewa odjazdy sprzed
            # horyzontu mapy; "HH:MM" po północy zawija się i nie da się
            # z niego porównywać (patrz gtfs.load_day).
            "sec": dep_sec,
            "line": line,
            "num": num,
            "mode": mode,
            "headsign": headsign,
        })

    return {"stop": name, "from_time": _fmt_time(from_sec), "departures": departures}


def _cheaper_boarding(earliest, journey, legs, walked, stop, dep_t, board_legs,
                      board_stop, trip=None, seated=None):
    """Czy w kurs, którym już jedziemy, można wsiąść na `stop` TANIEJ niż
    w zapisanym punkcie wsiadania - mniejszą liczbą przejazdów albo mniejszym
    marszem.

    Samo "mniej przejazdów" nie wystarczy - trzeba jeszcze zdążyć na odjazd
    z tego przystanku, tym samym buforem co przy zwykłym wsiadaniu.
    Przystanek osiągnięty PRZEZ TEN kurs nigdy nie przejdzie: ma o przejazd
    więcej niż punkt wsiadania, więc przesunięcie nie potrafi rozciąć jazdy
    jednym pojazdem na dwa etapy.

    Drugi powód - MNIEJ MARSZU - jest tą samą zasadą, co punkt 14 kontraktu:
    przejście ma sens tylko wtedy, gdy otwiera kurs, którego inaczej nie
    złapiemy. Kurs, który i tak zatrzyma się bliżej nas, takim kursem nie
    jest, więc chodzenie po niego dalej jest chodzeniem donikąd. Dwa
    zgłoszenia na żywo, ten sam kształt:
      - Wojszyce -> DWORZEC GŁÓWNY, 18:08: 112 staje na Parafialnej o 18:13
        i na Wojszycach o 18:14, więc skan kazał iść cztery minuty WSTECZ po
        autobus, który zaraz podjeżdżał pod sam start;
      - punkt kliknięty w Radwanicach: APK1 staje na Mickiewicza o 15:00
        i na Skrajnej o 15:01, więc skan kazał iść 14 minut zamiast 7 -
        po ten sam kurs, z tą samą godziną w celu.
    """
    reached = earliest.get(stop, INF)
    if reached is INF or legs[stop] > board_legs:
        return False
    if legs[stop] == board_legs and walked[stop] >= walked[board_stop]:
        return False
    return reached + _board_buffer(journey[stop][0], trip, seated) <= dep_t


def _target_reach(day, target_set):
    """{słupek: (sekundy pieszo do celu, który słupek celu)} - cel sam w sobie
    ma (0, on sam).

    "Jestem u celu" znaczyło w tym pliku dosłownie "stoję na słupku celu",
    i przez długi czas było to prawdą, bo most pieszy łączył wyłącznie słupki
    tej samej nazwy - a cel to całe MIEJSCE (patrz gtfs.match_stop), więc
    sąsiedzi celu sami byli celem. Od kiedy pieszo przechodzi się między
    RÓŻNYMI przystankami (gtfs._nearby_bridges), to przestało się zgadzać:
    stacja Wrocław Wojszyce leży trzy minuty od Dworca Głównego, ale celem
    nie jest - a każde z pięciu miejsc pytających "czy to już cel" odpowiadało
    "nie" i traktowało dojazd pod sam dworzec jak trasę donikąd. Skutki były
    ciche i różne: skan wstecz liczył `latest` z przypadkowego objazdu,
    reguła cofnięcia kasowała przez to cały kurs, a lista propozycji nie
    umiała ZAKOŃCZYĆ trasy dojściem i doklejała jeszcze jeden autobus.

    Jedna tablica na zapytanie zamiast przeglądania sąsiadów w każdej z tych
    pętli - część z nich chodzi po wszystkich połączeniach doby.
    """
    reach = {stop: (0, stop) for stop in target_set}
    if gtfs.is_city_group(day, target_set):
        # Patrz _origin_walk: celem jest któraś stacja, a nie przystanek obok
        # niej - "WARSZAWA -" -> "WROCŁAW -" rysowało inaczej autobusy
        # dowożące pod wrocławskie stacje.
        return reach
    for other, (sec, cel) in gtfs.walk_reach(day, target_set).items():
        if sec < reach.get(other, (INF, None))[0]:
            reach[other] = (sec, cel)
    return reach


def _origin_walk(day, source_stops):
    """Słupki osiągalne pieszo WPROST ze startu relacji: {słupek: (skąd, sek)}.

    Bez tego chodzenie było wyłącznie przesiadką: przejście relaksowało się
    tylko po WYSIADANIU z pojazdu, więc z przystanku startowego nie dawało
    się nigdzie wyjść na piechotę. Relacja "Wojszyce -> Dworzec Główny"
    pokazywała przez to sam autobus, choć stacja kolejowa stoi kilka minut
    marszu od startu i pociąg bywa szybszy - żeby z niego skorzystać, trzeba
    najpierw ODEJŚĆ ze startowego słupka, a tego skan nie umiał.

    JEDEN krok, tak samo jak przy przesiadce: wychodzimy ze startu na sąsiada
    i tam wsiadamy. Nie ma łańcucha "przejdź, przejdź, wsiądź" - inaczej
    zasięg startu rósłby wielokrotnością promienia i "dojście" zaczęłoby
    znaczyć spacer przez pół dzielnicy.

    Słupki, które SĄ startem, pomijamy: stoi się na nich od razu, o dep_sec,
    i dokładanie im czasu przejścia mogłoby tylko opóźnić prawdziwy start
    (całe miejsce jest startem naraz - patrz gtfs.match_stop).
    """
    # "Dowolna stacja w mieście" to wybór stacji, a nie miejsce, w którym się
    # stoi. Dojście z każdej z trzydziestu wrocławskich stacji do przystanków
    # MPK obok nich wpuszczało do szukania setki tramwajów: "WROCŁAW -" ->
    # Milicz liczyło się 15 s zamiast 3,5 s, przy identycznej mapie.
    if gtfs.is_city_group(day, source_stops):
        return {}
    return {
        other: (skad, sec)
        for other, (sec, skad) in gtfs.walk_reach(day, source_stops).items()
    }


def _board_buffer(arrived, trip, seated):
    """Ile zapasu trzeba mieć na przystanku, żeby zdążyć wsiąść w `trip`.

    `arrived` to sposób, w jaki się tu stanęło ('origin' | 'ride' | 'walk').
    `seated` to kurs, w którym pasażer już siedzi (start z pokładu, patrz
    onboard.py) - na swoim przystanku startowym nie stoi on na chodniku,
    tylko do niego PRZYJEŻDŻA: dalej tym samym kursem jedzie bez zapasu,
    ale każdy inny pojazd to już przesiadka i dostaje jej bufor. Bez startu
    z pokładu (`seated` None) reguła jest dokładnie ta sama co dotąd.
    """
    if arrived == "ride" or (arrived == "origin" and seated is not None
                             and trip != seated):
        return TRANSFER_SEC
    return 0


def _scan(day, source_stops, target_stops, dep_sec, banned_labels=None, deadline=None,
          seated=None):
    """Connection Scan: najwcześniejszy przyjazd do celu, ze śladem do rekonstrukcji.

    banned_labels to zbiór etykiet linii ("Tramwaj 17"), których skan ma nie
    używać - tak `plan_journeys` wymusza warianty strukturalnie inne od
    najszybszego. deadline ucina skan, gdy przy takim zakazie nie ma już
    czego szukać (inaczej skan jechałby do końca doby). seated to kurs, w którym
    pasażer już siedzi (start z pokładu, patrz _board_buffer).
    """
    conns = day.conns
    earliest = {}
    journey = {}      # stop_id -> ("origin",) | ("ride", idx_wsiadania, idx_wysiadania) | ("walk", skad)
    trip_board = {}   # trip_id -> indeks połączenia, na którym wsiedliśmy do kursu
    trip_legs = {}    # trip_id -> liczba przejazdów PRZED wsiadaniem do kursu
    trip_walk = {}    # trip_id -> ile marszu kosztowało dojście do wsiadania
    legs = {}         # stop_id -> liczba przejazdów w najlepszej drodze do niego
    # Ile sekund marszu kosztuje najlepsza droga do przystanku. Nie po to, żeby
    # wybierać trasę - o tym decyduje godzina przyjazdu - tylko po to, żeby
    # przy REMISIE wybrać wsiadanie z mniejszym marszem (patrz
    # _cheaper_boarding): dwa przystanki tego samego kursu są dla zegara
    # równoważne, a dla nóg nie.
    walked = {}

    # Użytkownik podaje nazwę przystanku, więc startuje ze wszystkich jego słupków.
    for stop in source_stops:
        earliest[stop] = dep_sec
        journey[stop] = ("origin",)
        legs[stop] = 0
        walked[stop] = 0

    # Wyjście pieszo ze startu (patrz _origin_walk). CELOWO bez note_target:
    # "po prostu dojdź tam pieszo" nie ma być propozycją trasy. Ta wyszukiwarka
    # planuje przejazdy - a poza tym trasa bez ani jednego przejazdu nie ma
    # godziny wyjazdu, na której opiera się okno mapy (_journey_start), więc
    # ogłoszenie jej celem zostawiłoby to okno bez punktu odniesienia.
    targets = set(target_stops)
    near_target = _target_reach(day, targets)
    for stop, (skad, sec) in _origin_walk(day, source_stops).items():
        if stop in targets:
            # Cel w zasięgu marszu ze startu. Dojścia tu NIE zapisujemy, i to
            # nie z ostrożności, tylko dlatego, że zapis byłby TRUJĄCY: skoro
            # samo dojście nie ogłasza celu (patrz niżej), to wpisana tu
            # wczesna godzina nie zostałaby nigdy ogłoszona, a jednocześnie
            # zasłoniłaby każdy późniejszy DOJAZD - żaden nie poprawiłby już
            # `earliest`, więc note_target nigdy by się nie odpalił i relacja
            # z działającym połączeniem wychodziła jako "nie znaleziono".
            # Ten sam kształt błędu, co naprawiony kiedyś przy note_target.
            continue
        earliest[stop] = dep_sec + sec
        journey[stop] = ("walk", skad)
        legs[stop] = 0
        walked[stop] = sec

    best_arr = INF
    best_stop = None
    best_legs = INF
    limit = INF if deadline is None else deadline

    def note_target(stop, when, ride_count):
        """Jedyne miejsce, w którym pada pytanie "czy to już cel".

        Pyta i pojazd, i przejście na sąsiedni słupek - bo celem można
        stanąć na oba sposoby. Rozdzielenie tych dwóch dróg było źródłem
        błędu: cel osiągalny WYŁĄCZNIE przejściem (stacja kolejowa obok
        przystanku, patrz pkp.py) nie był w ogóle zauważany, a jego godzina,
        raz wpisana do `earliest`, blokowała jeszcze późniejszy dojazd
        pojazdem - ten JEDEN zostałby zauważony. Wychodziło z tego
        "nie znaleziono połączenia" na relacji, którą skan miał policzoną.
        """
        nonlocal best_arr, best_stop, best_legs
        if stop in targets and (when < best_arr or
                                (when == best_arr and ride_count < best_legs)):
            best_arr, best_stop, best_legs = when, stop, ride_count

    for i in range(bisect_left(day.dep_times, dep_sec), len(conns)):
        dep_t, arr_t, dep_s, arr_s, trip = conns[i]
        if dep_t > best_arr or dep_t > limit:
            break                     # dalsze odjazdy nie mogą już poprawić wyniku
        if banned_labels and day.trip_info[trip][0] in banned_labels:
            continue

        if trip not in trip_board:
            reached = earliest.get(dep_s, INF)
            if reached is INF:
                continue
            # Bufor tylko przy przesiadce z pojazdu; przy starcie i po
            # przejściu pieszym czas przesiadki jest już uwzględniony.
            if reached + _board_buffer(journey[dep_s][0], trip, seated) > dep_t:
                continue
            trip_board[trip] = i
            trip_legs[trip] = legs[dep_s]
            trip_walk[trip] = walked[dep_s]
        elif _cheaper_boarding(earliest, journey, legs, walked, dep_s, dep_t,
                               trip_legs[trip], conns[trip_board[trip]][2],
                               trip, seated):
            # Jedziemy już tym kursem, ale właśnie mijamy przystanek, na
            # którym stalibyśmy MNIEJSZĄ liczbą przejazdów niż w zapisanym
            # punkcie wsiadania - w skrajnym przypadku sam start relacji.
            # Punkt wsiadania zapisuje się przy PIERWSZYM przystanku kursu,
            # do którego dało się zdążyć, a bywa nim miejsce, do którego
            # trzeba się dopiero dowieźć innym pojazdem. Bez przesunięcia
            # rekonstrukcja musi ten dojazd potem czymś wytłumaczyć i wypisuje
            # etap "dojedź dwa przystanki pod początek trasy tego autobusu",
            # choć autobus i tak zaraz przejeżdża obok nas. Godziny się przez
            # to nie zmieniają - to ten sam pojazd - więc przesunięcie tylko
            # zdejmuje z trasy etap, który niczego nie dawał.
            trip_board[trip] = i
            trip_legs[trip] = legs[dep_s]
            trip_walk[trip] = walked[dep_s]

        # Przy REMISIE na godzinie przyjazdu wygrywa droga z mniejszą liczbą
        # przejazdów - inaczej decyduje o tym kolejność skanowania i podróżny
        # dostaje polecenie przesiadki do sąsiedniego autobusu, który dowozi
        # go na miejsce o tej samej minucie. Lista propozycji z mapy
        # przepływów sortuje tak od dawna (patrz _enumerate_journeys: klucz
        # (arrival, len(chain) - 1, ...)); tu chodzi o to samo w samym skanie,
        # bo z niego bierze się trasa w gałęzi awaryjnej plan_flow.
        ride_legs = trip_legs[trip] + 1
        known = earliest.get(arr_s, INF)
        if arr_t < known or (arr_t == known and ride_legs < legs[arr_s]):
            earliest[arr_s] = arr_t
            journey[arr_s] = ("ride", trip_board[trip], i)
            legs[arr_s] = ride_legs
            walked[arr_s] = trip_walk[trip]     # jazda nóg nie kosztuje
            note_target(arr_s, arr_t, ride_legs)
            # Relaksacja pieszo na wszystko, dokąd stąd się dojdzie
            # (patrz gtfs.DayData.siblings) - sąsiedni peron, przystanek po
            # drugiej stronie ulicy, stacja kolejowa obok. JEDEN krok:
            # sąsiad zapisuje się jako osiągnięty pieszo, ale sam już
            # pieszo dalej nie relaksuje, więc nie da się złożyć trasy
            # z dwóch przejść pod rząd.
            for sibling in day.siblings.get(arr_s, ()):
                walk_sec = gtfs.walk_seconds(day, arr_s, sibling)
                walk_arr = arr_t + walk_sec
                known_sib = earliest.get(sibling, INF)
                if walk_arr < known_sib or (walk_arr == known_sib
                                            and ride_legs < legs[sibling]):
                    earliest[sibling] = walk_arr
                    journey[sibling] = ("walk", arr_s)
                    legs[sibling] = ride_legs
                    walked[sibling] = walked[arr_s] + walk_sec
                    note_target(sibling, walk_arr, ride_legs)
            # Dojście spod celu. Osobno od pętli wyżej, bo egress ma własny,
            # większy promień niż przesiadka (patrz gtfs.walk_reach) i takiej
            # pary w day.siblings po prostu nie ma. Bez tego skan - a więc
            # i najszybszy przyjazd, i sama trasa - nie widziałby dojazdu pod
            # przystanek obok celu, choć mapa przepływów przez _target_reach
            # już go widzi.
            blisko = near_target.get(arr_s)
            if blisko is not None and blisko[0]:
                sec, cel_stop = blisko
                walk_arr = arr_t + sec
                known_cel = earliest.get(cel_stop, INF)
                if walk_arr < known_cel or (walk_arr == known_cel
                                            and ride_legs < legs[cel_stop]):
                    earliest[cel_stop] = walk_arr
                    journey[cel_stop] = ("walk", arr_s)
                    legs[cel_stop] = ride_legs
                    walked[cel_stop] = walked[arr_s] + sec
                    note_target(cel_stop, walk_arr, ride_legs)

    return best_stop, best_arr, journey


def _reconstruct(day, journey, last_stop, geo_db=None):
    """Odtwarza trasę od celu do startu i skleja ją w czytelne etapy.

    Z otwartym `geo_db` ścieżka etapu jest wycinkiem geometrii kursu (realne
    ulice i tory, tak jak na mapie przepływów); bez niego - łamaną po
    przystankach.

    Oddaje trasę taką, jaka wyszła ze skanu; wariant bez nieopłacalnych
    przesiadek dokłada obok _variants.
    """
    legs = []
    stop = last_stop
    while journey[stop][0] != "origin":
        entry = journey[stop]
        if entry[0] == "walk":
            from_stop = entry[1]
            legs.append(_walk_leg(day, from_stop, stop))
            stop = from_stop
        else:
            _, board_i, exit_i = entry
            board = day.conns[board_i]
            legs.append(_ride_leg(day, board[4], board[2], board[0], stop,
                                  day.conns[exit_i][1], geo_db))
            stop = board[2]
    legs.reverse()
    # Trasa otwarta przejściem: wychodzi się tak późno, jak się da, czyli
    # dokładnie na odjazd pierwszego pojazdu. Bez tego etap zostawał
    # z zerowym `dep_sec` (dla przejść W ŚRODKU trasy jest on nieużywany,
    # bo godzinę widać po sąsiednich przejazdach) i cała trasa raportowała
    # wyjazd o 00:00 - patrz plan_route, które czyta legs[0]["dep_sec"].
    if len(legs) > 1 and legs[0]["kind"] == "walk":
        legs[0]["dep_sec"] = legs[1]["dep_sec"] - legs[0]["_sec"]
    return legs


_PRIVATE_LEG_KEYS = ("_trip", "_from_id", "_to_id", "_arr_sec", "_stops_t", "_sec")


def _next_ride(legs, i):
    """Indeks kolejnego przejazdu po `i` (po drodze może być przejście)."""
    for j in range(i + 1, len(legs)):
        if legs[j]["kind"] == "ride":
            return j
    return None


def _seated_exit(day, leg, nxt, gain_sec, allow_siblings):
    """Gdzie i o której pojazd z etapu `leg` sam dowozi tam, dokąd dojeżdża
    `nxt` - albo None, jeśli nie dowozi wcale lub za późno."""
    limit = nxt["_arr_sec"] + gain_sec
    rodzenstwo = day.siblings.get(nxt["_to_id"], ()) if allow_siblings else ()
    for i in gtfs.trip_conns(day, leg["_trip"]):
        _, arr_t, _, arr_s, _ = day.conns[i]
        if arr_t <= leg["_arr_sec"]:
            continue             # jeszcze przed naszym wysiadaniem
        if arr_t > limit:
            break                # czasy w kursie rosną - dalej może być tylko gorzej
        if arr_s == nxt["_to_id"] or arr_s in rodzenstwo:
            return arr_s, arr_t
    return None


def _seated_legs(day, legs, horizon_sec, geo_db=None):
    """Ta sama trasa bez przesiadek, które nie zarabiają na siebie - albo
    None, gdy nie ma czego zdejmować.

    Dla dwóch kolejnych przejazdów sprawdza, czy pojazd z pierwszego sam
    dojeżdża tam, gdzie kończy się drugi - i czy nie później niż `gain_sec`
    po nim. Jeśli tak, oba etapy (razem z przejściem między nimi) zastępuje
    jedną, dłuższą jazdą. Przyjazd może się przez to opóźnić o mniej niż
    `gain_sec`.

    Wejścia NIE rusza: oba warianty trasy - z przesiadką i bez - jadą dalej
    obok siebie na listę propozycji, a próg rozstrzyga tylko, który z nich
    jest proponowany jako najlepszy (patrz _variants). `horizon_sec` mówi,
    jak dużo później wolno dojechać, żeby wariant w ogóle uznać za sensowny
    do pokazania - to NIE jest próg opłacalności.

    Na CELU relacji dopuszczamy inny słupek tego samego miejsca - nocne linie
    zjeżdżają na różne perony jednego dworca (241 na 3512, 249 na 3519),
    a dla pasażera to ten sam przystanek, o który pytał. W środku trasy
    wymagamy dokładnie tego samego słupka, bo następny etap musi odjechać
    stamtąd, gdzie go zostawiliśmy.
    """
    if horizon_sec <= 0:
        return None
    legs = list(legs)
    zmienione = False
    while True:
        for i, leg in enumerate(legs):
            j = _next_ride(legs, i) if leg["kind"] == "ride" else None
            if j is None:
                continue
            cel = _seated_exit(day, leg, legs[j], horizon_sec,
                               allow_siblings=(j == len(legs) - 1))
            if cel is None:
                continue
            legs[i:j + 1] = [_ride_leg(day, leg["_trip"], leg["_from_id"],
                                       leg["dep_sec"], cel[0], cel[1], geo_db)]
            zmienione = True
            break
        else:
            return legs if zmienione else None


def _transfers(legs):
    return max(sum(1 for leg in legs if leg["kind"] == "ride") - 1, 0)


def _journey_cost(legs, gain_sec):
    """Przyjazd z karą za każdą przesiadkę - klucz wyboru wariantu
    "proponowany jako najlepszy". Sam przyjazd pokazujemy prawdziwy."""
    return _arrival_of(legs) + _transfers(legs) * gain_sec


def _variants(day, legs, gain_sec, geo_db=None):
    """Trasa w wariantach: tak jak wyszła ze skanu (najwcześniejszy przyjazd)
    i - jeśli jest co zdjąć - bez nieopłacalnych przesiadek. Obie zostają
    widoczne; kolejność mówi, którą przy obecnym progu uważamy za lepszą."""
    warianty = [legs]
    bez_przesiadki = _seated_legs(
        day, legs, max(gain_sec, SEATED_HORIZON_SEC), geo_db)
    if bez_przesiadki is not None:
        warianty.append(bez_przesiadki)
    warianty.sort(key=lambda w: (_journey_cost(w, gain_sec), _transfers(w)))
    return warianty


def _arrival_of(legs):
    """Godzina dojazdu do celu wg samych etapów - po sklejeniu w _stay_seated
    nie musi się już równać najwcześniejszemu możliwemu przyjazdowi."""
    rides = [leg for leg in legs if leg["kind"] == "ride"]
    return rides[-1]["_arr_sec"] if rides else None


def _drop_private(legs):
    """Zdejmuje pola robocze, żeby nie wyciekły do odpowiedzi API."""
    for leg in legs:
        for key in _PRIVATE_LEG_KEYS:
            leg.pop(key, None)
    return legs


def _via_stops(day, rows):
    """Przystanki MIJANE w czasie przejazdu (zgłoszenie #169) - bez wsiadania
    i wysiadania, które etap ma już w `from`/`to`. Front wypisuje je w osi
    rozwiniętej propozycji i stawia na mapie wybranej trasy.

    `rows` to pary (przystanek, odjazd z niego). Godzina to odjazd, tak jak
    w kursie z rozkładu (timetables.trip) - na mijanym przystanku pojazd stoi
    chwilę, a liczy się, kiedy z niego rusza. Współrzędne tylko tam, gdzie są
    znane: stacja PKP bywa bez nich, a na liście i tak ma swoje miejsce.
    """
    via = []
    for stop, sec in rows:
        item = {"name": day.stop_names[stop],
                "t": _fmt_time(sec) if sec is not None else ""}
        coords = day.stop_coords.get(stop)
        if coords:
            item["lat"], item["lon"] = _round_path([coords])[0]
        via.append(item)
    return via


def _ride_leg(day, trip, board_stop, board_dep, exit_stop, exit_arr, geo_db=None):
    """Etap przejazdu jednym kursem, od wsiadania do wysiadania.

    Prywatne pola `_trip`/`_from_id`/`_to_id` służą wyłącznie sklejaniu
    etapów w _stay_seated i są z odpowiedzi zdejmowane.
    """
    line, headsign = day.trip_info[trip]
    # Pełna lista przystanków etapu - do narysowania linii na mapie. `data=day`
    # jest potrzebne wyłącznie kursom kolejowym (patrz gtfs.trip_path).
    path_rows = gtfs.trip_path(trip, board_stop, board_dep, exit_stop, exit_arr, geo_db, day)
    coords = [day.stop_coords[s] for s, _, _ in path_rows]
    if geo_db is not None and len(coords) >= 2:
        coords = gtfs.shape_slice(day.trip_shape.get(trip), coords, geo_db)
    num, mode = _line_parts(line)
    return {
        "kind": "ride",
        "line": line,
        "num": num,
        "mode": mode,
        "headsign": headsign,
        "from": day.stop_names[board_stop],
        "from_time": _fmt_time(board_dep),
        "to": day.stop_names[exit_stop],
        "to_time": _fmt_time(exit_arr),
        "dep_sec": board_dep,
        "arr_sec": exit_arr,
        "minutes": round((exit_arr - board_dep) / 60),
        "stops": [day.stop_names[s] for s, _, _ in path_rows],
        "stops_count": max(len(path_rows) - 1, 1),
        "via": _via_stops(day, [(s, dep) for s, _, dep in path_rows[1:-1]]),
        "path": _round_path(coords),
        "_trip": trip,
        "_from_id": board_stop,
        "_to_id": exit_stop,
        "_arr_sec": exit_arr,
        # Godziny przejazdu przez kolejne przystanki etapu - do interpolacji
        # godziny w dowolnym punkcie linii na mapie (patrz _piece_times; tu
        # to samo, tylko dla trybu awaryjnego plan_flow, gdzie mapa rysuje
        # się wprost z etapów trasy, a nie z kawałków). Pole prywatne: do
        # odpowiedzi listy tras nie trafia (patrz _PRIVATE_LEG_KEYS).
        "_stops_t": [
            [*_round_path([day.stop_coords[stop]])[0],
             departure if i == 0 else arrival]
            for i, (stop, arrival, departure) in enumerate(path_rows)
        ],
    }


def _walk_leg(day, from_stop, to_stop):
    """Etap pieszy krawędzią mostu (patrz gtfs.siblings) - współdzielony przez
    _reconstruct (rekonstrukcja CSA) i _enumerate_journeys (przesiadka między
    segmentami mapy przepływów).

    Dwa różne przejścia, jeden etap. Zmiana stanowiska w obrębie jednego
    przystanku to dla pasażera co innego niż marsz na przystanek o innej
    nazwie albo pod dworzec - pierwsze się "robi po drodze", drugie trzeba
    ŚWIADOMIE przejść i trzeba wiedzieć DOKĄD.

    Rozstrzygają o tym NAZWY, nie miejsce. Miejsce (gtfs._build_places) bywa
    szersze niż jedna nazwa: zbiera dziś stację kolejową razem z przystankiem
    MPK przy niej (patrz naming.PLACE_MERGES), a wysiadającemu z pociągu na
    "Wrocław Nadodrze" zdanie o zmianie stanowiska nic nie mówi - on ma dojść
    do "DWORZEC NADODRZE". Gdy nazwy się różnią, mówimy więc dokąd, a nie że
    "gdzieś tu obok". Front rozstrzyga tak samo (patrz static/app.js): jedno
    przejście nie może mieć dwóch różnych opisów.

    `same_place` jedzie obok jako fakt o MIEJSCU - to inne pytanie niż
    o nazwę i tylko stąd da się na nie odpowiedzieć, więc etap niesie je
    gotowe dla każdego, kto go potrzebuje.
    """
    same_place = (day.place_of.get(from_stop, from_stop)
                  == day.place_of.get(to_stop, to_stop))
    walk_sec = gtfs.walk_seconds(day, from_stop, to_stop)
    minutes = round(walk_sec / 60)
    from_name = day.stop_names[from_stop]
    to_name = day.stop_names[to_stop]
    text = (
        f"Zmiana stanowiska na przystanku {to_name}"
        if from_name == to_name
        else f"Przejście z {from_name} do {to_name}"
    )
    return {
        "kind": "walk",
        "text": f"{text} (ok. {minutes} min)",
        "minutes": minutes,
        # Sekundy obok zaokrąglonych minut: gdy przejście OTWIERA trasę
        # (wyjście pieszo ze startu - patrz _origin_walk), godzina wyjścia
        # liczy się jako odjazd pierwszego pojazdu minus TO, a różnica
        # między 372 s a "6 min" potrafi być tą, która decyduje o zdążeniu.
        "_sec": walk_sec,
        "same_place": same_place,
        "from": day.stop_names[from_stop],
        "to": to_name,
        "dep_sec": 0,
        "path": _round_path([day.stop_coords[from_stop], day.stop_coords[to_stop]]),
    }


# ----------------------------------------------------------------- Traficar --
# Ostatni kawałek podróży wynajętym autem (patrz traficar.py): komunikacja
# dowozi w okolicę celu, a tam, gdzie nie ma już sensownej linii - albo gdzie
# byłaby to trzecia przesiadka na piętnaście minut - wsiada się do auta.
#
# To DODATKOWA pozycja na liście propozycji, nie zmiana wyszukiwania. Mapa
# przepływów nic o aucie nie wie i wiedzieć nie ma: rysuje kursy z rozkładu,
# z godzinami odczytanymi z tego rozkładu (punkty 10 i 12 kontraktu), a auto
# nie ma ani kursu, ani rozkładu - jego czas jest szacunkiem. Propozycja
# z autem rysuje się więc dopiero wtedy, gdy się ją wybierze, tak jak każda
# inna trasa z listy, i znika razem z nią.
#
# Gdy feed nie działa, propozycji po prostu nie ma - żaden błąd Traficara nie
# ma prawa zabrać odpowiedzi na pytanie "jak tam dojadę".
TRAFICAR_LIMIT = 2   # ile propozycji z autem najwyżej dokładamy do listy


def _reached_times(day, journey):
    """stop_id -> godzina, o której trasa ze skanu DOWOZI na ten słupek.

    Czytane wprost ze śladu `journey` (ten sam, z którego korzysta
    _reconstruct), więc nie trzeba drugiego skanu tylko po godziny.

    Bez słupków startowych: na nich się stoi od początku, a "dojdź do auta
    i jedź" nie jest propozycją dojazdu komunikacją miejską.
    """
    times = {}
    for stop, entry in journey.items():
        if entry[0] == "ride":
            times[stop] = day.conns[entry[2]][1]
        elif entry[0] == "walk":
            parent = journey[entry[1]]
            # Przejście z przejścia nie występuje (patrz _scan: piesze
            # relaksacje wychodzą wyłącznie z przyjazdu pojazdem), ale gdyby
            # kiedyś wystąpiło, brak wpisu jest bezpieczniejszy niż zła godzina.
            if parent[0] == "ride":
                times[stop] = (day.conns[parent[2]][1]
                               + gtfs.walk_seconds(day, entry[1], stop))
    return times


def _endpoint_point(day, stops, point):
    """(lat, lon) końca trasy: kliknięty punkt albo środek słupków miejsca.

    Auto jedzie do CELU, a nie na słupek - ale gdy cel podano nazwą,
    współrzędne tego miejsca są jedynym, co o nim wiadomo.
    """
    if point is not None:
        return point
    coords = [day.stop_coords[s] for s in stops if s in day.stop_coords]
    if not coords:
        return None
    return (sum(lat for lat, _ in coords) / len(coords),
            sum(lon for _, lon in coords) / len(coords))


def _car_walk_leg(day, from_stop, option):
    """Dojście z przystanku wysiadania do auta.

    Osobny etap od _walk_leg, choć tego samego rodzaju ("walk"): tamten
    prowadzi na słupek i nazywa go nazwą z rozkładu, a ten do pojazdu
    stojącego przy ulicy - nazwą miejsca postoju z feedu.
    """
    car = option["car"]
    minutes = round(option["walk_sec"] / 60)
    # Bez adresu z feedu nie ma dokąd iść z nazwy - zostaje sama odległość
    # i czas, a KTÓRE to auto powie już następny wiersz (patrz _car_drive_leg).
    where = car["where"]
    dokad = f" ({where})" if where else ""
    return {
        "kind": "walk",
        # Front rysuje ten etap tak samo jak każde inne przejście, ale opisuje
        # inaczej - "przejście na inne stanowisko" nie mówi nic komuś, kto ma
        # dojść do konkretnego auta (patrz static/app.js, detailHtml).
        "to_car": True,
        "text": f"Dojście do auta Traficar{dokad} - ok. {minutes} min",
        "minutes": minutes,
        "metres": option["walk_m"],
        "from": day.stop_names[from_stop],
        "to": where,
        "dep_sec": 0,
        "path": _round_path([day.stop_coords[from_stop], (car["lat"], car["lon"])]),
    }


def _car_drive_leg(option, dep_sec, end_name, dest):
    """Jazda autem do celu - ostatni etap propozycji z Traficarem.

    `estimated` jest w odpowiedzi po to, żeby front nie musiał wiedzieć, które
    rodzaje etapów mają rozkład, a które nie: godziny tego jednego są
    policzone z prędkości (patrz traficar.drive_time), więc wszędzie idą
    z "ok." i z kreskowaną, a nie ciągłą linią na mapie.

    `km` w PEŁNYCH kilometrach, choć `drive_m` zna metry: przy zmierzonym
    rozrzucie tego szacunku (22% na 90. centylu - patrz traficar.py) miejsce
    po przecinku byłoby udawaną dokładnością. Lepiej "ok. 8 km", które jest
    prawdziwe, niż "7,7 km", które brzmi jak odczyt z licznika.

    `path` to odcinek prosty od auta do celu - i tak ma być: prawdziwego
    przebiegu jazdy nikt tu nie liczy, a udawanie go ulicami byłoby
    obietnicą, której ta liczba nie pokrywa.
    """
    car = option["car"]
    arr_sec = dep_sec + option["drive_sec"]
    return {
        "kind": "drive",
        "line": f"Traficar {car['plate']}",
        "num": "Traficar",
        "mode": "car",
        "headsign": end_name,
        "from": car["where"] or "Postój Traficara",
        "from_time": _fmt_time(dep_sec),
        "to": end_name,
        "to_time": _fmt_time(arr_sec),
        "dep_sec": dep_sec,
        "arr_sec": arr_sec,
        "minutes": round(option["drive_sec"] / 60),
        "km": round(option["drive_m"] / 1000),
        "start_min": round(option["start_sec"] / 60),
        "plate": car["plate"],
        "model": car["model"],
        "fuel": car["fuel"],
        "range": car["range"],
        "estimated": True,
        "path": _round_path([(car["lat"], car["lon"]), dest]),
    }


def _traficar_journeys(day, journey, dep_sec, deadline, dest, end_name, geo_db):
    """Propozycje kończące się jazdą Traficarem - lista w kształcie takim
    samym jak z _enumerate_journeys, więc front nie musi ich rozpoznawać,
    żeby narysować.

    Dojazd do auta bierzemy ze śladu skanu CSA (`journey`), a nie z grafu
    segmentów mapy: mapa rysuje to, czym da się dojechać DO CELU, a tu trzeba
    czegoś innego - czym da się dojechać W OKOLICĘ auta, które do celu dowiezie
    już samo. Dlatego ta lista może zaproponować wysiadanie tam, gdzie mapa
    przepływów nic nie rysuje, i nie jest to sprzeczność: mapa odpowiada na
    pytanie o komunikację miejską, a to jest propozycja obok niej.

    `deadline` to to samo okno, którym mierzy się sensowność wszystkiego
    innego (patrz _deadline): auto, które dowozi później niż najwolniejszy
    pokazywany dojazd komunikacją, nie jest opcją, tylko szumem.
    """
    if dest is None:
        return []
    options = traficar.car_options(day, _reached_times(day, journey), dest,
                                   limit=TRAFICAR_LIMIT)
    journeys = []
    for option in options:
        if option["arrival"] > deadline:
            continue
        legs = _reconstruct(day, journey, option["stop"], geo_db)
        rides = [leg for leg in legs if leg["kind"] == "ride"]
        if not rides:
            continue
        legs.append(_car_walk_leg(day, option["stop"], option))
        legs.append(_car_drive_leg(
            option, option["arrival"] - option["drive_sec"], end_name, dest))
        summary = _summarize_journey(legs, rides, option["arrival"], dep_sec)
        # Wsiadanie do auta to zmiana pojazdu jak każda inna - karta ma mówić
        # "dwie przesiadki", gdy tyle razy trzeba z czegoś wysiąść i wsiąść
        # w coś innego, niezależnie od tego, czy to coś ma rozkład.
        summary["transfers"] = len(rides)
        summary["traficar"] = True
        _drop_private(legs)
        journeys.append(summary)
    return journeys


MODE_OF_LABEL = {"Tramwaj": "tram", "Autobus": "bus", "Pociąg": "train"}


def _line_parts(label):
    """'Tramwaj 17' -> ('17', 'tram') - numer na plakietkę i rodzaj do koloru."""
    kind, _, num = label.partition(" ")
    return (num or label), MODE_OF_LABEL.get(kind, "other")


def _round_path(coords):
    return [[round(lat, 5), round(lon, 5)] for lat, lon in coords]


# Próg mapy (punkt 2 kontraktu). Miarą jakości zostaje godzina, o której
# opcja dociera do celu; próg stoi tam, gdzie narysowana sieć osiąga docelową
# GĘSTOŚĆ (patrz _map_density), a nie tam, gdzie wypada jakaś liczba minut -
# ta sama liczba minut dawała raz pustą mapę, raz nieczytelny gąszcz.
# Domyślna wartość jest poniżej mediany dawnych map (3,5 przy oknie 125%,
# 5-15 min) - wybór użytkownika, pomiar w FLOW_MAP_NOTES.md, 2026-09-13.
DEFAULT_MAP_DENSITY = 2.5    # km różnych korytarzy na km boku kadru (km/√km²)
MIN_MAP_DENSITY = 0.5
MAX_MAP_DENSITY = 15.0       # sufit suwaka pod zębatką - pilnowany tutaj

# "Pokaż więcej" dokłada po jednej wyjściowej gęstości: x2, x3, x4.
MAX_MAP_MORE = 3

# Kadr relacji nie bywa węższy niż tyle z żadnej strony: kilometrowej trasy
# mapa i tak nie pokaże ciaśniej (maxZoom w app.js), więc liczenie gęstości
# na pasku szerokości ulicy dawało jej cel nieosiągalnie mały.
MIN_FRAME_SIDE_KM = 1.0

# Ile aut car-sharingu mapa pokazuje (punkt 15, patrz traficar.map_skyband).
# Auta, których nic nie bije, są na mapie i ponad tę liczbę - a bywa ich
# sporo: na dziesięciu relacjach (2026-09-13, 20:20) od 2 do 13, mediana 6-7.
DEFAULT_MAP_CARS = 3
MIN_MAP_CARS = 1
MAX_MAP_CARS = 30            # sufit suwaka pod zębatką - pilnowany tutaj

# Ile przejazdów rowerem mapa pokazuje (punkt 16, patrz bikes.map_places) -
# ta sama reguła co przy autach, osobny suwak.
DEFAULT_MAP_BIKES = 3
MIN_MAP_BIKES = 1
MAX_MAP_BIKES = 30           # sufit suwaka pod zębatką - pilnowany tutaj

# Założenia roweru pod zębatką (zgłoszenie #151): prędkość W LINII PROSTEJ
# (dziś bikes.MAP_RIDE_MPS, 10 km/h) i stały narzut przejazdu (dziś
# bikes.MAP_OVERHEAD_SEC, 2 min). Sufity suwaków - pilnowane tutaj.
MIN_BIKE_KMH = 5
MAX_BIKE_KMH = 20
MAX_BIKE_OVERHEAD_SEC = 10 * 60

# To samo dla jazdy Traficarem (zgłoszenie #150): prędkość W LINII PROSTEJ
# (dziś traficar.MAP_DRIVE_MPS) i stały narzut na ruszenie i parkowanie
# (dziś traficar.MAP_OVERHEAD_SEC). Sufity suwaków - pilnowane tutaj.
MIN_CAR_KMH = 10
MAX_CAR_KMH = 40
MAX_CAR_OVERHEAD_SEC = 20 * 60

# Ile pojazdów przed rowerem i ile po nim w ogóle się rozważa (punkt 16).
# Każda runda to przejście po całej narysowanej mapie, a podróż z pięcioma
# pojazdami po jednej stronie roweru nie jest tym, po co ktoś bierze rower.
MAX_BIKE_SIDE_RIDES = 4

# Mapa z wartości podróży (zgłoszenie #150, patrz _value_journeys).
# Ile pojazdów najwyżej w jednej podróży - więcej to już nie wybór, tylko
# objazd miasta, a każdy kolejny mnoży koszt szukania.
VALUE_MAX_RIDES = 5
# Jak daleko za najszybszym przyjazdem sięga szukanie - kolejne szerokości,
# po które mapa sięga dopiero wtedy, gdy poprzednia nie wystarcza. Przesiadki
# nie odcinają tu krążenia w czasie czekania, więc koszt rośnie z szerokością
# lawinowo: Rynek -> Sosnowiecka 21:55 - 30 min to 0,4 s, 40 min 3,7 s,
# godzina 19 minut (28.09). Stąd kroki po 5 minut, a nie skok z 20 od razu
# na 40: trzecie „więcej" na Leśnicy potrzebowało tolerancji 25 minut,
# a płaciło za 40 (15 s).
VALUE_WINDOWS_SEC = tuple(minutes * 60 for minutes in (20, 25, 30, 35, 40))
# Ile podróży i ile ukrytych wariantów wymienia podgląd "dlaczego ten
# kawałek" (Debug) - reszta jako liczba, bo dymek ma się dać przeczytać.
WHY_SHOWN = 3

DEFAULT_JOURNEY_LIMIT = 6     # domyślnie tyle propozycji tras szukamy/pokazujemy
MIN_JOURNEY_LIMIT = 1
MAX_JOURNEY_LIMIT = 20        # (suwak w UI go nadpisuje) - "na siłę" więcej wariantów
MAX_JOURNEY_CHAIN_LEGS = 4    # maks. liczba etapów przejazdu w jednej propozycji
MAX_JOURNEY_CANDIDATES = 18   # tyle łańcuchów zbieramy przed sortowaniem/ucięciem PRZY
                              # DOMYŚLNYM limicie (patrz CANDIDATES_PER_JOURNEY niżej)
# Sufit kosztu (węzły przeszukiwania) PRZY DOMYŚLNYM limicie. Podniesiony
# z 500 (2026-09-10): tamta wartość była kalibrowana na graf sprzed kolei
# i sprzed przejść pieszych między różnymi przystankami, a każde z tych
# rozszerzeń zagęszcza graf przesiadek. Przy 500 relacja "Wrocław Główny ->
# Warszawa Centralna" wyczerpywała budżet, ZANIM BFS zszedł do segmentów
# dojeżdżających do celu, i lista propozycji wychodziła PUSTA, mimo mapy
# z 1300 segmentów. Podniesienie jest praktycznie darmowe - zmierzone na
# pięciu relacjach: 500 -> 4000 to 2,30 s -> 2,38 s łącznie, czyli szum.
MAX_JOURNEY_VISITS = 4000
CANDIDATES_PER_JOURNEY = 3    # gdy suwak żąda więcej niż domyślne 6 - MAX_JOURNEY_CANDIDATES/
                              # DEFAULT_JOURNEY_LIMIT, żeby żądanie większej liczby
VISITS_PER_JOURNEY = 667      # propozycji faktycznie szukało głębiej, a nie tylko ucinało
                              # krócej listę tych samych paru znalezionych łańcuchów


def _city_group_error(day, start_name, source_stops, end_name, target_stops):
    """Błąd, gdy "dowolna stacja w mieście" (gtfs.is_city_group) stoi naprzeciw
    czegoś innego niż kolej, albo None. Grupa ma sens tylko w podróży koleją:
    na pl. Grunwaldzki i tak jedzie się tramwajem spod KONKRETNEJ stacji, więc
    "dowolna stacja we Wrocławiu -> pl. Grunwaldzki" nie jest pytaniem, na
    które da się uczciwie odpowiedzieć - zgłoszone przez użytkownika."""
    for name, stops, other_name, other in (
            (start_name, source_stops, end_name, target_stops),
            (end_name, target_stops, start_name, source_stops)):
        if not gtfs.is_city_group(day, stops):
            continue
        if not gtfs.is_rail(day, other):
            return {"error": f"„{name}” to dowolna stacja w mieście — działa "
                             f"tylko ze stacją kolejową po drugiej stronie."}
        # Stacja z tej samej grupy: stoi się na niej od początku, więc nie ma
        # dokąd jechać - bez tego "WROCŁAW -" -> Wrocław Brochów szukało przez
        # tydzień do przodu, a odwrotnie rysowało pełną mapę po mieście.
        if set(stops) & set(other):
            return {"error": f"„{other_name}” to jedna ze stacji „{name}” — "
                             f"dowolna stacja w mieście działa z inną miejscowością."}
    return None


def _resolve_endpoints(day, start_query, end_query, start_point, end_point,
                       ride=None):
    """Start i cel -> dzień, nazwy do pokazania + zbiory słupków do skanowania.

    Każda strona niezależnie: nazwa przystanku (match_stop, całe kanoniczne
    miejsce) albo dowolny punkt z mapy. Wspólne dla mapy przepływów i listy
    propozycji - obie muszą rozumieć krańce relacji tak samo.

    `ride` to rozpoznany kurs, w którym pasażer właśnie siedzi (patrz
    onboard.find_ride). Startem jest wtedy DOKŁADNIE JEDEN SŁUPEK - ten, przy
    którym pojazd zaraz stanie - a nie całe miejsce: z pokładu nie ma się do
    wyboru trzech peronów placu, tylko ten jeden, pod którym otworzą się
    drzwi. Na sąsiednie i tak da się przejść pieszo, zwykłym mostem, i będzie
    to widać jako etap.

    Punkt z mapy wchodzi do dnia jako zwykły słupek bez połączeń
    (gtfs.with_point), więc niżej nikt już nie musi wiedzieć, że relacja
    zaczyna się poza przystankiem: dojście z punktu na przystanek jest tym
    samym przejściem pieszo, co każde inne, i tyle samo kosztuje. Dlatego
    zwracamy też `day` - dołożenie punktu robi kopię dnia.
    """
    resolved = {}
    for side, query, point, missing in (
        ("start", start_query, start_point, "startowego"),
        ("end", end_query, end_point, "docelowego"),
    ):
        stops_key = "source_stops" if side == "start" else "target_stops"
        if side == "start" and ride is not None:
            resolved[side] = ride["stop_name"]
            resolved[stops_key] = {ride["stop"]}
        elif point is not None:
            day, point_stop = gtfs.with_point(day, point[0], point[1], side)
            if not day.siblings[point_stop]:
                return {"error": f"Brak przystanków w zasięgu wybranego punktu {missing}."}
            resolved[side] = day.stop_names[point_stop]
            resolved[stops_key] = {point_stop}
        else:
            name, stops, hints = gtfs.match_stop(query, day)
            if name is None:
                return _unknown_stop(query, hints)
            resolved[side] = name
            resolved[stops_key] = set(stops)

    if resolved["start"] == resolved["end"]:
        return {"error": "Przystanek początkowy i końcowy są takie same."}
    group_error = _city_group_error(day, resolved["start"], resolved["source_stops"],
                                    resolved["end"], resolved["target_stops"])
    if group_error:
        return group_error
    resolved["day"] = day
    return resolved


# Ile dób do przodu wolno szukać, gdy o podaną godzinę nie jedzie już nic
# (punkt 13 kontraktu). Tydzień, bo rozkład jest tygodniowy: relacja, która
# nie ma kursu przez siedem dni, nie ma go w ogóle - i wtedy "nie znaleziono"
# jest prawdziwą odpowiedzią, a nie poddaniem się po pierwszej próbie.
SEARCH_AHEAD_DAYS = 7


def _journey_start(day, journey, last_stop):
    """Godzina, o której trasa naprawdę RUSZA - odjazd pierwszego przejazdu.

    Nie to samo co godzina pytania: między jednym a drugim może być godzina
    czekania, a czekanie nie jest częścią podróży. Okno mapy liczy się od
    wyjazdu (punkt 13 kontraktu), inaczej trasa, na którą czeka się godzinę,
    dostawałaby wachlarz rozdęty o tę godzinę.
    """
    stop = last_stop
    start = None
    while journey[stop][0] != "origin":
        entry = journey[stop]
        if entry[0] == "walk":
            stop = entry[1]
        else:
            _, board_i, _ = entry
            start = day.conns[board_i][0]
            stop = day.conns[board_i][2]
    return start


def _frame_km2(day, legs, stops):
    """Powierzchnia kadru, w którym mapa pokazuje relację: prostokąt wokół
    najszybszej trasy i obu krańców.

    Z najszybszej trasy, a nie z tego, co narysowane: kadr liczony z rysunku
    rósłby razem z progiem, który sam ma wyznaczać."""
    points = [p for leg in legs for p in (leg.get("path") or ())]
    points += [day.stop_coords[s] for s in stops if s in day.stop_coords]
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    mid_lat = (min(lats) + max(lats)) / 2
    height = gtfs._haversine_m(min(lats), lons[0], max(lats), lons[0]) / 1000
    width = gtfs._haversine_m(mid_lat, min(lons), mid_lat, max(lons)) / 1000
    return max(height, MIN_FRAME_SIDE_KM) * max(width, MIN_FRAME_SIDE_KM)


def _corridor_km(day, kept, ranges):
    """Łączna długość RÓŻNYCH korytarzy narysowanej sieci, w kilometrach.

    Odcinkiem jest para sąsiednich MIEJSC, nie linia ani słupek: dwadzieścia
    numerów jednym korytarzem to w oku jedna kreska (punkt 7), a tramwaj
    i autobus stają na osobnych peronach tego samego placu. Długość idzie
    między środkami miejsc - liczona między słupkami zależałaby od tego,
    który peron akurat narysował ten odcinek, i gęstość drgałaby przy progu,
    który niczego nowego nie dołożył."""
    hops = {}
    for seg in kept:
        start_pos, cut = ranges[id(seg)]
        stops = seg["stops"]
        for k in range(start_pos, cut - 1):
            a, b = stops[k], stops[k + 1]
            place_a, place_b = day.place_of.get(a, a), day.place_of.get(b, b)
            key = _hop_key(place_a, place_b)
            hops.setdefault(key, (a, b) if key[0] == place_a else (b, a))
    metres = 0.0
    for (place_a, place_b), (stop_a, stop_b) in hops.items():
        lat_a, lon_a = _place_center(day, place_a, stop_a)
        lat_b, lon_b = _place_center(day, place_b, stop_b)
        metres += gtfs._haversine_m(lat_a, lon_a, lat_b, lon_b)
    return metres / 1000


def _map_density(corridor_km, frame_km2):
    """Gęstość z punktu 2, taka jak na ekranie: mapa wpasowuje każdy kadr
    w to samo okno, a kreska ma stałą grubość w pikselach - tłok to więc
    długość korytarzy przez BOK kadru, nie przez jego powierzchnię. Przez
    powierzchnię kadr 30 razy większy dostawał 30 razy mniej, choć na
    ekranie jest ciaśniej tylko √30 ≈ 5,5 raza (FLOW_MAP_NOTES.md,
    2026-09-13)."""
    return corridor_km / sqrt(frame_km2)


# ------------------------------------ mapa z wartości podróży (#150) ----
#
# Mapa rysuje PODRÓŻE od startu do celu, każdą opisaną trzema równymi
# wartościami: o której w celu, o której trzeba wyjść i iloma pojazdami.
# Chodzenie wartością nie jest - liczy się tylko w minutach - a przesiadki
# niczego nie rozstrzygają (decyzje z 28.09). Na mapie jest to, co jest
# w czymś najlepsze, a gęstość i "pokaż więcej" wybaczają MINUTY - przyjazdu
# i wyjścia. Przesiadkę, która niczego nie daje, wycina reguła numerów
# (_same_numbers_best): trasa jadąca tymi samymi numerami co nie gorsza, albo
# tymi samymi z dołożonym, nie pojawia się przy żadnej gęstości. Tak odpada
# przesiadka w pojazd, który i tak zaraz przyjedzie tam, gdzie się stoi
# (tramwaj 14 na Borku w relacji Pl. Zgody -> Klecina: ten sam wyjazd, ten sam
# przyjazd, piątka i siedemnastka z dołożoną czternastką).
# Bez wag: żadna wartość nie jest przeliczana na inną.


class _Label:
    """Jedna klasa podróży w jednym miejscu: na przystanku albo w pojeździe.

    Na przystanku `ready` to chwila, od której da się tu wsiąść (przyjazd,
    po pojeździe - z buforem przesiadki), a `arr` - sam przyjazd; w pojeździe
    obie są None. `dep` to godzina wyjścia ze startu, None, dopóki nie wsiadło
    się w nic - wtedy `lead` mówi, ile trwał marsz ze startu, bo wyjście
    ustala dopiero pierwszy pojazd.

    `parents` to WSZYSTKIE drogi, którymi da się tu być z tymi samymi
    wartościami: (etykieta, połączenie) - wsiadanie, wysiadanie albo, z None,
    dojście pieszo. Bliźniaki nie konkurują: idą jedną etykietą i razem
    trafiają na mapę - dwie linie dowożące na przesiadkę o tej samej minucie
    to wybór "wsiądź w to, co przyjedzie pierwsze", a nie jedna zbędna."""
    __slots__ = ("ready", "arr", "dep", "rides", "lead", "parents")

    def __init__(self, ready, arr, dep, rides, lead, parents):
        self.ready = ready
        self.arr = arr
        self.dep = dep
        self.rides = rides
        self.lead = lead
        self.parents = parents


def _stop_metres(day, a, b):
    # Stacje PKP współrzędnych nie mają (patrz pkp.py) - odcinek z nimi
    # liczy się bez odległości, jak przejście w gtfs.walk_seconds.
    pa, pb = day.stop_coords.get(a), day.stop_coords.get(b)
    return 0.0 if pa is None or pb is None else gtfs._haversine_m(*pa, *pb)


def _value_journeys(day, source_stops, target_set, dep_sec, best_arr,
                    forgive_sec, seated=None):
    """Wszystkie podróże od `dep_sec` z przyjazdem do `deadline`, pogrupowane
    po trzech wartościach w pokazywanej dokładności: [{key, arr, dep,
    rides, labels}, ...], `key` = (minuta w celu, minuta wyjścia, pojazdy).

    Connection Scan z etykietami wielokryterialnymi (jak McRAPTOR, tylko po
    połączeniach). Wcześniejszy przyjazd czy późniejsze wyjście same niczego
    nie odcinają, bo "pokaż więcej" je wybacza i ta podróż ma wtedy wrócić.
    Etykiety o tych samych minutach to bliźniaki - idą dalej razem, niezależnie
    od przesiadek, a rozdziela je dopiero reguła numerów w celu.

    Przejścia piesze liczą się tą samą regułą co wszędzie (punkt 14): ze
    startu (_origin_walk), jedno po wysiadce (day.siblings) i do celu
    (_target_reach) - nigdy dwa pod rząd. Skan wstecz (_backward) ucina
    przystanki, z których do `deadline` i tak się nie zdąży.

    Szuka się do `best_arr` + `forgive_sec` i tyle najwyżej da się podróży
    wybaczyć (_value_entries: spóźnienie w celu PLUS wcześniejsze wyjście).
    Minuty wybacza się każdej trasie, więc nic nie ucina krążenia w czasie
    czekania - wieczorem, przy rzadkich kursach, tych dróg są setki. Odcina
    się więc etykietę, która wyszła o więcej niż `forgive_sec` wcześniej niż
    inna, stojąca tu nie później: tyle co najmniej wynosi jej wcześniejsze
    wyjście, więc nie wejdzie przy żadnym „więcej". (Próbowane 28.09:
    dokładać do tego dolną granicę spóźnienia z profilu rozkładu - odcinało
    niewiele, a samo liczenie granicy kosztowało 13 s na Leśnicy.)

    `seated` to kurs, w którym pasażer już siedzi (start z pokładu, patrz
    _board_buffer): w niego "wsiada" się na starcie bez zapasu, w każdy inny
    pojazd ze startu - z buforem przesiadki, a ten, w którym się siedzi,
    liczy się wtedy jako pojazd podróży. Godzina wyjścia nie jest wartością,
    bo pasażer już jedzie - każda podróż wychodzi o `dep_sec`."""
    conns = day.conns
    deadline = best_arr + forgive_sec
    latest = _backward(day, target_set, dep_sec, deadline)
    near_target = _target_reach(day, target_set)

    at_stop = {}     # słupek -> etykiety, z których da się tu wsiąść
    on_trip = {}     # kurs -> etykiety jadące nim
    edges_of = {}    # kurs -> [najpóźniejsze, najwcześniejsze] wyjście w nim
    finals = {}      # key -> klasa podróży
    # Na przystanku, po pojeździe: (ready, arr, dep) -> etykieta, żeby
    # bliźniaka znaleźć od razu, oraz [najpóźniejsze wyjście, najwcześniejsze
    # wyjście] - granice, z których widać, że nikt tu nikogo nie odetnie,
    # zanim przejdzie się po całym worku (to był koszt szukania: 190 mln
    # porównań na Leśnicy). Zapas minuty na zaokrąglenia do pełnych minut.
    twins_at = {}
    spread_at = {}
    limit = forgive_sec + 60

    def settle(stop, twins, label):
        """Etykieta po pojeździe na przystanku, gdy nie jest bliźniakiem już
        zapisanej - bliźniaka sprawdza wołający, bo to najczęstszy przypadek,
        a wtedy etykieta nie musi nawet powstać. Do worka nie trafia
        etykieta, którą coś tu bije na zawsze."""
        dep = label.dep
        bag = at_stop.setdefault(stop, [])
        spread = spread_at.get(stop)
        if spread is None:
            spread_at[stop] = [dep, dep]
        else:
            if spread[0] - dep > limit:
                for other in bag:
                    if other.rides and beats(other, label):
                        return
            if dep - spread[1] > limit:
                beaten = [other for other in bag
                          if other.rides and beats(label, other)]
                if beaten:
                    gone = set(map(id, beaten))
                    bag[:] = [other for other in bag if id(other) not in gone]
                    for other in beaten:
                        del twins[other.ready, other.arr, other.dep]
            spread[0] = max(spread[0], dep)
            spread[1] = min(spread[1], dep)
        twins[label.ready, label.arr, dep] = label
        bag.append(label)

    def beats(one, other):
        """Czy `one` odcina `other` (na przystanku - stojąc tu nie później;
        w pojeździe - w tym samym miejscu tego samego kursu)."""
        if one.ready is not None and not (one.ready <= other.ready
                                          and one.arr <= other.arr):
            return False
        return one.dep - other.dep > limit

    def board(trip, bag, dep, rides, parent):
        """W pojeździe liczy się samo wyjście: bliźniak (to samo wyjście,
        sprawdza go wołający) już tu jest, a odcina wyjście późniejsze
        o więcej niż `limit` - więc worek to słownik wyjście -> etykieta
        i wystarczy patrzeć na jego skraje. Skraje leżą obok w `edges_of`:
        najpóźniejsze wyjście nigdy nie wypada (wypadają tylko wcześniejsze
        od nowego), więc przelicza się je dopiero po wyrzuceniu."""
        edges = edges_of.get(trip)
        if edges is None:
            edges_of[trip] = [dep, dep]
        else:
            if edges[0] - dep > limit:
                return
            if dep - edges[1] > limit:
                for old in [old for old in bag if dep - old > limit]:
                    del bag[old]
                edges[1] = min(bag, default=dep)
            edges[0] = max(edges[0], dep)
            edges[1] = min(edges[1], dep)
        bag[dep] = _Label(None, None, dep, rides, 0, [parent])

    def finish(arrival, label):
        if arrival > deadline:
            return
        key = (arrival // 60, label.dep // 60, label.rides)
        entry = finals.get(key)
        if entry is None:
            finals[key] = {"key": key, "arr": arrival, "dep": label.dep,
                           "rides": label.rides, "labels": [label]}
        else:
            entry["labels"].append(label)
            entry["arr"] = min(entry["arr"], arrival)

    # Przed pierwszym pojazdem nikt nikogo nie odcina ani nie jest niczyim
    # bliźniakiem - etykiety idą prosto do worka.
    for stop in source_stops:
        at_stop.setdefault(stop, []).append(
            _Label(dep_sec, dep_sec, None, 0, 0, []))
    for stop, (_, sec) in _origin_walk(day, source_stops).items():
        if stop in target_set:
            continue           # samo przejście nie jest trasą (patrz _scan)
        at_stop.setdefault(stop, []).append(
            _Label(dep_sec + sec, dep_sec + sec, None, 0, sec, []))

    lo = bisect_left(day.dep_times, dep_sec)
    hi = bisect_right(day.dep_times, deadline)
    # Wsiadanie do kursu, który od tego połączenia dalej nie wysadzi nikogo
    # w miejscu, skąd jeszcze się zdąży, niczego nie zmieni - etykieta w nim
    # nigdy nie wysiądzie. A to większość wsiadań: każda etykieta na
    # przystanku wsiada w każdy kurs, który stąd odjeżdża.
    onward = bytearray(hi - lo)
    alights = set()
    for i in range(hi - 1, lo - 1, -1):
        _, arr_t, _, arr_s, trip = conns[i]
        if arr_t <= deadline and arr_t <= latest.get(arr_s, -1):
            alights.add(trip)
        if trip in alights:
            onward[i - lo] = 1

    for i in range(lo, hi):
        if not onward[i - lo]:
            continue
        dep_t, arr_t, dep_s, arr_s, trip = conns[i]
        if arr_t > deadline:
            continue
        boarding = at_stop.get(dep_s)
        if boarding:
            bag = on_trip.setdefault(trip, {})
            for label in boarding:
                rides = label.rides + 1
                if label.rides:
                    ready, dep = label.ready, label.dep
                elif label.lead:
                    ready, dep = label.ready, dep_t - label.lead
                else:
                    ready = label.ready + _board_buffer("origin", trip, seated)
                    dep = dep_t
                if seated is not None:
                    dep = dep_sec
                    if not label.rides and trip != seated:
                        rides += 1
                if ready > dep_t or rides > VALUE_MAX_RIDES:
                    continue
                twin = bag.get(dep)
                if twin is not None:
                    twin.parents.append((label, i))
                else:
                    board(trip, bag, dep, rides, (label, i))
        riding = on_trip.get(trip)
        if not riding or arr_t > latest.get(arr_s, -1):
            continue
        # Dojście z tego wysiadania jest takie samo dla każdego, kto tu
        # wysiada - liczone raz, nie dla każdej etykiety osobno.
        reach = near_target.get(arr_s)
        walks = []
        for sibling, sec in day.siblings.get(arr_s, {}).items():
            when = arr_t + sec
            if when <= latest.get(sibling, -1):
                walks.append((sibling, when, sibling in target_set,
                              twins_at.setdefault(sibling, {})))
        twins = twins_at.setdefault(arr_s, {})
        ready = arr_t + TRANSFER_SEC
        for label in list(riding.values()):
            twin = twins.get((ready, arr_t, label.dep))
            if twin is not None:
                # Bliźniak dojdzie dalej razem z tamtą etykietą.
                twin.parents.append((label, i))
                continue
            here = _Label(ready, arr_t, label.dep, label.rides, 0, [(label, i)])
            settle(arr_s, twins, here)
            # Dalej pieszo - także z etykiety, która tu przegrała: przegrała
            # jako miejsce WSIADANIA (tamta zdąży na wszystko, na co ona), ale
            # jej własne dojście może dawać coś, czego tamtej brak.
            if reach is not None:
                finish(arr_t + reach[0], here)
            for sibling, when, at_target, there in walks:
                twin = there.get((when, when, here.dep))
                if at_target or twin is None:
                    walked = _Label(when, when, here.dep, here.rides, 0,
                                    [(here, None)])
                # Do celu idzie ta etykieta, nawet jeśli na przystanku okaże
                # się bliźniakiem - z tą jedną drogą, nie z cudzymi.
                if at_target:
                    finish(when, walked)
                if twin is not None:
                    twin.parents.append((here, None))
                else:
                    settle(sibling, there, walked)
    return list(finals.values())


def _label_variants(day, label, memo, ride_metres, pick):
    """Drogi prowadzące do etykiety przystanku (razem z bliźniakami) po
    numerach linii: {numery: ((metry jazdy, sekundy chodzenia), przejazdy)},
    przejazd to (połączenie wsiadania, połączenie wysiadania). Z dróg tymi
    samymi numerami zostaje ta, którą wybierze `pick` (patrz _value_map).

    Z dróg tymi samymi numerami zostaje najkrótsza: dłuższa to ta sama
    podróż z nadłożeniem drogi - np. trójką za przystanek przesiadki
    i z powrotem tym samym kursem dziesiątki, co wsiadając od razu."""
    key = id(label)
    if key not in memo:
        found = {} if label.parents else {frozenset(): [((0.0, label.lead), frozenset())]}
        for parent, conn in label.parents:
            if conn is None:
                # Dojście po wysiadce: od przyjazdu pojazdu do chwili tutaj.
                walk = label.ready - parent.arr
                options = [(nums, ((metres, walked + walk), rides))
                           for nums, ((metres, walked), rides)
                           in _label_variants(day, parent, memo, ride_metres, pick).items()]
            else:
                options = []
                line = day.trip_info[day.conns[conn][4]][0]
                # Rodzicem jest etykieta pojazdu: każde jego wsiadanie PRZED
                # tym wysiadaniem to jeden przejazd. Późniejsze wsiadania
                # dopisały się do tej samej etykiety już po nim i tej drogi
                # nie dotyczą.
                for board_label, board_conn in parent.parents:
                    if board_conn > conn:
                        continue
                    ride = (board_conn, conn)
                    length = ride_metres(ride)
                    for nums, ((metres, walked), rides) in _label_variants(
                            day, board_label, memo, ride_metres, pick).items():
                        options.append((nums | {line},
                                        ((metres + length, walked), rides | {ride})))
            for nums, value in options:
                found.setdefault(nums, []).append(value)
        out = {nums: pick(values) for nums, values in found.items()}
        # Warianty jednej etykiety mają te same minuty i te same przesiadki,
        # a dalej pojadą tak samo - ten, który tylko dokłada numer do innego,
        # przegra w celu (_same_numbers_best) i tak. Odcięty tu nie mnoży się
        # przez kolejne przesiadki: bez tego przy samych minutach wieczorny
        # Rynek liczył się minutami.
        memo[key] = {nums: value for nums, value in out.items()
                     if not any(other < nums for other in out)}
        memo["cut", key] = [(nums, value[1], next(other for other in memo[key]
                                                  if other < nums))
                            for nums, value in out.items()
                            if nums not in memo[key]]
    return memo[key]


def _rides_instead_of_walking(day, one, other):
    """Czy wariant `one` to `other`, tylko z dłuższą jazdą TYM SAMYM kursem
    zamiast chodzenia: różnią się jednym przejazdem, w `one` ten kurs łapie
    się wcześniej albo wysiada z niego później, i chodzi się mniej."""
    (_, walk), rides = one
    (_, other_walk), other_rides = other
    if walk >= other_walk:
        return False
    mine, theirs = rides - other_rides, other_rides - rides
    if len(mine) != 1 or len(theirs) != 1:
        return False
    (board, alight), = mine
    (other_board, other_alight), = theirs
    return (day.conns[board][4] == day.conns[other_board][4]
            and board <= other_board and alight >= other_alight)


def _same_numbers_best(classes):
    """Numery linii, którymi podróż jedzie. Wariant podróży znika na zawsze,
    gdy inna podróż jedzie CZĘŚCIĄ jego numerów (albo wszystkimi) i nie jest
    gorsza w minutach - a jeśli jedzie tymi samymi numerami, to jest w czymś
    lepsza.
    Z tych samych numerów pokazuje się więc tylko najlepszy wariant, a trasa
    z dołożonym numerem, która i tak jedzie tamtymi, nie pokazuje się wcale
    (czternastka na Borku: piątka, czternastka, siedemnastka przy piątce
    z siedemnastką o tych samych minutach). Innymi numerami wolno wejść nawet
    trasie dłuższej, a nie szybszej: to już inna trasa.

    Ustawia `rides_set` (przejazdy wariantów, które zostały), `hidden` (co
    zniknęło i przez co - dla podglądu) i `entry` INF, gdy nie został żaden."""
    by_nums = {}
    for journey in classes:
        for nums in journey["variants"]:
            by_nums.setdefault(nums, []).append(journey)
    inside = {nums: [other for other in by_nums if other <= nums]
              for nums in by_nums}
    for journey in classes:
        a, b, _ = journey["key"]
        # Odcięte już na przystanku celu (_label_variants) - ukrywa je
        # wariant tej samej podróży.
        kept = []
        journey["hidden"] = [(nums, rides, (inner, journey))
                             for nums, rides, inner in journey["cut"]]
        for nums, (_, rides) in journey["variants"].items():
            hider = next(
                ((other, rival) for other in inside[nums]
                 for rival in by_nums[other]
                 if rival["key"][0] <= a and rival["key"][1] >= b
                 and (other != nums or rival["key"] != journey["key"])),
                None)
            if hider is None:
                kept.append((nums, rides))
            else:
                journey["hidden"].append((nums, rides, hider))
        journey["kept"] = kept
        journey["rides_set"] = set().union(*(rides for _, rides in kept))
        if not kept:
            journey["entry"] = INF


def _value_entries(classes, best_arr):
    """Od jakiej tolerancji w minutach każda podróż jest na mapie - pole
    `entry`.

    Tolerancja wybacza dwie rzeczy i tylko je: przyjazd późniejszy od
    najszybszego (`late`) i wyjście wcześniejsze niż podróż, która w celu nie
    jest później (`early`, a ta podróż to `early_by` - dla podglądu). Liczy
    się ich SUMA - o tyle dłużej trwa ta podróż (decyzja użytkownika z 28.09:
    przy tolerancji 16 min wchodzą podróże najwyżej 16 min dłuższe, a nie
    spóźnione o 16 i do tego wychodzące 16 wcześniej). Przesiadki niczego tu
    nie rozstrzygają, więc trasa z dwiema przesiadkami 5 minut później wchodzi
    tak samo jak z jedną 5 minut później.

    `early` to najpóźniejsze wyjście wśród podróży z przyjazdem nie
    późniejszym - jedno przejście po klasach posortowanych po przyjeździe,
    a nie każda z każdą (tysiące klas przy szerszym szukaniu). Liczy się
    tylko na podróżach z przyjazdem nie późniejszym, więc wynik jest ten sam,
    jakkolwiek szeroko by szukano."""
    best_min = best_arr // 60
    ordered = sorted(classes, key=lambda c: c["key"][0])
    latest = None
    i = 0
    while i < len(ordered):
        # Ta sama minuta w celu to "nie później" w obie strony - najpierw cała
        # grupa do `latest`, dopiero potem jej tolerancje.
        j = i
        while j < len(ordered) and ordered[j]["key"][0] == ordered[i]["key"][0]:
            if latest is None or ordered[j]["key"][1] > latest["key"][1]:
                latest = ordered[j]
            j += 1
        for mine in ordered[i:j]:
            a, b, _ = mine["key"]
            mine["late"] = max(0, a - best_min)
            gain = latest["key"][1] - b
            mine["early"], mine["early_by"] = (gain, latest) if gain > 0 else (0, None)
            mine["entry"] = mine["late"] + mine["early"]
        i = j


def _value_segments(day, chosen):
    """Wybrane podróże jako segmenty -
    rysuje je dalej ta sama maszyneria (_finalize_segments, węzły, auta,
    rowery). Jasności nie ma: każdy kawałek jest pełny (q 1.0).

    Jeden segment to jeden pojazd na ciągłym odcinku - przejazdy tym samym
    kursem, które się stykają albo zachodzą na siebie, idą razem, bo inaczej
    ta sama linia leżałaby na mapie sama na sobie. `arrive` kawałka to
    najwcześniejszy przyjazd do celu podróży, która jedzie tędy dalej.

    Zwraca (segmenty, zakresy, {(linia, słupek, słupek): indeksy podróży}) -
    to ostatnie dla podglądu "dlaczego ten kawałek"."""
    conns = day.conns
    by_trip = {}
    ride_arrival = {}
    for n, journey in enumerate(chosen):
        for ride in journey["rides_set"]:
            by_trip.setdefault(conns[ride[0]][4], set()).add(ride)
            ride_arrival.setdefault(ride, []).append((journey["arr"], n))

    segs, hops = [], {}
    for trip, rides in by_trip.items():
        idxs = gtfs.trip_conns(day, trip)
        order = {i: p for p, i in enumerate(idxs)}
        label, headsign = day.trip_info[trip]
        spans = sorted((order[b], order[a], (b, a)) for b, a in rides)
        merged = []
        for pb, pa, ride in spans:
            if merged and pb <= merged[-1][1] + 1:
                merged[-1][1] = max(merged[-1][1], pa)
                merged[-1][2].append((pa, ride))
            else:
                merged.append([pb, pa, [(pa, ride)]])
        for pb, pa, ends in merged:
            run = list(idxs[pb:pa + 1])
            stops = [conns[run[0]][2]] + [conns[i][3] for i in run]
            for b_pos, a_pos, (b, a) in spans:
                if pb <= b_pos and a_pos <= pa:
                    for k in range(b_pos - pb, a_pos - pb + 1):
                        hops.setdefault((label, stops[k], stops[k + 1]), set()).update(
                            n for _, n in ride_arrival[(b, a)])
            exits = {}
            for end, ride in ends:
                pos = end - pb + 2
                best = min(ride_arrival[ride])[0]
                if pos not in exits or best < exits[pos][1]:
                    exits[pos] = (conns[idxs[end]][1], best)
            positions = sorted(exits)
            suffix = [exits[p][1] for p in positions]
            for j in range(len(suffix) - 2, -1, -1):
                suffix[j] = min(suffix[j], suffix[j + 1])
            segs.append({
                "label": label,
                "headsign": headsign,
                "trip_id": trip,
                "stops": stops,
                "pos_of": {s: p for p, s in enumerate(stops)},
                "exits": [(p, None, exits[p][0], stops[p - 1]) for p in positions],
                "suffix": suffix,
                "suffix_exact": [True] * len(positions),
                "best_deps": {stops[k]: conns[i][0] for k, i in enumerate(run)},
                "arr_times": {stops[k + 1]: conns[i][1] for k, i in enumerate(run)},
                "dep_times": {stops[k]: [conns[i][0]] for k, i in enumerate(run)},
                "runs": [run],
                "shape": day.trip_shape.get(trip),
                "q": 1.0,
            })
    # Ten sam fragment tej samej linii z kilku kursów rysuje się raz, z
    # godzinami pierwszego (_keep_piece) - niech to będzie kurs podróży
    # najszybszej.
    segs.sort(key=lambda seg: seg["suffix"][0])
    ranges = {id(seg): (0, len(seg["stops"])) for seg in segs}
    return segs, ranges, hops


def _value_map(day, source_stops, target_set, dep_sec, best_arr, frame_km2,
               density, more, seated=None):
    """Mapa z wartości podróży przy docelowej gęstości: najszersza tolerancja
    (w minutach, patrz _value_entries), przy której narysowana sieć nie jest
    gęstsza niż `density`, a przy "pokaż więcej" - kolejne, każda coś
    dokładająca (ta sama gwarancja, co w zwykłej mapie, zgłoszenie #141).

    Zwraca {kept, ranges, deadline, map_from, at_ceiling, why_of} - kept
    puste, gdy nie znalazło się nic (plan_flow ma na to tryb awaryjny)."""
    searches = {}
    along = {}      # kurs -> {połączenie: (metry przed nim, metry po nim)}

    def ride_metres(ride):
        board, alight = ride
        trip = day.conns[board][4]
        if trip not in along:
            total, marks = 0.0, {}
            for i in gtfs.trip_conns(day, trip):
                before = total
                total += _stop_metres(day, day.conns[i][2], day.conns[i][3])
                marks[i] = (before, total)
            along[trip] = marks
        return along[trip][alight][1] - along[trip][board][0]

    def pick(values):
        # Z bliźniaków tymi samymi numerami zostaje najkrótsza jazda: dłuższa
        # to ta sama podróż z nadłożeniem drogi - trójką za przystanek
        # przesiadki i z powrotem tym samym kursem dziesiątki, co wsiadając
        # od razu. Ale najpierw odpada wariant, który idzie tam, gdzie
        # mógłby dalej jechać tym samym kursem (punkty 2 i 14) - 10 minut
        # marszu z Armii Krajowej na Krakowską (Centrum Handlowe) do piątki,
        # która staje 3 minuty od autobusu. Objazd zmienia dwa przejazdy
        # naraz, więc tak nie wraca.
        values = [value for value in values
                  if not any(_rides_instead_of_walking(day, other, value)
                             for other in values)]
        return min(values, key=lambda value: value[0][0])

    def search(window_sec):
        if window_sec not in searches:
            searches[window_sec] = searched(window_sec)
        return searches[window_sec]

    def searched(window_sec):
        """Klasy podróży jednego okna - z pamięci dnia, jeśli ta sama relacja
        o tej samej godzinie była już tak szukana (patrz
        gtfs._SEARCHES_CACHE_MAX). Klucz to wszystko, od czego wynik zależy;
        start i cel w tej samej kolejności, bo kolejność rozstrzyga remisy."""
        key = (tuple(source_stops), tuple(target_set), dep_sec, best_arr,
               window_sec, seated)
        classes = day._searches.get(key)
        if classes is None:
            classes = _value_journeys(day, source_stops, target_set, dep_sec,
                                      best_arr, window_sec, seated)
            _value_entries(classes, best_arr)
            memo = {}
            for journey in classes:
                variants, journey["cut"] = {}, []
                # Poza tym oknem podróż nie wejdzie na mapę, a ukryć niczego
                # też nie może: to, co ukrywa, nie jest gorsze w minutach, więc
                # jego tolerancja nie jest większa. Warianty liczy się tylko
                # tu - na Leśnicy to 30 s z 52.
                if journey["entry"] > window_sec // 60:
                    journey["variants"] = {}
                    continue
                for label in journey["labels"]:
                    for nums, value in _label_variants(day, label, memo,
                                                       ride_metres, pick).items():
                        variants.setdefault(nums, []).append(value)
                    journey["cut"] += memo["cut", id(label)]
                journey["variants"] = {nums: pick(values)
                                       for nums, values in variants.items()}
            _same_numbers_best(classes)
            # Drogi do etykiet są już złożone w warianty - dalej nikt ich nie
            # czyta, a w pamięci zajmowałyby najwięcej.
            for journey in classes:
                del journey["labels"]
            if len(day._searches) >= gtfs._SEARCHES_CACHE_MAX:
                day._searches.clear()
            day._searches[key] = classes
        return classes

    def tolerances():
        below = -1
        for window_sec in VALUE_WINDOWS_SEC:
            found = search(window_sec)
            yield from sorted({j["entry"] for j in found
                               if below < j["entry"] <= window_sec // 60})
            below = window_sec // 60

    listed, pending = [], tolerances()

    def tolerance(k):
        while len(listed) <= k:
            nxt = next(pending, None)
            if nxt is None:
                return None
            listed.append(nxt)
        return listed[k]

    maps = {}

    def drawn(eps):
        if eps not in maps:
            window = next(w for w in VALUE_WINDOWS_SEC if eps <= w // 60)
            chosen = [j for j in search(window) if j["entry"] <= eps]
            segs, ranges, hops = _value_segments(day, chosen)
            maps[eps] = {
                "window": window,
                "chosen": chosen, "kept": segs, "ranges": ranges, "hops": hops,
                "rides": set().union(*(j["rides_set"] for j in chosen)),
                "density": _map_density(_corridor_km(day, segs, ranges), frame_km2),
            }
        return maps[eps]

    if tolerance(0) is None:
        return {"kept": [], "ranges": {}, "deadline": best_arr,
                "map_from": dep_sec, "at_ceiling": True, "why_of": None}

    def widest(k, target):
        while tolerance(k + 1) is not None and \
                drawn(tolerance(k + 1))["density"] <= target:
            k += 1
        return k

    k = widest(0, density)
    for level in range(1, more + 1):
        if tolerance(k + 1) is None:
            break
        before = len(drawn(tolerance(k))["rides"])
        k = widest(k + 1, density * (1 + level))
        while len(drawn(tolerance(k))["rides"]) <= before \
                and tolerance(k + 1) is not None:
            k += 1

    network = drawn(tolerance(k))
    chosen = network["chosen"]
    deadline = max(j["arr"] for j in chosen)

    def lines(rides):
        return " → ".join(_line_parts(day.trip_info[day.conns[board][4]][0])[0]
                          for board, _ in sorted(rides))

    def brief(journey):
        rides = (journey["kept"][0][1] if journey["kept"]
                 else next(iter(journey["variants"].values()))[1])
        return {"lines": lines(rides), "dep": journey["dep"],
                "arr": journey["arr"]}

    def why_of(label, stops_seq):
        """Podgląd "dlaczego ten kawałek" (Debug): podróże jadące tędy, od
        tej, która weszła na mapę najwcześniej, każda z powodem w minutach,
        i warianty, które ukryły numerki."""
        mine = set()
        for a, b in zip(stops_seq, stops_seq[1:]):
            mine |= network["hops"].get((label, a, b), set())
        journeys = sorted((chosen[n] for n in mine),
                          key=lambda j: (j["entry"], j["arr"]))
        shown = journeys[:WHY_SHOWN]
        return {
            "tolerance": tolerance(k),
            "fastest": best_arr,
            "journeys": [{
                "lines": [lines(rides) for _, rides in j["kept"]],
                "dep": j["dep"],
                "arr": j["arr"],
                "transfers": j["rides"] - 1,
                "entry": j["entry"],
                "late": j["late"],
                "early": j["early"],
                "early_by": j["early_by"] and brief(j["early_by"]),
            } for j in shown],
            "others": len(journeys) - len(shown),
            "hidden": [{"lines": lines(rides), "dep": j["dep"], "arr": j["arr"],
                        "added": sorted(_line_parts(line)[0] for line in nums - other),
                        "by": brief(rival) | {
                            "lines": lines(rival["variants"][other][1])}}
                       for j in shown
                       for nums, rides, (other, rival) in j["hidden"]][:WHY_SHOWN],
        }

    return {
        "kept": network["kept"],
        "ranges": network["ranges"],
        "deadline": deadline,
        "map_from": min(j["dep"] for j in chosen),
        # Czy jest jeszcze co dołożyć, wiadomo tylko z tego, co już
        # przeszukano - pytanie o następną tolerancję wymusiłoby szersze
        # szukanie, którego ta mapa nie potrzebowała. Nieprzeszukane znaczy
        # "jest", a kolejne „więcej" sięgnie po nie samo.
        "at_ceiling": (len(listed) <= k + 1
                       and VALUE_WINDOWS_SEC[-1] in searches
                       and tolerance(k + 1) is None),
        "why_of": why_of,
    }


def _latest_departure(day, source_stops, target_stops, dep_sec, best_arr):
    """Najpóźniejsza godzina wyjazdu, z której WCIĄŻ osiąga się `best_arr`.

    Wyjechanie później nie może dać wcześniejszego przyjazdu, więc warunek
    "stąd wciąż zdążę na best_arr" jest prawdziwy na początku przedziału
    i fałszywy dalej - wystarczy połowienie. Szukamy w pełnych minutach:
    rozkład i tak jest w minutach, a każde przybliżenie to jeden skan.

    Godzina "wyjeżdżasz o" (patrz plan_flow)."""
    lo, hi = dep_sec, best_arr
    while hi - lo > 60:
        mid = lo + (hi - lo) // 2
        _, arr, _ = _scan(day, source_stops, target_stops, mid)
        if arr == best_arr:
            lo = mid
        else:
            hi = mid
    return lo


def _no_connection(start_name, end_name, dep_sec, when):
    last = gtfs.last_service_date()
    if last and when.date() > last:
        return {
            "error": f"Rozkładu na {when:%d.%m} jeszcze nie opublikowano — "
                     f"obecny sięga do {last:%d.%m}."
        }
    return {
        "error": f"Nie znaleziono połączenia {start_name} → {end_name} "
                 f"po {_fmt_time(dep_sec)} tego dnia."
    }


def _summarize_journey(legs, rides, arrival, dep_sec, start_sec=None):
    """Nagłówek karty: odjazd, przyjazd, czas w drodze, czekanie, przesiadki.

    `start_sec` nadpisuje moment wyruszenia. Domyślnie jest nim odjazd
    pierwszego pojazdu, bo dopóki trasa zaczyna się od wsiadania, wcześniej
    po prostu się czeka. Trasa, która zaczyna się DOJŚCIEM - pieszo do
    przystanku albo do stacji roweru (patrz _bike_journeys) - wyrusza jednak
    wcześniej, niż „odjeżdża": inaczej karta obiecuje godzinę, o której
    pasażer stoi jeszcze kilkaset metrów dalej.

    `arrival_sec`/`departure_sec` to te same dwie godziny w sekundach doby
    rozkładowej - potrzebne do ustawienia propozycji w jednej kolejności
    niezależnie od tego, który algorytm je złożył (patrz _merge_journeys).
    """
    first_dep = rides[0]["dep_sec"] if start_sec is None else start_sec
    return {
        "departure": _fmt_time(first_dep),
        "arrival": _fmt_time(arrival),
        "departure_sec": first_dep,
        "arrival_sec": arrival,
        "duration_min": round((arrival - first_dep) / 60),
        "wait_min": round((first_dep - dep_sec) / 60),
        "transfers": len(rides) - 1,
        "legs": legs,
    }


def plan_flow(start_query, end_query, when=None,
              start_point=None, end_point=None, density=None, more=None,
              car_count=None, journey_limit=None, transfer_gain_sec=None,
              use_bikes=False, bike_count=None, car_vans=False,
              bike_electric=True, bike_regular=True,
              in_vehicle=None, walk_pace=None, bike_kmh=None,
              bike_overhead_sec=None, car_kmh=None, car_overhead_sec=None,
              with_journeys=True):
    """Mapa przepływów ("mrówki"): wszystkie użyteczne przejazdy start -> cel.

    Na mapie są podróże, które są w czymś najlepsze - o której w celu,
    o której trzeba wyjść i iloma pojazdami (mapa z wartości podróży,
    zgłoszenie #150, patrz _value_map). Narysowane kawałki złożone są
    z samych podróży od startu do celu, więc nic nie wisi w powietrzu.

    Lista propozycji tras ("journeys") to NIE osobny algorytm - to ścieżki
    przeczytane wprost z tego samego, już narysowanego grafu segmentów
    (_extract_transfer_graph + _enumerate_journeys), więc lista nigdy nie
    pokaże przesiadki, której nie ma na mapie, i przesuwa się razem z progiem
    mapy.

    density to docelowa gęstość narysowanej sieci (suwak pod zębatką, patrz
    DEFAULT_MAP_DENSITY), a more - ile razy kliknięto "pokaż więcej" (0-3).
    Razem wyznaczają, o ile minut dłuższe podróże mapa jeszcze wybacza: tyle,
    przy ilu sieć nie jest gęstsza niż density × (1 + more) (patrz _value_map).
    car_count to ile aut car-sharingu pokazać przy mapie (suwak pod zębatką,
    patrz DEFAULT_MAP_CARS i traficar.map_skyband); "pokaż więcej" mnoży ją
    tak samo jak gęstość. bike_count - to samo dla przejazdów rowerem (osobny
    suwak, patrz DEFAULT_MAP_BIKES i bikes.map_places). car_vans - czy
    pokazać też dostawczaki, wybierane osobno od osobówek.
    bike_electric/bike_regular - na jaki rodzaj roweru pasażer chce wsiąść
    (dwa przyciski w pasku warstw, patrz bikes.map_places). To odsiew MIEJSC, nie zmiana wyceny przejazdu: rower
    ma jedną prędkość niezależnie od rodzaju, więc odhaczenie jednego z nich
    nie przesuwa na mapie żadnej godziny.
    journey_limit to ile propozycji tras SZUKAĆ (suwak w UI, patrz
    DEFAULT_JOURNEY_LIMIT/MIN_JOURNEY_LIMIT/MAX_JOURNEY_LIMIT) - wyższa
    wartość nie zmyśla nieistniejących wariantów, tylko każe
    _enumerate_journeys przeszukać graf głębiej (patrz CANDIDATES_PER_JOURNEY/
    VISITS_PER_JOURNEY); gdy w grafie jest ich mniej, dostaje się tyle, ile
    faktycznie da się złożyć.

    use_bikes dokłada do listy propozycje z rowerem miejskim (patrz sekcja
    ROWER MIEJSKI niżej i bikes.py) - ale tylko przy pytaniu o DZIŚ, bo stan
    stojaków jest żywy, a nie rozkładowy. Wyłączone zmienia dokładnie zero
    rzeczy w reszcie odpowiedzi - rower niczego tu nie przestawia, tylko
    dopisuje.

    in_vehicle to start Z POKŁADU pojazdu (w API: parametry `onboard_*`,
    patrz onboard.py): {"num", "mode", "headsign", "stop"} - czym pasażer
    jedzie i który przystanek ma przed sobą. Zamiast `start_query`/
    `start_point` startem jest wtedy ten jeden słupek, a godziną - sekunda,
    o której pojazd z niego rusza. Siedzenie dalej w tym samym pojeździe jest
    dla skanu wsiadaniem w ten kurs na tym przystanku, z zerowym czekaniem,
    a każdy inny pojazd - przesiadką (patrz _board_buffer). Poza tym zmienia
    się tylko OPIS wyniku - każda propozycja dostaje `onboard` z przystankiem,
    na którym trzeba wysiąść (patrz onboard.mark_journeys).

    walk_pace, bike_kmh, bike_overhead_sec - założenia czasowe pytającego
    (sekcja pod zębatką, zgłoszenie #151): tempo marszu (klucz
    gtfs.WALK_PACES), prędkość roweru w linii prostej i stały narzut
    przejazdu rowerem. Brak to dzisiejsze wartości; sufity pilnowane tutaj.
    car_kmh i car_overhead_sec - to samo dla jazdy Traficarem (zgłoszenie
    #150): prędkość w linii prostej i stały narzut na ruszenie i parkowanie,
    z których mapa szacuje, o której auto dowiezie do celu.

    with_journeys False - propozycji tras nie ma wcale (opcja pod zębatką):
    ani z mapy, ani z rowerem, ani z Traficarem, a `journeys` jest puste.
    Reszta odpowiedzi zostaje co do bajtu ta sama - lista z niczego na mapie
    nie korzysta (zgłoszenie #171). Czasu to prawie nie oszczędza (setne
    sekundy), za to odpowiedź jest o 40-50% mniejsza.
    """
    when = when or datetime.now()
    journey_limit = (
        DEFAULT_JOURNEY_LIMIT if journey_limit is None
        else int(max(MIN_JOURNEY_LIMIT, min(MAX_JOURNEY_LIMIT, journey_limit)))
    )

    walk_pace = walk_pace if walk_pace in gtfs.WALK_PACES else gtfs.DEFAULT_WALK_PACE
    bike_mps = (bikes.MAP_RIDE_MPS if bike_kmh is None
                else max(MIN_BIKE_KMH, min(MAX_BIKE_KMH, float(bike_kmh))) / 3.6)
    bike_overhead = (bikes.MAP_OVERHEAD_SEC if bike_overhead_sec is None
                     else int(max(0, min(MAX_BIKE_OVERHEAD_SEC, bike_overhead_sec))))
    car_mps = (traficar.MAP_DRIVE_MPS if car_kmh is None
               else max(MIN_CAR_KMH, min(MAX_CAR_KMH, float(car_kmh))) / 3.6)
    car_overhead = (traficar.MAP_OVERHEAD_SEC if car_overhead_sec is None
                    else int(max(0, min(MAX_CAR_OVERHEAD_SEC, car_overhead_sec))))

    try:
        day = gtfs.with_pace(gtfs.load_day(when.date()), walk_pace)
    except FileNotFoundError as e:
        return {"error": str(e)}

    asked_sec = when.hour * 3600 + when.minute * 60 + when.second

    # Start z pokładu pojazdu: najpierw trzeba wiedzieć, KTÓRY to kurs - bo
    # dopiero on mówi, gdzie i kiedy zaczyna się podróż (patrz onboard.py).
    ride = None
    if in_vehicle:
        ride = onboard.find_ride(day, in_vehicle.get("num"),
                                 in_vehicle.get("mode"), in_vehicle.get("stop"),
                                 asked_sec, in_vehicle.get("headsign"))
        if "error" in ride:
            return ride

    ends = _resolve_endpoints(day, start_query, end_query, start_point, end_point,
                              ride)
    if "error" in ends:
        return ends
    day = ends["day"]          # z punktem z mapy dołożonym jako słupek
    start_name, source_stops = ends["start"], ends["source_stops"]
    end_name, target_stops = ends["end"], ends["target_stops"]

    # Z pokładu godziną wyjazdu jest odjazd pojazdu z najbliższego przystanku,
    # a nie godzina z formularza: przed tą sekundą nie da się zrobić NICZEGO -
    # ani zostać w pojeździe, ani z niego wysiąść.
    dep_sec = ride["sec"] if ride else asked_sec
    # Z pokładu pasażer na tym przystanku nie stoi, tylko do niego przyjeżdża
    # swoim kursem - dalej nim jedzie bez zapasu, a na każdy inny pojazd
    # się przesiada (patrz _board_buffer).
    seated = ride["trip"] if ride else None
    gain_sec = TRANSFER_GAIN_SEC if transfer_gain_sec is None else int(transfer_gain_sec)

    # Najszybsza trasa wyznacza skalę ("większość mrówek") i jest zapasowym
    # planem, gdyby mapa (patrz niżej) nie wybrała niczego.
    best_stop, best_arr, best_journey = _scan(day, source_stops, target_stops, dep_sec,
                                              seated=seated)

    # Nic już dziś nie jedzie - szukamy w kolejnych dobach (punkt 13:
    # "nie znaleziono połączenia" nie jest odpowiedzią na pytanie "jak tam
    # dojadę"). Doba rozkładowa zaczyna się o północy, więc pytamy od zera;
    # przystanki rozwiązujemy w niej od nowa, bo to dane tamtego dnia.
    # Z pokładu pojazdu kolejnych dób nie przeszukujemy: pytanie brzmi "co
    # zrobić z TYM przejazdem", a on kończy się dzisiaj. Odpowiedź "pojedź
    # jutro" byłaby odpowiedzią na inne pytanie - i to z przystanku, na którym
    # pasażer za chwilę stanie tylko przejazdem.
    day_offset = 0
    asked_sec = dep_sec        # godzina, od której liczy się czekanie: z pytania,
                               # a z pokładu - odjazd pojazdu spod najbliższego
                               # przystanku (czekaniem nie jest jazda w nim)
    while best_stop is None and ride is None and day_offset < SEARCH_AHEAD_DAYS:
        day_offset += 1
        try:
            later = gtfs.with_pace(
                gtfs.load_day(when.date() + timedelta(days=day_offset)), walk_pace)
        except FileNotFoundError:
            break
        ends = _resolve_endpoints(later, start_query, end_query,
                                  start_point, end_point)
        if "error" in ends:
            break
        day = ends["day"]
        start_name, source_stops = ends["start"], ends["source_stops"]
        end_name, target_stops = ends["end"], ends["target_stops"]
        dep_sec = 0
        best_stop, best_arr, best_journey = _scan(
            day, source_stops, target_stops, dep_sec)

    if best_stop is None:
        return _no_connection(start_name, end_name, asked_sec, when)

    # Godzina, którą odpowiedź RAPORTUJE - z pytania. Mapa przesuwa niżej
    # `dep_sec`, od którego się RYSUJE, ale "za ile tam będziesz" i punkt
    # zerowy dymka (#143) muszą dalej liczyć od tego, o co pytano.
    report_dep_sec = dep_sec

    # Godzina "wyjeżdżasz o" w pasku nad mapą to NAJPÓŹNIEJSZY wyjazd, który
    # wciąż daje najszybszy przyjazd (zgłoszenie #141). Najszybsza trasa
    # z krążeniem na początku kazałaby wyjść wcześniej, niż trzeba, żeby
    # i tak wsiąść w ten sam pojazd. Z pokładu pojazdu tego nie liczymy -
    # tam godziny wyjazdu się nie wybiera, bo pasażer już jedzie.
    if ride is None:
        latest_dep = _latest_departure(day, source_stops, target_stops,
                                       dep_sec, best_arr)
        best_stop, best_arr, best_journey = _scan(day, source_stops,
                                                  target_stops, latest_dep)

    # Kiedy najszybsza trasa naprawdę RUSZA - czekanie ma być widoczne, nie
    # schowane (punkt 13). Progu mapy to nie dotyczy: liczy się go od
    # najszybszego PRZYJAZDU, więc godzina czekania niczego w nim nie rozdyma.
    best_dep = _journey_start(day, best_journey, best_stop)
    if best_dep is None:
        best_dep = dep_sec

    # Współrzędne celu - potrzebne wyłącznie propozycjom z Traficarem (dokąd
    # ma dojechać auto); liczone raz, bo `target_stops` bywa całym placem.
    end_point_ll = _endpoint_point(day, target_stops, end_point)

    target_set = target_stops

    # Słupki, w których trasa może się ZACZĄĆ: sam start plus to, dokąd stąd
    # dojdzie się pieszo (patrz _origin_walk) - dla listy propozycji
    # (_extract_transfer_graph).
    start_reach = _origin_walk(day, source_stops)
    anchor_stops = set(source_stops) | set(start_reach)

    gtfs.geo_generation()      # jeden stat na zapytanie; czyści cache po podmianie bazy
    geo_db = gtfs.open_db()    # jedno połączenie na WSZYSTKIE wycinki geometrii zapytania
    try:
        # Najszybsza trasa i tak jest już policzona wyżej (_scan wyznacza nią
        # skalę całej mapy) - odtwarzamy ją raz, tutaj, żeby front mógł podać
        # "najszybciej tyle a tyle" i pokazać, KTÓRĄ trasą to jest, bez
        # sięgania po listę propozycji (i bez drugiego szukania). Wyznacza też
        # kadr relacji, a z nim gęstość, do której dobiera się próg.
        best_legs = _reconstruct(day, best_journey, best_stop, geo_db)
        fastest = _fastest_summary(best_legs, best_arr, dep_sec)

        # Kadr relacji - z niego gęstość mapy (punkt 2).
        frame_km2 = _frame_km2(day, best_legs, [*source_stops, *target_stops])
        density = (DEFAULT_MAP_DENSITY if density is None
                   else max(MIN_MAP_DENSITY, min(MAX_MAP_DENSITY, float(density))))
        more = 0 if more is None else int(max(0, min(MAX_MAP_MORE, more)))
        car_count = (DEFAULT_MAP_CARS if car_count is None
                     else int(max(MIN_MAP_CARS, min(MAX_MAP_CARS, car_count))))
        bike_count = (DEFAULT_MAP_BIKES if bike_count is None
                      else int(max(MIN_MAP_BIKES, min(MAX_MAP_BIKES, bike_count))))
        # Mapa z wartości podróży (zgłoszenie #150, patrz _value_map). Liczy
        # się od godziny z pytania, bo późniejsze wyjście jest już jedną z jej
        # wartości - a z pokładu od odjazdu pojazdu (patrz _value_journeys).
        dep_sec = report_dep_sec
        chosen_map = _value_map(day, source_stops, target_set, dep_sec,
                                best_arr, frame_km2, density, more, seated)
        deadline, at_ceiling = chosen_map["deadline"], chosen_map["at_ceiling"]
        kept, ranges = chosen_map["kept"], chosen_map["ranges"]
        why_of = chosen_map["why_of"]
        earliest, _, _ = _forward(day, source_stops, dep_sec, deadline)
        profile = _target_profile(day, target_set, dep_sec, deadline)
        dep_sec = chosen_map["map_from"]
        degraded = False
        if kept:
            seg_list, nodes = _finalize_segments(
                day, kept, ranges, geo_db, earliest, profile[2], deadline,
                source_stops, why_of)
            journeys = []
            if with_journeys:
                graph = _extract_transfer_graph(day, kept, ranges, anchor_stops,
                                                target_set, dep_sec, start_reach)
                journeys = _enumerate_journeys(day, graph, dep_sec, geo_db,
                                               limit=journey_limit,
                                               gain_sec=gain_sec, ride=ride)
        else:
            # Zabezpieczenie: _scan już udowodnił, że połączenie istnieje
            # (best_stop nie jest None), więc jeśli mapa i tak nie wybrała
            # niczego (patrz _value_map), narysuj i wylistuj przynajmniej
            # samą najszybszą trasę zamiast pustej odpowiedzi.
            #
            # To NIE jest zwykła mapa i odpowiedź mówi o tym wprost
            # (degraded), bo łamie kontrakt: rysuje jedną trasę zamiast
            # całego wachlarza (punkt 1), a jasności ma wpisane na sztywno,
            # nie policzone (punkty 2 i 9). Bez tego znacznika rzadka mapa
            # wygląda tak samo jak "tędy naprawdę nic nie jedzie".
            # Najszybsza trasa i - jeśli jest co zdjąć - ta sama bez
            # nieopłacalnej przesiadki. Pokazujemy OBIE; pierwsza jest tą
            # proponowaną jako najlepsza przy obecnym progu.
            warianty = _variants(day, best_legs, gain_sec, geo_db)
            degraded = True
            journeys = []
            seg_list = []
            nodes = []
            narysowane = set()
            for rank, wariant in enumerate(warianty):
                rides = [leg for leg in wariant if leg["kind"] == "ride"]
                if not rides:
                    continue
                # Przyjazd bierzemy z samej trasy; best_arr zostaje
                # najwcześniejszym możliwym i dalej wyznacza okno mapy.
                arrival = _arrival_of(wariant) or best_arr
                if with_journeys:
                    journeys.append(_summarize_journey(
                        wariant, rides, arrival, dep_sec))
                # Jaśniej rysujemy wariant proponowany - tak jak wszędzie
                # indziej na tej mapie jasność znaczy "lepsza opcja".
                waga = 1.0 if rank == 0 else 0.6
                for leg in rides:
                    num, mode = _line_parts(leg["line"])
                    klucz = (num, mode, tuple(map(tuple, leg["path"])))
                    if klucz in narysowane:
                        continue
                    narysowane.add(klucz)
                    item = {"path": leg["path"], "num": num, "kind": mode, "w": waga}
                    # Tryb awaryjny też ma podawać godziny - inaczej mapa raz
                    # je ma, a raz nie, zależnie od tego, czy mapa coś
                    # wybrała. Cel osiąga się tu z definicji tą trasą, więc
                    # przyjazd jest odczytany, nie zgadnięty.
                    if leg.get("_stops_t"):
                        item["stops_t"] = leg["_stops_t"]
                        item["arrive"] = arrival
                    seg_list.append(item)
            seg_list.sort(key=lambda seg: seg["w"])   # blade pierwsze, jaskrawe na wierzchu
            for wariant in warianty:
                _drop_private(wariant)

        if ride:
            onboard.count_exit(journeys, ride)

        # Rower dokładamy PO obu gałęziach, bo obie zostawiają tę samą rzecz:
        # listę propozycji. Także po trybie awaryjnym - to właśnie tam, gdzie
        # z rozkładu nie składa się prawie nic, skrót rowerem bywa jedyną
        # sensowną odpowiedzią.
        #
        # Warunek na dobę: stan stojaków jest sprzed minuty i tyle jest wart -
        # mówi, ile rowerów stoi TERAZ, a nie ile będzie stało we wtorek. Na
        # pytanie o inny dzień (także ten, na który wyszukiwarka sama zeszła,
        # nie znalazłszy nic dzisiaj - patrz day_offset wyżej) roweru więc nie
        # proponujemy: propozycja oparta na dzisiejszych stojakach byłaby
        # zgadywaniem podanym jako fakt. `live` w odpowiedzi mówi to wprost,
        # żeby zero propozycji z tego powodu nie wyglądało jak zero z powodu
        # milczącego kanału operatora.
        bike_shown, bike_stations = 0, 0
        bikes_live = (use_bikes and with_journeys and day_offset == 0
                      and when.date() == date.today())
        if bikes_live:
            # Rower spod startu liczy się od godziny z pytania, nie od
            # początku mapy: czekanie na późniejszy wyjazd opłaca się tylko
            # komunikacji, a rowerem można ruszyć od razu.
            found, bike_stations = _bike_journeys(
                day, source_stops, target_stops, report_dep_sec, deadline, earliest,
                profile, geo_db, start_point, end_point, start_name, end_name,
                (bike_mps, bike_overhead))
            if ride:
                onboard.count_exit(found, ride)
            journeys, bike_shown = _merge_journeys(journeys, found, gain_sec)

        # Dodatkowe propozycje kończące się Traficarem (patrz
        # _traficar_journeys). Dokładane po wszystkim i osobno, bo powstają
        # poza mapą przepływów - w trybie awaryjnym po prostu na końcu listy,
        # żeby nie przestawić dwóch wariantów, których kolejność jest tam
        # świadoma (pierwszy = proponowany).
        with_car = (_traficar_journeys(day, best_journey, dep_sec, deadline,
                                       end_point_ll, end_name, geo_db)
                    if with_journeys else [])
        if ride:
            onboard.count_exit(with_car, ride)
        if with_car:
            journeys = (journeys + with_car) if degraded else sorted(
                journeys + with_car, key=lambda j: _journey_key(j, gain_sec))

        # Auta car-sharingu stojące przy narysowanej mapie (patrz
        # traficar.map_cars). Nie są kursem i nie mają na mapie linii - są
        # miejscem, do którego mapa dowozi, z godziną dotarcia, odległością
        # celu w linii prostej i szacowanym przyjazdem autem (punkt 15
        # kontraktu; szacunek - zgłoszenie #150).
        #
        # Zasięg to to, co mapa RYSUJE, plus sam start: do auta stojącego pod
        # nosem idzie się od razu, bez wsiadania w cokolwiek. Marsz liczy się
        # od miejsca, w którym się JEST, więc dalej jest to jedno przejście
        # (punkt 14), a nie łańcuch "dojdź na przystanek, potem do auta".
        #
        # Warunek na dobę ten sam, co przy rowerze: auta stoją tam, gdzie
        # stoją TERAZ. Przy pytaniu o inny dzień (także ten, na który
        # wyszukiwarka sama zeszła) nie pokazujemy ich wcale - pokazanie
        # byłoby zgadywaniem podanym jako fakt.
        #
        # W trybie awaryjnym (kept puste) aut nie ma wcale: godziny ze skanu
        # znają pół miasta, a tu ma być to, co widać na ekranie - i akurat
        # tam mapa nie jest wachlarzem, tylko jedną trasą (patrz gałąź else
        # wyżej). Auto przy przystanku, którego nikt nie narysował, mówiłoby
        # o mapie coś, czego na niej nie ma.
        cars, bike_places = [], []
        cars_dest_in_zone = None
        # Przy "dowolnej stacji w mieście" po którejkolwiek stronie aut
        # i rowerów nie ma wcale - decyzja użytkownika: to podróż koleją między
        # miastami, a auto czy rower przy którejś ze stacji nie jest na nią
        # odpowiedzią ("WROCŁAW -" -> "WARSZAWA -" pokazywało auta przy
        # Nadodrzu i Kuźnikach).
        if kept and not (gtfs.is_city_group(day, source_stops)
                         or gtfs.is_city_group(day, target_set)):
            # Na starcie jest się od godziny z pytania, nie od początku mapy -
            # do auta czy roweru pod nosem idzie się od razu (tak samo jak
            # przy propozycjach z rowerem wyżej).
            reach = dict.fromkeys(source_stops, report_dep_sec)
            for stop, at in _drawn_reach(kept, ranges).items():
                if at < reach.get(stop, INF):
                    reach[stop] = at
            if day_offset == 0 and when.date() == date.today():
                # Ile z nich: suwak razy to samo "pokaż więcej", co przy
                # liniach - a auta, których nic nie bije, zostają i tak.
                cars = traficar.map_choice(
                    traficar.map_cars(day, reach, end_point_ll, car_mps,
                                      car_overhead),
                    car_count * (1 + more), car_vans,
                    min_level=more)
                # Czy przy celu da się auto zostawić (zgłoszenie #157). Auta
                # zostają na mapie tak czy inaczej - to ostrzeżenie w dymku,
                # nie odsiew: wziąć auto i oddać je na granicy strefy dalej
                # bywa sensownym wyborem, tylko trzeba o tym wiedzieć.
                if cars:
                    cars_dest_in_zone = traficar.can_end_at(*end_point_ll)

            # Rower miejski na mapie (punkt 16, patrz bikes.map_places).
            # Inaczej niż auto: z roweru się JEDZIE, więc przejazd ocenia się
            # w całej podróży - dojazd do roweru i dalsza droga po nim idą
            # tym, co mapa RYSUJE, rundami po liczbie pojazdów. Nie skanem
            # wstecz po całym dniu: ten zna pół miasta i uznałby za sensowny
            # przejazd na przystanek, z którego mapa nie rysuje ani jednego
            # odjazdu. Oba przebiegi są leniwe - bez kandydatów na rower nie
            # odpala się żaden.
            #
            # Kropki zostają także przy pytaniu o inny dzień - stacje stoją
            # tam zawsze, a zniknięcie ich z mapy mówiłoby nieprawdę. Nieznany
            # jest wtedy sam STAN stojaka i `bikes_live` mówi to wprost, żeby
            # front nie podał zgadywania jako liczby rowerów.
            runs = _drawn_runs(day, kept, ranges)
            bike_places = bikes.map_places(
                day,
                lambda: _drawn_arrivals(day, runs, source_stops, report_dep_sec),
                lambda: _drawn_onward(day, runs, target_set),
                target_set, bike_count * (1 + more),
                live=day_offset == 0 and when.date() == date.today(),
                min_level=more, electric=bike_electric, regular=bike_regular,
                ride_mps=bike_mps, overhead_sec=bike_overhead)
    finally:
        geo_db.close()

    # Wysiadka dopisuje się na samym końcu, po rowerze i po Traficarze: dotyczy
    # KAŻDEJ propozycji, bez względu na to, który algorytm ją złożył, a pytanie
    # jest zawsze to samo - gdzie opuścić pojazd, w którym się siedzi.
    if ride:
        onboard.mark_journeys(journeys, ride)

    return {
        "start": start_name,
        "end": end_name,
        "departure": _fmt_time(report_dep_sec),
        # Ta sama godzina w sekundach doby rozkładowej. Od niej - a nie od
        # tego, o której MAPA sądzi, że pasażer stanie na danej kropce - liczy
        # swoje godziny dymek przesiadki (zgłoszenie #143, patrz app.js
        # timetableAnchor).
        "departure_sec": report_dep_sec,
        "best_arrival": _fmt_time(best_arr),
        "deadline": _fmt_time(deadline),
        # Cały zakres czasowy mapy w sekundach, tą samą miarą co kawałki:
        # od najszybszego możliwego dojazdu do najpóźniejszego, jaki mapa
        # jeszcze rysuje (deadline). "Najszybciej X, pokazane do Y".
        "best_sec": best_arr - report_dep_sec,
        "limit_sec": deadline - report_dep_sec,
        # Horyzont mapy na osi doby. Odjazd późniejszy nie należy do ŻADNEGO
        # rysowanego wariantu - to warunek konieczny, liczony z best_arr
        # (skan CSA), więc nie zależy od szacowanych przyjazdów kawałków.
        "deadline_sec": deadline,
        # Z czego ta mapa wyszła (punkt 2): docelowa gęstość z suwaka i ile
        # razy kliknięto "pokaż więcej". `at_ceiling` - nie ma już podróży do
        # wybaczenia, więc kolejne kliknięcie nie miałoby czego dołożyć.
        "density": density,
        "more": more,
        "at_ceiling": at_ceiling,
        "fastest": fastest,
        # Kiedy ta trasa RUSZA i za ile dni - czekanie ma być widoczne, nie
        # schowane (punkt 13). `day_offset` 0 to dzień z pytania.
        "starts": _fmt_time(best_dep),
        "starts_sec": best_dep,
        # Sama jazda, od odjazdu pierwszego pojazdu do celu - bez czekania,
        # które siedzi w `best_sec`. Pasek dopisuje ją tylko na życzenie.
        "ride_sec": best_arr - best_dep,
        # Od kiedy mapa się RYSUJE: godzina z pytania, a przy odsiewie
        # krążenia - najpóźniejszy wyjazd. Lewy kraniec zakresu w pasku.
        "map_from": _fmt_time(dep_sec),
        # Czekanie liczone od PYTANIA, przez granicę doby: po zejściu na
        # kolejny dzień `dep_sec` jest już zerem tamtej doby, więc sama
        # różnica pokazywałaby kilka minut zamiast prawie doby.
        "waits_sec": max(0, day_offset * 24 * 3600 + best_dep - asked_sec),
        "day_offset": day_offset,
        "segments": seg_list,
        # Węzły przesiadkowe: po jednym na miejsce, z liniami, w które MAPA
        # pozwala tu wsiąść (patrz _transfer_nodes).
        "nodes": nodes,
        # Wolne auta car-sharingu w zasięgu tej mapy (patrz traficar.map_cars).
        "cars": cars,
        # Czy cel leży w strefie oddawania aut; None, gdy aut nie ma albo
        # strefy nie znamy (patrz traficar.can_end_at).
        "cars_dest_in_zone": cars_dest_in_zone,
        # Rowery miejskie w zasięgu tej mapy, każdy z listą przejazdów, które
        # jeszcze mieszczą się w oknie (patrz bikes.map_places). Przejazdów
        # mapa NIE rysuje - front pokazuje je po najechaniu.
        "bike_places": bike_places,
        # Czy liczby rowerów pochodzą z tej chwili. Przy pytaniu o inny dzień
        # kropki stacji zostają, ale stan stojaka jest nieznany - front ma to
        # napisać, a nie pokazać wczorajszą liczbę jako dzisiejszą.
        "bike_places_live": day_offset == 0 and when.date() == date.today(),
        "journeys": journeys,
        # Rozpoznany kurs, w którym siedzi pasażer - tylko przy starcie
        # z pokładu (patrz onboard.py). Front pisze z tego nagłówek "jedziesz
        # linią X w stronę Y" i ma po czym poznać, że KAŻDA propozycja na
        # liście zaczyna się wysiadką, a nie wsiadaniem.
        **({"onboard": {k: ride[k] for k in
                        ("num", "mode", "line", "headsign", "stop_name", "at",
                         "not_yet", "late")}}
           if ride else {}),
        # Stan warstwy rowerowej - tylko gdy o nią pytano (i jest lista, do
        # której rower dokłada propozycje). Front ma po czym
        # odróżnić "policzone, rower nic tu nie daje" od "kanał operatora nie
        # odpowiedział" (patrz bikes.stations_quiet): w obu przypadkach lista
        # wygląda tak samo, a to zupełnie różne odpowiedzi.
        **({"bikes": {"journeys": bike_shown, "stations": bike_stations,
                      "live": bikes_live}}
           if use_bikes and with_journeys else {}),
        # True tylko w trybie awaryjnym (patrz gałąź else wyżej): mapa jest
        # wtedy jedną trasą z jasnościami wpisanymi na sztywno, a nie
        # wachlarzem opcji. Front ma po czym poznać, że pokazuje coś innego
        # niż zwykle - i nie brać rzadkiej mapy za "tędy nic nie jedzie".
        "degraded": degraded,
    }


def _fastest_summary(legs, arrival, dep_sec):
    """Najszybsza trasa w postaci minimalnej: ile trwa i którędy biegnie.

    To NIE jest pozycja listy propozycji tras - nie ma tu przystanków,
    godzin ani opisów etapów, tylko tyle, ile trzeba, żeby napisać "najszybciej
    X min" i po najechaniu na tę liczbę pokazać na mapie, która to trasa.

    Etapy PIESZE też, choć nie mają numeru linii. Do 2026-09-10 lecialy tu
    przez filtr "tylko ride" i pasek nad mapą pokazywał sam pojazd - a odkąd
    trasa potrafi zacząć się DOJŚCIEM (patrz _origin_walk), znaczyło to, że
    pasek obiecywał wsiadanie na przystanku, którego użytkownik nie wskazał
    i o którym nic nie mówił. Podświetlenie trasy na mapie miało z tego samego
    powodu dziurę: rysowały się same przejazdy, więc trasa zaczynała się
    "w powietrzu", kawałek od zaznaczonego startu.
    """
    return {
        "sec": arrival - dep_sec,
        "arrival": _fmt_time(arrival),
        "legs": [
            {
                "num": leg["num"],
                "kind": leg["mode"],
                "sec": leg["minutes"] * 60,
                "path": leg["path"],
            }
            if leg["kind"] == "ride" else
            {
                "kind": "walk",
                "minutes": leg["minutes"],
                "sec": leg["minutes"] * 60,
                "same_place": leg["same_place"],
                "to": leg["to"],
                "path": leg["path"],
            }
            for leg in legs
        ],
    }


def _sibling_places(day, stop):
    """Ten sam przystanek plus wszystko, do czego stąd się dojdzie pieszo
    (patrz gtfs.DayData.siblings) - dziś także słupki o innej nazwie i stacje
    kolejowe, nie tylko "bracia" z jednego miejsca, od których wzięła się
    nazwa. Jedyne miejsce, które rozwija "przystanek -> skąd jeszcze mogę
    tu wsiąść". Dla samych identyfikatorów; gdy potrzebny jest też koszt
    dojścia, patrz _reach_from."""
    return (stop, *day.siblings.get(stop, ()))


def _reach_from(day, stop):
    """To samo co _sibling_places, ale z BUFOREM, jaki kosztuje wsiadanie
    w każdym z tych punktów: na własnym słupku to bufor przesiadki, u sąsiada
    - czas dojścia, który krawędź piesza niesie już ze sobą (patrz
    gtfs.DayData.siblings; bufora przesiadki nie dokładamy, bo podłoga
    gtfs.WALK_MIN_SEC jest od niego większa)."""
    yield stop, TRANSFER_SEC
    yield from day.siblings.get(stop, {}).items()


def _board_index(day, segs):
    """Przystanek (+ siblingi) -> segmenty, w które da się tam wskoczyć (mają
    tam zapisany odjazd)."""
    index = {}
    for seg in segs:
        for stop in seg["dep_times"]:
            for anchor in _sibling_places(day, stop):
                index.setdefault(anchor, []).append(seg)
    return index


def _target_profile(day, target_set, dep_sec, deadline):
    """Dla KAŻDEGO przystanku: o której najwcześniej jest się w celu, będąc tu
    o godzinie t. Profilowy CSA - jeden skan wstecz po tych samych połączeniach,
    które i tak są posortowane po odjeździe.

    To jest odpowiedź ODCZYTANA z rozkładu, nie oszacowana. Zastąpiła
    zgadywankę, która stąd wyrosła: dawne join_value brało gotową wartość
    kontynuacji i dodawało do niej `shift` - opóźnienie wsiadania względem
    kursu, dla którego tamtą wartość policzono - zakładając, że cały dalszy
    łańcuch przesunie się dokładnie o tyle samo. Rozkład jest sztywny, więc
    to założenie myli się w obie strony, a przede wszystkim NIE WIE, kiedy
    późniejszy kurs traci przesiadkę i realny przyjazd skacze o kwadrans.
    Na relacji LEŚNICA -> BARTOSZOWICE (16:44, 2026-08-29) 116 z 232 wyjść
    obiecywało w ten sposób przyjazd wcześniejszy, niż da się osiągnąć - do
    10 minut za wcześnie - a że wartość poniżej optimum i tak jest obcinana
    do q=1.0, połowa mapy świeciła pełnym blaskiem bez pokrycia.

    Zwraca (neg_deps, arrs): dla przystanku dwie równoległe listy - godziny
    odjazdu ze znakiem minus (rosnąco, więc bisect działa wprost) i przyjazdy
    do celu. Obie maleją wzdłuż listy: im wcześniej się tu stoi, tym więcej
    kursów zostaje do wyboru, więc przyjazd może tylko być wcześniejszy.
    Wpis dopisujemy TYLKO gdy poprawia - lista jest z definicji Pareto-
    optymalna i krótka.

    Koszt: 17,7 tys. połączeń w oknie, 23 ms (relacja jak wyżej).
    """
    conns = day.conns
    neg_deps, arrs = {}, {}
    board_value = {}       # (kurs, słupek) -> przyjazd do celu, wsiadając tu w ten kurs
    trip_arr = {}          # kurs -> przyjazd do celu, jadąc nim dalej stąd

    near_target = _target_reach(day, target_set)
    # Malejąco po odjeździe: zanim dojdziemy do połączenia, wszystko, na co da
    # się z niego przesiąść, jest już policzone (tak samo jak w _backward).
    for i in range(bisect_left(day.dep_times, deadline) - 1, -1, -1):
        dep_t, arr_t, dep_s, arr_s, trip = conns[i]
        if dep_t < dep_sec:
            break
        # Trzy sposoby dojechania do celu tym połączeniem: wysiąść w celu,
        # jechać dalej tym samym kursem, przesiąść się na przystanku dojazdu.
        best = arr_t + near_target[arr_s][0] if arr_s in near_target else INF
        stay = trip_arr.get(trip, INF)
        if stay < best:
            best = stay
        for stop2, buffer in _reach_from(day, arr_s):
            times = neg_deps.get(stop2)
            if times is None:
                continue
            # Wpisy z odjazdem >= t to prefiks listy, a przyjazdy wzdłuż niej
            # maleją - więc najlepszy z nich stoi na jego końcu. W pętli, nie
            # w osobnej funkcji: to setki tysięcy pytań na jedno wyszukiwanie.
            j = bisect_right(times, -(arr_t + buffer)) - 1
            if j >= 0 and arrs[stop2][j] < best:
                best = arrs[stop2][j]
        if best == INF:
            continue
        trip_arr[trip] = best
        # To samo `best`, tylko zaadresowane inaczej: profil odpowiada "stoję
        # tu o tej godzinie", a to - "wsiadam TU w TEN kurs". Tablica odjazdów
        # pyta o to drugie, bo wiersz dotyczy konkretnego odjazdu, nie
        # najlepszego, jaki stąd jest (patrz _line_deadlines).
        board_value[(trip, dep_s)] = best
        known = arrs.get(dep_s)
        if known is None:
            neg_deps[dep_s], arrs[dep_s] = [-dep_t], [best]
        elif best < known[-1]:
            neg_deps[dep_s].append(-dep_t)
            known.append(best)
    return neg_deps, arrs, board_value


def _can_board(day, arr_t, stop, other, other_board):
    """Czy z przyjazdu (arr_t, stop) da się REALNIE wsiąść w KONKRETNY,
    już rozstrzygnięty kurs `other` (jego faktyczny, zapisany odjazd z
    other_board), a nie tylko JAKIŚ kurs wzorca `other` (dep_times to suma
    odjazdów wszystkich kursów tego wzorca w oknie, nie tego jednego).

    Budowa KONKRETNEJ propozycji trasy musi trzymać się jednego, już
    wybranego kursu `other`, więc liczy się wyłącznie jego własny, zapisany
    odjazd -
    inaczej propozycja mogłaby "przesiąść się" z przyjazdu o 21:00 w kurs,
    który przy tym konkretnym odjeździe już dawno odjechał."""
    dep_t = other["best_deps"].get(other_board)
    if dep_t is None:
        return False
    if other_board == stop:
        return arr_t + TRANSFER_SEC <= dep_t
    walk_sec = day.siblings.get(stop, {}).get(other_board)
    return walk_sec is not None and arr_t + walk_sec <= dep_t


def _extract_transfer_graph(day, kept, ranges, source_stops, target_set,
                            dep_sec, start_reach=None):
    """Krok 5 (propozycje tras): zamienia narysowane, przycięte segmenty
    w mały graf przesiadkowy - węzły to segmenty, krawędzie to miejsca,
    gdzie da się realnie wskoczyć/wysiąść między nimi. To ten sam graf,
    który mapa już rysuje: żadna propozycja trasy nie może więc pokazać
    przesiadki, której mapa by nie narysowała.

    Zwraca słownik z:
    - origin_ids: id() segmentu -> pozycja wsiadania, dla segmentów, które
      mapa rysuje OD przystanku startowego relacji - punkty startowe
      przeszukiwania (patrz _enumerate_journeys). Pozycja wsiadania to
      zakotwiczony początek narysowanego kawałka (ranges), a nie stops[0]:
      kurs wyjeżdżający z pętli końcowej mija start dopiero w swoim środku
      i właśnie tam się do niego wsiada,
    - exit_edges: dla każdego segmentu - lista jego wyjść w narysowanej
      części: albo dojazd do celu, albo przesiadka w inny segment,
    - seg_by_id: id() -> sam segment (wygodny odczyt),
    - origin_walk: {słupek: (skąd, sek)} - te ze startowych słupków, do
      których trzeba najpierw DOJŚĆ (patrz _origin_walk). Propozycja
      zaczynająca się w takim słupku musi otworzyć się etapem pieszym,
      inaczej karta każe wsiąść w pociąg na stacji, o której nie powiedziała,
      że trzeba do niej podejść.

    `dep_sec` to godzina, od której pasażer stoi na starcie - dojście
    musi z niej zdążyć na odjazd (patrz origin_ids niżej).

    Przeszukiwanie idzie tylko w przód, wyłącznie po exit_edges.
    """
    passing_index = _board_index(day, kept)
    near_target = _target_reach(day, target_set)
    drawn_stops = {
        id(seg): set(seg["stops"][ranges[id(seg)][0]:ranges[id(seg)][1]])
        for seg in kept
    }

    start_reach = start_reach or {}
    origin_ids = {}
    for seg in kept:
        start_pos = ranges[id(seg)][0]
        board = seg["stops"][start_pos]
        if board not in source_stops:
            continue
        # Słupek w zasięgu dojścia jest punktem startowym tylko wtedy, gdy
        # wychodząc o dep_sec zdąży się na TEN kurs - to samo pytanie, które
        # przesiadce zadaje _can_board. Kurs bywa narysowany od takiego słupka
        # z innego powodu: dowozi do niego inny pojazd. Zgłoszenie #168
        # (DWORZEC GŁÓWNY -> PL. GRUNWALDZKI, 08:31): szesnastka z Kościuszki
        # o 08:37 była na mapie dzięki przesiadce, a lista złożyła z niej
        # "idź 14 minut i wsiądź", z wyjściem o 08:23 - przed godziną
        # z pytania. Ta fikcyjna trasa bez przesiadki wypierała na dodatek
        # prawdziwą (tramwaj na Kościuszki i tam szesnastka) jako
        # zdominowaną.
        walk = start_reach.get(board)
        if walk is not None and dep_sec + walk[1] > seg["best_deps"][board]:
            continue
        origin_ids[id(seg)] = start_pos

    # Krawędź: ("target", pos, arr_t, stop, None, None, None) albo
    # ("transfer", pos, arr_t, stop, id(other), other_start, other_board) -
    # pos to wyjście TEGO segmentu (koniec etapu), other_start/other_board
    # to stały, już rozstrzygnięty punkt wsiadania w segment `other`.
    exit_edges = {}
    for seg in kept:
        sid = id(seg)
        start_pos, cut = ranges[sid]
        edges = []
        for j, (pos, _, arr_t, stop) in enumerate(seg["exits"]):
            if not (start_pos < pos <= cut):
                continue                        # wyjście poza narysowaną częścią
            if stop in near_target:
                # Slot 5/6 krawędzi "target" niesie DOJŚCIE do celu: ile
                # sekund i do którego słupka. Dla wyjścia na sam cel to
                # (0, ten sam słupek) i nic się nie zmienia.
                sec, cel_stop = near_target[stop]
                edges.append(("target", pos, arr_t, stop, None, sec, cel_stop))
                continue
            for other in passing_index.get(stop, ()):
                if other is seg:
                    continue
                other_start, _ = ranges[id(other)]
                other_board = other["stops"][other_start]
                # _can_board (nie _joins!) - propozycja trasy musi trzymać
                # się REALNEGO odjazdu tego jednego, konkretnego kursu
                # `other`, nie samej "zdążalności wzorca w ogóle" (patrz
                # docstring _can_board) - inaczej powstaje "teleportacja":
                # przesiadka na kurs, który przy tym konkretnym przyjeździe
                # już odjechał.
                if _can_board(day, arr_t, stop, other, other_board):
                    edges.append(
                        ("transfer", pos, arr_t, stop, id(other), other_start, other_board)
                    )
        exit_edges[sid] = edges

    return {
        "origin_ids": origin_ids,
        "exit_edges": exit_edges,
        "seg_by_id": {id(seg): seg for seg in kept},
        "origin_walk": start_reach,
    }


def _hop_key(a, b):
    return (a, b) if a <= b else (b, a)


def _build_hop_members(kept, ranges):
    """Dla każdego odcinka toru/ulicy (pary sąsiednich przystanków w
    narysowanej części kursu) - zbiór ETYKIET linii, które tamtędy jadą.
    Liczone z DOKŁADNYCH id przystanków (nie współrzędnych) - dwie linie
    zatrzymujące się na tych samych dwóch, kolejnych słupkach fizycznie
    dzielą tę samą ulicę/tory, więc to dokładne, nie przybliżone kryterium
    "co się tu nakłada" (patrz _finalize_segments, sekcja o rozdzielaniu
    wiązki)."""
    hop_members = {}
    for seg in kept:
        start_pos, cut = ranges[id(seg)]
        stops = seg["stops"]
        for k in range(start_pos, cut - 1):
            hop_members.setdefault(_hop_key(stops[k], stops[k + 1]), set()).add(seg["label"])
    return hop_members


def _membership_boundaries(hop_members, stops, start_pos, cut):
    """Pozycje (konwencja 'exits' - wyłączna górna granica kawałka), w
    których zestaw linii dzielących ten sam odcinek się zmienia - druga,
    niezależna od jasności, przyczyna cięcia kawałka na mapie (patrz
    _finalize_segments).

    Zwraca INDEKSY PRZYSTANKÓW (nie pozycje w konwencji "exits"): indeks k
    oznacza, że odcinek zaczynający się na stops[k] ma już inny zestaw linii
    niż ten, który się na stops[k] kończy. Kawałek rysowany jako całość nie
    ma prawa przez taki punkt przechodzić - inaczej cały dostałby skład
    korytarza policzony dla swojego PIERWSZEGO odcinka i twierdziłby "tędy
    jadą też X i Y" także tam, gdzie X i Y już dawno skręciły."""
    boundaries = set()
    prev_members = None
    for k in range(start_pos, cut - 1):
        members = frozenset(hop_members.get(_hop_key(stops[k], stops[k + 1]), ()))
        if prev_members is not None and members != prev_members:
            boundaries.add(k)
        prev_members = members
    return boundaries


def _line_sort_key(label):
    """JEDEN, globalny porządek linii - tramwaje przed autobusami, w obrębie
    rodzaju numerycznie. To nie jest kosmetyka: skład korytarza (patrz
    _corridor_lines) jest zawsze OBCIĘCIEM tego jednego porządku do linii
    obecnych na danym odcinku, więc grupka numerów rysowana na wspólnym
    korytarzu ma zawsze tę samą kolejność - i ta sama kolejność wychodzi w
    podpowiedzi pod kursorem (patrz app.js). Numer nie ma jak przeskoczyć w
    grupce z miejsca na miejsce między jednym odcinkiem a drugim."""
    num, mode = _line_parts(label)
    return (
        {"tram": 0, "bus": 1}.get(mode, 2),
        int(num) if num.isdigit() else 10 ** 6,
        num,
    )


def _corridor_lines(pieces, hop_members):
    """Dla każdego kawałka - PEŁNY skład korytarza, którym jedzie: wszystkie
    linie dzielące z nim te same, kolejne przystanki, RAZEM Z NIM SAMYM, w
    jednym, globalnym porządku (_line_sort_key). Kawałki jadące solo nie
    dostają nic.

    To jest cała odpowiedź backendu na kontrakt p.7 ("zawsze wiadomo, co tam
    jedzie"): mapa rysuje prawdziwą geometrię, więc linie wspólnego korytarza
    leżą jedna na drugiej i po samym kształcie nie da się ich rozróżnić.
    Rozróżnia je front - grupką numerów postawioną raz na całym korytarzu i
    przełączaniem między nimi pod kursorem (patrz app.js). Do jednego i do
    drugiego potrzebna jest właśnie ta lista.

    Liczona jest z ROZKŁADU (dokładne id przystanków, patrz
    _build_hop_members), nie z odległości na ekranie. Front próbował kiedyś
    zgadywać skład korytarza, mierząc piksele wokół kursora, i przy widoku
    całego miasta doliczał linie z sąsiednich ulic - stąd brały się plakietki
    "13 linii" tam, gdzie realnie jadą dwie.

    Skład jest stały na całej długości kawałka, bo kawałki są cięte dokładnie
    tam, gdzie się zmienia (patrz _membership_boundaries) - dlatego wystarczy
    odczytać go z pierwszego odcinka."""
    result = {}
    for label, stops_seq in pieces:
        members = hop_members.get(_hop_key(stops_seq[0], stops_seq[1]))
        if not members or len(members) < 2:
            continue
        result[(label, stops_seq)] = [
            {"num": _line_parts(other)[0], "kind": _line_parts(other)[1]}
            for other in sorted(members, key=_line_sort_key)
        ]
    return result



def _drawn_reach(kept, ranges):
    """{słupek: najwcześniejsza godzina, o której MAPA tu dowozi}.

    Czytane z narysowanej części kawałków, nie z całego skanu: skan zna
    godziny dla pół miasta, a tu chodzi o miejsca, które mapa naprawdę
    pokazuje. Stąd biorą się auta car-sharingu na mapie (patrz
    traficar.map_cars) - stoją przy tym, co narysowane, albo nie ma ich wcale.

    Godziny czytamy tak samo jak _piece_times: pierwszy przystanek narysowanej
    części opisuje ODJAZD, każdy następny PRZYJAZD.
    """
    reach = {}
    for seg in kept:
        start_pos, cut = ranges[id(seg)]
        for pos in range(start_pos, cut):
            stop = seg["stops"][pos]
            when = (seg["best_deps"] if pos == start_pos else seg["arr_times"]).get(stop)
            if when is not None and when < reach.get(stop, INF):
                reach[stop] = when
    return reach


def _drawn_runs(day, kept, ranges):
    """Narysowana mapa jako prawdziwe kursy: połączenia każdego kursu, który
    stoi za narysowanym kawałkiem, na jego narysowanej długości.

    Kawałek pamięta godziny tylko najlepszego kursu, a kursów o tej samej
    trasie bywa kilka. Przesiadki przy rowerze (punkt 16) mają być odczytane
    z rozkładu, więc idą po każdym z nich - bez przykładania godzin jednego
    kursu do drugiego."""
    conns = day.conns
    runs = []
    for seg in kept:
        start_pos, cut = ranges[id(seg)]
        for run in seg["runs"]:
            if cut - 1 > start_pos:
                runs.append([conns[i] for i in run[start_pos:cut - 1]])
    return runs


def _drawn_arrivals(day, runs, source_stops, dep_sec):
    """{słupek: [(godzina, ile pojazdów), ...]} - o której mapa tu dowozi,
    osobno dla każdej liczby pojazdów po drodze (punkt 16).

    Rundami, jak RAPTOR: runda k to wszystko, dokąd da się dojechać najwyżej
    k pojazdami po narysowanych kursach. Wpis przybywa tylko wtedy, gdy więcej
    pojazdów daje wcześniejszą godzinę, więc lista jest krótka i każdy jej
    wiersz to jedna prawdziwa droga. Start to zero pojazdów o godzinie wyjazdu
    - i wsiada się na nim bez bufora przesiadki. Ze startu wolno też odejść
    jeden krok pieszo i wsiąść u sąsiada, tak jak w skanie (_origin_walk):
    bez tego start z klikniętego punktu, który sam nie ma żadnego odjazdu,
    nie wsiadał w nic i rower dało się wziąć tylko spod samego startu."""
    best = dict.fromkeys(source_stops, dep_sec)
    out = {stop: [(dep_sec, 0)] for stop in source_stops}
    ready = dict(best)
    for stop, (_, sec) in _origin_walk(day, source_stops).items():
        if dep_sec + sec < ready.get(stop, INF):
            ready[stop] = dep_sec + sec
    for rides in range(1, MAX_BIKE_SIDE_RIDES + 1):
        arrived = {}
        for run in runs:
            on = False
            for dep_t, arr_t, dep_s, arr_s, _ in run:
                if not on:
                    if ready.get(dep_s, INF) > dep_t:
                        continue
                    on = True
                if arr_t < arrived.get(arr_s, INF):
                    arrived[arr_s] = arr_t
        improved = {stop: when for stop, when in arrived.items()
                    if when < best.get(stop, INF)}
        if not improved:
            break
        for stop, when in improved.items():
            best[stop] = when
            out.setdefault(stop, []).append((when, rides))
            for other, buffer in _reach_from(day, stop):
                if when + buffer < ready.get(other, INF):
                    ready[other] = when + buffer
    return out


def _drawn_onward(day, runs, target_set):
    """{słupek: [(odjazd, w celu o, ile pojazdów), ...]} - wsiadając tu w ten
    odjazd, o której jest się w celu i iloma pojazdami (punkt 16).

    Lustro _drawn_arrivals: runda k to przyjazd do celu najwyżej k pojazdami
    po narysowanych kursach, z dojściem na końcu tą samą regułą co wszędzie
    (_target_reach). Wpis przybywa tylko wtedy, gdy poprawia to, co dawało
    mniej pojazdów - więc godzina i liczba pojazdów w jednym wierszu są zawsze
    z tej samej drogi, a nie najlepszą godziną z jednej i najmniejszą liczbą
    z drugiej."""
    near = _target_reach(day, target_set)
    board_times, suffix_best, rows_of = {}, {}, {}

    def value_at(stop, t):
        # Najwcześniejszy przyjazd z poprzednich rund, stojąc tu o godzinie t.
        times = board_times.get(stop)
        if times is None:
            return INF
        i = bisect_left(times, t)
        return suffix_best[stop][i] if i < len(times) else INF

    out = {}
    for rides in range(1, MAX_BIKE_SIDE_RIDES + 1):
        found = []
        for run in runs:
            best = INF
            for dep_t, arr_t, dep_s, arr_s, _ in reversed(run):
                if arr_s in near:
                    best = min(best, arr_t + near[arr_s][0])
                if rides > 1:
                    for other, buffer in _reach_from(day, arr_s):
                        best = min(best, value_at(other, arr_t + buffer))
                if best < value_at(dep_s, dep_t):
                    found.append((dep_s, dep_t, best))
        if not found:
            break
        for stop, dep_t, arrival in found:
            out.setdefault(stop, []).append((dep_t, arrival, rides))
            rows_of.setdefault(stop, []).append((dep_t, arrival))
        for stop in {stop for stop, _, _ in found}:
            rows = sorted(rows_of[stop])
            board_times[stop] = [dep_t for dep_t, _ in rows]
            running, suffix = INF, []
            for _, arrival in reversed(rows):
                running = min(running, arrival)
                suffix.append(running)
            suffix_best[stop] = suffix[::-1]
    return out


def _finalize_segments(day, kept, ranges, geo_db, earliest=None,
                       board_value=None, deadline=None, source_stops=None,
                       why_of=None):
    """Tnie każdy narysowany kurs na kawałki tam, gdzie zmienia się zestaw
    linii dzielących ten sam odcinek ulicy/torów (patrz
    _membership_boundaries) - kawałek niesie jeden skład korytarza na całej
    długości. Prosty kurs bez współdzielonego odcinka dostaje jeden kawałek
    na całej narysowanej długości.

    Poza tym: agregacja po (linia, dokładny fragment) - kilka kursów tego
    samego wzorca w oknie to jeden kawałek - tnie geometrię
    (patrz gtfs.shape_slice) i formatuje odpowiedź.

    Nakładające się linie (kontrakt p.7 - zawsze wiadomo, co tu jedzie):
    geometria zostaje prawdziwa, po torach i ulicach (kontrakt p.6), więc
    linie wspólnego korytarza leżą na mapie jedna na drugiej. Backend nie
    próbuje ich rozsuwać - podaje tylko SKŁAD korytarza (_corridor_lines,
    pole `corridor` w odpowiedzi), a rozróżnianie robi front: grupką numerów
    i przełączaniem pod kursorem.

    geo_db to połączenie współdzielone z resztą zapytania (patrz plan_flow) -
    jedno połączenie na wszystkie wycinki geometrii, także te do propozycji
    tras.

    why_of(linia, przystanki) - dlaczego ten kawałek jest na mapie, do
    podglądu w Debug (patrz _value_map)."""
    hop_members = _build_hop_members(kept, ranges)

    pieces = {}   # (linia, dokładny fragment) -> (shape_id, godziny, ...)
    for seg in kept:
        start_pos, cut = ranges[id(seg)]
        boundary_stops = _membership_boundaries(hop_members, seg["stops"], start_pos, cut)
        piece_start = start_pos
        pending_end = None
        pending_reach = None      # o której jest się w celu, jadąc dalej stąd
        pending_ok = False        # ...i czy ta godzina jest odczytana, czy zgadnięta
        for (pos, _, _, _), reach, reach_ok in zip(
                seg["exits"], seg["suffix"], seg["suffix_exact"]):
            if pos <= start_pos + 1 or pos > cut:
                continue         # wyjście przed/na starcie narysowanej części - pomiń
            # Wydłużenie kawałka do `pos` obejmie odcinki o indeksach
            # piece_start .. pos-2. Jeśli któryś z nich zaczyna już inny
            # zestaw linii, kawałek przeszedłby przez zmianę składu korytarza
            # i podawałby ten sam skład na całej długości, także tam, gdzie
            # jest już inny (patrz _membership_boundaries).
            crosses = any(piece_start < k <= pos - 2 for k in boundary_stops)
            if pending_end is not None and not crosses:
                pending_end = pos          # nic się nie zmieniło - wydłuż bieżący kawałek
                continue
            if pending_end is not None:
                _keep_piece(pieces, seg, piece_start, pending_end,
                            pending_reach, pending_ok)
                # Kolejny kawałek zaczyna się DOKŁADNIE tam, gdzie poprzedni
                # się skończył (ten sam przystanek na styku - inaczej dwa
                # kawałki tego samego fizycznego kursu miałyby dziurę między
                # sobą na mapie). `pending_end` to `pos` (liczba przystanków,
                # wyłączna górna granica wycinka) - jego WŁASNY indeks w
                # `stops` to `pending_end - 1`.
                piece_start = pending_end - 1
            # Zmiana składu korytarza między dwoma wyjściami: kawałek tnie się
            # na niej i tak, choć nie ma tam wyjścia. Mapa ma wyjścia rzadko,
            # więc bez tego 143 z Księża Małego szła do Mostu Grunwaldzkiego ze
            # składem "112, 124, ..." z pierwszego odcinka, a 124 skręca po
            # trzech przystankach (#159). Ta sama godzina w celu - to wciąż
            # jedno wyjście.
            for k in sorted(k for k in boundary_stops if piece_start < k <= pos - 2):
                _keep_piece(pieces, seg, piece_start, k + 1, reach, reach_ok)
                piece_start = k
            pending_end = pos
            pending_reach, pending_ok = reach, reach_ok
        if pending_end is not None:
            _keep_piece(pieces, seg, piece_start, pending_end,
                        pending_reach, pending_ok)

    corridors = _corridor_lines(pieces, hop_members)

    seg_list = []
    for (label, stops_seq), (shape_id, times, reach, reach_ok, _headsign) in pieces.items():
        coords = [day.stop_coords[s] for s in stops_seq]
        path = gtfs.shape_slice(shape_id, coords, geo_db)
        num, mode = _line_parts(label)
        item = {
            "path": _round_path(path),
            "num": num,
            "kind": mode,
            # Pełna jasność - mapa z wartości jej nie stopniuje, a front
            # liczy z niej krycie (patrz lookOpacity w app.js).
            "w": 1.0,
        }
        # Godziny - z rozkładu, nie z geometrii (patrz _piece_times):
        #   stops_t - [lat, lon, sekunda] dla każdego przystanku kawałka;
        #             front interpoluje z tego godzinę w punkcie pod kursorem
        #   arrive  - o której jest się W CELU, jadąc dalej stąd; tylko gdy
        #             ta liczba jest odczytana, nie zgadnięta
        if times is not None:
            item["stops_t"] = [
                [*point, when] for point, when in zip(_round_path(coords), times)
            ]
        if reach is not None and reach_ok:
            item["arrive"] = reach
        corridor = corridors.get((label, stops_seq))
        if corridor:
            # Kto tędy jedzie - CAŁY skład, razem z tą linią, z rozkładu, nie
            # z odległości na ekranie (patrz _corridor_lines). Kawałki jadące
            # solo (zdecydowana większość) nie dostają tego pola wcale, żeby
            # nie puchła odpowiedź.
            item["corridor"] = corridor
        if why_of is not None:
            item["why"] = why_of(label, stops_seq)
        seg_list.append(item)
    return seg_list, _transfer_nodes(day, pieces, earliest, board_value, deadline,
                                    source_stops)


def _rides_back(earliest, board, alight):
    """Czy ten kawałek WIEZIE Z POWROTEM - mierzone rozkładem, nie geometrią.

    `earliest[stop]` (patrz _forward) to najwcześniejsza godzina, o której da
    się być na przystanku. Jeśli tam, dokąd ten kurs wiezie, można było być
    WCZEŚNIEJ niż tam, gdzie stoimy, to jedziemy w miejsce już za nami -
    przejazd nie daje postępu, tylko cofa. Węzeł nie ma prawa proponować
    takiego kursu (zgłoszone 2026-08-29: Kamiennogórska oferowała tramwaj 3
    na Leśnicę osobie, która właśnie z Leśnicy przyjechała).

    To NIE jest nowa reguła - punkt 4 kontraktu mówi ją od początku ("kurs
    zawracający na JAKIKOLWIEK przystanek, przez który już przejechaliśmy,
    jest drogą powrotną, nie kontynuacją") - tu egzekwowana dla listy pod
    kropką.

    Miara luzu (`_backward`) się do tego nie nadaje: w szerokim oknie objazd
    o przystanek kosztuje minutę terminu, więc próg cofnięcia go nie łapie.
    Postęp łapie, bo pyta o coś innego - nie "czy zdążę", tylko "czy to
    w ogóle jest przede mną".

    Dotyczy WYŁĄCZNIE tego, co węzeł proponuje. Rysowanej mapy nie rusza.

    Bez `earliest` (tryb awaryjny) nie odsiewamy nic.
    """
    if not earliest:
        return False
    here, there = earliest.get(board), earliest.get(alight)
    if here is None or there is None:
        return False
    return there <= here


def _line_deadlines(day, stops, board_value, from_sec, deadline):
    """Dla każdej linii odjeżdżającej z tego miejsca: OSTATNI odjazd, którym
    jeszcze da się dojechać do celu w oknie mapy.

    To jest mocna wersja reguły "tylko to, co jeszcze zdąży" (punkt 11
    kontraktu). Słaba, dotychczasowa, sprawdzała tylko, czy sam ODJAZD mieści
    się w oknie - warunek konieczny, nie wystarczający: autobus odjeżdżający
    minutę przed zamknięciem okna prawie na pewno do celu w nim nie dowiezie.
    Tutaj pytamy wprost: wsiadam W TEN kurs W TYM miejscu - o której jestem
    w celu? Odpowiedź jest odczytana z rozkładu (patrz _target_profile).

    JEDNA godzina na linię wystarczy, bo późniejszy kurs tej samej linii w tę
    samą stronę nie może dojechać wcześniej niż wcześniejszy - wartość jest
    względem godziny niemalejąca. Ostatni zdążający odjazd dzieli więc listę
    na dwie części i front ma do sprawdzenia jedną liczbę na wiersz.

    Linia bez ANI JEDNEGO zdążającego kursu w ogóle nie jest opcją i nie
    powinna się pojawić na liście.
    """
    out = {}
    for dep, trip, stop in gtfs.departures_between(day, stops, from_sec, deadline):
        value = board_value.get((trip, stop))
        if value is None or value > deadline:
            continue
        label, headsign = day.trip_info[trip]
        num, mode = _line_parts(label)
        key = (num, mode, headsign)
        if dep > out.get(key, -1):
            out[key] = dep
    return out


def _place_center(day, place_key, fallback_stop):
    """Środek wszystkich słupków jednego miejsca.

    Zwykła średnia współrzędnych, nie mediana ani środek prostokąta: miejsce
    to z definicji słupki w promieniu PLACE_MAX_SPAN_M (patrz
    gtfs._build_places), więc nie ma tu rozrzutu, który średnią mógłby
    przesunąć gdziekolwiek poza sam węzeł.
    """
    stops = day.stops_by_place.get(place_key) or [fallback_stop]
    coords = [day.stop_coords[s] for s in stops if s in day.stop_coords]
    if not coords:
        return _round_path([day.stop_coords[fallback_stop]])[0]
    return _round_path([(
        sum(lat for lat, _ in coords) / len(coords),
        sum(lon for _, lon in coords) / len(coords),
    )])[0]


def _transfer_nodes(day, pieces, earliest=None, board_value=None, deadline=None,
                    source_stops=None):
    """Węzły przesiadkowe mapy - to, na czym front stawia kropki z tablicą
    odjazdów.

    Po jednym na MIEJSCE, nie na słupek: plac z trzema peronami ma być jedną
    kropką mówiącą wszystko, a nie trzema, z których każda mówi co innego
    (patrz gtfs._build_places).

    `lines` to linie, o których węzeł ma coś do powiedzenia - z kierunkiem, bo
    ta sama linia mija węzeł w obie strony, a mapa mówi o jednej. Każda niesie
    `flow`, czyli CO SIĘ TU Z NIĄ DZIEJE - trzy rzeczy, nie jedna
    (patrz punkt 11 kontraktu):

      "start"   - mapa wiezie tą linią DALEJ stąd, ale nie wiezie nią DO tego
                  miejsca. Wsiadasz tu pierwszy raz - wcześniej nie było jak.
      "through" - mapa wiezie tą linią i DO tego miejsca, i DALEJ. Tym
                  pojazdem można już jechać, więc wsiadanie tutaj to jedna
                  z możliwości, a nie jedyna.
      "end"     - mapa wiezie tą linią DO tego miejsca i dalej nią nie wiezie.
                  Tu się z niej wysiada.

    GDZIE w ogóle stoi kropka: tam, gdzie coś się ZACZYNA albo KOŃCZY - jakaś
    linia staje się stąd dostępna, albo mapa przestaje którąś dalej wieźć.
    Przystanek, przez który wszystko tylko przejeżdża, kropki nie dostaje,
    choćby leżał na styku dwóch narysowanych kawałków.

    Czytane z KAŻDEGO przystanku kawałka, nie tylko z jego końców. Kawałek
    "Galeria Dominikańska -> Urząd Wojewódzki -> Katedra" przejeżdża przez
    urząd w połowie swojej długości: patrzenie na same końce mówiło, że tej
    linii tam nie ma, choć mapa rysuje ją przez ten przystanek i można w nią
    tam wsiąść (zgłoszone 2026-08-31). Ta sama wąska miara kasowała całe
    kropki - na Katedrze kończyły się kawałki 5 i N, a 10 i 111 tylko tamtędy
    przejeżdżały, więc węzeł orzekał "tylko się tu wysiada" i znikał, mimo że
    to jest dokładnie ta przesiadka, po którą się tam jedzie.

    Wcześniej węzeł niósł wyłącznie pierwsze dwa przypadki zlane w jedno
    ("w co da się tu wsiąść"), więc tablica milczała o tym, czym się tu w ogóle
    przyjechało - a to połowa odpowiedzi na "gdzie ja jestem i co dalej".

    `arrive` (tylko przy "end") to godzina, o której się tu tą linią jest -
    z rozkładu tego samego kursu, z którego narysowano kawałek. Wiersz "end"
    nie jest odjazdem, więc front nie ma go skąd wziąć z tablicy przystanku.

    `depart_by` (tylko przy "start"/"through") to ostatni odjazd, którym
    jeszcze się zdąży (patrz _line_deadlines).

    `sec` to najwcześniejsza godzina, o której można tu być - od niej liczy się
    "co stąd jeszcze odjedzie".

    Współrzędne idą DWIE, bo obie odpowiadają na to samo pytanie inaczej,
    a wybór między nimi to sprawa gustu (przełącznik w panelu ⚙):
    `lat`/`lon` to słupek, z którego wzięta jest godzina - kropka stoi wtedy
    na peronie; `clat`/`clon` to środek WSZYSTKICH słupków miejsca - kropka
    stoi wtedy pośrodku węzła, którego dotyczy, zamiast na losowo wybranym
    jego krańcu. Liczymy obie tutaj, więc przełącznik nic nie dopytuje.
    """
    # Które miejsce jest startem, wiadomo WPROST: `source_stops` to słupki,
    # z których rozwiązano zapytanie (patrz gtfs.match_stop), a węzły i tak
    # idą po kluczu miejsca. Front nie ma tego z czego odtwarzać - nazwa węzła
    # to nazwa jednego z jego słupków, więc zgadywanie po niej myliłoby się
    # dokładnie tam, gdzie plac ma słupki o różnych nazwach.
    start_places = {day.place_of.get(s, s) for s in (source_stops or ())}

    # Każde dotknięcie przystanku przez narysowany kawałek: (miejsce, linia,
    # słupek, godzina, czy wiezie DALEJ, czy dowozi TU, czy to koniec kawałka).
    touches = []
    for (label, stops_seq), (_shape, times, _reach, _ok, headsign) in pieces.items():
        if times is None:
            continue                     # bez godzin nie ma o co pytać
        num, mode = _line_parts(label)
        line = (num, mode, headsign)
        last = len(stops_seq) - 1
        for i, stop in enumerate(stops_seq):
            touches.append((
                day.place_of.get(stop, stop), line, stop, times[i],
                # Wiezie dalej - chyba że dalej znaczy Z POWROTEM (patrz
                # _rides_back). Pytamy o KAWAŁEK JAKO CAŁOŚĆ, nie o drogę od
                # tego przystanku: `_rides_back` uznaje za cofnięcie także
                # RÓWNE godziny, a na przedostatnim przystanku przed celem
                # "najwcześniej tutaj" i "najwcześniej u celu" są zwykle
                # identyczne - pytany per przystanek orzekłby, że linia się tu
                # kończy, choć jedzie jeszcze przystanek do celu (Reja, 111).
                # Kawałek zawracający i tak nie ma prawa być narysowany
                # (punkt 4), więc miara na całości niczego nie przepuszcza.
                i < last and not _rides_back(earliest, stops_seq[0], stops_seq[-1]),
                i > 0,                   # dowozi tu - czyli można już nim jechać
                i == 0 or i == last,     # koniec kawałka - to on stawia kropkę
            ))

    nodes = {}
    for key, line, stop, when, onward, arriving, _is_end in touches:
        node = nodes.setdefault(
            key, {"sec": None, "stop": None, "boards": set(), "arrivals": {}})
        # Najwcześniej, kiedy mapa potrafi tu kogoś postawić - także pojazdem,
        # który tędy tylko przejeżdża: siedząc w nim, jest się tu o tej godzinie.
        if node["sec"] is None or when < node["sec"]:
            node["sec"], node["stop"] = when, stop
        if onward:
            node["boards"].add(line)
        # Kilka kawałków tej samej linii może tu dowozić (różne kursy w oknie).
        # Liczy się NAJWCZEŚNIEJSZY przyjazd - ta sama zasada, co przy `sec`.
        if arriving and when < node["arrivals"].get(line, INF):
            node["arrivals"][line] = when

    out = []
    for key, node in nodes.items():
        # Ostatni odjazd każdej linii, którym jeszcze się zdąży. Liczone dla
        # WSZYSTKICH słupków miejsca, bo dymek i tak scala je w jedną tablicę.
        limits = {}
        if node["boards"] and board_value is not None and deadline is not None:
            limits = _line_deadlines(
                day, day.stops_by_place.get(key, [node["stop"]]),
                board_value, node["sec"], deadline,
            )
        lines = []
        for line in sorted(node["boards"] | set(node["arrivals"])):
            num, kind, headsign = line
            depart_by = limits.get(line)
            # Linia, którą stąd już się nie dojedzie, przestaje być opcją do
            # wsiadania - ale jeśli mapa nią tu PRZYWOZI, wciąż jest czym
            # innym niż niczym: zostaje jako "end".
            boards = line in node["boards"] and not (limits and depart_by is None)
            arrive = node["arrivals"].get(line)
            if not boards and arrive is None:
                continue
            entry = {"num": num, "kind": kind, "headsign": headsign}
            if boards:
                entry["flow"] = "through" if arrive is not None else "start"
                if depart_by is not None:
                    entry["depart_by"] = depart_by
            else:
                entry["flow"] = "end"
                entry["arrive"] = arrive
            lines.append(entry)
        # Miejsce, w którym da się tylko wysiąść, nie jest przesiadką i nie
        # dostaje kropki.
        if not any(l["flow"] != "end" for l in lines):
            continue
        # ...a miejsce, przez które WSZYSTKO tylko przejeżdża, też nie: nic się
        # tu nie staje dostępne i nic nie przestaje, więc nie ma o czym
        # decydować - to przystanek, który się mija siedząc (punkt 11: "nie na
        # każdym mijanym przystanku").
        #
        # To jest właściwa miara "sensownego wysiadania" - i celowo NIE jest nią
        # koniec narysowanego kawałka. Kawałki tnie też zmiana składu korytarza
        # (punkt 7, patrz `crosses` w _finalize_segments), czyli sprawa czysto
        # rysunkowa: na Urzędzie Wojewódzkim (Impart) D i 146 schodzą się w
        # jeden korytarz, więc oba kawałki dostały tam szew. Kropka dziedziczyła ten szew i stawała
        # w miejscu, w którym nie da się zrobić nic (zgłoszone 2026-08-31).
        # Linia przecięta z powodu korytarza wychodzi tu jako "through" -
        # i tu dowozi, i dalej wiezie - więc sama z siebie kropki nie stawia.
        if not any(l["flow"] != "through" for l in lines):
            continue
        lat, lon = _round_path([day.stop_coords[node["stop"]]])[0]
        clat, clon = _place_center(day, key, node["stop"])
        entry = {
            "name": day.stop_names[node["stop"]],
            "lat": lat,
            "lon": lon,
            "clat": clat,
            "clon": clon,
            "sec": node["sec"],
            # Pełna jasność, jak kawałki (patrz _finalize_segments).
            "w": 1.0,
            "lines": lines,
        }
        if key in start_places:
            entry["start"] = True     # tylko przy tym jednym - pole ma nie puchnąć
        out.append(entry)
    return out


def _keep_piece(pieces, seg, start, end, reach, reach_ok):
    """Zapisuje kawałek pod kluczem (linia, dokładny fragment). Ten sam
    fragment tej samej linii może pochodzić z kilku kursów w oknie - zostaje
    pierwszy, i to JEGO godziny jadą razem z nim: kawałek i podane przy nim
    czasy mają pochodzić z tego samego, jednego kursu.

    reach to przyjazd DO CELU, gdy jedzie się dalej stąd. reach_ok mówi, czy
    ta liczba jest ODCZYTANA z rozkładu, czy ZGADNIĘTA (brak widocznej
    kontynuacji) - zgadniętej mapa nie pokazuje."""
    key = (seg["label"], tuple(seg["stops"][start:end]))
    if key not in pieces:
        pieces[key] = (seg["shape"], _piece_times(seg, start, end),
                       reach, reach_ok, seg["headsign"])


def _piece_times(seg, start, end):
    """Godziny przejazdu tego kursu przez KAŻDY przystanek kawałka, po kolei.

    Front ma z tego dwie rzeczy: czas samego kawałka (ostatnia minus pierwsza)
    i - to ważniejsze - godzinę w DOWOLNYM punkcie pod kursorem, przez
    interpolację między dwiema sąsiednimi godzinami (patrz punkt 10
    kontraktu). Dlatego godziny jadą per przystanek, a nie jako jedna para
    na cały kawałek: interpolować wolno tylko MIĘDZY dwoma sąsiednimi
    przystankami, nie przez pół trasy.

    None w środku listy jest niemożliwe do wykorzystania, więc gdy
    czegokolwiek brakuje (przystanek powtórzony w pętli potrafi nadpisać wpis
    w słowniku), oddajemy None i front nie pokazuje dla tego kawałka godziny
    wcale, zamiast pokazywać zmyśloną."""
    stops = seg["stops"][start:end]
    times = []
    for i, stop in enumerate(stops):
        # Pierwszy przystanek kawałka opisuje ODJAZD (stąd się rusza), każdy
        # następny PRZYJAZD (do niego się dojeżdża).
        when = seg["best_deps"].get(stop) if i == 0 else seg["arr_times"].get(stop)
        if when is None:
            return None
        times.append(when)
    for a, b in zip(times, times[1:]):
        if b < a:
            return None      # czasy się cofają - kurs odczytany niewiarygodnie
    return times


def _segment_ride_leg(day, seg, board_pos, alight_pos, geo_db):
    """Etap przejazdu wycięty z segmentu mapy przepływów - odpowiednik
    _reconstruct dla łańcucha znalezionego przez _enumerate_journeys.

    Segment ma już wszystko potrzebne (odjazdy/przyjazdy najlepszego kursu,
    kierunek, geometrię), więc w przeciwieństwie do _reconstruct nie trzeba
    odpytywać stop_times przez gtfs.trip_path.

    board_pos to indeks w `stops`, alight_pos to pozycja
    w konwencji "exits" (już wliczająca przystanek wyjścia) - `stops`
    wycinamy więc jako [board_pos:alight_pos], bez +1.
    """
    stops = seg["stops"][board_pos:alight_pos]
    from_stop, to_stop = stops[0], stops[-1]
    dep_t = seg["best_deps"][from_stop]
    arr_t = seg["arr_times"][to_stop]
    path = gtfs.shape_slice(seg["shape"], [day.stop_coords[s] for s in stops], geo_db)
    num, mode = _line_parts(seg["label"])
    return {
        "kind": "ride",
        "line": seg["label"],
        "num": num,
        "mode": mode,
        "headsign": seg["headsign"],
        "from": day.stop_names[from_stop],
        "from_time": _fmt_time(dep_t),
        "to": day.stop_names[to_stop],
        "to_time": _fmt_time(arr_t),
        "dep_sec": dep_t,
        "arr_sec": arr_t,
        "minutes": round((arr_t - dep_t) / 60),
        "stops": [day.stop_names[s] for s in stops],
        "stops_count": len(stops) - 1,
        "via": _via_stops(day, [(s, seg["best_deps"].get(s, seg["arr_times"].get(s)))
                                for s in stops[1:-1]]),
        "path": _round_path(path),
    }


def _enumerate_journeys(day, graph, dep_sec, geo_db, limit=DEFAULT_JOURNEY_LIMIT,
                        gain_sec=TRANSFER_GAIN_SEC, ride=None):
    """Lista konkretnych propozycji tras, czytana wprost z grafu przesiadek
    mapy przepływów (patrz _extract_transfer_graph) - żadnego osobnego
    przeszukiwania CSA. Propozycja to po prostu ścieżka przez ten sam graf,
    który mapa już narysowała: nic tu nie może pokazać przesiadki, której
    nie ma na mapie.

    Przeszukiwanie KOLEJKĄ (BFS po całym drzewie wariantów, najpierw
    najjaśniejsze gałęzie), nie rekurencją: dawniej jeden globalny licznik
    odwiedzin, sprawdzany na wejściu do KAŻDEGO wywołania, pozwalał JEDNEJ
    gałęzi (jednemu miejscu startowemu albo jednemu gęsto rozgałęzionemu
    węzłowi po drodze) zejść rekurencyjnie na pełną głębokość i wyczerpać
    cały budżet na warianty JEDNEGO korytarza (np. kilka linii o zbliżonej
    jasności z tego samego przystanku), zanim reszta origin_ids - albo inne
    rozgałęzienie tej samej trasy - w ogóle dostała szansę. FIFO gwarantuje
    przeciwnie: żadna gałąź nie zejdzie o poziom głębiej, dopóki WSZYSTKIE
    inne żywe gałęzie (inne miejsca startowe, inne rozgałęzienia po drodze)
    nie dostaną swojej kolejki na TYM SAMYM poziomie - jedna bogata okolica
    nie może więc zmonopolizować przeszukiwania kosztem korytarzy, które
    mapa przepływów i tak już narysowała gdzie indziej w grafie. Węzła NIE
    ograniczamy do paru najjaśniejszych krawędzi na raz (kuszące, ale przy
    ciasnym MAX_JOURNEY_CHAIN_LEGS zdarzają się węzły z kilkoma
    kontynuacjami REMISUJĄCYMI na tej samej, najlepszej jasności - obcięcie
    remisu byłoby arbitralne i mogłoby wyciąć jedyną krawędź, która akurat
    prowadzi dalej do celu w limicie etapów, gubiąc nawet najszybszą
    trasę). Duplikat (ten sam ciąg linii i te same miejsca wsiadania) jest
    odrzucany w momencie ukończenia łańcucha, nie dopiero po zebraniu
    wszystkich kandydatów - inaczej zajmowałby miejsce w limicie kosztem
    korytarza znalezionego później.

    Sufity kosztu skalowane do `limit` (patrz CANDIDATES_PER_JOURNEY/
    VISITS_PER_JOURNEY) - przy domyślnym limicie sufity to dokładnie
    MAX_JOURNEY_VISITS/MAX_JOURNEY_CANDIDATES; suwak "ile propozycji szukać"
    wyżej niż domyślne każe przeszukać graf głębiej, a nie tylko wypisać
    dłuższy fragment tych samych paru znalezionych łańcuchów. Bez tego duże
    miasto przy szerokim oknie czasowym mogłoby dać kombinatoryczną eksplozję
    wariantów.

    Może zwrócić listę bez najszybszej trasy w ogóle (patrz plan_flow) -
    to funkcja wyżej pilnuje, żeby najszybsza trasa zawsze była pokazana -
    tu liczy się tylko to, co faktycznie da się złożyć z narysowanego grafu.

    `ride` to kurs, w którym pasażer już siedzi (start z pokładu, patrz
    onboard.find_ride): trasa, która nie zaczyna się dalszą jazdą nim, zaczyna
    się przesiadką - i tak jest liczona.
    """
    origin_ids = graph["origin_ids"]
    exit_edges = graph["exit_edges"]
    seg_by_id = graph["seg_by_id"]
    origin_walk = graph.get("origin_walk", {})

    candidate_cap = max(MAX_JOURNEY_CANDIDATES, limit * CANDIDATES_PER_JOURNEY)
    visit_cap = max(MAX_JOURNEY_VISITS, limit * VISITS_PER_JOURNEY)

    def edge_priority(edge):
        kind, _, arr_t, _, other_id, dojscie, _ = edge
        if kind == "target":
            # Po FAKTYCZNYM przyjeździe do celu, czyli razem z dojściem.
            # Ten sam kurs wsiadany w tym samym miejscu daje dziś kilka wyjść
            # "do celu" - pod sam cel i wcześniejsze, z dojściem pieszo
            # (patrz _target_reach) - a deduplikacja łańcuchów patrzy na
            # linię i miejsce wsiadania, więc zachowa TEN, który trafi tu
            # pierwszy. Bez tego klucza pierwszy bywał wariant "wysiądź
            # wcześniej i idź", a wariant "dojedź pod sam cel" przepadał
            # jako rzekomy duplikat - mimo że jest po prostu szybszy.
            return (0, arr_t + dojscie)
        return (1, -seg_by_id[other_id]["q"])

    queue = deque(
        ([], sid, origin_ids[sid], {sid})
        for sid in sorted(origin_ids, key=lambda i: -seg_by_id[i]["q"])
    )
    candidates = []   # łańcuchy: [(seg, board_pos, alight_pos), ...]
    seen = set()
    visits = 0
    while queue and visits < visit_cap and len(candidates) < candidate_cap:
        chain, sid, board_pos, visited = queue.popleft()
        visits += 1
        seg = seg_by_id[sid]
        edges = sorted(exit_edges.get(sid, ()), key=edge_priority)
        for edge in edges:
            kind, alight_pos, _, _, other_id, other_start, cel_stop = edge
            new_chain = chain + [(seg, board_pos, alight_pos)]
            if kind == "target":
                signature = tuple(
                    (s["label"], day.stop_names[s["stops"][bp]])
                    for s, bp, _ in new_chain
                )
                if signature in seen:
                    continue
                seen.add(signature)
                # `other_start` niesie tu DOJŚCIE do celu (patrz
                # _extract_transfer_graph): zero dla wyjścia na sam cel.
                candidates.append((new_chain, other_start, cel_stop))
                if len(candidates) >= candidate_cap:
                    break
            elif other_id not in visited and len(new_chain) < MAX_JOURNEY_CHAIN_LEGS:
                queue.append((new_chain, other_id, other_start, visited | {other_id}))

    ranked = []
    for chain, dojscie_sec, cel_stop in candidates:
        first_dep = chain[0][0]["best_deps"][chain[0][0]["stops"][chain[0][1]]]
        last_seg, _, last_alight = chain[-1]
        # Przyjazd liczy się DO CELU, nie do przystanku, na którym się wysiada:
        # trasa kończąca się dojściem jest gotowa dopiero po tym dojściu.
        arrival = (last_seg["arr_times"][last_seg["stops"][last_alight - 1]]
                   + dojscie_sec)
        # Przesiadka kosztuje gain_sec: propozycja z przesiadką musi tyle
        # oszczędzić, żeby wyprzedzić jazdę bez niej (patrz TRANSFER_GAIN_SEC).
        # Sortujemy po koszcie z karą, ale pokazujemy prawdziwy przyjazd.
        # Liczba przesiadek zostaje rozstrzygnięciem remisu, więc przy progu 0
        # klucz jest dokładnie taki jak przed wprowadzeniem kary.
        przesiadki = len(chain) - 1
        if ride is not None:
            seg, board_pos, _ = chain[0]
            board_stop = seg["stops"][board_pos]
            if not (board_stop == ride["stop"] and seg["label"] == ride["line"]
                    and seg["best_deps"][board_stop] == ride["sec"]):
                przesiadki += 1
        ranked.append((arrival + przesiadki * gain_sec, przesiadki, -first_dep,
                       chain, arrival, dojscie_sec, cel_stop))
    ranked.sort(key=lambda item: item[:3])

    # Odsiew propozycji ZDOMINOWANYCH: taka, która dowozi DOKŁADNIE O TEJ
    # SAMEJ godzinie, każe wyjść nie później, a wymaga większej liczby
    # przesiadek, nie jest alternatywą - jest tą samą trasą z doklejoną
    # robotą. Zgłoszone na żywo ("jaki to ma sens? lepiej od razu tam
    # pójść"): obok trasy "dojdź na stację i wsiądź w pociąg" stała druga,
    # z tym samym przyjazdem, w której trzeba było najpierw przejechać JEDEN
    # przystanek autobusem, żeby dojść na tę samą stację od innej strony.
    #
    # Równość przyjazdu, nie "nie później" - i to jest tu istotne. Trasa
    # dojeżdżająca PÓŹNIEJ, choćby i z przesiadką więcej, zostaje: ta lista
    # ma pokazywać także opcje niszowe (inny korytarz, inna częstotliwość),
    # a nie tylko czoło rankingu. Odsiewamy wyłącznie pracę wykonaną za
    # darmo, nie gorszy wybór.
    niezdominowane = []
    for wpis in ranked:
        _koszt, przes, neg_dep, _chain, arr = wpis[:5]
        if any(lepszy_arr == arr and lepszy_przes < przes
               and lepszy_neg <= neg_dep
               for _lk, lepszy_przes, lepszy_neg, _lc, lepszy_arr in
               (w[:5] for w in niezdominowane)):
            continue
        niezdominowane.append(wpis)
    ranked = niezdominowane

    journeys = []
    for (_cost, _przesiadki, neg_dep, chain, arrival,
         dojscie_sec, cel_stop) in ranked[:limit]:
        legs = []
        for i, (seg, board_pos, alight_pos) in enumerate(chain):
            if i > 0:
                prev_seg, _, prev_alight = chain[i - 1]
                prev_stop = prev_seg["stops"][prev_alight - 1]
                this_board_stop = seg["stops"][board_pos]
                if prev_stop != this_board_stop:
                    legs.append(_walk_leg(day, prev_stop, this_board_stop))
            elif seg["stops"][board_pos] in origin_walk:
                # Trasa zaczyna się nie na starcie, tylko tam, dokąd stąd
                # trzeba dojść (patrz _origin_walk) - to musi być widoczne
                # jako etap, bo inaczej karta milczy o jedynej rzeczy, od
                # której cała reszta zależy.
                legs.append(_walk_leg(
                    day, origin_walk[seg["stops"][board_pos]][0],
                    seg["stops"][board_pos]))
            legs.append(_segment_ride_leg(day, seg, board_pos, alight_pos, geo_db))
        if dojscie_sec:
            # Dojście z ostatniego przystanku pod sam cel - bez tego etapu
            # trasa urywa się kilkaset metrów wcześniej, a lista musiałaby
            # doklejać jeszcze jeden przejazd tylko po to, żeby skończyć na
            # słupku celu (patrz _target_reach).
            last_seg, _, last_alight = chain[-1]
            legs.append(_walk_leg(day, last_seg["stops"][last_alight - 1],
                                  cel_stop))
        rides = [leg for leg in legs if leg["kind"] == "ride"]
        # Odjazd trasy otwartej dojściem to moment WYJŚCIA, nie odjazd
        # pojazdu: pasażer, który wyjdzie o godzinie z karty, ma zdążyć.
        start_sec = None
        if legs[0]["kind"] == "walk":
            legs[0]["dep_sec"] = start_sec = rides[0]["dep_sec"] - legs[0]["_sec"]
        journeys.append(_summarize_journey(legs, rides, arrival, dep_sec,
                                           start_sec))

    return journeys


# ============================================================================
#                            ROWER MIEJSKI (WRM)
# ============================================================================
# Rower wchodzi do trasy jako JEDEN przejazd między dwiema stacjami - bo tak
# działa WRM: wypożyczenie zaczyna się i kończy w stojaku (patrz bikes.py).
# Wolno mu stać w DOWOLNYM miejscu trasy, i to bez czterech osobnych
# algorytmów, bo plan_flow ma już policzone obie połówki odpowiedzi:
#
#   earliest[słupek]  - o której najwcześniej da się tu być, startując
#                       z relacji o dep_sec (skan w przód, _forward);
#   profile(słupek, t) - stojąc tu o godzinie t, o której jest się w celu
#                       (profilowy skan wstecz, _target_profile).
#
# Wstawienie roweru to więc jedno złożenie tych dwóch funkcji przez parę
# stacji (A, B):
#
#   earliest[X] -> dojście X→A -> ODBLOKOWANIE -> przejazd A→B -> zwrot
#               -> dojście B→Y -> profile(Y, ...)
#
# Skrajne przypadki wychodzą z tego samego wzoru, nie z osobnego kodu:
# X bywa samym punktem startu (rower NA POCZĄTKU trasy), Y samym celem
# (rower NA KOŃCU), obie naraz (sam rower), obie w środku sieci (rower
# W ŚRODKU, jako skrót między dwiema liniami).
#
# Czego tu świadomie NIE ma: roweru na mapie przepływów. Jasność segmentu
# znaczy tam „jak dobrym wyborem jest siedzieć TERAZ w TYM kursie" i liczy
# się z rozkładu (patrz docs/FLOW_MAP_CONTRACT.md) - rower rozkładu nie ma,
# więc nie ma też czego porównywać. Trasa z rowerem rysuje się natomiast
# w całości po wybraniu jej z listy propozycji, tak samo jak każda inna.

# Ile propozycji z rowerem wolno dołożyć do listy. Dwie, bo rower ma tu być
# alternatywą, a nie zalewem: przy ciasnym oknie czasowym wszystkie warianty
# z rowerem są do siebie podobne (ta sama okolica, sąsiednie stacje).
BIKE_JOURNEY_LIMIT = 2
# Ile najbliższych słupków bierzemy pod uwagę przy jednej stacji. Dalsze i tak
# przegrywają czasem dojścia, a każdy kosztuje odczyt profilu w pętli po parach.
BIKE_NEAR_STOPS = 6
# Bok komórki siatki przystanków, w stopniach. Musi być na tyle duży, żeby
# kwadrat 3x3 wokół stacji na pewno objął cały promień dojścia: 0,01° to
# ~1110 m wzdłuż południka i ~700 m wzdłuż równoleżnika na szerokości
# Wrocławia, więc gwarantowany zasięg to 700 m > gtfs.WALK_M.
BIKE_GRID_DEG = 0.01
# Górna granica prędkości czegokolwiek w tej sieci (pociąg podmiejski) -
# służy WYŁĄCZNIE jako dolne ograniczenie czasu dojazdu w linii prostej,
# czyli do odsiewania par stacji, które i tak nie mieszczą się w oknie.
# Zawyżona celowo: ograniczenie ma nie odrzucić niczego, co jest osiągalne.
BIKE_MAX_SPEED_MPS = 30.0


def _endpoint_point(day, stops, point):
    """Współrzędne końca relacji: kliknięty punkt albo środek jego słupków.

    Rower wymaga tego, czego reszta wyszukiwarki nie potrzebuje - dojście
    liczone w metrach. Przy relacji podanej z nazwy „gdzie stoi pasażer"
    nie jest znane w ogóle, więc bierzemy środek miejsca; przy kliknięciu
    w mapę znamy to dokładnie.
    """
    if point is not None:
        return point
    coords = [day.stop_coords[s] for s in stops if s in day.stop_coords]
    if not coords:
        return None
    return (sum(c[0] for c in coords) / len(coords),
            sum(c[1] for c in coords) / len(coords))


def _stop_grid(day):
    """Słupki w siatce kwadratów - żeby „co jest w zasięgu dojścia od tej
    stacji" nie było przemiataniem wszystkich czterech tysięcy słupków
    dla każdej z 273 stacji."""
    grid = {}
    for stop, (lat, lon) in day.stop_coords.items():
        cell = (int(lat // BIKE_GRID_DEG), int(lon // BIKE_GRID_DEG))
        grid.setdefault(cell, []).append(stop)
    return grid


def _near_stops(day, grid, lat, lon, max_m=None, limit=BIKE_NEAR_STOPS):
    """Najbliższe słupki w promieniu dojścia, od najbliższego: [(metry, słupek)]."""
    max_m = gtfs.WALK_M if max_m is None else max_m
    cx, cy = int(lat // BIKE_GRID_DEG), int(lon // BIKE_GRID_DEG)
    found = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for stop in grid.get((cx + dx, cy + dy), ()):
                slat, slon = day.stop_coords[stop]
                dist = bikes.haversine_m(lat, lon, slat, slon)
                if dist <= max_m:
                    found.append((dist, stop))
    found.sort()
    return found[:limit]


def _profile_best(day, profile, stop, arr_t):
    """Najwcześniejszy przyjazd do celu z profilu, jadąc dalej z `stop`
    (z sąsiadami, patrz _reach_from), i KTÓRYM słupkiem jedzie się dalej
    i o której się na nim staje.

    Sama wartość nie wystarczy, bo trasę trzeba potem odtworzyć etap po
    etapie - a odtworzenie musi wyjść z dokładnie tego słupka i tej godziny,
    z których policzono wartość, inaczej godziny na karcie nie zgadzałyby
    się z tym, po co ta karta w ogóle jest na liście.
    """
    neg_deps, arrs, _board = profile
    best, best_stop, best_t = INF, None, None
    for stop2, buffer in _reach_from(day, stop):
        # Bufor: przyjście na słupek nie jest jeszcze staniem przy właściwej
        # krawędzi.
        times = neg_deps.get(stop2)
        if times is None:
            continue
        i = bisect_right(times, -(arr_t + buffer)) - 1
        if i >= 0 and arrs[stop2][i] < best:
            best, best_stop, best_t = arrs[stop2][i], stop2, arr_t + buffer
    return best, best_stop, best_t


def _bike_boardings(day, grid, stations, earliest, dep_sec, deadline, origin,
                    bike_model):
    """Dla każdej stacji: najwcześniejszy moment, w którym można przy niej
    stanąć - i skąd się tam przyszło.

    Dwie drogi, obie przez DOJŚCIE (bo stacja stoi obok przystanku, nie na
    nim): wprost z punktu startu albo z dowolnego słupka, do którego dowozi
    komunikacja. Ta druga jest całym sekretem „roweru w środku trasy".
    """
    boardings = []
    floor_sec = bikes.ride_time_sec(bikes.MIN_RIDE_M, *bike_model)
    for station in stations:
        if not station["renting"] or station["bikes"] <= 0:
            continue
        best_t, source = INF, None
        if origin is not None:
            dist = bikes.haversine_m(origin[0], origin[1],
                                     station["lat"], station["lon"])
            if dist <= gtfs.WALK_M:
                best_t = dep_sec + gtfs.walk_time_sec(dist, day.walk_mps)
                source = ("origin", None)
        for dist, stop in _near_stops(day, grid, station["lat"], station["lon"]):
            reached = earliest.get(stop)
            if reached is None or reached > deadline:
                continue
            when = reached + gtfs.walk_time_sec(dist, day.walk_mps)
            if when < best_t:
                best_t, source = when, ("stop", stop)
        if source is None:
            continue
        # Nawet najkrótszy dopuszczalny przejazd musi się zmieścić w oknie -
        # inaczej ta stacja nie ma po co wchodzić do pętli po parach.
        if best_t + floor_sec > deadline:
            continue
        boardings.append((best_t, station, source))
    boardings.sort(key=lambda item: item[0])
    return boardings


def _bike_alightings(day, grid, stations, profile, target_set, dest):
    """Dla każdej stacji: czym można stąd jechać dalej i ile to NAJMNIEJ
    może potrwać.

    `tail_lb` jest dolnym ograniczeniem czasu od zwrotu roweru do celu -
    prawdziwa wartość zależy od godziny, ale ograniczenie nie, więc da się
    nim odsiewać pary stacji, zanim policzy się cokolwiek z rozkładu.
    """
    neg_deps = profile[0]
    alightings = []
    for station in stations:
        if not station["returning"] or station["docks"] <= 0:
            continue
        to_dest = None
        dist_dest = INF
        if dest is not None:
            dist_dest = bikes.haversine_m(dest[0], dest[1],
                                          station["lat"], station["lon"])
            if dist_dest <= gtfs.WALK_M:
                to_dest = gtfs.walk_time_sec(dist_dest, day.walk_mps)
        onward = []
        for dist, stop in _near_stops(day, grid, station["lat"], station["lon"]):
            if stop in target_set or any(s in neg_deps
                                         for s in _sibling_places(day, stop)):
                onward.append((gtfs.walk_time_sec(dist, day.walk_mps), stop))
        if to_dest is None and not onward:
            continue
        tail_lb = INF if to_dest is None else to_dest
        if onward:
            # Dalej jedzie się komunikacją, więc nie szybciej niż w linii
            # prostej najszybszym pojazdem w sieci - i nie szybciej, niż
            # trwa samo dojście na przystanek.
            by_transit = max(min(walk for walk, _ in onward),
                             dist_dest / BIKE_MAX_SPEED_MPS)
            tail_lb = min(tail_lb, by_transit)
        alightings.append({
            "station": station,
            "to_dest": to_dest,
            "onward": onward,
            "tail_lb": tail_lb,
        })
    return alightings


def _bike_candidates(day, boardings, alightings, profile, target_set, deadline, dest,
                     bike_model):
    """Pętla po parach stacji - najlepszy przyjazd do celu dla każdego
    KSZTAŁTU trasy z rowerem.

    Kształt to para „skąd się wsiadło na rower, dokąd się nim dojechało"
    w sensie rodzaju, nie konkretnej stacji: (start albo przystanek) x (cel
    albo przystanek). Wychodzą z tego cztery warianty - sam rower, rower na
    początku, rower na końcu, rower w środku - i po jednym, najlepszym
    przedstawicielu każdego z nich. Bez tego lista dostałaby kilka niemal
    identycznych propozycji z sąsiednich stacji tej samej okolicy.
    """
    best_by_shape = {}
    ride_mps, overhead_sec = bike_model
    max_ride_m = bikes.MAX_RIDE_SEC * ride_mps    # patrz bikes.MAX_RIDE_M
    for board_t, station_a, source in boardings:
        # Dolne ograniczenie dla CAŁEJ stacji A: cokolwiek się stąd zrobi,
        # do celu jest tyle a tyle metrów w linii prostej.
        if dest is not None:
            floor = bikes.haversine_m(station_a["lat"], station_a["lon"],
                                      dest[0], dest[1]) / BIKE_MAX_SPEED_MPS
            if board_t + overhead_sec + floor > deadline:
                continue
        for slot in alightings:
            station_b = slot["station"]
            if station_b["id"] == station_a["id"]:
                continue
            straight = bikes.haversine_m(station_a["lat"], station_a["lon"],
                                         station_b["lat"], station_b["lon"])
            if not bikes.MIN_RIDE_M <= straight <= max_ride_m:
                continue
            dock_t = board_t + bikes.ride_time_sec(straight, *bike_model)
            if dock_t + slot["tail_lb"] > deadline:
                continue

            arrival, tail = INF, None
            if slot["to_dest"] is not None:
                arrival, tail = dock_t + slot["to_dest"], ("dest", None, None)
            for walk, stop in slot["onward"]:
                at_stop = dock_t + walk
                if stop in target_set:
                    if at_stop < arrival:
                        arrival, tail = at_stop, ("dest", stop, None)
                    continue
                value, board_stop, board_at = _profile_best(
                    day, profile, stop, at_stop)
                if value < arrival:
                    arrival, tail = value, ("transit", stop, (board_stop, board_at))
            if arrival > deadline:
                continue

            shape = (source[0], tail[0])
            known = best_by_shape.get(shape)
            if known is None or arrival < known[0]:
                best_by_shape[shape] = (arrival, station_a, station_b, board_t,
                                        source, dock_t, tail)
    return sorted(best_by_shape.values(), key=lambda item: item[0])


def _foot_leg(from_name, to_name, from_point, to_point, seconds, note, dep_sec):
    """Etap pieszy podany wprost w metrach i sekundach - inaczej niż
    _walk_leg, który opisuje przejście między słupkami TEGO SAMEGO miejsca
    i ma przez to jeden, stały czas. Dojście do stacji roweru trwa tyle, ile
    wynika z odległości, i nie ma powodu tego uśredniać."""
    return {
        "kind": "walk",
        "text": f"{note} (ok. {seconds // 60} min)",
        # Front woli `note` od własnego opisu, gdy jest - „Przejście do
        # Stacja WRM ..." brzmiałoby jak nazwa przystanku (patrz app.js).
        "note": note,
        "minutes": seconds // 60,
        "from": from_name,
        "to": to_name,
        "dep_sec": dep_sec,
        "path": _round_path([from_point, to_point]),
    }


def _bike_ride_leg(station_a, station_b, start_sec, bike_model):
    """Etap „jedź rowerem miejskim ze stacji A do stacji B".

    Czas etapu to dwie różne rzeczy naraz i obie są tu widoczne osobno:
    wypożyczenie ze zwrotem (stoisz przy stojaku) i sam przejazd. Pasażer
    ma prawo wiedzieć, że z 14 minut dwie to nie jazda - inaczej pierwszy
    przegapiony autobus po drugiej stronie zrobi z tej propozycji kłamstwo.
    Liczone tym samym modelem co na mapie (bikes.ride_time_sec).
    """
    straight = bikes.haversine_m(station_a["lat"], station_a["lon"],
                                 station_b["lat"], station_b["lon"])
    ride_mps, overhead_sec = bike_model
    total = bikes.ride_time_sec(straight, ride_mps, overhead_sec)
    return {
        "kind": "bike",
        "line": "Rower miejski",
        "num": "WRM",
        "mode": "bike",
        "headsign": station_b["name"],
        "from": station_a["name"],
        "to": station_b["name"],
        "from_time": _fmt_time(start_sec),
        "to_time": _fmt_time(start_sec + total),
        "dep_sec": start_sec,
        "arr_sec": start_sec + total,
        # Wszystkie trzy liczby są w pełnych minutach i sumują się dokładnie
        # (patrz bikes._whole_minutes) - karta pokazuje je obok siebie.
        "minutes": total // 60,
        "ride_minutes": (total - overhead_sec) // 60,
        "overhead_minutes": overhead_sec // 60,
        "distance_m": int(round(bikes.ride_distance_m(straight))),
        # Stan stacji w chwili wyszukiwania - z tego samego kanału, z którego
        # wzięła się cała ta propozycja (patrz bikes.py). Stąd „6 rowerów"
        # na karcie: propozycja bez tej liczby każe iść pod stojak w ciemno.
        "bikes_available": station_a["bikes"],
        "docks_available": station_b["docks"],
        "station_from_id": station_a["id"],
        "station_to_id": station_b["id"],
        # Linia prosta, nie przebieg ulicami: nie mamy routera rowerowego,
        # a udawanie geometrii, której nie znamy, byłoby gorsze niż jej brak.
        # Front rysuje ten etap kreską przerywaną właśnie dlatego.
        "path": _round_path([(station_a["lat"], station_a["lon"]),
                             (station_b["lat"], station_b["lon"])]),
    }


def _bike_journey(day, candidate, source_stops, target_stops, dep_sec, deadline,
                  geo_db, origin, dest, start_name, end_name, bike_model):
    """Kandydat (para stacji + czasy) -> gotowa propozycja z etapami.

    Czasy liczymy TU jeszcze raz, do przodu, z faktycznie odtworzonych
    etapów - a nie przepisujemy tych z pętli po parach. Tamte pochodzą
    z dwóch skanów po całej sieci i są dobre do WYBORU pary; kartę ogląda
    się jednak minuta po minucie, więc musi się zgadzać sama ze sobą.
    """
    _arrival, station_a, station_b, _board_t, source, _dock_t, tail = candidate
    legs = []

    # 1. Dojazd do stacji A. Słupek, który sam jest startem relacji, nie
    # wymaga dojazdu - ale kotwicą dojścia zostaje ON, nie środek miejsca:
    # to z jego współrzędnych policzono, że stacja jest w zasięgu.
    if source[0] == "stop":
        stop = source[1]
        if stop in source_stops:
            from_point = day.stop_coords[stop]
            from_name, now = day.stop_names[stop], dep_sec
        else:
            reached, arr, journey = _scan(day, source_stops, {stop}, dep_sec,
                                          deadline=deadline)
            if reached is None:
                return None
            legs.extend(_reconstruct(day, journey, reached, geo_db))
            from_point = day.stop_coords[reached]
            from_name, now = day.stop_names[reached], arr
    else:
        if origin is None:
            return None
        from_point, from_name, now = origin, start_name, dep_sec

    to_station = bikes.haversine_m(from_point[0], from_point[1],
                                   station_a["lat"], station_a["lon"])
    if to_station > gtfs.WALK_M:
        return None
    legs.append(_foot_leg(
        from_name, station_a["name"], from_point,
        (station_a["lat"], station_a["lon"]),
        gtfs.walk_time_sec(to_station, day.walk_mps),
        f"Dojście do stacji WRM {station_a['name']}", now))
    now += gtfs.walk_time_sec(to_station, day.walk_mps)
    # Trasa, która zaczyna się dojściem do stacji, WYRUSZA wtedy, a nie
    # dopiero gdy coś odjeżdża (patrz _summarize_journey).
    start_sec = legs[0]["dep_sec"] if legs[0]["kind"] == "walk" else None

    # 2. Sam przejazd.
    ride = _bike_ride_leg(station_a, station_b, now, bike_model)
    legs.append(ride)
    now = ride["arr_sec"]

    # 3. Dalsza droga: pieszo do celu albo przesiadka na komunikację.
    if tail[0] == "dest":
        end_point = (day.stop_coords[tail[1]] if tail[1] is not None else dest)
        if end_point is None:
            return None
        walk_m = bikes.haversine_m(station_b["lat"], station_b["lon"],
                                   end_point[0], end_point[1])
        if walk_m > gtfs.WALK_M:
            return None
        seconds = gtfs.walk_time_sec(walk_m, day.walk_mps)
        legs.append(_foot_leg(
            station_b["name"], end_name, (station_b["lat"], station_b["lon"]),
            end_point, seconds, f"Dojście do celu: {end_name}", now))
        arrival = now + seconds
    else:
        drop_stop, (board_stop, _board_at) = tail[1], tail[2]
        if board_stop is None:
            return None
        walk_m = bikes.haversine_m(station_b["lat"], station_b["lon"],
                                   *day.stop_coords[drop_stop])
        seconds = gtfs.walk_time_sec(walk_m, day.walk_mps)
        legs.append(_foot_leg(
            station_b["name"], day.stop_names[drop_stop],
            (station_b["lat"], station_b["lon"]), day.stop_coords[drop_stop],
            seconds, f"Dojście na przystanek {day.stop_names[drop_stop]}", now))
        now += seconds
        # Ten sam bufor, którym liczył profil - patrz _profile_best.
        now += (TRANSFER_SEC if board_stop == drop_stop
                else gtfs.walk_seconds(day, drop_stop, board_stop))
        if board_stop != drop_stop:
            legs.append(_walk_leg(day, drop_stop, board_stop))
        reached, arrival, journey = _scan(day, {board_stop}, target_stops, now,
                                          deadline=deadline)
        if reached is None:
            return None
        legs.extend(_reconstruct(day, journey, reached, geo_db))

    if arrival > deadline:
        return None
    rides = [leg for leg in legs if leg["kind"] in ("ride", "bike")]
    journey = _summarize_journey(legs, rides, arrival, dep_sec,
                                 start_sec=start_sec)
    _drop_private(legs)
    return journey


def _bike_journeys(day, source_stops, target_stops, dep_sec, deadline, earliest,
                   profile, geo_db, start_point, end_point, start_name, end_name,
                   bike_model, limit=BIKE_JOURNEY_LIMIT):
    """Propozycje tras z rowerem miejskim - albo pusta lista.

    Zwraca (propozycje, liczba_stacji). Pusta lista jest odpowiedzią
    normalną, nie awarią: kanał operatora może nie odpowiadać (patrz
    bikes.stations_quiet), w okolicy może nie być stacji, a najczęściej po
    prostu żadne wstawienie roweru nie mieści się w oknie czasowym mapy -
    czyli rower nic tu nie daje. W każdym z tych przypadków reszta
    wyszukiwarki działa bez najmniejszej zmiany, a liczba stacji pozwala
    odróżnić "policzone i nic z tego" od "nie było czego liczyć".

    `bike_model` to (prędkość w linii prostej, narzut) pytającego - ten sam,
    którym liczy mapa (patrz bikes.ride_time_sec).
    """
    stations = bikes.stations_quiet()
    if not stations:
        return [], 0

    origin = _endpoint_point(day, source_stops, start_point)
    dest = _endpoint_point(day, target_stops, end_point)
    grid = _stop_grid(day)

    boardings = _bike_boardings(day, grid, stations, earliest, dep_sec,
                                deadline, origin, bike_model)
    if not boardings:
        return [], len(stations)
    alightings = _bike_alightings(day, grid, stations, profile, target_stops, dest)
    if not alightings:
        return [], len(stations)

    journeys, seen = [], set()
    for candidate in _bike_candidates(day, boardings, alightings, profile,
                                      target_stops, deadline, dest, bike_model):
        journey = _bike_journey(day, candidate, source_stops, target_stops,
                                dep_sec, deadline, geo_db, origin, dest,
                                start_name, end_name, bike_model)
        if journey is None:
            continue
        # Dwa różne KSZTAŁTY potrafią się zejść w tę samą trasę (np. stacja
        # tuż przy przystanku startowym - „rower od startu" i „rower po
        # jednym przystanku" wychodzą wtedy identycznie).
        signature = (journey["departure_sec"], journey["arrival_sec"],
                     tuple(leg.get("line", leg["kind"]) for leg in journey["legs"]))
        if signature in seen:
            continue
        seen.add(signature)
        journeys.append(journey)
        if len(journeys) >= limit:
            break
    return journeys, len(stations)


def _journey_key(journey, gain_sec):
    """Ten sam klucz, którym sortuje _enumerate_journeys: przyjazd z karą za
    każdą przesiadkę, remis po liczbie przesiadek, a potem po PÓŹNIEJSZYM
    wyjeździe (mniej czekania)."""
    return (journey["arrival_sec"] + journey["transfers"] * gain_sec,
            journey["transfers"], -journey["departure_sec"])


def _merge_journeys(journeys, extra, gain_sec):
    """Dokłada propozycje z rowerem do listy z mapy przepływów i układa
    wszystko w JEDNĄ kolejność. Zwraca (lista, ile roweru weszło).

    Rower podlega tu dokładnie tej samej regule co wszystko inne na liście:
    mieści się w oknie czasowym mapy (patrz _deadline) - wchodzi, i staje tam,
    gdzie mu wypada wg tego samego klucza (_journey_key). Gorsza opcja ląduje
    na dole listy, a nie znika - dokładnie tak, jak mapa od zawsze pokazuje
    też niszowe objazdy.

    Kuszące jest dołożyć tu drugi próg („rower wchodzi tylko wtedy, gdy
    WYGRYWA z najlepszym dojazdem bez niego"), bo rower kosztuje osobno:
    konto, dojście do stojaka, wypożyczenie, pedałowanie. Nie robimy tego
    z dwóch powodów. Po pierwsze, zgoda na ten koszt już padła - odhaczenie
    🚲 jest właśnie nią, więc drugi raz pytać o to nie ma po co. Po drugie
    i ważniejsze: taki próg sprawdzano by na danych, które zmieniają się co
    minutę (stan stojaków), a kandydaci potrafią stać dokładnie na jego
    styku - dwa wyszukania TEJ SAMEJ relacji w odstępie minuty dawałyby więc
    raz propozycję z rowerem, raz żadną. Z zewnątrz jest to nieodróżnialne od
    zepsutej funkcji. Sprawdzone na relacji Wojszyce -> pl. Grunwaldzki.
    """
    if not extra:
        return journeys, 0
    merged = journeys + extra
    merged.sort(key=lambda j: _journey_key(j, gain_sec))
    return merged, len(extra)


def _forward(day, source_stops, dep_sec, deadline, seated=None):
    """Jak _scan, ale bez celu: najwcześniejsze przyjazdy wszędzie do deadline.

    Zwraca (earliest, arrived_by, trip_board); trip_board[kurs] to indeks
    pierwszego połączenia, na które w ogóle da się zdążyć (właściwe miejsce
    wsiadania, z regułą postępu, wybiera dopiero plan_flow).
    """
    conns = day.conns
    earliest = {}
    arrived_by = {}     # 'origin' | 'ride' | 'walk' - do bufora przesiadki
    trip_board = {}

    for stop in source_stops:
        earliest[stop] = dep_sec
        arrived_by[stop] = "origin"
    # To samo wyjście pieszo ze startu, co w _scan - inaczej mapa przepływów
    # nie widziałaby kursów, do których wsiada się dopiero po dojściu.
    for stop, (_skad, sec) in _origin_walk(day, source_stops).items():
        earliest[stop] = dep_sec + sec
        arrived_by[stop] = "walk"

    for i in range(bisect_left(day.dep_times, dep_sec), len(conns)):
        dep_t, arr_t, dep_s, arr_s, trip = conns[i]
        if dep_t > deadline:
            break
        if trip not in trip_board:
            reached = earliest.get(dep_s)
            if reached is None:
                continue
            if reached + _board_buffer(arrived_by[dep_s], trip, seated) > dep_t:
                continue
            trip_board[trip] = i
        if arr_t < earliest.get(arr_s, INF):
            earliest[arr_s] = arr_t
            arrived_by[arr_s] = "ride"
            for sibling in day.siblings.get(arr_s, ()):
                walk_arr = arr_t + gtfs.walk_seconds(day, arr_s, sibling)
                if walk_arr < earliest.get(sibling, INF):
                    earliest[sibling] = walk_arr
                    arrived_by[sibling] = "walk"
    return earliest, arrived_by, trip_board


def _backward(day, target_set, dep_sec, deadline):
    """Skan wstecz: najpóźniejszy moment na każdym przystanku, z którego
    da się jeszcze dotrzeć do celu przed deadline.

    Połączenia przetwarzamy malejąco po odjeździe - wszystko, co wpływa na
    latest[przystanek] po czasie t, jest już policzone, zanim do t dojdziemy.
    """
    conns = day.conns
    latest = {stop: deadline for stop in target_set}
    # Dojście pieszo DO celu. Skan wstecz cofa się połączeniami, więc bez
    # tego zasiewu w ogóle nie wie, że stojąc kilkaset metrów od celu jest
    # się już właściwie na miejscu. Dopóki most pieszy łączył wyłącznie
    # słupki jednej nazwy, nie było czego zasiewać - sąsiedzi celu SAMI byli
    # celem (match_stop oddaje całe miejsce). Od kiedy pieszo przechodzi się
    # między różnymi przystankami (gtfs._nearby_bridges), przestało tak być,
    # a skutek był dotkliwy i cichy: stacja Wrocław Wojszyce dostawała
    # `latest` policzone z jakiegoś objazdu autobusem zamiast z pociągu,
    # który stąd dowozi wprost pod Dworzec Główny.
    for stop, (sec, _cel) in _target_reach(day, target_set).items():
        latest[stop] = deadline - sec
    # `latest` to najpóźniejsza z dwóch dróg dalej, ale po wysiadce kosztują
    # one co innego: przesiadka na TYM SAMYM słupku - bufor przesiadki, dalej
    # pieszo - sam marsz. Tak samo liczy skan w przód (_scan, _forward)
    # i profil (_reach_from). Dopóki tu bufor doliczało się zawsze, mapa
    # odrzucała przesiadki, które wyszukiwanie uznawało: setka na pętlę GAJ
    # o 13:51, trzy minuty pieszo na osiemnastkę z Morwowej o 13:54
    # (22.09, odsiew krążenia zaczynał mapę dokładnie od tej trasy, więc
    # z mapy nie zostawało nic i wpadała w tryb awaryjny).
    board = {}                  # słupek -> najpóźniejsze wsiadanie na nim
    on_foot = dict(latest)      # słupek -> najpóźniej stąd pieszo (albo cel)
    trip_ok = set()

    for i in range(bisect_left(day.dep_times, deadline) - 1, -1, -1):
        dep_t, arr_t, dep_s, arr_s, trip = conns[i]
        if dep_t < dep_sec:
            break
        if trip not in trip_ok:
            if not (arr_t <= on_foot.get(arr_s, -1)
                    or arr_t + TRANSFER_SEC <= board.get(arr_s, -1)):
                continue
            trip_ok.add(trip)
        if dep_t > board.get(dep_s, -1):
            board[dep_s] = dep_t
            if dep_t > latest.get(dep_s, -1):
                latest[dep_s] = dep_t
            for sibling in day.siblings.get(dep_s, ()):
                walk_dep = dep_t - gtfs.walk_seconds(day, sibling, dep_s)
                if walk_dep > on_foot.get(sibling, -1):
                    on_foot[sibling] = walk_dep
                    if walk_dep > latest.get(sibling, -1):
                        latest[sibling] = walk_dep
    return latest


def _unknown_stop(query, hints):
    result = {"error": f"Nie znam przystanku „{query.strip()}”."}
    if hints:
        result["suggestions"] = hints
    return result


def _fmt_time(sec):
    hours = sec // 3600
    if hours >= 24:                    # kursy po północy zapisane jako 24:xx, 25:xx
        hours -= 24
    return f"{hours:02d}:{(sec % 3600) // 60:02d}"
