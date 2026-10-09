"""Żeby wyszukiwanie nie czekało: rozkład dnia wczytany z góry, a dane na żywo
odświeżane w tle (zgłoszenie #229).

Pierwsze wyszukiwanie po starcie czekało na wczytanie rozkładu (ok. 1 s),
a każde po chwili ciszy - na pobranie rowerów WRM i aut Traficara z cudzych
serwerów, po kolei, zanim policzyło cokolwiek (0,5-2 s). Samo liczenie trasy
trwało przy tym ułamek sekundy.

Rozkład: jeden wątek trzyma w pamięci rozkład dnia - od startu, po nocnej
podmianie bazy (gtfs.load_day sam pozna nową bazę po jej mtime) i od 23:00
także dnia następnego, żeby pierwsze pytanie po północy nie czekało.

Dane na żywo: bez odpytywania w tle bez przerwy (decyzja użytkownika) -
pyta dalej tylko wyszukiwanie i nie częściej niż dotąd (te same czasy
ważności kanałów). Zmienia się tylko to, że wyszukiwanie (no_waiting) na nie
nie czeka: liczy na tym, co jest w pamięci, choćby starym, a świeże pobierają
się obok (refresh_in_background). Odpowiedź mówi wtedy, że dane się
pobierają, a strona dopytuje o tę samą trasę z `fresh=1` - takie pytanie
czeka już na koniec pobierania (wait_for_refreshes) i dostaje wynik na
świeżych danych (patrz routes.api_flow i loadPlan w app.js).

Wątek rozkładu startuje w procesie, który obsługuje zapytania: z
post_worker_init w gunicorn.conf.py (master tylko forkuje - wątek w nim nie
przeszedłby do workerów) i z app.py przy uruchomieniu lokalnym.
"""

import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta

import gtfs

DAYS_TICK_SEC = 60
NEXT_DAY_FROM_HOUR = 23

_pending = set()
_pending_changed = threading.Condition()
_search = threading.local()


class _Search:
    stale = False     # czy któreś dane na żywo były stare albo ich nie było


@contextmanager
def no_waiting():
    """W tym bloku (jedno wyszukiwanie w jednym wątku) dane na żywo nie
    czekają na sieć. Zwraca obiekt, którego `stale` mówi po wszystkim, czy
    wynik policzono na starych albo brakujących danych."""
    search = _Search()
    _search.current = search
    try:
        yield search
    finally:
        _search.current = None


def waiting_allowed():
    """Czy dane na żywo wolno tu pobrać od razu - poza wyszukiwaniem tak."""
    return getattr(_search, "current", None) is None


def refresh_in_background(name, refresh):
    """Woła `refresh` w osobnym wątku, chyba że ten sam kanał (`name`) już
    się pobiera - wtedy nic: dwa wyszukiwania naraz nie pobiorą go dwa razy.
    Błąd jest cichy: kanał zostaje stary, a następne wyszukiwanie spróbuje
    znowu, tak jak dotąd próbowało przy każdym."""
    current = getattr(_search, "current", None)
    if current is not None:
        current.stale = True
    with _pending_changed:
        if name in _pending:
            return
        _pending.add(name)

    def run():
        try:
            refresh()
        except Exception:
            pass
        finally:
            with _pending_changed:
                _pending.discard(name)
                _pending_changed.notify_all()

    threading.Thread(target=run, name=f"refresh-{name}", daemon=True).start()


def wait_for_refreshes(timeout_sec):
    """Czeka, aż skończą się pobierania z refresh_in_background, najwyżej
    `timeout_sec`."""
    with _pending_changed:
        _pending_changed.wait_for(lambda: not _pending, timeout_sec)


def days_to_warm(now):
    """Dni, których rozkład ma czekać w pamięci o godzinie `now`."""
    today = now.date()
    if now.hour >= NEXT_DAY_FROM_HOUR:
        return [today, today + timedelta(days=1)]
    return [today]


def _warm_days():
    try:
        for day in days_to_warm(datetime.now()):
            gtfs.load_day(day)
    except FileNotFoundError:
        pass     # bazy jeszcze nie ma - wyszukiwanie powie o tym samo
    except Exception as e:
        # Wątek, który zdechnie na wyjątku, przestaje trzymać rozkład
        # w pamięci aż do restartu - logujemy i próbujemy za minutę.
        print(f"Rozgrzewka: błąd wczytania rozkładu: {e}", file=sys.stderr, flush=True)


def _loop():
    while True:
        _warm_days()
        time.sleep(DAYS_TICK_SEC)


def start():
    """Uruchamia wątek rozkładu. Raz na proces obsługujący zapytania."""
    thread = threading.Thread(target=_loop, name="warmup", daemon=True)
    thread.start()
    return thread
