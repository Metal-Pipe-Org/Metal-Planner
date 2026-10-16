import hashlib
import http.client
import json
import math
from datetime import datetime
from functools import partial
from pathlib import Path

from flask import Response, abort, jsonify, render_template, request
from werkzeug.exceptions import RequestEntityTooLarge

import bikes
import config
import gtfs
import naming
import onboard
import pkp
import pkp_live
import polinka
import siechnice
import timetables
import traficar
import update_gtfs
import vehicles
import warmup
from planner import (TIMETABLE_LIMIT, TIMETABLE_MAX, plan_flow_stages,
                     plan_route, stop_timetable)

# Ile najwyżej ostatni etap wyszukiwania czeka na świeże rowery i auta - wynik
# jest już na ekranie, więc to czekanie nikogo nie blokuje, ale wątek serwera
# trzyma. Ponad timeouty feedów (10 s, patrz traficar._fetch).
LIVE_REFRESH_WAIT_SEC = 20

# Tydzień rozkładu Siechnic to ~3 MB; sufit tylko po to, żeby przypadkowo
# wybrany duży plik nie lądował cały w pamięci workera.
SIECHNICE_MAX_BYTES = 30 * 1024 * 1024


def _frontend_digest(app):
    """Odcisk zawartości frontu - wersja cache'ów service workera.

    Podstawiany w `sw.js` zamiast ręcznie podbijanego numeru: zmiana
    czegokolwiek w `static/` albo w szablonie zmienia treść workera, więc
    przeglądarka widzi nową wersję, a ta przy aktywacji kasuje stare cache'e.
    To odpowiednik hashowanych nazw plików z bundlerów - tylko liczony
    w locie, bez build stepu.

    Liczone z zawartości, nie z dat - w kontenerze po każdym buildzie daty
    są nowe, a pliki te same. Koszt: ~0,3 ms na kilkanaście plików, raz
    na nawigację.
    """
    files = sorted(Path(app.static_folder).rglob("*"))
    files.append(Path(app.root_path) / "templates" / "index.html")

    digest = hashlib.sha256()
    for path in files:
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def _finite(text):
    """float() bez "nan" i "inf" - Python przyjmuje je jako liczby, a dalej
    każde int() albo porównanie z nimi kończy się błędem 500."""
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(text)
    return value


def _float_arg(name):
    """Liczba z query stringa albo None (planner podstawi wtedy domyślną)."""
    try:
        return _finite(request.args.get(name, ""))
    except ValueError:
        return None


def _point_arg(prefix):
    """Para (lat, lon) z `<prefix>_lat`/`<prefix>_lon` - klik w dowolny punkt mapy."""
    try:
        return (
            _finite(request.args[f"{prefix}_lat"]),
            _finite(request.args[f"{prefix}_lon"]),
        )
    except (KeyError, ValueError):
        return None


def _latlon_arg():
    """Para (lat, lon) z `lat`/`lon` - punkt wskazany wprost, bez prefiksu."""
    try:
        return _finite(request.args["lat"]), _finite(request.args["lon"])
    except (KeyError, ValueError):
        return None


def _parse_when(time_str,data_str):
    """Godzina 'HH:MM' z formularza -> datetime dzisiaj o tej porze (domyślnie teraz)."""
    when = datetime.now()
    time_str = (time_str or "").strip()
    data_str = (data_str or "").strip()
    if time_str:
        try:
            hours, minutes = time_str.split(":")
            when = when.replace(hour=int(hours), minute=int(minutes), second=0)
        except ValueError:
            pass
    if data_str:
        try:
            year,month,day = data_str.split("-")
            when = when.replace(day=int(day), month=int(month),year=int(year))
        except ValueError:
            pass
    return when


