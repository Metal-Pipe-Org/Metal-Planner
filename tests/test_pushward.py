"""Odliczenie do odjazdu w Dynamic Island przez PushWard (zgłoszenie #453).

Bez sieci i bez prawdziwego klucza: wywołania HTTP (`pushward._request`) są
podmienione na zapis tego, co poszłoby do API. Pilnujemy trzech rzeczy:
każdy przejazd zakłada nową kartę z własnym slugiem i tytułem tego odjazdu
(POST, PATCH), bez kasowania poprzedniej; a `end_date` to dokładnie ta chwila, w którą odjeżdża pojazd - także po
północy i dla godzin rozkładu powyżej 24:00.
"""

from datetime import datetime
from types import SimpleNamespace
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


KONIEC = _czas(2026, 1, 5, 22, 57)


def _zamroz_czas(monkeypatch, teraz):
    monkeypatch.setattr(pushward, "time", SimpleNamespace(time=lambda: teraz))


@pytest.fixture
def api(monkeypatch):
    """Zapisuje wywołania; `odpowiedzi` to kolejka wyników (wyjątek = błąd API).

    Czas stoi godzinę przed odjazdem z ODJAZD, żeby mieścił się w oknie 8 h."""
    _zamroz_czas(monkeypatch, KONIEC - 3600)
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


def test_przejazd_zaklada_nowa_karte_i_wypelnia_ja_bez_kasowania(api):
    pushward.send_departure(KLUCZ, ODJAZD)
    slug = f"metal-planner-{KONIEC - 3600}"
    assert [(m, p) for m, p, _, _ in api.wywolania] == [
        ("POST", "/activities"),
        ("PATCH", f"/activities/{slug}")]
    tworzenie = api.wywolania[0][3]
    assert tworzenie["slug"] == slug
    assert tworzenie["name"] == "5 → Księże Małe"
    assert tworzenie["ended_ttl"] == 600
    assert tworzenie["dismissal_ttl"] == 300


def test_kolejne_przejazdy_dostaja_rozne_karty(api, monkeypatch):
    pushward.send_departure(KLUCZ, ODJAZD)
    _zamroz_czas(monkeypatch, KONIEC - 3000)
    pushward.send_departure(KLUCZ, ODJAZD)
    assert api.wywolania[0][3]["slug"] != api.wywolania[2][3]["slug"]
    assert "DELETE" not in [m for m, _, _, _ in api.wywolania]


def test_tresc_karty(api):
    pushward.send_departure(KLUCZ, ODJAZD)
    _, _, klucz, body = api.wywolania[-1]
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
    assert tresc["state"] == "Pl. Grunwaldzki"
    assert "subtitle" not in tresc
    assert "accent_color" not in tresc


def test_link_trafia_do_karty_jako_tap_action(api):
    link = "https://metal-testing.sze.one/?from=Sosnowiecka&to=Wojszyce&time=22:00"
    pushward.send_departure(KLUCZ, {**ODJAZD, "link": link})
    assert api.wywolania[-1][3]["content"]["tap_action"] == {"url": link, "foreground": True}


@pytest.mark.parametrize("zly", [None, "", "javascript:alert(1)", "ftp://host/", "https://",
                                 "/?from=A", "metal.sze.one", 5])
def test_zly_link_pomija_tap_action_ale_wysyla(api, zly):
    pushward.send_departure(KLUCZ, {**ODJAZD, "link": zly})
    assert len(api.wywolania) == 2
    assert "tap_action" not in api.wywolania[-1][3]["content"]


def test_trasa_przekazuje_link(klient, api):
    link = "http://192.168.1.5:5001/?from=A&to=B"
    odpowiedz = klient.post("/api/dev/pushward", json={**ODJAZD, "link": link},
                            headers={"X-PushWard-Key": KLUCZ})
    assert odpowiedz.status_code == 200
    assert api.wywolania[-1][3]["content"]["tap_action"]["url"] == link


@pytest.mark.parametrize("link", [
    "https://siechnice.kiedyprzyjedzie.pl/#/stops/30329:54854/departures",
    "https://czynaczas.pl/wroclaw?przystanek=1605",
])
def test_link_do_zewnetrznego_rozkladu_przechodzi_z_fragmentem(api, link):
    pushward.send_departure(KLUCZ, {**ODJAZD, "link": link})
    assert api.wywolania[-1][3]["content"]["tap_action"]["url"] == link


def test_autobus_dostaje_ikone_autobusu(api):
    pushward.send_departure(KLUCZ, {**ODJAZD, "mode": "bus"})
    assert api.wywolania[-1][3]["content"]["icon"] == "bus.fill"


def test_blad_api_przerywa_przejazd(api):
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


