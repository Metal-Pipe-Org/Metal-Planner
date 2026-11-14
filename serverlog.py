"""Log serwera: jedno miejsce konfiguracji dla app.py, gunicorna i skryptów.

Plik leży w data/ (wolumen kontenera, poza gitem) i ma twardy limit rozmiaru:
rotacja po LOG_MAX_BYTES, z LOG_BACKUPS poprzednimi plikami. Do logu nie
trafiają dane użytkowników (współrzędne, wyszukiwania, IP) - tylko zdarzenia
po stronie serwera. Pobiera się go przyciskiem w ustawieniach deweloperskich.
"""

import logging
import logging.handlers
import time
from pathlib import Path

LOG_PATH = Path(__file__).resolve().parent / "data" / "server.log"
LOG_MAX_BYTES = 512 * 1024
LOG_BACKUPS = 2

_NAME = "metal-planner"


def get_logger():
    logger = logging.getLogger(_NAME)
    if not logger.handlers:
        LOG_PATH.parent.mkdir(exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            LOG_PATH, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        # Bez propagacji do roota: gunicorn i Flask mają własne handlery i nie
        # chcemy dubli w konsoli.
        logger.propagate = False
    return logger


# Przy długiej awarii zewnętrznego źródła ten sam błąd wpadałby co kilka sekund
# i rotacja wypychałaby z pliku starsze, ważniejsze wpisy - dlatego powtórki
# tej samej przyczyny tylko liczymy, a przy końcu serii zapisujemy jedno podsumowanie.
_streak = {"reason": None, "message": None, "count": 0, "since": 0.0}


def log_repeating_error(reason, message):
    """Pierwszy błąd danej przyczyny idzie do logu, kolejne tylko się liczą."""
    if _streak["reason"] == reason:
        _streak["count"] += 1
        return
    close_error_streak()
    _streak.update(reason=reason, message=message, count=0, since=time.monotonic())
    get_logger().error(message)


def close_error_streak():
    """Zapisuje podsumowanie powtórek (tylko gdy jakieś były) i kończy serię."""
    if _streak["reason"] is not None and _streak["count"] > 0:
        minutes = max(1, round((time.monotonic() - _streak["since"]) / 60))
        get_logger().error("%s - powtórzone %d razy przez %d min", _streak["message"], _streak["count"], minutes)
    _streak.update(reason=None, message=None, count=0)
