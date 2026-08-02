"""Uruchamia aplikację lokalnie tak, jak robi to Docker: sprawdza, czy
rozkład trzeba pobrać/odświeżyć, i DOPIERO POTEM startuje serwer.

W Dockerze o to samo dbają dwa mechanizmy: `docker/entrypoint.sh` pobiera
rozkład przy pierwszym starcie (gdy `data/gtfs.sqlite` jeszcze nie istnieje),
a `dev.py` (`GTFS_AUTO_UPDATE_HOUR`) odświeża go codziennie w działającym
kontenerze. Przy zwykłym `python app.py` na laptopie żaden z tych mechanizmów
nie działa - trzeba pamiętać, żeby przed uruchomieniem odpalić ręcznie
`update_gtfs.py`, a `calendar.txt` w paczce GTFS obejmuje tylko ok. 2 tygodnie,
więc baza sprzed dłuższej przerwy jest przeterminowana i wyszukiwarka nie
znajdzie ŻADNEGO połączenia (patrz PROJECT.md, Changelog).

Użycie - zamiast `python app.py`:
    python3 run.py
"""

import subprocess
import sys
from datetime import date

import gtfs


def _update_reason():
    """Zwraca powód aktualizacji rozkładu, albo None gdy baza jest świeża."""
    if not gtfs.DB_PATH.exists():
        return "brak bazy rozkładów (data/gtfs.sqlite)"

    db = gtfs.open_db()
    try:
        if not gtfs.active_service_ids(db, date.today()):
            return "rozkład nie obejmuje dzisiejszej daty (przeterminowany)"
    finally:
        db.close()
    return None


def main():
    reason = _update_reason()
    if reason:
        print(f"Aktualizuję rozkład GTFS ({reason})...")
        result = subprocess.run([sys.executable, "update_gtfs.py"])
        if result.returncode != 0:
            print(
                "Aktualizacja się nie powiodła - startuję mimo to na tym, "
                "co jest w bazie (może być pusta/przeterminowana).",
                file=sys.stderr,
            )
    subprocess.run([sys.executable, "app.py"])


if __name__ == "__main__":
    main()
