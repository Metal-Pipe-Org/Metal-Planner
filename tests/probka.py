"""Zapisana próbka danych na żywo (2026-11-06 12:33): rowery WRM (GBFS nextbike)
i Traficary (fioletowe.live) - do testów bez sieci. Pliki leżą w
tests/probka_rower_traficar/; free_bike_status.json jest pusty (rowery spoza
stacji nie są tu potrzebne, a prawdziwy plik ma megabajt)."""

import datetime
import json
from pathlib import Path

import bikes
import planner
import traficar

KATALOG = Path(__file__).parent / "probka_rower_traficar"
DZIEN = datetime.date(2026, 11, 6)


def _plik(nazwa):
    return json.loads((KATALOG / nazwa).read_text(encoding="utf-8"))


def _rowery(url):
    return _plik(url.rsplit("/", 1)[1])


def _traficar(url):
    if "/car-models" in url:
        return _plik("car-models.json")
    if "/shapes" in url:
        return _plik("zone-shapes.json")
    return _plik("cars.json")


def podlacz(monkeypatch):
    """Rowery i Traficar czytają próbkę zamiast sieci, a „dzisiaj" to dzień
    próbki (warstwy na żywo liczą się tylko przy pytaniu o dziś)."""
    monkeypatch.setattr(bikes, "_fetch", _rowery)
    monkeypatch.setattr(traficar, "_fetch", _traficar)
    monkeypatch.setattr(bikes, "enabled", lambda: True)
    monkeypatch.setattr(traficar, "enabled", lambda: True)
    monkeypatch.setattr(bikes, "_cache", {})
    monkeypatch.setattr(traficar, "_cars_cache", {"at": 0.0, "cars": [], "generation": 0})
    monkeypatch.setattr(traficar, "_models_cache", {"at": 0.0, "models": {}})
    monkeypatch.setattr(traficar, "_zone_cache", {"at": 0.0, "zone": None})

    class _Dzis(datetime.date):
        @classmethod
        def today(cls):
            return DZIEN
    monkeypatch.setattr(planner, "date", _Dzis)
