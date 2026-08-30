#!/bin/sh
# Wszystko, co trwałe - bazy rozkładów (MPK i PKP) i token menu
# deweloperskiego - leży w /app/data, czyli w folderze ./data obok
# docker-compose.yml na serwerze.
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
elif [ "$GTFS_UPDATE_ON_START" != "off" ]; then
    # Restart = odświeżenie rozkładu, żeby po wdrożeniu nie jechać na paczce
    # sprzed tygodnia. W tle, bo baza już jest: serwer wstaje natychmiast na
    # obecnych danych, a gotową paczkę update_gtfs.py podmienia atomowo
    # i gtfs.py przeładuje ją sam (klucz cache zawiera mtime bazy).
    #
    # Subshell z || i & - błąd aktualizacji ma zostać ostrzeżeniem w logu,
    # a nie procesem potomnym kończącym się niezerowym kodem pod PID-em 1.
    echo "Odświeżam rozkład w tle (serwer startuje na obecnej bazie)..."
    (python -u /app/update_gtfs.py || echo "OSTRZEŻENIE: nie udało się odświeżyć rozkładu - zostaje poprzedni.") &
fi

# Rozkład kolejowy (update_pkp.py) - bez PKP_API_KEY oba wywołania są
# no-opami (patrz pkp.enabled()), więc bezpiecznie wołać je bezwarunkowo,
# tak samo jak update_gtfs.py powyżej.
if [ ! -f "$DATA_DIR/pkp.sqlite" ]; then
    echo "Brak lokalnego rozkładu PKP - pobieram (pierwsze uruchomienie, kilkanaście sekund)..."
    # Tylko sam rozkład, blokująco - geokodowanie stacji (do godziny przy
    # pierwszym wypełnieniu cache'u) NIE MA prawa opóźniać startu serwera,
    # więc leci osobno, od razu w tle (patrz update_pkp.main).
    python -u /app/update_pkp.py --schedule-only \
        || echo "OSTRZEŻENIE: nie udało się pobrać rozkładu PKP."
    echo "Geokoduję stacje PKP w tle (pierwsze wypełnienie cache'u może potrwać do godziny)..."
    (python -u /app/update_pkp.py --geocode-only || true) &
elif [ "$PKP_UPDATE_ON_START" != "off" ]; then
    echo "Odświeżam rozkład PKP w tle (serwer startuje na obecnej bazie)..."
    (python -u /app/update_pkp.py || echo "OSTRZEŻENIE: nie udało się odświeżyć rozkładu PKP - zostaje poprzedni.") &
fi

exec "$@"
