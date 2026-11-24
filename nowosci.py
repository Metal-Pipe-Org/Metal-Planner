"""Okienka nowości (static/nowosci/): serwerowa połowa.

Dokładka z boku: aplikacja o niej nie wie. Usunięcie okienek to skasowanie
tego pliku, folderu static/nowosci/, rejestru nowosci.json ze schematem
i jednej linii w app.py.

Rejestr (nowosci.json obok settings.json, opis pól w nowosci.schema.json) nie ma daty wdrożenia - zapisałby ją
człowiek, a człowiek zapomina. Każdy serwer sam zapamiętuje, kiedy pierwszy raz
zobaczył dany wpis, i od tej chwili liczy się wygasanie. Zapis leży w folderze
danych, bo ten przeżywa wdrożenia (w kontenerze to podmontowany ./data).
"""

import fcntl
import json
import time
from pathlib import Path

from flask import jsonify

import config

REGISTRY_PATH = Path(__file__).resolve().parent / "nowosci.json"
SEEN_PATH = config.ENV_PATH.parent / "nowosci-widziane.json"


def load():
    """Wpisy rejestru (pole `$schema` służy tylko edytorowi)."""
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))["popups"]


def first_seen(ids):
    """Chwila (ms) pierwszego zobaczenia każdego id; brakujące zapisuje teraz.

    Blokada pliku sprawia, że przy kilku procesach gunicorna pierwszy zapis
    wygrywa, a nikt go potem nie nadpisuje.
    """
    SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SEEN_PATH, "a+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        text = f.read()
        seen = json.loads(text) if text.strip() else {}
        now = int(time.time() * 1000)
        added = False
        for entry_id in ids:
            if entry_id not in seen:
                seen[entry_id] = now
                added = True
        if added:
            f.seek(0)
            f.truncate()
            json.dump(seen, f)
    return seen


def init_nowosci(app):

    @app.route("/api/nowosci")
    def nowosci():
        popups = load()
        seen = first_seen([entry["id"] for entry in popups])
        return jsonify({"popups": [
            {**entry, "deployed": seen[entry["id"]]} for entry in popups
        ]})
