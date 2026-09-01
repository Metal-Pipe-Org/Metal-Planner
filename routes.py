import hashlib
from datetime import datetime
from pathlib import Path

from flask import jsonify, render_template, request

import gtfs
import pkp
from planner import (TIMETABLE_LIMIT, TIMETABLE_MAX, plan_flow, plan_route,
                     stop_timetable)


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


def _float_arg(name):
    """Liczba z query stringa albo None (planner podstawi wtedy domyślną)."""
    try:
        return float(request.args.get(name, ""))
    except ValueError:
        return None


def _point_arg(prefix):
    """Para (lat, lon) z `<prefix>_lat`/`<prefix>_lon` - klik w dowolny punkt mapy."""
    try:
        return (
            float(request.args[f"{prefix}_lat"]),
            float(request.args[f"{prefix}_lon"]),
        )
    except (KeyError, ValueError):
        return None


def _latlon_arg():
    """Para (lat, lon) z `lat`/`lon` - punkt wskazany wprost, bez prefiksu."""
    try:
        return float(request.args["lat"]), float(request.args["lon"])
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


def init_routes(app):

    @app.route("/")
    def index():
        try:
            stops = gtfs.all_stop_names()
            data_error = None
        except FileNotFoundError as e:
            stops = []
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

        return render_template(
            "index.html",
            stops=stops,
            data_error=data_error,
            form_time=datetime.now().strftime("%H:%M"),
            form_date=datetime.now().strftime("%d.%m.%y"),
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
        return jsonify(stops)

    @app.route("/api/plan")
    def api_plan():
        return jsonify(plan_route(
            request.args.get("start", ""),
            request.args.get("end", ""),
            _parse_when(request.args.get("time"),request.args.get("date")),
            transfer_gain_sec=_float_arg("transfer_gain_sec"),
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
        ))

    @app.route("/api/flow")
    def api_flow():
        # Ani jedna wzmianka o PKP tutaj - patrz pkp.py: kursy kolejowe są
        # doklejone wprost do tablicy połączeń, którą wczytuje gtfs.load_day
        # (wołane z wnętrza plan_flow), więc dla tego endpointu to zwykłe
        # wyszukiwanie MPK, tylko z szerszą siecią pod spodem.
        return jsonify(plan_flow(
            request.args.get("start", ""),
            request.args.get("end", ""),
            _parse_when(request.args.get("time"),request.args.get("date")),
            _point_arg("start"),
            _point_arg("end"),
            _float_arg("range_m"),
            extra_pct=_float_arg("extra_pct"),
            extra_floor_sec=_float_arg("extra_floor_sec"),
            extra_cap_sec=_float_arg("extra_cap_sec"),
            transfer_gain_sec=_float_arg("transfer_gain_sec"),
        ))
