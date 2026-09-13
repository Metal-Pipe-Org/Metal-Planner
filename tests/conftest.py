import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import bikes
import gtfs
import pkp
import traficar


@pytest.fixture
def install_day(monkeypatch):
    """install_day(day) podmienia gtfs.load_day tak, żeby plan_flow/plan_route
    dostawały syntetyczny DayData zamiast czytać SQLite."""
    def _install(day):
        monkeypatch.setattr(gtfs, "load_day", lambda d: day)
    return _install


@pytest.fixture(autouse=True)
def _pkp_disabled_by_default(monkeypatch):
    """gtfs.load_day() dokleja rozkład kolejowy przez pkp.augment_day (patrz
    pkp.py) - bez tej blokady KAŻDY test wołający load_day (nie tylko
    test_pkp.py) sięgałby po prawdziwy data/pkp.sqlite i prawdziwy
    PKP_API_KEY ze środowiska, w którym akurat działa pytest. To łamie
    hermetyczność testów (wynik zależy od tego, czy ktoś ma skonfigurowany
    klucz akurat na tej maszynie) i wolno robi się w każdym teście
    dotykającym gtfs.load_day, nie tylko tych o PKP.

    Testy, którym PKP faktycznie jest potrzebne (patrz tests/test_pkp.py),
    same nadpisują `pkp.enabled` w swoim fixturze - ten sam `monkeypatch`
    ma zasięg całego testu, więc kolejne setattr po prostu wygrywa."""
    monkeypatch.setattr(pkp, "enabled", lambda: False)


@pytest.fixture(autouse=True)
def _traficar_disabled_by_default(monkeypatch):
    """plan_flow dokłada do listy propozycje kończące się Traficarem (patrz
    traficar.py) - a te biorą się z ŻYWEGO feedu fioletowe.live. Bez tej
    blokady każdy test wołający plan_flow strzelałby w internet: wynik
    zależałby od tego, ile aut akurat stoi we Wrocławiu i czy serwis żyje,
    a testy chodziłyby tyle, ile trwa timeout HTTP.

    Test, który Traficara faktycznie dotyczy, sam podstawia dane (ten sam
    `monkeypatch` ma zasięg całego testu, więc kolejne setattr wygrywa) -
    patrz tests/test_traficar.py.
    """
    monkeypatch.setattr(traficar, "enabled", lambda: False)


@pytest.fixture(autouse=True)
def _bikes_disabled_by_default(monkeypatch):
    """Ten sam powód, co przy Traficarze, tylko źródłem jest kanał GBFS
    operatora WRM. Odkąd rowery stoją NA MAPIE (patrz bikes.map_places),
    sięga po nie każde wyszukanie, nie tylko to z odhaczonym rowerem - więc
    bez tej blokady w internet strzelałby każdy test wołający plan_flow.

    Zatkana jest sama granica sieci, a nie wyłącznik `bikes.enabled` - inaczej
    nie dałoby się przetestować tego wyłącznika (patrz tests/test_rower.py).
    Test o rowerach sam podstawia dane (patrz tests/test_rowery_na_mapie.py).
    """
    def _bez_sieci(url):
        raise OSError(f"test nie wychodzi do sieci: {url}")

    bikes._cache.clear()
    monkeypatch.setattr(bikes, "_fetch", _bez_sieci)
