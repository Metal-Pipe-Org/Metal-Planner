from datetime import datetime

from flask import render_template, request

import gtfs
from planner import plan_route


def init_routes(app):

    @app.route("/", methods=["GET", "POST"])
    def index():
        route = None

        try:
            stops = gtfs.all_stop_names()
            data_error = None
        except FileNotFoundError as e:
            stops = []
            data_error = str(e)

        now = datetime.now()
        form_time = now.strftime("%H:%M")

        if request.method == "POST" and data_error is None:
            when = now
            time_str = request.form.get("time", "").strip()
            if time_str:
                try:
                    hours, minutes = time_str.split(":")
                    when = now.replace(hour=int(hours), minute=int(minutes), second=0)
                    form_time = time_str
                except ValueError:
                    pass

            route = plan_route(
                request.form.get("start", ""),
                request.form.get("end", ""),
                when,
            )

        return render_template(
            "index.html",
            route=route,
            stops=stops,
            data_error=data_error,
            form_time=form_time,
            form_start=request.form.get("start", ""),
            form_end=request.form.get("end", ""),
        )
