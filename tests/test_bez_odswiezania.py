"""Opcja „Wyłącz odświeżanie przeciągnięciem w dół" (zgłoszenie #170).

Stoi w sekcji Layout panelu ⚙, a ta sekcja ma być na każdej instancji -
także bez `DEV_MODE`, czyli na produkcji.
"""

import re
from pathlib import Path

import pytest
from flask import Flask, render_template

ROOT = Path(__file__).resolve().parent.parent


def _index(dev_mode):
    app = Flask(__name__, template_folder=str(ROOT / "templates"),
                static_folder=str(ROOT / "static"))
    with app.test_request_context("/"):
        return render_template("index.html", stops=[], abbreviations={},
                               lines=[], data_error=None, form_time="12:00",
                               form_date="2026-10-01", dev_mode=dev_mode)


@pytest.mark.parametrize("dev_mode", [False, True])
def test_opcja_w_layoucie_niezaleznie_od_trybu(dev_mode):
    html = _index(dev_mode)
    start = html.index('id="fold-layout"')
    layout = html[start:html.index("</details>", start)]
    assert 'id="no-pull-refresh"' in layout
    assert 'id="dev-toggle"' in html


def test_domyslnie_wylaczona():
    html = _index(dev_mode=False)
    tag = re.search(r'<input[^>]*id="no-pull-refresh"[^>]*>', html).group(0)
    assert "checked" not in tag
