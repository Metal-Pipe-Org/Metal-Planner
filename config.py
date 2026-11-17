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
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parent / "data" / ".env"
DISCORD_IDS_PATH = ENV_PATH.parent / "discord.json"
# Czas budowy obrazu, zapisywany przez Dockerfile (#446). Poza kontenerem pliku
# nie ma i wtedy liczy się start procesu - patrz deployed_at().
DEPLOYED_AT_PATH = Path(__file__).resolve().parent / "DEPLOYED_AT"
_STARTED_AT = datetime.now(timezone.utc)

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


def deployed_at():
    """Kiedy wstała ta wersja aplikacji - do napisu pod nazwą w DEV_MODE (#446).

    Zwraca czas w strefie serwera. W kontenerze to czas
    budowy obrazu z pliku DEPLOYED_AT: obraz buduje się przy każdym deployu
    z nowym commitem, a restart kontenera go nie zmienia. Lokalnie, przy
    `python app.py`, pliku nie ma i zastępstwem deployu jest start serwera -
    reloader i tak wstaje od nowa po każdej zmianie kodu. Zepsuty plik
    traktujemy jak brak, żeby napis nie wywracał strony.
    """
    try:
        text = DEPLOYED_AT_PATH.read_text(encoding="utf-8").strip()
        when = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return when.astimezone()
    except (OSError, ValueError):
        return _STARTED_AT.astimezone()


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
