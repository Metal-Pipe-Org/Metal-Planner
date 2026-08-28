> **ZROBIONE 2026-08-27 wieczorem — wszystkie sześć zadań z dołu tego pliku.**
> Przyczyna trybu awaryjnego: kotwica początku w `_select_and_anchor` pytała
> „czy kurs się tu zaczyna" zamiast „czy da się tu wsiąść", więc kurs
> wyjeżdżający z pętli końcowej i mijający start w swoim środku nie miał się
> o co zakotwiczyć. Naprawione tam i w `_extract_transfer_graph`; tryb
> awaryjny zostaje, ale mówi o sobie wprost — na ekranie, małym komunikatem
> nad listą, takim samym jak pozostałe błędy. Emulator frontu stoi
> na JavaScriptCore (`tests/js/`, 11 testów punktów 7, 8 i 10), sprawdzony
> pięcioma mutacjami `app.js`. Pomiary, decyzje i ograniczenia — w
> [FLOW_MAP_NOTES.md](FLOW_MAP_NOTES.md), wpisy z 2026-08-27.
> Poniżej zostaje oryginalna diagnoza, dla porządku.

# Do zrobienia: emulator frontu i tryb awaryjny mapy

Plik przekazania, spisany 2026-08-27 na koniec sesji „czas na mapie”.
Zawiera dwie osobne, ale powiązane sprawy. Obie były zdiagnozowane, żadna
nie była naprawiana. Zaczynając od czystego kontekstu: przeczytaj ten plik,
potem [FLOW_MAP_CONTRACT.md](FLOW_MAP_CONTRACT.md).

---

## Sprawa 1: tryb awaryjny łamie kontrakt i nikt tego nie widzi

### Co to jest

W `planner.plan_flow` jest gałąź `else` na wypadek, gdyby `_select_and_anchor`
przyciął WSZYSTKIE segmenty do zera. Rysuje wtedy samą najszybszą trasę (plus
jej wariant bez nieopłacalnej przesiadki), z jasnością wpisaną na sztywno:
`waga = 1.0` dla wariantu proponowanego, `0.6` dla drugiego.

W komentarzu w kodzie jest to nazwane „skrajnym, rzadkim przypadkiem”.

### Nie jest ani skrajny, ani rzadki — zmierzone

Liczba kandydatów z `_discover_segments` i tych, które przeżyły
`_select_and_anchor`, dla realnych zapytań (2026-08-27, okno 125%, floor 5 min,
cap 15 min):

| relacja | godzina | kandydatów | zatrzymanych |
|---|---|---|---|
| Sosnowiecka → Borowska (Szpital) | 21:10 | 7 | 7 |
| **Sosnowiecka → Wojszyce** | **15:37** | **31** | **0 ← tryb awaryjny** |
| KSIĘŻE MAŁE → Wojszyce | 22:37 | 120 | 103 |
| Sosnowiecka → Wojszyce (okno 200%) | 15:37 | 403 | 376 |
| Biskupin → Dworzec Główny | 17:00 | 6 | 6 |

Czyli zwykła, dzienna relacja przez pół miasta wpada w tryb awaryjny przy
domyślnym oknie, a przy szerszym oknie nagle zatrzymuje 376 segmentów. Skok
jest gwałtowny: 125% i 150% → 0 zatrzymanych, 175% → 376.

### Dlaczego wszystko ginie (wstępna diagnoza, do potwierdzenia)

Dla Sosnowiecka → Wojszyce o 15:37 (okno 15:37 → 16:33, deadline 16:47):

- kandydatów: **31**
- zaczyna się na przystanku startowym: **1**
- ma wyjście prosto na cel: **3**
- robi obie rzeczy naraz (dowiózłby sam): **0**

`_select_and_anchor` to punkt stały, w którym każdy segment musi być
zakotwiczony z OBU stron o inne ZATRZYMANE segmenty. Skoro nic nie dowozi
samo, cała sieć musi się zazębić w łańcuch start → przesiadka → … → cel.
Wystarczy jedno słabe ogniwo i punkt stały rozplątuje całość do zera —
zachowanie jest „wszystko albo nic”, bez stanów pośrednich.

Podejrzenie do sprawdzenia: jedyny segment startowy to `Autobus 124` o
zaledwie 3 przystankach. Jeśli nie zazębia się z niczym, co prowadzi dalej,
ginie pierwszy i pociąga za sobą resztę.

### Dlaczego to łamie kontrakt

Ocena do zatwierdzenia przez użytkownika, ale wygląda jednoznacznie:

- **Punkt 1 („cały wachlarz, nie jedna trasa”)** — tryb awaryjny rysuje
  dokładnie jedną trasę i jej wariant. To jest dosłownie to, czego punkt 1
  zakazuje.
- **Punkt 9 („pełny zakres jasności zawsze wykorzystany”)** — jasności `1.0`
  i `0.6` są wpisane w kod, nie policzone z niczego. Skala nie jest ani pełna,
  ani wyliczona.
- **Punkt 2 („sensowność względem najlepszej trasy”)** — nic nie jest tu
  mierzone względem najlepszej trasy; najlepsza trasa JEST całą zawartością.

Do tego mapa nie mówi ani słowa o tym, że jest w trybie zdegradowanym.
Użytkownik widzi rzadką mapę i nie ma jak odróżnić „tędy naprawdę nic nie
jedzie” od „algorytm się poddał”.

