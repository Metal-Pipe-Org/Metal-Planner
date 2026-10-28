"""Wgrywanie rozkładu Siechnic odblokowuje kod wysłany na Discorda (zgłoszenie #433).

Wysyłka na Discorda jest podmieniona - żadne żądanie nie wychodzi z testów.
"""

import io
import json

import pytest

import config
import devcode
import siechnice
import update_gtfs

IDS = {"Ala": "111", "Olek": "222"}


@pytest.fixture
def wyslane(monkeypatch, tmp_path):
    monkeypatch.setenv("DEV_MODE", "1")
    monkeypatch.setenv("DISCORD", "https://discord.invalid/webhook")
    osoby = tmp_path / "discord.json"
    osoby.write_text(json.dumps(IDS), encoding="utf-8")
    monkeypatch.setattr(config, "DISCORD_IDS_PATH", osoby)
    monkeypatch.setattr(devcode, "_pending", None)
    monkeypatch.setattr(devcode, "_tokens", set())
    wiadomosci = []
    monkeypatch.setattr(devcode, "_post_to_discord",
                        lambda tresc, user_id: wiadomosci.append((tresc, user_id)))
    monkeypatch.setattr(siechnice, "save_snapshot", lambda raw, path: None)
    monkeypatch.setattr(update_gtfs, "start_update_in_background", lambda: None)
    return wiadomosci


@pytest.fixture
def klient():
    import app as aplikacja
    return aplikacja.app.test_client()


def _kod(wiadomosci):
    return wiadomosci[-1][0].split(": ")[1].split(" ")[0]


def _wyslij_kod(klient, wiadomosci, login="Ala"):
    assert klient.post("/api/dev/kod", json={"login": login}).status_code == 200
    return _kod(wiadomosci)


def _odblokuj(klient, kod):
    return klient.post("/api/dev/odblokuj", json={"code": kod})


def _wgraj(klient, token=None):
    naglowki = {"X-Dev-Token": token} if token else {}
    return klient.post("/api/dev/siechnice", headers=naglowki,
                       data={"file": (io.BytesIO(b"{}"), "s.json")})


def test_wiadomosc_pinguje_wybrana_osobe_i_podaje_kod(klient, wyslane):
    kod = _wyslij_kod(klient, wyslane, "Olek")

    tresc, user_id = wyslane[0]
    assert user_id == "222"
    assert tresc.startswith("<@222> prosi o kod do ustawień deweloperskich: ")
    assert len(kod) == 6 and kod.isdigit()
    assert tresc.endswith("(ważny 10 min)")


def test_lista_osob_to_loginy(klient, wyslane):
    info = klient.get("/api/dev/kod").get_json()

    assert info == {"available": True, "people": ["Ala", "Olek"]}


def test_nieznana_osoba_nie_dostaje_kodu(klient, wyslane):
    assert klient.post("/api/dev/kod", json={"login": "Ktos"}).status_code == 400
    assert wyslane == []


def test_wgrywanie_bez_tokenu_odrzucone(klient, wyslane):
    assert _wgraj(klient).status_code == 403
    assert _wgraj(klient, "zmyslony").status_code == 403


def test_dobry_kod_daje_token_i_odblokowuje_wgrywanie(klient, wyslane):
    kod = _wyslij_kod(klient, wyslane)

    odpowiedz = _odblokuj(klient, kod)
    token = odpowiedz.get_json()["token"]

    assert odpowiedz.status_code == 200
    assert _wgraj(klient, token).status_code == 200
    status = klient.get("/api/dev/siechnice", headers={"X-Dev-Token": token}).get_json()
    assert status["unlocked"] is True
    assert klient.get("/api/dev/siechnice").get_json()["unlocked"] is False


def test_zly_kod_nie_odblokowuje(klient, wyslane):
    kod = _wyslij_kod(klient, wyslane)
    zly = "000000" if kod != "000000" else "111111"

    assert _odblokuj(klient, zly).status_code == 403
    assert _odblokuj(klient, kod).status_code == 200


def test_kod_zuzywa_sie_przy_pierwszym_uzyciu(klient, wyslane):
    kod = _wyslij_kod(klient, wyslane)

    assert _odblokuj(klient, kod).status_code == 200
    assert _odblokuj(klient, kod).status_code == 403


def test_kod_przeterminowany(klient, wyslane, monkeypatch):
    kod = _wyslij_kod(klient, wyslane)
    teraz = devcode.time.monotonic()
    monkeypatch.setattr(devcode.time, "monotonic", lambda: teraz + devcode.CODE_TTL_SEC + 1)

    assert _odblokuj(klient, kod).status_code == 403


def test_po_zbyt_wielu_blednych_probach_kod_przepada(klient, wyslane):
    kod = _wyslij_kod(klient, wyslane)
    zly = "000000" if kod != "000000" else "111111"
    for _ in range(devcode.MAX_WRONG_TRIES):
        _odblokuj(klient, zly)

    assert _odblokuj(klient, kod).status_code == 403


def test_nowy_kod_uniewaznia_poprzedni(klient, wyslane):
    stary = _wyslij_kod(klient, wyslane)
    nowy = _wyslij_kod(klient, wyslane)
    if stary == nowy:
        pytest.skip("wylosowano ten sam kod dwa razy")

    assert _odblokuj(klient, stary).status_code == 403
    assert _odblokuj(klient, nowy).status_code == 200


def test_bez_webhooka_nie_da_sie_wyslac_kodu_ani_wgrac(klient, wyslane, monkeypatch):
    monkeypatch.delenv("DISCORD")

    assert klient.get("/api/dev/kod").get_json()["available"] is False
    assert klient.post("/api/dev/kod", json={"login": "Ala"}).status_code == 503
    assert wyslane == []
    assert _wgraj(klient).status_code == 403


@pytest.mark.parametrize("zawartosc", [None, "", "{}", "to nie json", "[]"])
def test_bez_poprawnej_listy_osob_nie_da_sie_wyslac_kodu_ani_wgrac(
        klient, wyslane, tmp_path, monkeypatch, zawartosc):
    plik = tmp_path / "inny.json"
    if zawartosc is not None:
        plik.write_text(zawartosc, encoding="utf-8")
    monkeypatch.setattr(config, "DISCORD_IDS_PATH", plik)

    assert klient.get("/api/dev/kod").get_json() == {"available": False, "people": []}
    assert klient.post("/api/dev/kod", json={}).status_code == 503
    assert klient.post("/api/dev/kod", json={"login": "Ala"}).status_code == 503
    assert wyslane == []
    assert _wgraj(klient).status_code == 403


def test_poza_trybem_deweloperskim_endpointow_nie_ma(klient, wyslane, monkeypatch):
    monkeypatch.setenv("DEV_MODE", "")

    assert klient.get("/api/dev/kod").status_code == 404
    assert klient.post("/api/dev/odblokuj", json={"code": "123456"}).status_code == 404


def test_blad_discorda_to_502(klient, wyslane, monkeypatch):
    def pada(tresc, user_id):
        raise OSError("sieć")
    monkeypatch.setattr(devcode, "_post_to_discord", pada)

    assert klient.post("/api/dev/kod", json={"login": "Ala"}).status_code == 502
