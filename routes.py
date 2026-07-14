from flask import render_template, request
from planner import plan_route


def init_routes(app):

    @app.route("/", methods=["GET", "POST"])
    def index():
        route = None

        if request.method == "POST":
            start = request.form.get("start")
            end = request.form.get("end")

            route = plan_route(start, end)

        return render_template("index.html", route=route)