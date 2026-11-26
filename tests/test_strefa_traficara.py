"""Strefa oddawania aut Traficara (zgłoszenie #157).

Wziąć auto da się spod każdego miejsca, ale zostawić - tylko w strefie. Mapa
auta i tak pokazuje, a gdy cel leży poza strefą, mówi o tym w dymku auta.

Feed fioletowe.live jest tu zawsze podstawiony (patrz tests/conftest.py).
"""

import copy
from types import SimpleNamespace

import traficar
from tests.test_trafikary_na_mapie import CAR, E, _flow

# Prawdziwe pobieranie strefy - conftest podmienia je na "nieznana".
_real_zone = traficar.zone
# Pamięć świeżego procesu: moduły testów wczytują się przed pierwszym testem.
_PO_STARCIE = copy.deepcopy(traficar._zone_cache)


def _square(lat, lon, half):
    """Kwadrat jako wielokąt GeoJSON (lon, lat) o środku w (lat, lon)."""
    return [[[lon - half, lat - half], [lon + half, lat - half],
             [lon + half, lat + half], [lon - half, lat + half],
             [lon - half, lat - half]]]


# Strefa wokół celu E z wyciętym środkiem, w którym oddać nie wolno.
ZONE = {"end": [_square(*E, 0.05)], "no_end": [_square(*E, 0.001)]}


def _zone(monkeypatch, zone):
    monkeypatch.setattr(traficar, "zone", lambda: zone)


def test_w_strefie_mozna_oddac(monkeypatch):
    _zone(monkeypatch, ZONE)
    assert traficar.can_end_at(E[0] + 0.01, E[1]) is True


def test_poza_strefa_nie_mozna(monkeypatch):
    _zone(monkeypatch, ZONE)
    assert traficar.can_end_at(E[0] + 0.1, E[1]) is False


def test_wyciete_miejsce_w_strefie_sie_nie_liczy(monkeypatch):
    _zone(monkeypatch, ZONE)
    assert traficar.can_end_at(*E) is False


def test_nieznana_strefa_to_brak_odpowiedzi(monkeypatch):
    _zone(monkeypatch, None)
    assert traficar.can_end_at(*E) is None


def test_strefa_z_feedu_rozdzielona_po_typach(monkeypatch):
    """Typ 1 - wolno oddać, typ 2 - wycięte, typ 3 - cele relokacji."""
    feed = {"shapes": [
        {"name": "A", "type": 1, "geo": {"type": "MultiPolygon", "coordinates": [[1]]}},
        {"name": "B", "type": 2, "geo": {"type": "MultiPolygon", "coordinates": [[2]]}},
        {"name": "C", "type": 3, "geo": {"type": "MultiPolygon", "coordinates": [[3]]}},
    ]}
    monkeypatch.setattr(traficar, "_fetch", lambda url: feed)
    monkeypatch.setattr(traficar, "_zone_cache", {"at": -traficar.ZONE_TTL_SEC,
                                                  "zone": None})
    assert _real_zone() == {"end": [[1]], "no_end": [[2]], "relocation": [[3]]}


def test_zaraz_po_starcie_maszyny_strefa_sie_pobiera(monkeypatch):
    """Zegar pobrań liczy od startu maszyny. Strefa, której jeszcze nikt nie
    pobrał, nie jest świeża tylko dlatego, że maszyna działa krócej niż doba -
    inaczej przez tę dobę nie byłoby ostrzeżenia o celu poza strefą, a warstwa
    strefy pokazywałaby błąd."""
    feed = {"shapes": [
        {"name": "A", "type": 1, "geo": {"type": "MultiPolygon", "coordinates": [[1]]}},
    ]}
    monkeypatch.setattr(traficar, "_fetch", lambda url: feed)
    monkeypatch.setattr(traficar, "time", SimpleNamespace(monotonic=lambda: 60.0))
    monkeypatch.setattr(traficar, "_zone_cache", copy.deepcopy(_PO_STARCIE))
    assert _real_zone() == {"end": [[1]], "no_end": [], "relocation": []}


def test_mapa_ostrzega_o_celu_poza_strefa(install_day, monkeypatch):
    _zone(monkeypatch, {"end": [_square(0, 0, 1)], "no_end": []})
    result = _flow(install_day, monkeypatch)
    assert [car["plate"] for car in result["cars"]] == [CAR["plate"]]
    assert result["cars_dest_in_zone"] is False


def test_mapa_nie_ostrzega_o_celu_w_strefie(install_day, monkeypatch):
    _zone(monkeypatch, {"end": [_square(*E, 0.05)], "no_end": []})
    result = _flow(install_day, monkeypatch)
    assert result["cars"]
    assert result["cars_dest_in_zone"] is True