### Co z tym zrobić (propozycja kolejności)

1. Napisać test, który to ŁAPIE: zbiór realnych relacji × godzin, asercja
   „`kept` nigdy nie jest puste”. Ten test ma teraz padać — to jego zadanie.
2. Dopiero potem zrozumieć, czemu punkt stały rozplątuje wszystko, i
   naprawić przyczynę (nie łatać objawu — patrz zasada „root cause over
   patches”).
3. Zdecydować, czy tryb awaryjny w ogóle ma zostać. Jeśli tak, to musi
   przestać udawać zwykłą mapę: albo liczyć jasności uczciwie, albo mówić
   wprost, że jest trybem awaryjnym.

### Jak to odtworzyć

```
.venv/bin/python -c "
from datetime import datetime; import planner
r = planner.plan_flow('Sosnowiecka','Wojszyce', datetime(2026,8,27,15,37),
                      extra_pct=125, extra_floor_sec=300, extra_cap_sec=900)
print(len(r['segments']), 'segmentow')"
```

Uwaga na jednostki: `extra_pct` to **procent** (125), nie ułamek (1.25).
Podanie 1.25 cicho spada na `extra_floor_sec` i daje zupełnie inne okno —
łatwo się na tym przejechać.

---

## Sprawa 2: nie ma emulatora frontu, więc pół kontraktu nie jest testowane

### Problem

Kontrakt mapy przepływów ma 10 punktów. Testy (`tests/`, 84 sztuki) sprawdzają
wyłącznie backend — `planner.py`. Tymczasem kilka punktów mieszka W CAŁOŚCI
albo w połowie we froncie (`static/app.js`) i nie jest sprawdzane niczym:

- **Punkt 7** („zawsze wiadomo, co tam jedzie”) — kondensacja numerów w grupki,
  brak nachodzenia, kursor nazywający dokładnie jedną linię. To wszystko jest
  w `placeLineLabels`, `clusterBox`, `flowHitsAt`, `corridorOptions`.
- **Punkt 8** („minimalna jasność nigdy nie spada do niewidoczności”) — progi
  `LOOK_DEFAULTS` i mapowanie `w` → opacity/grubość.
- **Punkt 10** („mapa mówi, ile to trwa i o której”) — interpolacja godziny
  między przystankami, kropka na linii, pasek nad mapą.

Nic z tego nie ma testu. Zmiana w `app.js` może po cichu złamać punkt
kontraktu i nikt się nie dowie.

To wyszło na jaw, gdy okazało się, że nie da się zweryfikować nawet tak
prostej rzeczy jak „czy kliknięcie checkboxa odświeża mapę”. Na maszynie nie
ma `node`, a AppleScript do żywej karty Vivaldi kończy się timeoutem
(przeglądarka blokuje JS z Apple Events).

### Co jest potrzebne

Emulator/harness, który pozwala uruchomić logikę mapy bez przeglądarki:

- podstawiony, minimalny Leaflet (`L.latLng`, `L.polyline`, `L.circleMarker`,
  `L.layerGroup`, `L.tooltip`, `L.divIcon`) i minimalna mapa (rzut latlng →
  piksele, `getBounds`, `getSize`);
- podstawiony DOM na tyle, żeby `$()`, `addEventListener` i `innerHTML`
  działały;
- wejście: prawdziwa odpowiedź `/api/flow` zapisana jako fixture.

Wtedy da się asertować wprost:

- grupki numerów nie nachodzą na siebie (punkt 7);
- najbledszy kawałek ma opacity ≥ próg (punkt 8);
- godzina na każdym przystanku wychodzi dokładnie z rozkładu, a między
  przystankami rośnie monotonicznie (punkt 10);
- przełącznik zmienia rysunek (regresja, której nie umieliśmy sprawdzić).

### Zaczątek, który już działa

W tej sesji powstał jednorazowy test dokładnie tego typu — wyciąga funkcje
z `app.js` po nazwie, podstawia atrapę Leafletu i puszcza je na prawdziwym
kawałku z API, przez `osascript -l JavaScript` (JavaScriptCore jest w systemie,
`node` nie ma). Sprawdzał interpolację godzin i przeszedł:

```
kotwice przystankow rosna: true
max blad na samych przystankach [s]: 0
godzina rosnie wzdluz linii: true
kropka odleglosc od linii [m]: 0.0
```

To jest dowód, że taki emulator da się zrobić bez dokładania zależności do
projektu. Trzeba go tylko zrobić porządnie i wpiąć w `tests/`.

### Decyzja do podjęcia

Czy `osascript -l JavaScript` (JavaScriptCore, zero zależności, tylko macOS)
wystarczy, czy wchodzimy w `node` + `jsdom` (przenośne, ale dokłada zależność
i build step do projektu, który dziś nie ma żadnego).

---

## Zadania, jednym ciągiem

1. Test łapiący pusty `kept` (tryb awaryjny) — ma teraz padać.
2. Znaleźć i naprawić przyczynę rozplątywania się punktu stałego w
   `_select_and_anchor`.
3. Rozstrzygnąć los trybu awaryjnego: usunąć, naprawić jasności albo oznaczyć
   go wprost na mapie.
4. Zdecydować: JavaScriptCore czy node+jsdom.
5. Zbudować emulator frontu i wpiąć w `tests/`.
6. Dopisać testy punktów 7, 8 i 10 kontraktu.
