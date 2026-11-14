"""Log serwera: jedno miejsce konfiguracji dla app.py, gunicorna i skryptów.

Plik leży w data/ (wolumen kontenera, poza gitem) i ma twardy limit rozmiaru:
rotacja po LOG_MAX_BYTES, z LOG_BACKUPS poprzednimi plikami. Do logu nie
trafiają dane użytkowników (współrzędne, wyszukiwania, IP) - tylko zdarzenia
po stronie serwera. Pobiera się go przyciskiem w ustawieniach deweloperskich.
"""

import logging
import logging.handlers
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
