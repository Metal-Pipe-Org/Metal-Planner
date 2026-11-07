"""Odliczenie do odjazdu w Dynamic Island przez PushWard (zgłoszenie #453).

Bez sieci i bez prawdziwego klucza: wywołania HTTP (`pushward._request`) są
podmienione na zapis tego, co poszłoby do API. Pilnujemy trzech rzeczy:
karta powstaje tylko wtedy, gdy jej nie ma; każdy przejazd to jeden PATCH;
a `end_date` to dokładnie ta chwila, w którą odjeżdża pojazd - także po
północy i dla godzin rozkładu powyżej 24:00.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import app as aplikacja
import pushward

WARSAW = ZoneInfo("Europe/Warsaw")
KLUCZ = "hlk_testowy"
ODJAZD = {"num": "5", "headsign": "Księże Małe", "mode": "tram",
          "stop": "Pl. Grunwaldzki", "date": "2026-01-05", "sec": 22 * 3600 + 57 * 60}


def _czas(rok, miesiac, dzien, h, m):
    return int(datetime(rok, miesiac, dzien, h, m, tzinfo=WARSAW).timestamp())


@pytest.fixture
def api(monkeypatch):
    """Zapisuje wywołania; `odpowiedzi` to kolejka wyników (wyjątek = błąd API)."""
    wywolania = []
    odpowiedzi = []

    def udawane(method, path, key, body):
        wywolania.append((method, path, key, body))
        wynik = odpowiedzi.pop(0) if odpowiedzi else {}
        if isinstance(wynik, Exception):
            raise wynik
        return wynik

    monkeypatch.setattr(pushward, "_request", udawane)
    udawane.wywolania = wywolania
    udawane.odpowiedzi = odpowiedzi
    return udawane


def test_end_date_zwykly_wieczor():
    assert pushward.end_date("2026-01-05", 22 * 3600 + 57 * 60) == _czas(2026, 1, 5, 22, 57)


def test_end_date_po_polnocy_to_nastepny_dzien():
    assert pushward.end_date("2026-01-05", 24 * 3600 + 10 * 60) == _czas(2026, 1, 6, 0, 10)
    assert pushward.end_date("2026-01-05", 25 * 3600 + 10 * 60) == _czas(2026, 1, 6, 1, 10)


def test_end_date_w_dobie_zmiany_czasu_liczy_prawdziwe_sekundy():
    # 29 marca 2026 o 2:00 zegar skacze na 3:00. Doba rozkładu startuje 12 h
    # przed południem, czyli o 23:00 poprzedniego dnia: godzina 01:00 rozkładu
    # to prawdziwa godzina po starcie, a na zegarze dopiero 00:00 (i 08:00 to 08:00).
    assert pushward.end_date("2026-03-29", 3600) == _czas(2026, 3, 29, 0, 0)
    assert pushward.end_date("2026-03-29", 8 * 3600) == _czas(2026, 3, 29, 8, 0)


def test_pierwszy_przejazd_tworzy_karte_i_aktualizuje_ja(api):
    api.odpowiedzi.extend([pushward.PushWardError(404, "activity.not_found"), {}, {}])
    pushward.send_departure(KLUCZ, ODJAZD)
    metody = [(m, p) for m, p, _, _ in api.wywolania]
    assert metody == [("PATCH", "/activities/metal-planner-odjazd"),
                      ("POST", "/activities"),
                      ("PATCH", "/activities/metal-planner-odjazd")]
    tworzenie = api.wywolania[1][3]
    assert tworzenie["slug"] == "metal-planner-odjazd"
    assert tworzenie["ended_ttl"] == 30 * 24 * 3600
    assert api.wywolania[0][3] == api.wywolania[2][3]


def test_kolejny_przejazd_to_jeden_patch(api):
    pushward.send_departure(KLUCZ, ODJAZD)
    assert [(m, p) for m, p, _, _ in api.wywolania] == [("PATCH", "/activities/metal-planner-odjazd")]


def test_tresc_karty(api):
    pushward.send_departure(KLUCZ, ODJAZD)
    _, _, klucz, body = api.wywolania[0]
    tresc = body["content"]
    assert klucz == KLUCZ
    assert body["state"] == "ONGOING"
    assert body["dismissal_ttl"] == 300
    assert tresc["template"] == "countdown"
    assert tresc["show_ruler"] is False
    assert tresc["end_date"] == _czas(2026, 1, 5, 22, 57)
    assert tresc["warning_threshold"] == 120
    assert tresc["completion_message"] == "Odjeżdża"
    assert tresc["icon"] == "tram.fill"
    assert tresc["state"] == "5 → Księże Małe"
    assert tresc["subtitle"] == "Pl. Grunwaldzki · 22:57"


def test_autobus_dostaje_ikone_autobusu(api):
    pushward.send_departure(KLUCZ, {**ODJAZD, "mode": "bus"})
    assert api.wywolania[0][3]["content"]["icon"] == "bus.fill"


def test_godzina_po_dobie_w_podtytule_jest_zegarowa(api):
    pushward.send_departure(KLUCZ, {**ODJAZD, "sec": 25 * 3600 + 10 * 60})
    assert api.wywolania[0][3]["content"]["subtitle"] == "Pl. Grunwaldzki · 01:10"


def test_inny_blad_niz_brak_karty_nie_tworzy_jej(api):
    api.odpowiedzi.append(pushward.PushWardError(429, "rate_limited"))
    with pytest.raises(pushward.PushWardError) as blad:
        pushward.send_departure(KLUCZ, ODJAZD)
    assert blad.value.status == 429
    assert len(api.wywolania) == 1


@pytest.fixture
def klient(monkeypatch):
    monkeypatch.setenv("DEV_MODE", "1")
    return aplikacja.app.test_client()


def test_trasa_przekazuje_klucz_i_nie_oddaje_go(klient, api):
    odpowiedz = klient.post("/api/dev/pushward", json=ODJAZD,
                            headers={"X-PushWard-Key": KLUCZ})
    assert odpowiedz.status_code == 200
    assert api.wywolania[0][2] == KLUCZ
    assert KLUCZ not in odpowiedz.get_data(as_text=True)


def test_trasa_oddaje_kod_bledu_api(klient, api):
    api.odpowiedzi.append(pushward.PushWardError(401, "auth.invalid_token", "bad key"))
    odpowiedz = klient.post("/api/dev/pushward", json=ODJAZD,
                            headers={"X-PushWard-Key": KLUCZ})
    assert odpowiedz.status_code == 502
    assert odpowiedz.get_json()["status"] == 401
    assert KLUCZ not in odpowiedz.get_data(as_text=True)


def test_poza_trybem_deweloperskim_trasy_nie_ma(monkeypatch, api):
    monkeypatch.delenv("DEV_MODE", raising=False)
    odpowiedz = aplikacja.app.test_client().post(
        "/api/dev/pushward", json=ODJAZD, headers={"X-PushWard-Key": KLUCZ})
    assert odpowiedz.status_code == 404
    assert api.wywolania == []
