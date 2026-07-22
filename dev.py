"""Menu deweloperskie: aktualizacja rozkładów, status danych, czyszczenie cache.

Dostępu pilnuje token (nagłówek X-Dev-Token) losowany przy pierwszym starcie
i zapisywany w data/dev_token.txt - czyli w dockerowym wolumenie, więc
przeżywa przebudowy obrazu. Dzięki temu wdrożenie nie ma domyślnego,
publicznie znanego hasła i nie wymaga niczego konfigurować.

DEV_TOKEN narzuca własny token zamiast losowanego, a DEV_MENU=off wyłącza
menu w całości - endpointy nie są wtedy rejestrowane, a UI nie pokazuje
przycisku.

Aktualizacja leci przez podproces update_gtfs.py, a nie przez import: budowa
bazy zjada kilkaset MB RAM i kończy się sys.exit(), więc lepiej trzymać ją
poza procesem serwera. Wyjście czytamy linia po linii do bufora, żeby panel
mógł pokazywać postęp na żywo.
"""

import hmac
import os
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import date, datetime, timedelta
from functools import wraps
from pathlib import Path

from flask import jsonify, request

import gtfs

# Godzina codziennej automatycznej aktualizacji (pusto = tylko ręcznie).
# W kontenerze zastępuje crona z hosta.
AUTO_UPDATE_HOUR = os.environ.get("GTFS_AUTO_UPDATE_HOUR", "").strip()

BASE_DIR = Path(__file__).resolve().parent
UPDATE_SCRIPT = BASE_DIR / "update_gtfs.py"
LOG_LINES_MAX = 500

# Token leży obok bazy rozkładów, bo data/ to jedyny katalog trwały w kontenerze.
TOKEN_FILE = Path(os.environ.get("DEV_TOKEN_FILE") or BASE_DIR / "data" / "dev_token.txt")
TOKEN_BYTES = 18       # ~24 znaki base64url - da się przekleić, nie da zgadnąć

_OFF = ("off", "0", "false", "no", "nie")


def _read_token_file():
    try:
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _load_or_create_token():
    """Token z DEV_TOKEN, a gdy go nie podano - losowy, zapamiętany w pliku.

    Plik zakładamy przez O_EXCL, bo przy kilku workerach gunicorna import leci
    równolegle i bez tego każdy wylosowałby sobie inny token.
    """
    from_env = os.environ.get("DEV_TOKEN", "").strip()
    if from_env:
        return from_env

    existing = _read_token_file()
    if existing:
        return existing
    TOKEN_FILE.unlink(missing_ok=True)       # pusty plik po przerwanym zapisie

    token = secrets.token_urlsafe(TOKEN_BYTES)
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(TOKEN_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(token + "\n")
    except FileExistsError:
        return _read_token_file() or token   # wyścig: token sąsiada równie dobry
    except OSError as e:
        print(f"Nie mogę zapisać {TOKEN_FILE} ({e}) - token tylko na czas życia procesu.")
        return token

    # Jedyne miejsce, w którym token trafia do logów - stąd bierze go operator.
    line = "=" * 64
    print(f"\n{line}\n  Token menu deweloperskiego: {token}\n"
          f"  Zapisany w: {TOKEN_FILE}\n{line}\n", flush=True)
    return token


DEV_MENU = os.environ.get("DEV_MENU", "on").strip().lower() not in _OFF
DEV_TOKEN = _load_or_create_token() if DEV_MENU else ""


class UpdateJob:
    """Stan bieżącej lub ostatniej aktualizacji rozkładu - jedna naraz."""

    def __init__(self):
        self._lock = threading.Lock()
        self._thread = None
        self._log = deque(maxlen=LOG_LINES_MAX)
        self.status = "idle"      # idle | running | ok | error
        self.trigger = None       # co ją uruchomiło
        self.started_at = None
        self.finished_at = None

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self, trigger="ręcznie"):
        """Uruchamia aktualizację w tle. Zwraca False, gdy już trwa."""
        with self._lock:
            if self.running:
                return False
            self._log.clear()
            self.status = "running"
            self.trigger = trigger
            self.started_at = time.time()
            self.finished_at = None
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            return True

    def snapshot(self):
        return {
            "status": "running" if self.running else self.status,
            "trigger": self.trigger,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "log": list(self._log),
        }

    def _run(self):
        self._log.append(f"$ python update_gtfs.py   ({self.trigger})")
        try:
            process = subprocess.Popen(
                [sys.executable, "-u", str(UPDATE_SCRIPT)],
                cwd=str(UPDATE_SCRIPT.parent),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                encoding="utf-8",
                errors="replace",
            )
            for line in process.stdout:
                self._log.append(line.rstrip())
            code = process.wait()
        except Exception as e:
            self._log.append(f"Nie udało się uruchomić aktualizacji: {e}")
            code = -1

        if code == 0:
            gtfs.clear_caches()
            self._log.append("Gotowe - nowy rozkład wchodzi bez restartu aplikacji.")
        else:
            self._log.append(
                f"Aktualizacja nieudana (kod {code}). Stara baza została nietknięta."
            )
        self.status = "ok" if code == 0 else "error"
        self.finished_at = time.time()


