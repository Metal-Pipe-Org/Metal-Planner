#!/bin/sh
# Wszystko, co trwałe - baza rozkładów i token menu deweloperskiego - leży
# w /app/data, czyli w folderze ./data obok docker-compose.yml na serwerze.
set -e

APP_USER=app
DATA_DIR=/app/data

# Bind mount przychodzi z hosta z prawami hosta i Docker ich nie rusza, więc
# świeży ./data należy do roota. Wchodzimy jako root tylko po to, żeby ustawić
# właściciela, i od razu schodzimy na resztę życia kontenera do zwykłego
# użytkownika - serwer nie działa z uprawnieniami roota.
if [ "$(id -u)" = "0" ]; then
    mkdir -p "$DATA_DIR"
    chown -R "$APP_USER:$APP_USER" "$DATA_DIR"
    exec gosu "$APP_USER" "$0" "$@"
fi

if [ ! -f "$DATA_DIR/gtfs.sqlite" ]; then
    echo "Brak bazy rozkładów - pobieram paczkę GTFS (pierwsze uruchomienie, ~1 min)..."
    # Niepowodzenie nie blokuje startu: aplikacja wstanie z komunikatem o braku
    # danych, a rozkład da się dociągnąć przyciskiem w menu deweloperskim.
    python -u /app/update_gtfs.py || echo "OSTRZEŻENIE: nie udało się pobrać rozkładu."
fi

exec "$@"
