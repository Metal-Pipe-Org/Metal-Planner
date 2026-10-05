"""Ciemny motyw (zgłoszenie #206).

Wybór stoi w sekcji „Wygląd aplikacji” panelu ⚙, a kolory żyją wyłącznie
w zmiennych na górze style.css - ciemny motyw nadpisuje tylko je, więc kolor
wpisany wprost w regułę zostałby jasny.
"""

import re
from pathlib import Path

import pytest
from flask import Flask, render_template

ROOT = Path(__file__).resolve().parent.parent
COLOR = re.compile(r"#[0-9a-fA-F]{3,8}\b|rgba?\(")


def _index(dev_mode):
    app = Flask(__name__, template_folder=str(ROOT / "templates"),
                static_folder=str(ROOT / "static"))
    with app.test_request_context("/"):
        return render_template("index.html", stops=[], abbreviations={},
                               lines=[], data_error=None, form_time="12:00",
                               form_date="2026-10-01", dev_mode=dev_mode)


def _css(name):
    """Arkusz bez komentarzy - numery zgłoszeń (#206) to nie kolory."""
    return re.sub(r"/\*.*?\*/", "", (ROOT / "static" / name).read_text(), flags=re.S)


def _block(css, selector):
    start = css.index(selector + " {")
    return css[start:css.index("}", start)]


def _vars(block):
    return set(re.findall(r"(--[\w-]+):", block))


@pytest.mark.parametrize("dev_mode", [False, True])
def test_wybor_w_layoucie_niezaleznie_od_trybu(dev_mode):
    html = _index(dev_mode)
    start = html.index('id="fold-layout"')
    layout = html[start:html.index("</details>", start)]
    select = re.search(r'<select id="theme">(.*?)</select>', layout, re.S).group(1)
    assert re.findall(r'value="(\w+)"', select) == ["auto", "light", "dark"]
    assert "selected" not in select


def test_motyw_stawiany_przed_rysowaniem():
    html = _index(dev_mode=False)
    head = html[:html.index("</head>")]
    assert "applyTheme" in head
    assert "prefers-color-scheme: dark" in head


def test_ciemny_nadpisuje_tylko_istniejace_zmienne():
    css = _css("style.css")
    dark = _vars(_block(css, ':root[data-theme="dark"]'))
    assert dark <= _vars(_block(css, ":root"))
    assert {"--surface", "--ink", "--map-tiles"} <= dark


@pytest.mark.parametrize("name", ["style.css", "phone.css"])
def test_kolory_tylko_w_zmiennych(name):
    css = _css(name)
    for selector in (":root", ':root[data-theme="dark"]'):
        if selector + " {" in css:
            css = css.replace(_block(css, selector), "")
    assert COLOR.findall(css) == []
