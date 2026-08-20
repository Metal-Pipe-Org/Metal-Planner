"""Konfiguracja serwera aplikacyjnego w kontenerze.

Jeden worker to świadomy wybór, nie zaniedbanie: rozkład całego dnia leży
w pamięci procesu (~60 MB, a _day_cache w gtfs.py trzyma do dwóch dni), więc
każdy kolejny worker to kolejna pełna kopia - z interpreterem i cache
geometrii ok. 150-200 MB. Współbieżność załatwiają wątki, które ten cache
współdzielą.

WEB_CONCURRENCY podnoś tylko z zapasem RAM-u. Automatycznej aktualizacji
rozkładu (GTFS_AUTO_UPDATE_HOUR) to nie dotyczy: harmonogram startuje
w procesie master, w on_starting na dole tego pliku, więc jest jeden
niezależnie od liczby workerów.
"""

import os

import update_gtfs

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
workers = int(os.environ.get("WEB_CONCURRENCY", "1"))
threads = int(os.environ.get("GUNICORN_THREADS", "8"))
# Wyszukiwanie przepływów na zimnym cache potrafi zająć kilkanaście sekund.
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "120"))
accesslog = "-"
errorlog = "-"

# Control socket (gunicornc) doszedł w 25.1.0 i domyślnie lądowałby w $HOME.
# W kontenerze entrypoint schodzi na użytkownika app przez gosu, które nie
# zmienia HOME - ten nadal wskazuje na /root, więc próba założenia gniazda
# kończy się błędem w logu przy każdym starcie. Serwer wstaje mimo to, ale
# pożytku z gniazda tu nie ma, więc go nie tworzymy.
control_socket_disable = True


def on_starting(server):
    """Master, raz na kontener, przed forkiem workerów.

    Stąd, a nie z app.py: app.py wykonuje się w każdym workerze osobno, więc
    przy WEB_CONCURRENCY > 1 mielibyśmy tyle harmonogramów, ile workerów -
    i tyle równoległych przebudów bazy o 3:00.
    """
    update_gtfs.start_daily_scheduler()
