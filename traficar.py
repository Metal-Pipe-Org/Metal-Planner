"""Pozycje samochodów Traficar (car-sharing) do naniesienia na mapę.

Dane pobieramy na żywo z publicznego API projektu Traficar Map
(https://fioletowe.live, kod: github.com/divadsn/traficar-map), które udostępnia
aktualne pozycje dostępnych aut. Bierzemy auta ze strefy Wrocławia i doklejamy
czytelne nazwy modeli. Zawsze świeże zapytanie - bez cache i bez danych
zastępczych: gdy API jest niedostępne, zwracamy pustą listę.
"""

import json
import os
import urllib.request

API_BASE = os.environ.get("TRAFICAR_API_BASE", "https://fioletowe.live")
ZONE_ID = int(os.environ.get("TRAFICAR_ZONE_ID", "3"))   # 3 = Wrocław


def positions():
    """Aktualne dostępne auta Traficar w strefie (na żywo). Lista obiektów
    {"name", "model", "plate", "lat", "lon", "fuel", "range", "location"};
    pusta, gdy API nie odpowiada."""
    try:
        payload = _get(f"/api/v1/cars?zoneId={ZONE_ID}")
    except Exception:
        return []

    names = _model_names()
    cars = []
    for item in payload.get("cars", []):
        if item.get("available") is False:
            continue
        lat = _to_float(item.get("lat"))
        lon = _to_float(item.get("lng"))
        if lat is None or lon is None:
            continue
        cars.append({
            "name": item.get("regPlate") or "",
            "model": names.get(item.get("modelId")) or "Traficar",
            "plate": item.get("regPlate") or "",
            "lat": lat,
            "lon": lon,
            "fuel": item.get("fuel"),
            "range": item.get("range"),
            "location": item.get("location") or "",
        })
    return cars


def _get(path):
    req = urllib.request.Request(API_BASE + path, headers={"User-Agent": "Metal-Planner"})
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.load(resp)


def _model_names():
    """modelId -> nazwa modelu; pusty słownik, gdy zapytanie zawiedzie."""
    try:
        payload = _get("/api/v1/car-models")
        return {m["id"]: m["name"] for m in payload.get("carModels", [])}
    except Exception:
        return {}


def _to_float(value):
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None
