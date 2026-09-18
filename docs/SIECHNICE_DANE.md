# Skąd wziąć rozkład Siechnickiej Komunikacji Publicznej

Notatka z rozpoznania do [issue #11](https://github.com/Metal-Pipe-Org/Metal-Planner/issues/11).
Stan: sierpień 2026. Kod, który z tego wynikł, jest w [`siechnice.py`](../siechnice.py).

## Czego szukamy

Linie gminy Siechnice: **800, 810, 83, 84, 85, 89, 860, 870, 890** (w API
występują parami, np. `800+80`, `89+890`). Organizatorem jest Gmina Siechnice,
przewoźnikiem Marek Wierzbicki TRAKO sp. z o.o., a kursy wewnątrzgminne
obsługuje PKS Oława. Wcześniej były to linie z serii 900 - zlikwidowane
i przenumerowane od 1 marca.

## Czego NIE ma — sprawdzone, nie domniemane

| Źródło | Wynik |
| --- | --- |
| Otwarte Dane Wrocławia (paczka GTFS, której używa projekt) | brak - żadna z linii 800/810/83…890 nie występuje w `data/gtfs.sqlite` (135 różnych `route_short_name`) |
| dane.gov.pl | `q=Siechnice` → 1 trafienie, i to spółka mieszkaniowa. `q=GTFS` → 6 zbiorów w całym kraju (GZM ×3, Wrocław, 2 fałszywe trafienia) |
| Krajowy Punkt Dostępowy (KPD/MMTIS) | rejestr to arkusz na [dane.gov.pl/pl/dataset/1739](https://dane.gov.pl/pl/dataset/1739), 900 wierszy. `Siechnic` → 0 trafień, `Trako` → 0. Gmina nigdy się nie zgłosiła, mimo obowiązku z rozporządzenia (UE) 2017/1926 |
| mkuran.pl/gtfs | tylko Warszawa, WKD, pociągi PL, Tokio - nic dolnośląskiego |
| transit.land / Mobility Database / transitfeeds | 401 / token / 403 (serwis wygaszony) - i tak nie mają czego indeksować |
| przewozy.trako.com.pl | HTTP 500, „Błąd połączenia z bazą danych" - strona przewoźnika nie działa |
| siechnice.gmina.pl | wyłącznie PDF-y (do tego zepsuty łańcuch TLS - `curl -k`) |

Portal mieszkańców `komunikacja.siechnice.com.pl` sam to przyznaje:

> Dane o rozkładach nowych linii 800-870 nie są publikowane w ramach Otwartych
> Danych miasta Wrocław. Z tego mechanizmu korzystają m.in. aplikacja
> Jakdojade, serwis miejski Wrocławia czy nasz dział komunikacja. Po zmianach
> zarówno nasz serwis jak i wymienione wcześniej nie mają bezpośredniego
> dostępu do danych przewozowych.

Backend tego portalu (`/api/stops`, `/api/home/…`) dziś zwraca 404 - serwis jest
martwy. Time4BUS (`time4bus.com`) ma publiczne API i 71 miast, ale Siechnic
wśród nich nie ma.

## Skąd ma je jakdojade.pl

Z **umowy, nie z otwartych danych**. Komunikat gminy z 25.10.2019
([siechnice.gmina.pl](https://siechnice.gmina.pl/aktualnosc-3443-jakdojade_pl_wjezdza_do_siechnic.html)):

> dzięki porozumieniu nawiązanemu pomiędzy Wydziałem Komunalnym Urzędu
> Miejskiego w Siechnicach a City-nav sp. z o.o., mieszkańcy gminy Siechnice
> mogą zaplanować podróże korzystając z wyszukiwarki jakdojade.pl. Współpraca
> zaowocuje systematycznym przesyłaniem rozkładów jazdy do wyszukiwarki.

Czyli: gmina wysyła rozkłady bezpośrednio do City-nav (operatora jakdojade).
Żadnego GTFS-a, żadnego publicznego kanału - dwustronne porozumienie. Warto
odnotować, że jest starsze niż przenumerowanie linii, a portal mieszkańców
twierdzi, że po zmianach jakdojade też straciło bezpośredni dostęp.

## Skąd ma je „kiedy przyjedzie"

Bo gmina **jest jego klientem**. `siechnice.kiedyprzyjedzie.pl` to wdrożenie
systemu informacji pasażerskiej firmy **Operibus sp. z o.o.** (ul. Jedności
Narodowej 234/3, Wrocław - firma wrocławska), z danymi wprost od organizatora,
razem z pozycjami pojazdów w czasie rzeczywistym.

To jedyne istniejące strukturalne źródło tych rozkładów. Jego API jest
niedokumentowane, ale kompletne - kontrakt odtworzony z bundla aplikacji
opisuje nagłówek [`siechnice.py`](../siechnice.py):

- `GET /stops` — 237 słupków z nazwami i współrzędnymi,
- `GET /api/directions/<słupek>` — linie i kierunki na słupku,
- `GET /api/timetables/<słupek>?date=RRRR-MM-DD` — cały dzień odjazdów,
  z `trip_id` i `index` (pozycja słupka w kursie),
- `GET /api/departures/<słupek>` — najbliższe odjazdy, z czasem rozkładowym
  i rzeczywistym (tego jeszcze nie używamy).

Z `trip_id` + `index` odtwarza się kompletne kursy - to dokładnie tyle
informacji, ile ma GTFS. **Nie trzeba tykać PDF-ów ani OSM-a**, wbrew temu, co
zakładała pierwsza diagnoza w issue.

## Dlaczego mimo to jest domyślnie wyłączone

`https://siechnice.kiedyprzyjedzie.pl/robots.txt` to `User-agent: * / Disallow: /`.
Regulaminu nie ma (`kiedyprzyjedzie.pl/regulamin` → 404), dokumentacji API nie
ma, deklaracji o ponownym wykorzystaniu danych nie ma. To najgorszy możliwy
zestaw: zgody nikt nie udzielił i nie ma nawet warunków, których dałoby się
dotrzymać.

Dlatego `siechnice.py` pobiera cokolwiek tylko po jawnym `SIECHNICE_ENABLED=on`,
z wymuszoną przerwą między zapytaniami i własnym `User-Agent`.

## Co zrobić, żeby było legalnie i trwale

Poprosić gminę o eksport GTFS z systemu, który już ma. **To prośba o włączenie
funkcji, nie o zbudowanie czegokolwiek** - w tabeli KPD widać inne wdrożenia
Operibusa, które GTFS-a już wystawiają:

- wiersz 68, MPK Zduńska Wola, `zdw.kiedyprzyjedzie.pl`, formaty
  „HTML, JSON, GTFS static GTFS realtime, PDF",
- wiersz 21, Gmina Oborniki, `oborniki.kiedyprzyjedzie.pl`, „GTFS realtime", API = TAK.

Adresat: **Wydział Komunalny, Urząd Miejski w Siechnicach** (to on jest
właścicielem danych i to on podpisał porozumienie z City-nav), do wiadomości
`biuro@operibus.pl`. `info@kiedyPrzyjedzie.pl` to infolinia pasażerska - zły
kanał.

Argument dodatkowy, uprzejmy i faktyczny: rozporządzenie (UE) 2017/1926
zobowiązuje organizatora do udostępnienia statycznych danych rozkładowych przez
Krajowy Punkt Dostępowy, a gminy Siechnice nie ma w rejestrze KPD w żadnym
wierszu.

### Szkic pisma

> Szanowni Państwo,
>
> tworzymy otwartą, niekomercyjną wyszukiwarkę połączeń komunikacji miejskiej
> aglomeracji wrocławskiej. Korzystamy z danych GTFS publikowanych przez
> Wrocław, ale nie obejmują one linii Siechnickiej Komunikacji Publicznej,
> przez co połączenia do i z gminy nie są w niej widoczne.
>
> Zwracamy się z prośbą o udostępnienie rozkładów jazdy tych linii w formacie
> GTFS. Wiemy, że system kiedyPrzyjedzie (Operibus sp. z o.o.), z którego
> korzysta gmina, potrafi taki eksport wystawiać - robi to m.in. dla MPK
> Zduńska Wola i Gminy Oborniki, co figuruje w rejestrze Krajowego Punktu
> Dostępowego. Po stronie gminy byłoby to włączenie istniejącej funkcji,
> a nie nowe wdrożenie.
>
> Przy okazji uprzejmie zwracamy uwagę, że rozporządzenie delegowane Komisji
> (UE) 2017/1926 przewiduje udostępnianie statycznych danych o rozkładach przez
> Krajowy Punkt Dostępowy, a gmina Siechnice nie występuje obecnie w jego
> rejestrze.
>
> Z wyrazami szacunku,

## Co dalej, gdy feed się pojawi

Warstwa przekształceń w `siechnice.py` przestaje być potrzebna - zostaje
`merge_into()`, a wejściem staje się zwykły zip GTFS, czytany tak samo jak
wrocławski. Sklejanie słupków ze wspólnymi (`match_existing_stops`) zostaje
niezależnie od źródła: linie 800/810 dojeżdżają na wrocławskie Bardzką i Suchą
przez Iwiny, a bez sklejenia byłyby to dwa osobne markery bez przesiadki.

## Wynik na żywych danych (27.08.2026)

Pełne przejście po 237 słupkach na 3 dni rozkładu, ok. 95 s na dzień:

- **linie z kursami:** 800+80, 810+81, 83, 84, 85, 86+860, 87+870, 89+890
  (w `/api/directions` widać dodatkowo 59, 59A, 60, 61, 61A — na tych
  słupkach nie było w tym oknie żadnych odjazdów);
- **kursy:** 303 w czwartek i piątek, 173 w sobotę — proporcja zgodna
  z rozkładem dnia roboczego i weekendu;
- **kursów bez rozpoznanej linii: 0** na 779 — przecięcie zbiorów linii
  rozstrzyga numer za każdym razem;
- **słupki:** 55 sklejonych z wrocławskimi, 182 nowe, zero nazw
  występujących równolegle w obu źródłach (czyli nic się nie zdublowało);
- wyszukiwanie `Siechnice - Urząd Miejski` → `GALERIA DOMINIKAŃSKA` zwraca
  trasę łączącą autobus gminny z komunikacją Wrocławia.

### Pułapka wydajnościowa, na wypadek gdyby ktoś to przepisywał

Pierwsza wersja klienta używała `urllib.request.urlopen`, czyli nowego
połączenia TLS na każde zapytanie — i co kilkanaste zapytanie wisiało 20–60 s
(czasy retransmisji SYN), niezależnie od tempa pytania. Przejście po jednym
słupku zajmowało ~20 minut. Na **połączeniu trwałym** te same zapytania idą
po ~0,14 s i nie zwiecha się ani jedno. To nie było przeciążenie serwera
naszym tempem — zwolnienie do 1,5 s między zapytaniami nic nie dało.