def _day_arg():
    """Data z query stringa (YYYY-MM-DD) jako `date`; brak/śmieci = dzisiaj.

    Tryb rozkładów pyta o całą dobę, a nie o moment, więc w przeciwieństwie
    do wyszukiwarki nie potrzebuje godziny (patrz _parse_when).
    """
    return _parse_when(None, request.args.get("date")).date()


def _int_arg(name):
    try:
        return int(request.args[name])
    except (KeyError, ValueError):
        return None


def _onboard_arg():
    """Start z pokładu pojazdu z `onboard_num`/`onboard_mode`/`onboard_headsign`/
    `onboard_stop` albo None (patrz onboard.py).

    Wystarczy numer linii i słupek, przy którym pojazd zaraz stanie - reszta
    tylko zawęża wybór kursu. `onboard_stop` to identyfikator słupka z
    /api/onboard, a nie nazwa: pasażer jedzie jedną krawędzią przystanku,
    a nie całym placem, i tylko na niej otworzą się drzwi.

    `onboard_slack_sec` to próg trybu względnego z ⚙ (zgłoszenie #231, patrz
    onboard.find_ride); bez niego obowiązuje domyślny.
    """
    num = (request.args.get("onboard_num") or "").strip()
    stop = (request.args.get("onboard_stop") or "").strip()
    if not num or not stop:
        return None
    return {
        "num": num,
        "mode": request.args.get("onboard_mode") or None,
        "headsign": request.args.get("onboard_headsign") or None,
        "stop": stop,
        "slack_sec": _float_arg("onboard_slack_sec"),
    }