def data_status():
    """Metryki, po których widać, czy rozkład w bazie jest świeży i kompletny."""
    status = {"exists": gtfs.DB_PATH.exists(), "path": str(gtfs.DB_PATH)}
    if not status["exists"]:
        return status

    stat = gtfs.DB_PATH.stat()
    status["size_mb"] = round(stat.st_size / 1_000_000, 1)
    status["modified"] = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")

    try:
        db = gtfs.open_db()
        try:
            for table in ("stops", "routes", "trips"):
                status[table] = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            first, last = db.execute(
                "SELECT MIN(start_date), MAX(end_date) FROM calendar"
            ).fetchone()
            status["calendar_from"] = first
            status["calendar_to"] = last
            # Zero kursów dzisiaj = baza jest, ale rozkład się przeterminował.
            status["services_today"] = len(gtfs.active_service_ids(db, date.today()))
        finally:
            db.close()
    except (sqlite3.Error, OSError) as e:
        status["error"] = str(e)
    return status


def _requires_token(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        token = request.headers.get("X-Dev-Token", "")
        if not hmac.compare_digest(token, DEV_TOKEN):
            return jsonify({"error": "Zły token deweloperski."}), 401
        return view(*args, **kwargs)

    return wrapper


def _start_scheduler(job):
    """Codzienna aktualizacja o pełnej godzinie AUTO_UPDATE_HOUR.

    Zakłada jednego workera (WEB_CONCURRENCY=1); przy kilku każdy miałby
    własny wątek i pobierałby tę samą paczkę równolegle.
    """
    try:
        hour = int(AUTO_UPDATE_HOUR)
    except ValueError:
        return

    def loop():
        while True:
            now = datetime.now()
            target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            time.sleep((target - now).total_seconds())
            job.start(trigger="harmonogram")

    threading.Thread(target=loop, daemon=True).start()
    print(f"Automatyczna aktualizacja rozkładu: codziennie o {hour}:00")


def enabled():
    return bool(DEV_TOKEN)


def init_dev_routes(app):
    job = UpdateJob()
    _start_scheduler(job)

    if not enabled():
        return

    @app.route("/api/dev/status")
    @_requires_token
    def dev_status():
        return jsonify({"data": data_status(), "job": job.snapshot()})

    @app.route("/api/dev/update", methods=["POST"])
    @_requires_token
    def dev_update():
        if not job.start():
            return jsonify({"error": "Aktualizacja już trwa."}), 409
        return jsonify({"job": job.snapshot()}), 202

    @app.route("/api/dev/clear-cache", methods=["POST"])
    @_requires_token
    def dev_clear_cache():
        gtfs.clear_caches()
        return jsonify({"message": "Cache rozkładów i geometrii zrzucony."})
