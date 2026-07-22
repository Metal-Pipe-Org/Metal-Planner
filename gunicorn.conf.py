"""Konfiguracja serwera aplikacyjnego w kontenerze.

Jeden worker to świadomy wybór, nie zaniedbanie: rozkład całego dnia leży
w pamięci procesu (~60 MB, a _day_cache w gtfs.py trzyma do dwóch dni), więc
każdy kolejny worker to kolejna pełna kopia - z interpreterem i cache
geometrii ok. 150-200 MB. Współbieżność załatwiają wątki, które ten cache
współdzielą.

WEB_CONCURRENCY podnoś tylko z zapasem RAM-u i pamiętaj, że automatyczna
aktualizacja rozkładu (GTFS_AUTO_UPDATE_HOUR) wystartuje wtedy w każdym workerze.
"""

import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
workers = int(os.environ.get("WEB_CONCURRENCY", "1"))
threads = int(os.environ.get("GUNICORN_THREADS", "8"))
# Wyszukiwanie przepływów na zimnym cache potrafi zająć kilkanaście sekund.
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "120"))
accesslog = "-"
errorlog = "-"
