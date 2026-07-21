from datetime import datetime

from flask import jsonify, render_template, request

import bikes
import gtfs
from planner import plan_flow, plan_route


def _parse_when(time_str):
    """Godzina 'HH:MM' z formularza -> datetime dzisiaj o tej porze (domyślnie teraz)."""
    when = datetime.now()
    time_str = (time_str or "").strip()
    if time_str:
        try:
            hours, minutes = time_str.split(":")
            when = when.replace(hour=int(hours), minute=int(minutes), second=0)
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

        return render_template(
            "index.html",
            stops=stops,
            data_error=data_error,
            form_time=datetime.now().strftime("%H:%M"),
        )

    @app.route("/api/stops")
    def api_stops():
        try:
            return jsonify(gtfs.all_stops_geo())
        except FileNotFoundError as e:
            return jsonify({"error": str(e)}), 503

    @app.route("/api/bikes")
    def api_bikes():
        try:
            return jsonify(bikes.station_list())
        except bikes.BikeDataError as e:
            return jsonify({"error": str(e)}), 503

    @app.route("/api/plan")
    def api_plan():
        return jsonify(plan_route(
            request.args.get("start", ""),
            request.args.get("end", ""),
            _parse_when(request.args.get("time")),
        ))

    @app.route("/api/flow")
    def api_flow():
        try:
            q_min = float(request.args.get("qmin", ""))
        except ValueError:
            q_min = None
        return jsonify(plan_flow(
            request.args.get("start", ""),
            request.args.get("end", ""),
            _parse_when(request.args.get("time")),
            q_min,
        ))
