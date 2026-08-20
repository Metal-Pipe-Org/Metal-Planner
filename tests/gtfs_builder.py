"""Budowa syntetycznego gtfs.DayData do testów - bez SQLite, z pełną kontrolą
nad przystankami/kursami, żeby móc precyzyjnie i deterministycznie
odtworzyć konkretne scenariusze (patrz tests/test_flow_map_contract.py)."""

import gtfs

BASE_LAT, BASE_LON = 51.11, 17.03   # okolice Wrocławia - współrzędne tu nieistotne


def make_day(trips, names=None, siblings=None):
    """trips: [{"trip_id", "label", "headsign"?, "shape_id"?,
                "stops": [(stop_id, arrival_sec, departure_sec), ...]}, ...]
    names: {stop_id: nazwa wyświetlana} (domyślnie = stop_id)
    siblings: {stop_id: (inny_stop_id, ...)} - most pieszy między słupkami
    tego samego miejsca (patrz gtfs.DayData.siblings / _walking_bridges).
    """
    day = gtfs.DayData()

    stop_ids = []
    for trip in trips:
        for stop_id, _, _ in trip["stops"]:
            if stop_id not in day.stop_names and stop_id not in stop_ids:
                stop_ids.append(stop_id)

    names = names or {}
    for i, stop_id in enumerate(stop_ids):
        name = names.get(stop_id, stop_id)
        day.stop_names[stop_id] = name
        day.stop_coords[stop_id] = (BASE_LAT + i * 0.001, BASE_LON + i * 0.001)
        key = name.casefold()
        day.stops_by_key.setdefault(key, []).append(stop_id)
        day.display_name.setdefault(key, name)
        day.stops_by_norm_key.setdefault(key, []).append(stop_id)
        day.norm_display_name.setdefault(key, name)
        day.stops_by_place.setdefault(key, []).append(stop_id)
        day.place_of[stop_id] = key

    day.siblings = dict(siblings or {})

    conns = []
    for trip in trips:
        trip_id = trip["trip_id"]
        day.trip_info[trip_id] = (trip["label"], trip.get("headsign", ""))
        if trip.get("shape_id"):
            day.trip_shape[trip_id] = trip["shape_id"]
        rows = trip["stops"]
        for (s1, _, dep1), (s2, arr2, _) in zip(rows, rows[1:]):
            conns.append((dep1, arr2, s1, s2, trip_id))

    conns.sort(key=lambda c: c[0])
    day.conns = conns
    day.dep_times = [c[0] for c in conns]
    return day
