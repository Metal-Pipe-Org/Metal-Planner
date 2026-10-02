"""Sekcje ⚙ dla każdego i tylko dla dewelopera (zgłoszenie #175).

Sekcja DEV nosi klasę `dev-only` i znaczek; bez `DEV_MODE` strona nie ma
klasy `dev-mode` na <body>, więc style.css ją chowa.
"""

import re
from pathlib import Path

import pytest
from flask import Flask, render_template

ROOT = Path(__file__).resolve().parent.parent

USER_FOLDS = {"fold-map", "fold-assumptions", "fold-layout"}
DEV_FOLDS = {"fold-window", "fold-time", "fold-places", "fold-vehicles",
             "fold-experiments", "fold-debug", "look-section", "fold-version",
             "fold-transfer"}


def _index(dev_mode):
    app = Flask(__name__, template_folder=str(ROOT / "templates"),
                static_folder=str(ROOT / "static"))
    with app.test_request_context("/"):
        return render_template("index.html", stops=[], abbreviations={},
                               lines=[], data_error=None, form_time="12:00",
                               form_date="2026-10-01", dev_mode=dev_mode)


def _folds(html):
    """id sekcji -> (klasy, treść aż do </details>)."""
    out = {}
    for m in re.finditer(r'<details class="([^"]*)" id="([^"]*)"', html):
        out[m.group(2)] = (m.group(1).split(),
                           html[m.end():html.index("</details>", m.end())])
    return out


def test_tryb_deweloperski_na_body():
    assert '<body class="dev-mode">' in _index(dev_mode=True)
    assert "<body>" in _index(dev_mode=False)


@pytest.mark.parametrize("dev_mode", [False, True])
def test_podzial_sekcji_niezaleznie_od_trybu(dev_mode):
    folds = _folds(_index(dev_mode))
    dev = {fid for fid, (classes, _) in folds.items() if "dev-only" in classes}
    assert dev == DEV_FOLDS
    assert USER_FOLDS <= set(folds) - dev
    for fid, (classes, body) in folds.items():
        assert ('class="dev-badge"' in body) == (fid in DEV_FOLDS), fid


@pytest.mark.parametrize("fold, inputs", [
    ("fold-map", ["time-show-headline", "tt-past", "car-vans"]),
    ("fold-assumptions", ["walk-pace", "bike-kmh", "bike-overhead",
                          "car-kmh", "car-overhead"]),
    ("fold-layout", ["bikes-merged", "no-pull-refresh",
                     "start-mode-switch", "routes-on"]),
])
def test_ustawienia_uzytkownika(fold, inputs):
    _, body = _folds(_index(dev_mode=False))[fold]
    for input_id in inputs:
        assert f'id="{input_id}"' in body


def test_wyglad_startu_to_eksperyment():
    html = _index(dev_mode=True)
    assert 'id="dot-start"' not in html
    _, body = _folds(html)["fold-experiments"]
    tag = re.search(r'<input[^>]*id="old-ends"[^>]*>', body).group(0)
    assert "checked" not in tag
