"""Ustawienia dla każdego pod ☰ (zgłoszenie #170).

Opcje zwykłego użytkownika stoją w osobnej karcie, nie w panelu ⚙ - ten
zostaje dla zespołu.
"""

import re
from pathlib import Path

from flask import Flask, render_template

ROOT = Path(__file__).resolve().parent.parent


def _index():
    app = Flask(__name__, template_folder=str(ROOT / "templates"),
                static_folder=str(ROOT / "static"))
    with app.test_request_context("/"):
        return render_template("index.html", stops=[], abbreviations={},
                               lines=[], data_error=None, form_time="12:00",
                               form_date="2026-10-01", dev_mode=False)


def _block(html, start, end):
    return html[html.index(start):html.index(end, html.index(start))]


def test_przycisk_ustawien_w_naglowku():
    html = _index()
    assert 'id="settings-toggle"' in html
    assert 'id="settings"' in html


def test_odswiezanie_przeciagnieciem_w_ustawieniach_nie_w_dev():
    html = _index()
    settings = _block(html, '<dialog id="settings"', "</dialog>")
    assert 'id="no-pull-refresh"' in settings
    assert html.count('id="no-pull-refresh"') == 1


def test_odswiezanie_przeciagnieciem_domyslnie_wylaczone():
    html = _index()
    tag = re.search(r'<input[^>]*id="no-pull-refresh"[^>]*>', html).group(0)
    assert "checked" not in tag


def test_ustawienia_pod_osobnym_kluczem():
    js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert "metal-planner:user-prefs" in js
    # „Przywróć domyślne" w ⚙ czyści tylko klucze deweloperskie.
    reset = js[js.index("$('dev-reset')"):]
    reset = reset[:reset.index("location.reload()")]
    assert "USER_PREFS_KEY" not in reset
