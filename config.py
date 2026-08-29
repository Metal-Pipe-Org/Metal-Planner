"""Wczytuje data/.env do zmiennych środowiskowych.

Sekrety (klucz API PKP) nie mogą trafić do repozytorium ani do obrazu
dockerowego, a jednocześnie muszą być pod ręką przy zwykłym `python app.py`.
Stąd plik: data/.env leży w tym samym wolumenie co baza rozkładów, więc na
serwerze wystarczy go położyć obok gtfs.sqlite - bez przebudowy obrazu
i bez zmian w docker-compose.yml. Wzór do skopiowania to data/.env.example.

Zmienne już ustawione w środowisku mają pierwszeństwo (override=False):
wpisy z `environment:` w docker-compose.yml i doraźne
`PKP_API_KEY=... python app.py` przebijają plik, a nie odwrotnie.

Import tego modułu wystarczy - wczytanie dzieje się przy nim, a Python
importuje moduł raz na proces. Robią to wszystkie trzy wejścia do aplikacji:
app.py (lokalnie), gunicorn.conf.py (kontener) i update_gtfs.py (uruchamiany
osobno przez docker/entrypoint.sh i z crona).
"""

import os
from pathlib import Path

from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parent / "data" / ".env"

# Brak pliku to nie błąd: na czystym klonie i w kontenerze bez sekretów
# aplikacja ma wstać normalnie, po prostu bez klucza.
load_dotenv(ENV_PATH, override=False)


def pkp_api_key():
    """Klucz do API PKP albo None, jeśli nie ustawiono.

    Puste `PKP_API_KEY=` z szablonu traktujemy jak brak - inaczej wołający
    musiałby sprawdzać i None, i pusty napis.
    """
    return os.environ.get("PKP_API_KEY", "").strip() or None


# Ile odjazdów pokazuje dymek pod kropką przesiadki. Wartość dobrana tak, żeby
# dymek mieścił się bez przewijania - podnosząc ją, sprawdź, czy nie zasłania
# mapy, o którą się właśnie pyta.
TIMETABLE_ROWS_DEFAULT = 8
TIMETABLE_ROWS_MAX = 20


def timetable_rows():
    """Liczba odjazdów w dymku (TIMETABLE_ROWS z .env albo środowiska).

    Sufit jest po to, żeby literówka w .env nie zamieniła dymka w pełnoekranowy
    rozkład jazdy przykrywający mapę.
    """
    try:
        rows = int(os.environ.get("TIMETABLE_ROWS", "").strip())
    except ValueError:
        return TIMETABLE_ROWS_DEFAULT
    return max(1, min(rows, TIMETABLE_ROWS_MAX))
