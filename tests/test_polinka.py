"""Polinka - kolej gondolowa nad Odrą, wpisana ręcznie (zgłoszenie #165).

Pilnujemy trzech rzeczy: że jeździ wtedy, kiedy według PWr jeździ (i ani
minuty dłużej), że wyszukiwarka bierze ją pod uwagę jak każdy inny pojazd
i że ma własny rodzaj (kolor) na plakietce.
"""

from datetime import date, datetime

import gtfs
import planner
import polinka
from tests.gtfs_builder import make_day

WW, NG = polinka.ID_PREFIX + "WW", polinka.ID_PREFIX + "NG"
WTOREK = date(2026, 10, 13)


def test_wielkanoc_i_swieta_ruchome():
    """Wielkanoc 2026 to 5 kwietnia; Boże Ciało 60 dni później."""
    assert polinka._easter(2026) == date(2026, 4, 5)
    swieta = polinka.holidays(2026)
    assert {date(2026, 4, 6), date(2026, 6, 4), date(2026, 11, 11),
            date(2026, 12, 24)} <= swieta


def test_jezdzi_tylko_w_dni_robocze():
    assert polinka.runs_on(WTOREK)
    assert not polinka.runs_on(date(2026, 10, 17))     # sobota
    assert not polinka.runs_on(date(2026, 11, 11))     # środa, święto


def test_odjazdy_omijaja_przerwe_i_zamkniecie():
    """Kabina, która nie zdąży przed przerwą albo przed 18:00, nie rusza."""
    odjazdy = polinka.departures()
    assert odjazdy[0] == 7 * 3600
    assert odjazdy[-1] + polinka.RIDE_SEC <= polinka.CLOSE_SEC
    przerwa_od, przerwa_do = polinka.BREAK_SEC
    assert all(d + polinka.RIDE_SEC <= przerwa_od or d >= przerwa_do
               for d in odjazdy)
    assert przerwa_do in odjazdy


def test_stacje_zawsze_przejazdy_tylko_w_dni_kursowania():
    """Do stacji da się dojść także w sobotę - kursów wtedy nie ma."""
    sobota, wtorek = gtfs.DayData(), gtfs.DayData()
    polinka.augment_day(sobota, date(2026, 10, 17))
    polinka.augment_day(wtorek, WTOREK)

    assert set(sobota.stop_names) == set(wtorek.stop_names) == {WW, NG}
    assert sobota.conns == []
    assert len(wtorek.conns) == 2 * len(polinka.departures())
    dep, arr, skad, dokad, kurs = wtorek.conns[0]
    assert arr - dep == polinka.RIDE_SEC and {skad, dokad} == {WW, NG}
    assert polinka.trip_rows(kurs) == [(skad, dep, dep), (dokad, arr, arr)]


def _dzien_z_polinka():
    """Autobus z NG na WW jedzie naokoło pół godziny; Polinka - trzy minuty."""
    day = make_day([
        {"trip_id": "naokolo", "label": "Autobus 146", "headsign": "WW",
         "stops": [(NG, 7 * 3600 + 120, 7 * 3600 + 120),
                   (WW, 7 * 3600 + 1920, 7 * 3600 + 1920)]},
    ], names={stop_id: name for stop_id, (name, _, _) in polinka.STATIONS.items()})
    for dep in polinka.departures():
        trip = polinka.trip_id(NG, WW, dep)
        day.trip_info[trip] = (polinka.LABEL, polinka.STATIONS[WW][0])
        day.conns.append((dep, dep + polinka.RIDE_SEC, NG, WW, trip))
    day.conns.sort(key=lambda c: c[0])
    day.dep_times = [c[0] for c in day.conns]
    return day


def test_wyszukiwarka_jedzie_polinka(install_day):
    install_day(_dzien_z_polinka())
    wynik = planner.plan_flow(polinka.STATIONS[NG][0], polinka.STATIONS[WW][0],
                              datetime(2026, 10, 13, 7, 0))

    pierwsza = wynik["journeys"][0]
    etap = pierwsza["legs"][0]
    assert etap["num"] == "Polinka" and etap["mode"] == "cable"
    assert pierwsza["arrival"] == "07:03"