def init_routes(app):

    @app.route("/")
    def index():
        try:
            stops = gtfs.all_stop_names()
            lines = timetables.all_lines()
            data_error = None
        except FileNotFoundError as e:
            stops = []
            lines = []
            data_error = str(e)

        # Stacje PKP (patrz pkp.py) w tej samej liście podpowiedzi co
        # przystanki MPK - to jedyne miejsce, gdzie formularz w ogóle
        # dowiaduje się, że taka nazwa istnieje (samo wyszukiwanie już zna
        # obie sieci jednakowo - patrz gtfs.load_day/pkp.augment_day - to
        # tu tylko podpowiedzi, zanim ktokolwiek cokolwiek wpisze).
        # `pkp.all_station_names()` nigdy nie rzuca (pusta lista bez
        # bazy/klucza), więc bez try/except.
        #
        # `kind` jedzie osobno od samej nazwy (nie doklejone do stringa) -
        # front dokłada z niego plakietkę "PKP" w podpowiedziach (patrz
        # static/app.js), ale do pola wyszukiwania i tak wstawia samą nazwę:
        # doklejenie "PKP" wprost do nazwy zepsułoby dopasowanie po stronie
        # wyszukiwarki, która zna stację tylko pod jej prawdziwą nazwą.
        # Nazwa, która trafia do OBU list (MPK i PKP - w praktyce nie
        # zdarza się w tych danych, ale nie ma gwarancji, że nigdy), zostaje
        # bez plakietki: to nie tylko stacja kolejowa, więc oznaczenie
        # "PKP" byłoby mylące.
        gtfs_names = set(stops)
        pkp_names = set(pkp.all_station_names())
        train_only = pkp_names - gtfs_names
        stops = [
            {"name": name, "kind": "train" if name in train_only else "stop"}
            for name in sorted(gtfs_names | pkp_names)
        ]
        # Stacje Polinki (zgłoszenie #165) - do podpowiedzi jak każdy słupek.
        stops += [{"name": s["name"], "kind": "cable"} for s in polinka.stations_geo()]
        stops.sort(key=lambda s: s["name"])

        return render_template(
            "index.html",
            stops=stops,
            abbreviations=naming.ABBREVIATIONS,
            lines=lines,
            data_error=data_error,
            form_time=datetime.now().strftime("%H:%M"),
            # ISO, bo tego i tylko tego wymaga <input type="date"> - przy
            # formacie dziennym pole zostawało puste i data nie docierała
            # do serwera wcale.
            form_date=datetime.now().strftime("%Y-%m-%d"),
            dev_mode=config.dev_mode(),
        )

    @app.route("/sw.js")
    def service_worker():
        """Service worker musi jechać z korzenia - z /static/ obejmowałby
        zasięgiem tylko statyki, a nie całą aplikację. Przy okazji wstrzykujemy
        wersję cache'ów, żeby nie trzeba było jej pamiętać ręcznie."""
        source = Path(app.static_folder, "sw.js").read_text(encoding="utf-8")
        response = app.response_class(
            source.replace("__VERSION__", _frontend_digest(app)),
            mimetype="text/javascript",
        )
        # Bez tego nowa wersja workera potrafi wisieć w cache przeglądarki.
        response.headers["Cache-Control"] = "no-cache"
        return response

    @app.route("/healthz")
    def healthz():
        """Sonda dla dockerowego HEALTHCHECK - żyje też bez bazy rozkładów."""
        return jsonify({"status": "ok"})

    @app.route("/api/stops")
    def api_stops():
        try:
            stops = [{**s, "kind": "stop"} for s in gtfs.all_stops_geo()]
        except FileNotFoundError as e:
            return jsonify({"error": str(e)}), 503
        # Stacje PKP z ustalonymi współrzędnymi (patrz pkp.all_stations_geo -
        # dogadane osobno przez geokodowanie, update_pkp.py) dostają marker
        # na mapie tak jak słupki MPK, tylko oznaczone `kind: "train"`, żeby
        # front mógł je odróżnić stylem (patrz static/app.js). To jedyne
        # miejsce, gdzie PKP i MPK są traktowane inaczej - bo tylko to
        # naprawdę je różni (markery), nie samo wyszukiwanie tras.
        stops += [{**s, "kind": "train"} for s in pkp.all_stations_geo()]
        # Stacje Polinki (zgłoszenie #165) - własny rodzaj, własny kolor.
        stops += [{**s, "kind": "cable"} for s in polinka.stations_geo()]
        # Nazwa miejsca dopiero na całej liście: DWORZEC GŁÓWNY i stacja
        # Wrocław Główny to jedno miejsce, więc i jeden podpis na mapie.
        return jsonify(gtfs.with_place_names(stops))

    @app.route("/api/vehicles")
    def api_vehicles():
        """Żywe pozycje autobusów/tramwajów (mpk.wroc.pl/bus_position, patrz
        vehicles.py) - warstwa włączana przyciskiem ◉ w pasku warstw.

        Odpowiedź niesie same pozycje: front zawęża je do linii narysowanych na
        mapie i na tym koniec. Odstawione dopisywanie rozkładu kursu, którym
        dany pojazd jedzie, czeka zakomentowane w vehicles.py.
        """
        try:
            return jsonify({"vehicles": vehicles.get_vehicles()})
        except FileNotFoundError as e:
            return jsonify({"error": str(e)}), 503
        except (OSError, ValueError) as e:
            return jsonify({"error": f"Nie udało się pobrać pozycji pojazdów: {e}"}), 503

    @app.route("/api/cars")
    def api_cars():
        """Wszystkie wolne auta Traficara we Wrocławiu - warstwa 🚗 włączana
        w pasku warstw, gdy na mapie nie ma jeszcze wyszukiwania. Z narysowaną
        mapą auta przychodzą razem z nią (patrz plan_flow), bo tam niosą też
        godzinę dojścia; tutaj jest tylko to, co feed wie sam z siebie."""
        try:
            return jsonify({"cars": traficar.car_list()})
        except traficar.TraficarDataError as e:
            return jsonify({"error": f"Nie udało się pobrać aut: {e}"}), 503

    @app.route("/api/traficar-zone")
    def api_traficar_zone():
        """Strefa, w której da się oddać auto Traficara - warstwa włączana
        w pasku warstw (zgłoszenie #157, patrz traficar.zone)."""
        shapes = traficar.zone()
        if shapes is None:
            return jsonify({"error": "Nie udało się pobrać strefy Traficara"}), 503
        return jsonify(shapes)

    @app.route("/api/bikes")
    def api_bikes():
        """Wszystkie stacje WRM i rowery stojące luzem - warstwa 🚲 w pasku
        warstw, bez związku z wyszukiwaniem (patrz api_cars)."""
        try:
            return jsonify({"stations": bikes.stations(), "free": bikes.free_bikes()})
        except (OSError, ValueError, http.client.HTTPException) as e:
            return jsonify({"error": f"Nie udało się pobrać rowerów: {e}"}), 503

    @app.route("/api/plan")
    def api_plan():
        return jsonify(plan_route(
            request.args.get("start", ""),
            request.args.get("end", ""),
            _parse_when(request.args.get("time"),request.args.get("date")),
            transfer_gain_sec=_float_arg("transfer_gain_sec"),
        ))

    @app.route("/api/line")
    def api_line():
        """Rozkład jednej linii: warianty trasy, przystanki, kursy, geometria."""
        return jsonify(timetables.line_timetable(
            request.args.get("num", ""),
            _day_arg(),
            request.args.get("mode") or None,
            trip=request.args.get("trip") or None,
        ))

    @app.route("/api/onboard")
    def api_onboard():
        """Kierunki linii i ich przystanki - pod wybór „jestem w pojeździe"
        (patrz onboard.py). To samo źródło co /api/line, tylko pogrupowane po
        napisie z czoła pojazdu i bez geometrii: ten wybór niczego nie rysuje,
        a przebieg trasy jest najcięższą częścią tamtej odpowiedzi."""
        return jsonify(onboard.directions(
            request.args.get("num", ""),
            _day_arg(),
            request.args.get("mode") or None,
        ))

    @app.route("/api/onboard/trains")
    def api_onboard_trains():
        """Podpowiedzi numeru pociągu w polu „linia" (zgłoszenie #167) -
        pociągi wybranego dnia, których numer zaczyna się od wpisanych cyfr."""
        try:
            day = gtfs.load_day(_day_arg())
        except FileNotFoundError:
            return jsonify({"error": "Nie udało się wczytać danych rozkładu."}), 503
        return jsonify({"trains": onboard.trains(day, request.args.get("q", ""))})

    @app.route("/api/train_delays")
    def api_train_delays():
        """Opóźnienia pociągów z pokazanych tras (zgłoszenie #71, patrz
        pkp_live.py) - front pyta o nie osobno, PO narysowaniu wyniku, więc
        lista tras nigdy na nie nie czeka."""
        trips = [t for t in request.args.get("trips", "").split(",") if t]
        when = _day_arg()
        try:
            day = gtfs.load_day(when)
        except FileNotFoundError:
            return jsonify({"error": "Nie udało się wczytać danych rozkładu."}), 503
        return jsonify({"delays": pkp_live.delays(day, trips, when)})

    @app.route("/api/stop_board")
    def api_stop_board():
        """Tablica odjazdów z jednego przystanku - wszystkie linie naraz."""
        return jsonify(timetables.stop_board(
            request.args.get("stop", ""),
            _day_arg(),
        ))

    @app.route("/api/trip")
    def api_trip():
        """Jeden kurs: przystanki z godzinami i przebieg na mapie."""
        return jsonify(timetables.trip_detail(
            request.args.get("trip", ""),
            _day_arg(),
            request.args.get("stop") or None,
            _int_arg("dep"),
        ))

    @app.route("/api/timetable")
    def api_timetable():
        """Tablica odjazdów przystanku - dymek pod kropką przesiadki na mapie.

        `from_sec` to godzina na osi doby rozkładowej (patrz gtfs.load_day):
        front podaje ją wprost z etapu trasy, żeby przesiadka po północy
        pytała o właściwą dobę.
        """
        # `limit` z zapytania: mapa przepływów odsiewa potem linie, których
        # z tego miejsca i tak nie proponuje, więc musi dostać z zapasem.
        limit = _float_arg("limit")
        return jsonify(stop_timetable(
            request.args.get("stop", ""),
            _parse_when(request.args.get("time"), request.args.get("date")),
            _float_arg("from_sec"),
            limit=min(int(limit), TIMETABLE_MAX) if limit else TIMETABLE_LIMIT,
            point=_latlon_arg(),
            until_sec=_float_arg("until_sec"),
        ))

    @app.route("/api/flow")
    def api_flow():
        # Ani jedna wzmianka o PKP tutaj - patrz pkp.py: kursy kolejowe są
        # doklejone wprost do tablicy połączeń, którą wczytuje gtfs.load_day
        # (wołane z wnętrza plan_flow), więc dla tego endpointu to zwykłe
        # wyszukiwanie MPK, tylko z szerszą siecią pod spodem.
        #
        # Odpowiedź idzie ETAPAMI, po jednej linijce JSON na etap (NDJSON,
        # zgłoszenie #229, patrz planner.plan_flow_stages): pasek z najszybszą
        # trasą jest na ekranie po ułamku sekundy, mapa z listą po nim,
        # a rowery i auta na końcu. Parametry czytamy od razu - generator biegnie już po wyjściu
        # z tej funkcji, gdzie `request` nie ma.
        #
        # Na rowery i auta wyszukiwanie nie czeka (patrz warmup.py): liczy na
        # tym, co jest w pamięci, a gdy to było stare, po wysłaniu wyniku
        # czeka na świeże i - jeśli coś zmieniły - dosyła je ostatnim etapem.
        # Strona przerywa odczyt, gdy pyta już o co innego; serwer kończy
        # wtedy przy najbliższej granicy etapów, na nieudanym zapisie.
        stages = _flow_stages()

        def stream():
            with warmup.no_waiting() as search:
                def refreshed():
                    if not search.stale:
                        return False
                    warmup.wait_for_refreshes(LIVE_REFRESH_WAIT_SEC)
                    return True
                for stage, delta in stages(live_refreshed=refreshed):
                    yield json.dumps({"stage": stage, **delta},
                                     ensure_ascii=False, separators=(",", ":")) + "\n"

        # X-Accel-Buffering: nginx przed serwerem trzymałby inaczej wszystkie
        # etapy do końca odpowiedzi i całe stopniowanie by znikło.
        return Response(stream(), mimetype="application/x-ndjson",
                        headers={"X-Accel-Buffering": "no",
                                 "Cache-Control": "no-store"})

    def _flow_stages():
        return partial(
            plan_flow_stages,
            request.args.get("start", ""),
            request.args.get("end", ""),
            _parse_when(request.args.get("time"),request.args.get("date")),
            _point_arg("start"),
            _point_arg("end"),
            # Docelowa gęstość mapy (suwak pod zębatką) i ile razy kliknięto
            # "pokaż więcej" - z nich planner dobiera próg. Sufity obu pilnuje
            # planner, nie front.
            density=_float_arg("density"),
            more=_float_arg("more"),
            # Ile aut Traficara przy mapie (suwak pod zębatką) - "pokaż
            # więcej" mnoży ją w plannerze tak samo jak gęstość.
            car_count=_float_arg("cars"),
            # Dostawczaki dopiero na życzenie - przełącznik pod zębatką.
            car_vans=request.args.get("car_vans") == "1",
            # To samo dla przejazdów rowerem - osobny suwak (punkt 16).
            bike_count=_float_arg("bike_count"),
            # Na jaki rodzaj roweru pasażer chce wsiąść - dwa przyciski w pasku
            # warstw. Brak parametru znaczy "oba" (inaczej niż przy
            # dostawczakach, których domyślnie nie ma).
            bike_electric=request.args.get("bike_electric", "1") == "1",
            bike_regular=request.args.get("bike_regular", "1") == "1",
            transfer_gain_sec=_float_arg("transfer_gain_sec"),
            # Rower miejski jest wyborem pasażera, nie ustawieniem serwera:
            # bez konta w WRM propozycja z rowerem jest bezużyteczna, więc
            # wchodzi do odpowiedzi tylko wtedy, gdy front o nią poprosi
            # (przełącznik 🚲 w karcie wyszukiwania).
            use_bikes=request.args.get("bikes") == "1",
            # Propozycje kończące się Traficarem - zgaszony przycisk 🚗 w pasku
            # warstw wysyła "0" i znikają z listy (zgłoszenie #163). Brak
            # parametru znaczy, że są, tak jak dotąd.
            use_cars=request.args.get("traficar", "1") == "1",
            # Start z pokładu pojazdu (patrz onboard.py): zamiast "skąd" jedzie
            # linia, kierunek i NASTĘPNY przystanek. Bez numeru linii nie ma
            # o czym mówić, więc reszta parametrów jest wtedy ignorowana -
            # to zwykłe wyszukiwanie.
            in_vehicle=_onboard_arg(),
            # Założenia czasowe pytającego - sekcja pod zębatką (zgłoszenie
            # #151). Brak to dzisiejsze wartości; sufity pilnuje planner.
            walk_pace=request.args.get("walk_pace") or None,
            bike_kmh=_float_arg("bike_kmh"),
            bike_overhead_sec=_float_arg("bike_overhead_sec"),
            # To samo dla jazdy Traficarem (zgłoszenie #150): z tego mapa
            # szacuje, o której auto dowiezie do celu.
            car_kmh=_float_arg("car_kmh"),
            car_overhead_sec=_float_arg("car_overhead_sec"),
            # Propozycje tras wyłączone pod zębatką - "0" i serwer ich nie
            # składa wcale. Brak parametru znaczy, że są.
            with_journeys=request.args.get("routes", "1") == "1",
        )

    @app.route("/api/dev/siechnice", methods=["GET", "POST"])
    def api_dev_siechnice():
        """Wgranie kopii rozkładu Siechnic (⚙ → „Wgraj rozkład Siechnic”).

        Tylko w trybie deweloperskim - poza nim endpointu po prostu nie ma.
        POST zapisuje plik do folderu ./siechnice i od razu odpala przebudowę bazy w tle;
        GET mówi, czy przebudowa trwa i czy obecna baza ma już wgrany plik.
        """
        if not config.dev_mode():
            abort(404)

        if request.method == "POST":
            # Limit tylko na tej trasie, nie na całej aplikacji. Werkzeug pilnuje go
            # przy czytaniu strumienia, więc nie obejdzie go ani zaniżony
            # Content-Length, ani kodowanie chunked.
            request.max_content_length = SIECHNICE_MAX_BYTES
            try:
                upload = request.files.get("file")
            except RequestEntityTooLarge:
                return jsonify({"error": "Plik jest za duży jak na tydzień rozkładu."}), 413
            if upload is None:
                return jsonify({"error": "Nie wybrano pliku."}), 400
            reason = siechnice.save_snapshot(upload.read(), siechnice.SNAPSHOT_PATH)
            if reason:
                return jsonify({"error": reason}), 400
            update_gtfs.start_update_in_background()

        path = siechnice.SNAPSHOT_PATH
        uploaded = path.stat().st_mtime if path.exists() else None
        built = update_gtfs.DB_PATH.stat().st_mtime if update_gtfs.DB_PATH.exists() else None
        return jsonify({
            "uploaded_at": uploaded and datetime.fromtimestamp(uploaded).isoformat(timespec="seconds"),
            "rebuilding": update_gtfs.update_running(),
            "in_database": bool(uploaded and built and built >= uploaded),
        })
