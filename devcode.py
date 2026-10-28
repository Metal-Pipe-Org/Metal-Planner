"""Jednorazowy kod z Discorda, który odblokowuje wgrywanie rozkładu Siechnic.

Sama flaga DEV_MODE nie wystarcza na serwerze testowym dostępnym z sieci:
kto tam wejdzie, mógłby podmienić rozkład. Dlatego wgranie wymaga kodu, który
serwer wysyła na Discorda z pingiem wybranej osoby - jeśli ktoś dostanie ping,
choć nie klikał, wie, że coś jest nie tak.

Stan (kod, tokeny sesji) leży tylko w pamięci procesu: restart serwera
zamyka wszystkie sesje, a to akurat jest tu pożądane. Przy WEB_CONCURRENCY > 1
kod i token trafiałyby do różnych workerów, ale gunicorn.conf.py trzyma jeden.
"""

import hmac
import json
import secrets
import threading
import time
import urllib.request

import config

CODE_TTL_SEC = 10 * 60
# Sześć cyfr to milion możliwości; bez limitu prób dałoby się je przejść
# seriami zapytań w ciągu tych dziesięciu minut.
MAX_WRONG_TRIES = 5
HTTP_TIMEOUT_SEC = 10

_lock = threading.Lock()
_pending = None  # {"code", "expires", "wrong"}
_tokens = set()


def available():
    return bool(config.discord_webhook() and config.discord_ids())


def people():
    return sorted(config.discord_ids())


def _post_to_discord(content, user_id):
    body = json.dumps({
        "content": content,
        # Ping tylko tej jednej osoby, nawet gdyby treść zawierała coś więcej.
        "allowed_mentions": {"users": [user_id]},
    }).encode()
    request = urllib.request.Request(
        config.discord_webhook(), data=body,
        # Discord odrzuca domyślny User-Agent urllib.
        headers={"Content-Type": "application/json",
                 "User-Agent": "Metal-Planner/0.1 (+https://metal.sze.one)"},
    )
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SEC):
        pass


def send_code(login):
    """Generuje kod i wysyła go z pingiem osoby `login`.

    KeyError, gdy takiej osoby nie ma na liście; OSError, gdy Discord
    nie przyjął wiadomości. Nowy kod unieważnia poprzedni.
    """
    global _pending
    user_id = config.discord_ids()[login]
    code = f"{secrets.randbelow(10**6):06d}"
    _post_to_discord(
        f"<@{user_id}> prosi o kod do ustawień deweloperskich: {code} "
        f"(ważny {CODE_TTL_SEC // 60} min)",
        user_id,
    )
    with _lock:
        _pending = {"code": code, "expires": time.monotonic() + CODE_TTL_SEC, "wrong": 0}


def redeem(code):
    """Token sesji za poprawny kod albo None. Kod zużywa się przy pierwszym
    poprawnym użyciu; po zbyt wielu błędnych próbach także przepada."""
    global _pending
    with _lock:
        if _pending is None or time.monotonic() > _pending["expires"]:
            _pending = None
            return None
        if not hmac.compare_digest(_pending["code"], code):
            _pending["wrong"] += 1
            if _pending["wrong"] >= MAX_WRONG_TRIES:
                _pending = None
            return None
        _pending = None
        token = secrets.token_urlsafe(32)
        _tokens.add(token)
        return token


def valid_token(token):
    with _lock:
        return any(hmac.compare_digest(token, known) for known in _tokens)
