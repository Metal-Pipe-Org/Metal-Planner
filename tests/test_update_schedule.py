"""Testy harmonogramu codziennej aktualizacji rozkładu (GTFS_AUTO_UPDATE_HOUR).

Zakres to wyłącznie czyste funkcje: liczenie czasu do terminu i czytanie
konfiguracji. Samego wątku ani pobierania paczki tu nie ma - nie da się ich
sprawdzić bez czekania i bez sieci, a cała logika, w której da się pomylić,
siedzi w tych dwóch funkcjach.
"""

import datetime

import update_gtfs


def _at(hour, minute=0, second=0):
    """Konkretna chwila w środę 2026-08-19 - dzień bez zmiany czasu."""
    return datetime.datetime(2026, 8, 19, hour, minute, second)


class TestNextRunAt:

    def test_termin_jeszcze_dzis(self):
        assert update_gtfs._next_run_at(3, _at(1)) == _at(3)

    def test_termin_juz_minal_czekamy_do_jutra(self):
        assert update_gtfs._next_run_at(3, _at(5)) == _at(3) + datetime.timedelta(days=1)

    def test_trafienie_co_do_sekundy_to_jutro(self):
        # Gdyby wyszło "teraz", pętla schedulera odpaliłaby aktualizację drugi
        # raz zaraz po powrocie z pierwszej - wciąż o tej samej godzinie.
        assert update_gtfs._next_run_at(3, _at(3)) == _at(3) + datetime.timedelta(days=1)

    def test_sekunda_przed_terminem(self):
        assert update_gtfs._next_run_at(3, _at(2, 59, 59)) == _at(3)

    def test_termin_jest_rowna_godzina_co_do_mikrosekundy(self):
        # Log ma pokazywać 03:00, nie 02:59 - stąd datetime zamiast liczby sekund.
        target = update_gtfs._next_run_at(3, _at(1).replace(microsecond=123_456))
        assert (target.minute, target.second, target.microsecond) == (0, 0, 0)

    def test_polnoc_jest_poprawna_godzina(self):
        assert update_gtfs._next_run_at(0, _at(23)) == _at(0) + datetime.timedelta(days=1)


class TestScheduledHour:

    def test_wartosc_z_argumentu_ma_pierwszenstwo(self, monkeypatch):
        monkeypatch.setenv("GTFS_AUTO_UPDATE_HOUR", "3")
        assert update_gtfs.scheduled_hour(17) == 17

    def test_czyta_zmienna_srodowiskowa(self, monkeypatch):
        monkeypatch.setenv("GTFS_AUTO_UPDATE_HOUR", " 3 ")
        assert update_gtfs.scheduled_hour() == 3

    def test_polnoc_nie_jest_brakiem_wartosci(self, monkeypatch):
        monkeypatch.setenv("GTFS_AUTO_UPDATE_HOUR", "0")
        assert update_gtfs.scheduled_hour() == 0

    def test_pusta_wartosc_wylacza_harmonogram(self, monkeypatch):
        monkeypatch.setenv("GTFS_AUTO_UPDATE_HOUR", "")
        assert update_gtfs.scheduled_hour() is None

    def test_brak_zmiennej_wylacza_harmonogram(self, monkeypatch):
        monkeypatch.delenv("GTFS_AUTO_UPDATE_HOUR", raising=False)
        assert update_gtfs.scheduled_hour() is None

    def test_smiec_wylacza_harmonogram(self, monkeypatch):
        monkeypatch.setenv("GTFS_AUTO_UPDATE_HOUR", "kanapka")
        assert update_gtfs.scheduled_hour() is None

    def test_godzina_poza_zakresem_wylacza_harmonogram(self, monkeypatch):
        monkeypatch.setenv("GTFS_AUTO_UPDATE_HOUR", "25")
        assert update_gtfs.scheduled_hour() is None

    def test_niepoprawna_wartosc_krzyczy_na_stderr(self, monkeypatch, capsys):
        # Cicho wyłączony harmonogram wyszedłby na jaw dopiero pustymi
        # wynikami wyszukiwania, po wygaśnięciu okna ważności paczki.
        monkeypatch.setenv("GTFS_AUTO_UPDATE_HOUR", "25")
        update_gtfs.scheduled_hour()
        assert "GTFS_AUTO_UPDATE_HOUR" in capsys.readouterr().err


class TestStartDailyScheduler:

    def test_wylaczony_nie_startuje_watku(self, monkeypatch):
        monkeypatch.setenv("GTFS_AUTO_UPDATE_HOUR", "")
        assert update_gtfs.start_daily_scheduler() is None

    def test_wlaczony_startuje_watek_demona(self, monkeypatch):
        monkeypatch.setenv("GTFS_AUTO_UPDATE_HOUR", "3")
        thread = update_gtfs.start_daily_scheduler()
        assert thread is not None and thread.is_alive() and thread.daemon
