import hashlib
from datetime import datetime
from pathlib import Path

from flask import jsonify, render_template, request

import gtfs
import timetables
from planner import plan_flow, plan_route


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

        return render_template(
            "index.html",
            stops=stops,
            lines=lines,
            data_error=data_error,
            form_time=datetime.now().strftime("%H:%M"),
            # ISO, bo tego i tylko tego wymaga <input type="date"> - przy
            # formacie dziennym pole zostawało puste i data nie docierała
            # do serwera wcale.
            form_date=datetime.now().strftime("%Y-%m-%d"),
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
            return jsonify(gtfs.all_stops_geo())
        except FileNotFoundError as e:
            return jsonify({"error": str(e)}), 503

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
        ))

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

    @app.route("/api/flow")
    def api_flow():
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
