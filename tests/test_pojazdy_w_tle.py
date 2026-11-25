"""Pozycje pojazdów śledzone w tle (#380): wątek odświeża kanał MPK także bez
widzów, rzadziej, a żądanie oddaje to, co jest w pamięci. Bez prawdziwego MPK:
pobranie i zegar są podstawione."""

import math
import time
import types

import pytest

import serverlog
import vehicles

RAW = [{"type": "bus", "name": "146", "x": 51.1, "y": 17.0, "k": 1}]


@pytest.fixture
def mpk(monkeypatch):
    """Licznik pobrań z MPK; `clock[0]` to monotoniczny zegar modułu."""
    clock = [1000.0]
    calls = []
    answers = []

    def fetch(fields):
        calls.append(clock[0])
        answer = answers.pop(0) if answers else RAW
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(vehicles, "_fetch_raw", fetch)
    monkeypatch.setattr(vehicles, "_query_fields", lambda: [])
    monkeypatch.setattr(vehicles, "_add_courses", lambda vs: None)
    fake_time = types.SimpleNamespace(monotonic=lambda: clock[0], time=time.time)
    monkeypatch.setattr(vehicles, "time", fake_time)
    monkeypatch.setattr(vehicles, "_cache", {"at": 0.0, "tried": -math.inf, "vehicles": None, "same_since": None,
                                            "snapshot": None, "watched": -math.inf, "failed": None})
    monkeypatch.setattr(vehicles, "_tracks", {})
    logged = []

    class _Log:
        def error(self, message, *args):
            logged.append(message % args)

    monkeypatch.setattr(serverlog, "get_logger", _Log)
    monkeypatch.setattr(serverlog, "time", fake_time)
    monkeypatch.setattr(serverlog, "_streak", {"reason": None, "message": None, "count": 0, "since": 0.0, "logged": 0.0})
    return clock, calls, answers, logged


def test_without_viewers_the_feed_is_refreshed_every_idle_period(mpk):
    clock, calls, _, _ = mpk
    for _ in range(12):
        clock[0] += vehicles._track_once()
    gaps = {round(b - a) for a, b in zip(calls, calls[1:])}
    assert gaps == {vehicles.IDLE_SEC}


def test_while_someone_watches_it_is_refreshed_every_cache_period(mpk):
    clock, calls, _, _ = mpk
    vehicles.get_vehicles()
    for _ in range(5):
        clock[0] += vehicles._track_once()
    gaps = {round(b - a) for a, b in zip(calls, calls[1:])}
    assert gaps == {vehicles.CACHE_SEC}


def test_a_request_on_fresh_data_does_not_ask_mpk(mpk):
    clock, calls, _, _ = mpk
    vehicles._track_once()
    clock[0] += vehicles.CACHE_SEC - 1
    vehicles.get_vehicles()
    assert len(calls) == 1


def test_the_first_viewer_after_a_break_gets_motion_already_tracked(mpk):
    """Pojazd przesuwał się, gdy nikt nie patrzył - pierwsze żądanie po
    przerwie ma już dwa przesunięcia, z których bierze się kierunek."""
    clock, _, answers, _ = mpk
    answers += [[{**RAW[0], "x": 51.1 + i * 0.001}] for i in range(3)]
    for _ in range(3):
        clock[0] += vehicles._track_once()
    assert len(vehicles._tracks[1]["pts"]) == 3


def test_a_failing_feed_is_not_asked_again_at_once(mpk):
    clock, calls, answers, _ = mpk
    vehicles._track_once()
    clock[0] += vehicles.IDLE_SEC
    answers.append(OSError("zerwane"))
    pause = vehicles._track_once()
    assert pause >= vehicles.IDLE_SEC - 1
    clock[0] += 1
    vehicles.get_vehicles()           # stare pozycje, bez nowego pytania
    assert len(calls) == 2


def test_an_intermittent_feed_is_one_entry_in_the_log(mpk):
    """Kanał zrywający co drugie pobranie przez noc to jedna awaria w logu,
    zamknięta dopiero po FEED_OK_SEC bez błędu."""
    clock, _, answers, logged = mpk
    for i in range(40):
        answers.append(OSError("zerwane") if i % 2 else RAW)
        clock[0] += vehicles._track_once()
    assert logged == ["Pozycje pojazdów z MPK: pobranie nie powiodło się - OSError"]
    for _ in range(vehicles.FEED_OK_SEC // vehicles.IDLE_SEC + 1):
        clock[0] += vehicles._track_once()
    assert len(logged) == 2 and "powtórzone 19 razy" in logged[1]