class _Odpowiedz:
    def read(self):
        return b"{}"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _blad_http(kod, tresc):
    import io
    import urllib.error
    return urllib.error.HTTPError("https://api.pushward.app/x", kod, "", {}, io.BytesIO(tresc))


def test_zapytania_ida_z_wlasnym_user_agentem(monkeypatch):
    wyslane = []

    def udawane(request, timeout):
        wyslane.append(request)
        return _Odpowiedz()

    _zamroz_czas(monkeypatch, KONIEC - 3600)
    monkeypatch.setattr(pushward.urllib.request, "urlopen", udawane)
    pushward.send_departure(KLUCZ, ODJAZD)
    pushward.account(KLUCZ)
    assert len(wyslane) == 3
    assert all(r.get_header("User-agent") == "Metal-Planner/1.0" for r in wyslane)
    assert wyslane[2].get_method() == "GET" and wyslane[2].full_url.endswith("/auth/me")


def test_blokada_cloudflare_to_blad_polaczenia_nie_brak_uprawnien(monkeypatch):
    def udawane(request, timeout):
        raise _blad_http(403, b"error code: 1010")

    monkeypatch.setattr(pushward.urllib.request, "urlopen", udawane)
    with pytest.raises(pushward.PushWardError) as blad:
        pushward.account(KLUCZ)
    assert blad.value.status == 0


def test_prawdziwe_403_zostaje_403(monkeypatch):
    def udawane(request, timeout):
        raise _blad_http(403, b'{"code": "auth.forbidden", "detail": "nope"}')

    monkeypatch.setattr(pushward.urllib.request, "urlopen", udawane)
    with pytest.raises(pushward.PushWardError) as blad:
        pushward.account(KLUCZ)
    assert (blad.value.status, blad.value.code) == (403, "auth.forbidden")


def test_sprawdzenie_klucza_oddaje_zuzycie_bez_klucza(klient, api):
    api.odpowiedzi.append({"live_activity_updates_used": 12, "live_activity_updates_limit": 250,
                           "email": "x@y.z"})
    odpowiedz = klient.get("/api/dev/pushward/check", headers={"X-PushWard-Key": KLUCZ})
    assert odpowiedz.get_json() == {"used": 12, "limit": 250}
    assert api.wywolania[0][:3] == ("GET", "/auth/me", KLUCZ)
    assert KLUCZ not in odpowiedz.get_data(as_text=True)


def test_sprawdzenie_zlego_klucza_oddaje_status(klient, api):
    api.odpowiedzi.append(pushward.PushWardError(401, "auth.invalid_token"))
    odpowiedz = klient.get("/api/dev/pushward/check", headers={"X-PushWard-Key": KLUCZ})
    assert odpowiedz.status_code == 502
    assert odpowiedz.get_json() == {"status": 401}
    assert KLUCZ not in odpowiedz.get_data(as_text=True)


def test_sprawdzenie_klucza_poza_dev_nie_istnieje(monkeypatch, api):
    monkeypatch.delenv("DEV_MODE", raising=False)
    odpowiedz = aplikacja.app.test_client().get(
        "/api/dev/pushward/check", headers={"X-PushWard-Key": KLUCZ})
    assert odpowiedz.status_code == 404
    assert api.wywolania == []


@pytest.mark.parametrize("przed_odjazdem, wysylane", [
    (-1, False),                # odjechał przed chwilą
    (0, False),                 # odjeżdża dokładnie teraz
    (1, True),
    (8 * 3600 - 60, True),      # za 7:59
    (8 * 3600, True),           # dokładnie 8 h
    (8 * 3600 + 60, False),     # za 8:01
])
def test_okno_osmiu_godzin(klient, api, monkeypatch, przed_odjazdem, wysylane):
    _zamroz_czas(monkeypatch, KONIEC - przed_odjazdem)
    odpowiedz = klient.post("/api/dev/pushward", json=ODJAZD, headers={"X-PushWard-Key": KLUCZ})
    if wysylane:
        assert odpowiedz.status_code == 200
        assert len(api.wywolania) == 2
    else:
        assert odpowiedz.status_code == 400
        assert "8 godzin" in odpowiedz.get_json()["error"]
        assert api.wywolania == []


def test_jutrzejszy_odjazd_w_oknie_dziala(klient, api, monkeypatch):
    # 23:00 w poniedziałek, odjazd we wtorek o 6:00 (doba rozkładu poniedziałku, 30:00).
    _zamroz_czas(monkeypatch, _czas(2026, 1, 5, 23, 0))
    odjazd = {**ODJAZD, "sec": 30 * 3600}
    assert klient.post("/api/dev/pushward", json=odjazd,
                       headers={"X-PushWard-Key": KLUCZ}).status_code == 200
