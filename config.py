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

import json
import os
from pathlib import Path

from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parent / "data" / ".env"
DISCORD_IDS_PATH = ENV_PATH.parent / "discord.json"

# Brak pliku to nie błąd: na czystym klonie i w kontenerze bez sekretów
# aplikacja ma wstać normalnie, po prostu bez klucza.
load_dotenv(ENV_PATH, override=False)


def pkp_api_key():
    """Klucz do API PKP albo None, jeśli nie ustawiono.

    Puste `PKP_API_KEY=` z szablonu traktujemy jak brak - inaczej wołający
    musiałby sprawdzać i None, i pusty napis.
    """
    return os.environ.get("PKP_API_KEY", "").strip() or None


def dev_mode():
    """Tryb deweloperski: `DEV_MODE=1` w data/.env.

    Domyślnie wyłączony: brak wpisu ma znaczyć produkcję, żeby zapomniane
    ustawienie nie wystawiało zwykłym użytkownikom rzeczy zespołu. Włącza się
    go świadomie - lokalnie i na serwerze testowym.
    """
    return os.environ.get("DEV_MODE", "").strip() == "1"


def discord_webhook():
    """Adres webhooka Discorda (`DISCORD`) albo None, jeśli nie ustawiono.

    To sekret: kto go zna, pisze na kanał jako webhook. Służy do wysyłania
    kodów odblokowujących ustawienia deweloperskie (devcode.py).
    """
    return os.environ.get("DISCORD", "").strip() or None


def discord_ids():
    """Konta GitHuba na ID z Discorda z pliku data/discord.json - ten sam JSON,
    co zmienna repo w .github/workflows/discord.yml: `{"login": "id"}`.

    To zwykły plik, nie zmienna: ID z Discorda nie są tajne, a data/ i tak nie
    trafia do repozytorium. Brak pliku, pusty albo zepsuty daje {}, a wołający
    traktuje to jak brak konfiguracji, bo kod nie miałby komu iść.
    """
    try:
        ids = json.loads(DISCORD_IDS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(ids, dict):
        return {}
    return {str(login): str(user_id) for login, user_id in ids.items()}
