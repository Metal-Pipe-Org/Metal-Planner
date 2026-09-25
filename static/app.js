/* Planer podróży - cały frontend: mapa Leaflet, panel wyszukiwania,
   lista propozycji tras i Ustawienia Developerskie.

   Dwa widoki tej samej odpowiedzi na to samo pytanie:
   - MAPA PRZEPŁYWÓW (/api/flow) - wachlarz wszystkich sensownych opcji,
     jasność = jak dobra opcja;
   - LISTA PROPOZYCJI (/api/journeys) - kilka z nich nazwanych po imieniu,
     z godzinami i przesiadkami. Wybór pozycji na liście podświetla ją na
     mapie i przygasza resztę przepływu. */

const map = L.map('map', {preferCanvas: true, zoomControl: false})
    .setView([51.107, 17.038], 13);
L.control.zoom({position: 'bottomright'}).addTo(map);
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; OpenStreetMap',
    // Kafelki pobierane z CORS zamiast "no-cors": service worker dostaje
    // wtedy normalną odpowiedź zamiast nieprzejrzystej, a te przeglądarka
    // rozlicza z limitu miejsca po ~7 MB za sztukę niezależnie od rozmiaru.
    crossOrigin: true,
}).addTo(map);

const $ = id => document.getElementById(id);

// Stan paneli (schowany/rozwinięty) i ostatnie wyszukiwanie zapisujemy
// w localStorage pod wspólnym kluczem - przeżywa odświeżenie strony i nowe
// wizyty. Jeden mały odczyt/zapis JSON-a na zmianę, nie warto osobnych kluczy
// na każde pole.
const UI_STATE_KEY = 'metal-planner:ui-state';

function loadUiState() {
    try {
        return JSON.parse(localStorage.getItem(UI_STATE_KEY)) || {};
    } catch {
        return {};
    }
}

function saveUiState(patch) {
    try {
        localStorage.setItem(UI_STATE_KEY, JSON.stringify({...loadUiState(), ...patch}));
    } catch {
        // localStorage niedostępny (tryb prywatny) - panel działa dalej, po prostu się nie zapamięta
    }
}

const uiState = loadUiState();

const sidebar = $('sidebar');
document.body.classList.toggle('panel-hidden', !!uiState.sidebarHidden);
$('sidebar-toggle').addEventListener('click', () => {
    const hidden = document.body.classList.toggle('panel-hidden');
    saveUiState({sidebarHidden: hidden});
});

// Ustawienia Developerskie są schowane za przyciskiem - normalny użytkownik
// nie ma po co go widzieć, a strojenie algorytmu musi zostać pod ręką.
const devPanel = $('dev-panel');
const devToggle = $('dev-toggle');
if (devPanel) {
    const setDev = (open, persist = true) => {
        devPanel.classList.toggle('hidden', !open);
        devToggle.classList.toggle('active', open);
        devToggle.setAttribute('aria-expanded', String(open));
        // Panel ⚙ i okienko z rozkładem zajmują ten sam róg - klasa na <body>
        // odsuwa okienko, żeby dało się widzieć oba naraz (jego własne
        // przełączniki siedzą właśnie w tym panelu).
        document.body.classList.toggle('dev-open', open);
        if (persist) saveUiState({devOpen: open});
    };
    setDev(!!uiState.devOpen, false);
    devToggle.addEventListener('click', () => setDev(devPanel.classList.contains('hidden')));
    $('dev-close').addEventListener('click', () => setDev(false));
}
// Panel wyboru dnia
const dayPanel = $('day-panel');
const dayToggle = $('day-toggle');

if (dayPanel && dayToggle) {
    // Sterowanie widocznością panelu
    const setDay = (open, persist = true) => {
        dayPanel.classList.toggle('hidden', !open);
        dayToggle.classList.toggle('active', open);
        dayToggle.setAttribute('aria-expanded', String(open));
        if (persist) saveUiState({ dayOpen: open });
    };

    setDay(!!uiState.dayOpen, false);
    dayToggle.addEventListener('click', () => setDay(dayPanel.classList.contains('hidden')));
}

// Pole godziny to zwykły tekst, nie <input type="time"> - natywny widget w
// niektórych przeglądarkach (np. Safari) pokazuje AM/PM zależnie od ustawień
// regionalnych systemu i ignoruje atrybut lang strony, więc format 24h nie
// dało się wymusić inaczej niż samodzielnym formatowaniem.
const timeInput = $('time');
timeInput.addEventListener('input', () => {
    let digits = timeInput.value.replace(/\D/g, '').slice(0, 4);
    if (digits.length > 2) digits = digits.slice(0, 2) + ':' + digits.slice(2);
    timeInput.value = digits;
});
timeInput.addEventListener('blur', () => {
    const match = timeInput.value.match(/^(\d{1,2}):?(\d{1,2})?$/);
    if (!match) { timeInput.value = ''; return; }
    const hours = Math.min(23, parseInt(match[1], 10) || 0);
    const minutes = Math.min(59, parseInt(match[2], 10) || 0);
    timeInput.value = String(hours).padStart(2, '0') + ':' + String(minutes).padStart(2, '0');
});

// Na telefonie panel i mapa nie mieszczą się naraz, więc zamiast nachodzić
// na siebie przełączają się zakładkami (na szerokim ekranie klasy widoku
// nic nie robią - tam widać jedno i drugie). Karta wyszukiwania zostaje
// widoczna w obu widokach; zakładki przełączają to, co pod nią.
const viewTabs = $('view-tabs');
const tabCount = $('tab-count');

/** Zakładki są trzy, a stan, który opisują, ma dwa wymiary: widok
    (mapa/lista) i tryb panelu (wyszukiwarka/rozkłady - patrz timetable.js).
    "Trasy" i "Rozkład" to ta sama lista w dwóch trybach, więc podświetlenie
    liczy się z obu klas na <body>, zamiast pamiętać ostatnio kliknięte:
    w tryb rozkładów wchodzi się także spoza paska (pływające ◷, przycisk
    "trasa" przy propozycji) i zakładki mają za tym nadążyć. */
function syncTabs() {
    if (!viewTabs) return;
    const current = !document.body.classList.contains('view-list') ? 'map'
        : document.body.classList.contains('mode-timetable') ? 'timetable'
        : 'list';
    for (const tab of viewTabs.querySelectorAll('.tab')) {
        const on = tab.dataset.view === current;
        tab.classList.toggle('active', on);
        tab.setAttribute('aria-pressed', String(on));
    }
}

/** Sam widok; trybu panelu NIE rusza. Rozkłady wołają to po wczytaniu treści
    ("pokaż listę"), a nie po to, żeby wrócić do wyszukiwarki. */
function setView(view) {
    if (!viewTabs) return;
    document.body.classList.toggle('view-list', view === 'list');
    document.body.classList.toggle('view-map', view !== 'list');
    syncTabs();
}

if (viewTabs) {
    viewTabs.addEventListener('click', event => {
        const tab = event.target.closest('.tab');
        if (!tab) return;
        const view = tab.dataset.view;
        // "Mapa" zostawia tryb taki, jaki jest: w rozkładach mapa to sposób
        // wybrania przystanku (klik w słupek), a nie wyjście z rozkładów.
        if (view !== 'map' && window.timetableMode) {
            window.timetableMode.setMode(view === 'timetable');
        }
        setView(view === 'map' ? 'map' : 'list');
    });
    setView('map');
}

const startInput = $('start');
const endInput = $('end');
const resultsBox = $('results');

if (startInput) {   // brak tych pól = brak bazy rozkładów (panel pokazuje błąd)

// ---------------------------------------------------------------- stan ----

// start/end: nazwa przystanku (string) ALBO dowolny punkt mapy ({lat, lon}) -
// obie strony niezależnie, dowolna kombinacja.
let sel = {start: null, end: null};
let journeys = [];
let selectedJourney = null;   // indeks pozycji z listy albo null
let requestToken = 0;         // odsiewa odpowiedzi na nieaktualne zapytania
let resultsCollapsed = !!uiState.resultsCollapsed; // ukrywa samą listę propozycji (nie cały panel)

const isPoint = v => v !== null && typeof v === 'object';
const samePlace = (a, b) => isPoint(a) || isPoint(b)
    ? isPoint(a) === isPoint(b) && a.lat === b.lat && a.lon === b.lon
    : a === b;

// Grupa stacji jednego miasta (patrz gtfs._match_city_group) przyjeżdża
// z serwera jako "WROCŁAW -". Myślnik to konwencja słownika PKP i ZARAZEM
// kształt, po którym wyszukiwarka rozpoznaje takie zapytanie, więc nazwa nie
// może zmienić postaci w drodze na serwer - zmieniamy tylko to, co widać:
// prettyStopName na ekran, rawStopName z powrotem przed wysłaniem.
//
// Zawsze PARĄ, nigdy samo prettyStopName: samo ładne wyświetlanie oznacza, że
// ładna nazwa siedzi w polu formularza, a stamtąd leci prosto do /api/flow -
// i wraca jako "nie znaleziono przystanku" (błąd zgłoszony na żywo, przez
// który etykieta wróciła kiedyś do myślnika - patrz docstring
// gtfs._match_city_group). Stan (`sel`), klucze markerów i zapis ostatniego
// wyszukiwania zostają przy postaci KANONICZNEJ, z myślnikiem.
const CITY_GROUP_LABEL = '(dowolna stacja)';
const prettyStopName = name => typeof name === 'string' && name.trimEnd().endsWith('-')
    ? `${name.trimEnd().slice(0, -1).trimEnd()} ${CITY_GROUP_LABEL}`
    : name;
const rawStopName = name => typeof name === 'string' && name.trimEnd().endsWith(CITY_GROUP_LABEL)
    ? `${name.trimEnd().slice(0, -CITY_GROUP_LABEL.length).trimEnd()} -`
    : name;

const displayValue = v => !v ? ''
    : isPoint(v) ? `(${v.lat.toFixed(4)}, ${v.lon.toFixed(4)})` : prettyStopName(v);

function esc(text) {
    const div = document.createElement('div');
    div.textContent = text ?? '';
    return div.innerHTML;
}

// `car` to ostatni etap propozycji z Traficarem (patrz planner._car_drive_leg) -
// fiolet, bo tym kolorem jeżdżą te auta i po nim się je poznaje na ulicy.
const LINE_COLORS = {tram: '#c62828', bus: '#1565c0', train: '#2e7d32',
                     bike: '#ef6c00', car: '#7b2ff2', other: '#6a1b9a'};
const WALK_COLOR = '#455a64';   // dojscie pieszo - patrz --walk w style.css
const MODE_LABEL = {tram: 'Tramwaj', bus: 'Autobus', train: 'Pociąg',
                    bike: 'Rower miejski', car: 'Traficar', other: 'Linia'};

/** Autko do wiersza "jedziesz autem" na osi trasy. Bierze `currentColor`, tak
    jak ROUTE_ICON, więc nie ma własnego koloru do pamiętania. */
const CAR_ICON =
    '<svg class="tl-car-icon" viewBox="0 0 24 14" aria-hidden="true">'
    + '<path d="M3 9.5 4.3 5.2A2.2 2.2 0 0 1 6.4 3.6h11.2a2.2 2.2 0 0 1 2.1 1.6'
    + 'L21 9.5v2.6h-2.6v-1.4H5.6v1.4H3z"/>'
    + '<circle cx="7.4" cy="9.4" r="1.5"/><circle cx="16.6" cy="9.4" r="1.5"/></svg>';

// ------------------------------------------------------- markery na mapie ----

const markersByName = new Map();          // nazwa -> [L.circleMarker, ...]
const stopsLayer = L.layerGroup();        // wszystkie słupki naraz
const stopKind = new Map();               // nazwa -> 'stop' (MPK) | 'train' (PKP)

const BASE_STYLE = {radius: 4, weight: 1, color: '#1565c0',
                    fillColor: '#42a5f5', fillOpacity: 0.8};
// Stacje PKP (patrz pkp.py) - kolor spójny z plakietką linii kolejowej
// (--train w style.css), żeby na pierwszy rzut oka było widać, że to nie
// zwykły słupek MPK, zanim jeszcze ktoś najedzie kursorem na nazwę.
const TRAIN_STYLE = {radius: 5, weight: 1, color: '#1b5e20',
                     fillColor: '#2e7d32', fillOpacity: 0.85};
// Gdy pokazujemy przepływy, zwykłe przystanki schodzą na dalszy plan.
const DIM_STYLE = {radius: 2.5, weight: 0, color: '#90a4ae',
                   fillColor: '#90a4ae', fillOpacity: 0.25};
let baseDimmed = false;
// W rozkładach mapa mówi o linii albo o przystanku, a nie o relacji wpisanej
// w wyszukiwarkę - zieleń startu i czerwień celu opisują wtedy pytanie,
// którego na ekranie nie ma (patrz suspendPlanner).
let plannerSuspended = false;

function styleFor(name) {
    if (!plannerSuspended) {
        if (name === sel.start) return {radius: 8, weight: 2, color: '#1b5e20',
                                        fillColor: '#4caf50', fillOpacity: 1};
        if (name === sel.end) return {radius: 8, weight: 2, color: '#b71c1c',
                                      fillColor: '#ef5350', fillOpacity: 1};
    }
    if (baseDimmed) return DIM_STYLE;
    return stopKind.get(name) === 'train' ? TRAIN_STYLE : BASE_STYLE;
}

function setBaseDim(dim) {
    if (baseDimmed === dim) return;
    baseDimmed = dim;
    for (const [name, markers] of markersByName) {
        const style = styleFor(name);
        for (const m of markers) m.setStyle(style);
    }
}

function restyle(...names) {
    for (const name of names) {
        if (!name) continue;
        for (const m of markersByName.get(name) || []) m.setStyle(styleFor(name));
    }
}

// Markery klikniętych punktów (poza markersByName - nie są prawdziwym
// przystankiem). Ten sam kolor co podświetlenie start/cel, ale przerywana
// obwódka - łatwo odróżnić od słupka.
const pointMarkers = {start: null, end: null};
const POINT_STYLE = {
    start: {radius: 9, weight: 3, dashArray: '2,4', color: '#1b5e20',
            fillColor: '#4caf50', fillOpacity: 0.9},
    end: {radius: 9, weight: 3, dashArray: '2,4', color: '#b71c1c',
          fillColor: '#ef5350', fillOpacity: 0.9},
};

function updatePointMarker(slot, value) {
    if (pointMarkers[slot]) {
        map.removeLayer(pointMarkers[slot]);
        pointMarkers[slot] = null;
    }
    if (isPoint(value)) {
        pointMarkers[slot] = L.circleMarker(
            [value.lat, value.lon], POINT_STYLE[slot],
        ).addTo(map);
    }
}

// -------------------------------------------------- pojazdy na mapie ----
//
// Warstwa markerów dokładana NAD słupkami: przycisk ◉ w nagłówku pokazuje
// żywe pozycje autobusów/tramwajów z /api/vehicles (backend odpytuje
// mpk.wroc.pl/bus_position - CORS nie pozwala zrobić tego wprost z
// przeglądarki, patrz vehicles.py). Słupki zostają na mapie: mówią, gdzie
// można wsiąść, a to inne pytanie niż to, co akurat jedzie.
//
// Punktem odniesienia jest MAPA: gdy stoi na niej wachlarz przepływów,
// warstwa zawęża się do LINII, które są na nim narysowane (patrz
// vehiclesFilter). Reszta miasta odpowiada na inne pytanie niż to, które
// zadał ktoś, rysując tę mapę - i tylko ją zasłania. W rozkładach zawężeniem
// rządzi to, co stoi na ekranie: rozkład linii - ta linia, tablica przystanku
// - zaznaczone linie tablicy. Bez jednego i drugiego nie ma czego zawężać
// i widać wszystko, co jeździ.
//
// ODSTAWIONE (2026-09-13): zawężanie dalej, do pojedynczych POJAZDÓW - tylko
// tych, których kurs zatrzyma się jeszcze tam, gdzie mapa prowadzi jego linię
// (zgłoszone: przy relacji Księże Małe - pl. Grunwaldzki warstwa pokazywała
// 146 stojące na Biskupinie). Działało i było zmierzone (z 49 pojazdów linii
// z mapy zostawało 12), ale zostało zdjęte na wyraźną prośbę - warstwa ma
// pokazywać wszystkie pojazdy linii z mapy. Kod serwera czeka zakomentowany
// w vehicles.py, a filtr wyglądał tak:
//
//     const drawn = only.stops && only.stops.get(key);   // przystanki z mapy
//     if (!drawn || !v.stops) return true;               // kursu nie rozpoznano
//     return v.stops.some(([lat, lon, sec]) =>
//         sec <= only.until && drawn.has(lat + ',' + lon));

const vehiclesLayer = L.layerGroup();
const vehiclesToggle = $('vehicles-toggle');
let vehiclesOn = !!uiState.vehiclesOn;
let lastVehicles = [];
let vehiclesTimer = null;
// Portal sam aktualizuje dane co kilkanaście-kilkadziesiąt sekund - częstsze
// odpytywanie tylko obciążałoby serwer bez świeższych danych.
const VEHICLES_REFRESH_MS = 15000;

function vehicleIcon(v) {
    return L.divIcon({
        className: `vehicle-marker ${esc(v.kind)}`,
        html: esc(v.line),
        iconSize: [26, 26],
        iconAnchor: [13, 13],
    });
}

function vehicleTooltipHtml(v) {
    const mode = MODE_LABEL[v.kind] || MODE_LABEL.other;
    return `<b>${esc(mode)} ${esc(v.line)}</b><br>Rodzaj: ${esc(mode)}`;
}

const vehicleKey = (kind, num) => kind + ' ' + String(num).trim();

/** Linie, do których zawęża się warstwa pojazdów - albo null, gdy nie ma czego
    zawężać. Liczone z flowHits, czyli z tego, co NAPRAWDĘ jest na ekranie,
    a nie z odpowiedzi serwera - to ta sama lista, którą kursor rozstrzyga
    korytarze. W rozkładach pytamy ekran (patrz timetableMode.vehicleLines). */
function vehiclesFilter() {
    const fromTimetable = window.timetableMode && window.timetableMode.vehicleLines();
    if (fromTimetable) return fromTimetable;
    if (!flowHits.length) return null;
    const lines = new Set();
    for (const hit of flowHits) {
        if (hit.seg.num) lines.add(vehicleKey(hit.seg.kind, hit.seg.num));
    }
    return lines;
}

function renderVehicles() {
    vehiclesLayer.clearLayers();
    if (!vehiclesOn) return;
    const only = vehiclesFilter();
    for (const v of lastVehicles) {
        if (only && !only.has(vehicleKey(v.kind, v.line))) continue;
        L.marker([v.lat, v.lon], {icon: vehicleIcon(v)})
            .bindTooltip(vehicleTooltipHtml(v))
            .addTo(vehiclesLayer);
    }
}

function loadVehicles() {
    fetch('/api/vehicles').then(r => r.json()).then(data => {
        if (data.error || !vehiclesOn) return;   // błąd - zostają ostatnie znane pozycje
        lastVehicles = data.vehicles;
        renderVehicles();
    }).catch(() => {});   // sieć/timeout - kolejna próba za VEHICLES_REFRESH_MS
}

function setVehiclesOn(on) {
    vehiclesOn = on;
    saveUiState({vehiclesOn: on});
    if (vehiclesToggle) {
        vehiclesToggle.classList.toggle('active', on);
        vehiclesToggle.setAttribute('aria-pressed', String(on));
    }
    clearInterval(vehiclesTimer);
    vehiclesTimer = null;
    if (on) {
        vehiclesLayer.addTo(map);
        loadVehicles();
        vehiclesTimer = setInterval(loadVehicles, VEHICLES_REFRESH_MS);
    } else {
        map.removeLayer(vehiclesLayer);
    }
}

if (vehiclesToggle) {
    vehiclesToggle.addEventListener('click', () => setVehiclesOn(!vehiclesOn));
}

// ------------------------------------------- auta i rowery jako warstwy ----
//
// Jeden włącznik na warstwę, dwa źródła danych pod spodem. Przy narysowanej
// mapie przepływów pokazuje się to, co przyszło razem z nią: tamte auta
// i rowery wiedzą, o której się przy nich jest i ile stąd do celu - to są
// odpowiedzi na zadane pytanie. Bez mapy nie ma pytania, więc zostaje samo
// „co gdzie stoi" i warstwa bierze cały miejski feed (/api/cars, /api/bikes).
//
// Włącznik NIGDY nie rusza propozycji tras ani samego wachlarza - dokłada
// i zdejmuje wyłącznie kropki na mapie.

const CARS_REFRESH_MS = 20000;    // tyle deklaruje feed Traficara (CARS_TTL_SEC)
const BIKES_REFRESH_MS = 60000;   // tyle deklaruje kanał WRM (`ttl`)

// Auta domyślnie włączone: na narysowanej mapie stały tam od zawsze (kontrakt
// p. 15), a zgaszenie ich przy okazji dokładania włącznika byłoby zabraniem
// czegoś, o co nikt nie prosił. Rower odwrotnie - to ten sam wybór, co dawne
// „mam konto w WRM": domyślnie nie, a kto go odhaczył wcześniej, ten ma go
// dalej (stara pamięć `bikes`). Pojazdy na żywo zostają zgaszone jak dotąd.
let carsOn = uiState.carsOn !== false;
// Rodzaj roweru ma własny przycisk w pasku (zgłoszenie #147). Warstwa świeci,
// gdy świeci choć jeden z nich - dwa zgaszone znaczą dokładnie to, co dawniej
// zgaszone „Rowery". Kto miał zapamiętany stary włącznik, dostaje oba w jego
// położeniu, więc nic mu się samo nie zapala ani nie gaśnie.
const bikesWere = uiState.bikesOn === undefined
    ? !!uiState.bikes : !!uiState.bikesOn;
let bikeRegularOn = uiState.bikeRegularOn === undefined
    ? bikesWere : !!uiState.bikeRegularOn;
let bikeElectricOn = uiState.bikeElectricOn === undefined
    ? bikesWere : !!uiState.bikeElectricOn;
let bikesOn = bikeRegularOn || bikeElectricOn;
let cityCarLayer = null;
let cityBikeLayer = null;
let carsTimer = null;
let bikesTimer = null;
const carsToggle = $('cars-toggle');
const bikesToggle = $('bikes-toggle');
const bikesElectricToggle = $('bikes-electric-toggle');

/** Czy na ekranie stoi wachlarz - to on rozstrzyga, z którego źródła biorą
    się auta i rowery. W rozkładach i przed wyszukiwaniem go nie ma. */
const flowOnScreen = () => !!flowLayer;

function cityCarMarkers(cars) {
    return cars.map(car => L.circleMarker([car.lat, car.lon], carStyle(car)).bindTooltip(
        `<b>${carName(car)}</b><br>` +
        // Opis miejsca postoju bywa w feedzie pusty - pusta linijka w dymku
        // wyglądałaby jak brakująca treść.
        (car.where ? `${esc(car.where)}<br>` : '') +
        `Paliwo ${car.fuel}%, zasięg ${car.range} km<br>` +
        ogarniamText(car.ogarniam),
        {direction: 'top', offset: [0, -4], opacity: 1},
    ));
}

function cityBikeMarkers(stations, free) {
    const places = stations
        .filter(s => s.renting)
        .map(s => ({...s, loose: false}))
        .concat(free.map(b => ({...b, loose: true, bikes: 1})));
    return places.map(place => L.circleMarker(
        [place.lat, place.lon], bikeStyle(place, true),
    ).bindTooltip(
        `<b>${esc(place.name || 'Rower luzem')}</b><br>${bikeCountText(place)}`,
        {direction: 'top', offset: [0, -4], opacity: 1},
    ));
}

function loadCityCars() {
    fetch('/api/cars').then(r => r.json()).then(data => {
        if (data.error || !carsOn || flowOnScreen()) return;
        if (cityCarLayer) map.removeLayer(cityCarLayer);
        // Feed miasta oddaje wszystkie auta - dostawczaki odsiewa się tutaj,
        // tym samym przełącznikiem, który przy mapie przepływów idzie do serwera.
        const vans = $('car-vans').checked;
        cityCarLayer = L.layerGroup(
            cityCarMarkers(data.cars.filter(car => vans || !car.van))).addTo(map);
    }).catch(() => {});   // sieć/timeout - kolejna próba za CARS_REFRESH_MS
}

/** Na jaki rodzaj roweru pasażer chce wsiąść - dwa przyciski w pasku warstw
    (zgłoszenie #147). Przy mapie przepływów idą do serwera, w warstwie
    miejskiej odsiewają tutaj - tak samo jak dostawczaki. */
function bikeKinds() {
    return {electric: bikeElectricOn, regular: bikeRegularOn};
}

function loadCityBikes() {
    fetch('/api/bikes').then(r => r.json()).then(data => {
        if (data.error || !bikesOn || flowOnScreen()) return;
        if (cityBikeLayer) map.removeLayer(cityBikeLayer);
        // Kanał miasta oddaje wszystkie stacje - rodzaj odsiewa się tutaj,
        // tym samym przełącznikiem, który przy mapie idzie do serwera.
        const {electric, regular} = bikeKinds();
        const stacje = data.stations.filter(s =>
            (electric && s.electric > 0) || (regular && s.bikes - s.electric > 0));
        const luzem = data.free.filter(b => b.electric ? electric : regular);
        cityBikeLayer = L.layerGroup(cityBikeMarkers(stacje, luzem)).addTo(map);
    }).catch(() => {});
}

/** Postawienie warstwy aut od zera - po przełączniku, po nowej mapie i po jej
    zdjęciu. Zawsze zdejmuje obie wersje i stawia najwyżej jedną, więc nie da
    się zostać z autami z wyniku pod autami z całego miasta. */
function refreshCarLayer() {
    clearInterval(carsTimer);
    carsTimer = null;
    if (cityCarLayer) { map.removeLayer(cityCarLayer); cityCarLayer = null; }
    if (flowCarLayer) { map.removeLayer(flowCarLayer); flowCarLayer = null; }
    // W rozkładach mapa jest o linii albo o przystanku - auta i rowery nie
    // mają tam czego dokładać, choćby włącznik został zapalony.
    if (!carsOn || plannerSuspended) return;
    if (flowOnScreen()) {
        flowCarLayer = L.layerGroup(flowCarMarkers(lastFlow.cars)).addTo(map);
        return;
    }
    loadCityCars();
    carsTimer = setInterval(loadCityCars, CARS_REFRESH_MS);
}

function refreshBikeLayer() {
    clearInterval(bikesTimer);
    bikesTimer = null;
    clearBikeRides();
    if (cityBikeLayer) { map.removeLayer(cityBikeLayer); cityBikeLayer = null; }
    if (flowBikeLayer) { map.removeLayer(flowBikeLayer); flowBikeLayer = null; }
    if (!bikesOn || plannerSuspended) return;   // patrz refreshCarLayer
    if (flowOnScreen()) {
        flowBikeLayer = L.layerGroup(flowBikeMarkers(
            lastFlow.bike_places, lastFlow.bike_places_live)).addTo(map);
        if (dotOpts.bikeRides) showAllBikeRides(lastFlow.bike_places);
        return;
    }
    loadCityBikes();
    bikesTimer = setInterval(loadCityBikes, BIKES_REFRESH_MS);
}

function paintLayerButton(button, on) {
    button.classList.toggle('active', on);
    button.setAttribute('aria-pressed', String(on));
}

function setCarsOn(on) {
    carsOn = on;
    saveUiState({carsOn: on});
    paintLayerButton(carsToggle, on);
    refreshCarLayer();
}

/** Jeden rodzaj roweru. Warstwa świeci, gdy świeci choć jeden - więc
    zgaszenie ostatniego gasi rower w całości, tak jak dawniej jeden włącznik.

    Zmienia nie tylko mapę, ale i sam wynik z serwera: ten włącznik mówi też
    „mam konto w WRM", a rodzaj rozstrzyga, które miejsca w ogóle są
    kandydatami (patrz bikes.map_places). */
function setBikeKind(kind, on) {
    if (kind === 'electric') {
        bikeElectricOn = on;
        saveUiState({bikeElectricOn: on});
    } else {
        bikeRegularOn = on;
        saveUiState({bikeRegularOn: on});
    }
    bikesOn = bikeRegularOn || bikeElectricOn;
    saveUiState({bikesOn});
    paintLayerButton(bikesToggle, bikeRegularOn);
    if (bikesElectricToggle) paintLayerButton(bikesElectricToggle, bikeElectricOn);
    refreshBikeLayer();
    replanForBikes();
}

/** Rower w całości - oba rodzaje naraz. */
function setBikesOn(on) {
    bikeRegularOn = on;
    bikeElectricOn = on;
    saveUiState({bikeRegularOn: on, bikeElectricOn: on, bikesOn: on});
    bikesOn = on;
    paintLayerButton(bikesToggle, on);
    if (bikesElectricToggle) paintLayerButton(bikesElectricToggle, on);
    refreshBikeLayer();
    replanForBikes();
}

if (carsToggle) {
    carsToggle.addEventListener('click', () => setCarsOn(!carsOn));
    paintLayerButton(carsToggle, carsOn);
}

if (bikesToggle) {
    bikesToggle.addEventListener('click',
                                 () => setBikeKind('regular', !bikeRegularOn));
    paintLayerButton(bikesToggle, bikeRegularOn);
}

if (bikesElectricToggle) {
    bikesElectricToggle.addEventListener(
        'click', () => setBikeKind('electric', !bikeElectricOn));
    paintLayerButton(bikesElectricToggle, bikeElectricOn);
}

// Kadrowanie wyniku potrzebuje współrzędnych startu i celu, a te znamy
// dopiero z markerów - kto rysuje, czeka na to zapytanie.
const stopsReady = fetch('/api/stops')
    .then(r => r.json())
    .then(stops => {
        if (stops.error) { showError(stops.error); return; }
        for (const s of stops) {
            stopKind.set(s.name, s.kind);
            const m = L.circleMarker([s.lat, s.lon], styleFor(s.name));
            // Sama etykieta dymka, w odróżnieniu od podpowiedzi w formularzu
            // (patrz STOP_KIND/attachAutocomplete), nie jedzie nigdzie jako
            // wyszukiwana nazwa - można doklejać "PKP" wprost do tekstu.
            m.bindTooltip(s.kind === 'train' ? `${s.name} PKP` : s.name);
            // Zatrzymujemy zdarzenie - inaczej klik w słupek dobiłby też do
            // map.on('click') i nadpisał wybór punktem.
            m.on('click', e => {
                L.DomEvent.stop(e);
                // W trybie rozkładów klik w słupek nie wybiera końca relacji,
                // tylko pokazuje jego tablicę odjazdów (static/timetable.js).
                if (!timetableTook(s.name)) pickEndpoint(s.name);
            });
            m.addTo(stopsLayer);
            if (!markersByName.has(s.name)) markersByName.set(s.name, []);
            markersByName.get(s.name).push(m);
        }
        stopsLayer.addTo(map);
        // Stan włącznika ◉ wraca z localStorage (patrz saveUiState).
        setVehiclesOn(vehiclesOn);
    });

/** Klik w mapę (pusty punkt albo słupek) uzupełnia brakujący koniec relacji.
    Nigdy nie kasuje gotowego wyszukiwania - od tego jest przycisk ✕; przy
    wybranej trasie pierwszy taki klik po prostu ją odznacza. */
function pickEndpoint(value) {
    if (selectedJourney !== null) { deselectJourney(); return; }
    if (sel.end && (sel.start || onboardOn)) return;
    const previous = [sel.start, sel.end];
    // Z pokładu pojazdu startu się nie klika - startem jest pojazd - więc
    // każdy klik w mapę wskazuje cel.
    if (onboardOn) {
        sel.end = value;
    } else if (!sel.start) {
        sel.start = value;
    } else if (!samePlace(value, sel.start)) {
        sel.end = value;
    }
    startInput.value = displayValue(sel.start);
    endInput.value = displayValue(sel.end);
    updatePointMarker('start', sel.start);
    updatePointMarker('end', sel.end);
    restyle(...previous, sel.start, sel.end);
    if (sel.end && (onboardOn ? onboardReady() : sel.start)) search();
}

/** Czy tryb rozkładów przejął ten klik. Pusty punkt mapy nie znaczy tam nic -
    rozkład ma przystanek albo linię, nie współrzędne - więc klik poza słupkiem
    jest po prostu ignorowany. */
function timetableTook(stopName) {
    return document.body.classList.contains('mode-timetable')
        && !!(window.timetableMode && window.timetableMode.pickStop(stopName));
}

map.on('click', e => {
    if (document.body.classList.contains('mode-timetable')) return;
    pickEndpoint({lat: e.latlng.lat, lon: e.latlng.lng});
});

// ------------------------------------------- moja lokalizacja jako start ----

// Pozycja z przeglądarki to dla nas zwykły punkt mapy, nie przystanek -
// backend dokłada go jako słupek i sam liczy dojście na okoliczne przystanki.
const locateButton = $('locate');
const locateMsg = $('locate-msg');

const GEO_MESSAGES = {
    1: 'Brak zgody na lokalizację — pozwól na nią w ustawieniach przeglądarki.',
    2: 'Nie udało się ustalić lokalizacji.',
    3: 'Ustalanie lokalizacji trwało zbyt długo — spróbuj ponownie.',
};

function showLocateMsg(text) {
    locateMsg.textContent = text || '';
    locateMsg.hidden = !text;
}

/** Przycisk ◎ - w przeciwieństwie do kliknięcia w mapę nadpisuje start, który
    już był (o to się prosi, klikając go), ale celu nie rusza. */
function useMyLocation(point) {
    const previous = sel.start;
    sel.start = point;
    startInput.value = displayValue(point);
    updatePointMarker('start', point);
    restyle(previous, sel.start);
    if (endInput.value) search();
    else map.setView([point.lat, point.lon], 15);   // stąd wybiera się cel
}

// Brak API (stara przeglądarka albo strona po http) - przycisk, który i tak
// nic by nie zrobił, lepiej schować.
if (!navigator.geolocation) {
    locateButton.hidden = true;
} else {
    locateButton.addEventListener('click', () => {
        showLocateMsg('');
        locateButton.disabled = true;      // GPS potrafi mielić kilka sekund
        const finish = () => { locateButton.disabled = false; };
        navigator.geolocation.getCurrentPosition(
            position => {
                finish();
                useMyLocation({lat: position.coords.latitude,
                               lon: position.coords.longitude});
            },
            error => {
                finish();
                showLocateMsg(GEO_MESSAGES[error.code] || GEO_MESSAGES[2]);
            },
            {enableHighAccuracy: true, timeout: 10000, maximumAge: 60000},
        );
    });
}

// ---------------------------------------------------- kadrowanie widoku ----

function fitTo(points) {
    if (!points.length) return;
    const wide = window.matchMedia('(min-width: 761px)').matches;
    const panelVisible = !document.body.classList.contains('panel-hidden');
    const gutter = 40;
    // maxZoom: krótka trasa nie ma wjeżdżać w widok pojedynczej ulicy.
    const options = {maxZoom: 16};
    if (wide) {
        options.paddingTopLeft = [panelVisible ? sidebar.offsetWidth + gutter : gutter, gutter];
        options.paddingBottomRight = [gutter, gutter];
    } else {
        // Telefon: kadrujemy zawsze pod widok mapy (nad kartą wyszukiwania,
        // nad zakładkami) - także wtedy, gdy akurat patrzymy na listę, bo to
        // ten kadr zobaczymy po przełączeniu zakładki.
        // Karta bywa wysoka (dwa pola + godzina), a przy dosłownym odsunięciu
        // się od niej na kadr zostaje pasek na dole ekranu - stąd sufit.
        const card = document.querySelector('.search-card');
        const top = panelVisible && card
            ? Math.min(card.getBoundingClientRect().bottom + 12,
                       window.innerHeight * 0.35)
            : gutter;
        options.paddingTopLeft = [gutter, top];
        options.paddingBottomRight = [gutter, (viewTabs ? viewTabs.offsetHeight : 0) + 12];
    }
    map.fitBounds(L.latLngBounds(points), options);
}

function endpointPoints() {
    const points = [];
    for (const endpoint of [sel.start, sel.end]) {
        if (isPoint(endpoint)) {
            points.push([endpoint.lat, endpoint.lon]);
            continue;
        }
        for (const m of markersByName.get(endpoint) || []) {
            const p = m.getLatLng();
            points.push([p.lat, p.lng]);
        }
    }
    return points;
}

// ------------------------------------------------------ mapa przepływów ----

// Każdy kurs to jeden ciągły segment od przystanku wsiadania do ostatniego
// sensownego wyjścia; jasność i grubość linii = zapas czasu najlepszego
// wyjścia. Geometria jest PRAWDZIWA (kontrakt p.6) - linie dzielące ten sam
// korytarz leżą jedna na drugiej i nikt ich nie rozsuwa. To, KTÓRA linia tam
// jedzie, mówią dwie rzeczy: grupka numerów postawiona raz na całym wspólnym
// korytarzu i przełączanie się między nimi pod kursorem (kontrakt p.7).

// --- WYGLĄD: wartości do strojenia -----------------------------------------
//
// Wartości dobrane przez użytkownika na żywo, na realnej mapie (2026-08-16),
// suwakami w Ustawieniach Developerskich - sekcja jest z powrotem WIDOCZNA
// (LOOK_TUNING niżej), żeby dało się stroić dalej.
//
// Grubość i krycie niosą tę samą różnicę razem: najbledszy kawałek jest i
// cieńszy, i bardziej przezroczysty (2 px / 0.3), najjaśniejszy - grubszy i
// pełny (3 px / 1). Do 2026-09-04 grubość była STAŁA (3 px) i całą różnicę
// niosło samo krycie; przy szerokim oknie setka bladych kresek o tej samej
// grubości sumowała się w plamę cięższą niż korytarz, który naprawdę
// prowadzi do celu.
const LOOK_DEFAULTS = {
    minOpacity: 0.3,      // krycie najbledszego kawałka (w=0)
    maxOpacity: 1,        // krycie najjaśniejszego (w=1)
    minWeight: 2,         // grubość najbledszego kawałka [px]
    maxWeight: 3,         // grubość najjaśniejszego [px]
    casingFrom: 0.45,     // od tej jasności kawałek dostaje białą otoczkę (1 = nigdy)
    dimFactor: 0.22,      // ile zostaje z krycia, gdy wybrana jest jedna trasa
    labelStep: 200,       // co tyle pikseli korytarza staje kolejna grupka numerów
    labelScale: 0.8,      // wielkość numerów (1 = jak w CSS)
    labelOpacity: 1,      // mnożnik krycia grupek numerów
};

// JEDYNY przełącznik strojenia wyglądu: `true` pokazuje sekcję „Wygląd mapy"
// w Ustawieniach Developerskich (i zaczyna pamiętać ustawienia suwaków w
// localStorage), `false` chowa ją w całości i zostawia same wartości wyżej.
// Kod suwaków zostaje w repo celowo - patrz znaczniki TYMCZASOWE w
// index.html i style.css.
const LOOK_TUNING = true;
const LOOK_PREFS_KEY = 'metal-planner:look-prefs';

function loadLookPrefs() {
    try {
        return JSON.parse(localStorage.getItem(LOOK_PREFS_KEY)) || {};
    } catch {
        return {};
    }
}

// Przy schowanych suwakach zapamiętane wartości są celowo POMIJANE - inaczej
// czyjeś stare ustawienia z localStorage przykryłyby domyślne na zawsze, bez
// żadnej kontrolki, którą dałoby się je cofnąć.
const look = {...LOOK_DEFAULTS, ...(LOOK_TUNING ? loadLookPrefs() : {})};

const lookOpacity = rel => look.minOpacity + (look.maxOpacity - look.minOpacity) * rel;
const lookWeight = rel => look.minWeight + (look.maxWeight - look.minWeight) * rel;

// --- CZAS NA MAPIE ---------------------------------------------------------
//
// Mapa mowi WSZYSTKO o tym, jak dojechac, i nic o tym, ile to trwa. To ten
// brak zasypuje ten blok - i tylko on: nic tutaj nie zmienia geometrii,
// jasnosci ani grubosci linii (kontrakt p.1, p.6, p.8, p.9 zostaja nietkniete).
// Czas dokladany jest WYLACZNIE jako liczba: w dymku pod kursorem, pod
// numerkiem w grupce i w pasku nad mapa.
//
// Godziny przychodza z serwera gotowe, z rozkladu (pole `stops_t` kawalka:
// [lat, lon, sekunda] dla kazdego jego przystanku, oraz `arrive` - o ktorej
// jest sie w celu, jadac dalej stad). Front robi z nimi dokladnie jedna rzecz,
// na ktora punkt 10 kontraktu daje prawo: INTERPOLUJE miedzy dwiema
// sasiednimi godzinami tego samego kursu, proporcjonalnie do przebytej drogi.
// Nic poza tym - zadnej sredniej predkosci, zadnego sklejania kursow.
//
// Kazda rzecz siedzi na wlasnym przelaczniku w Ustawieniach Developerskich - to
// wciaz szukanie formy, a nie gotowa decyzja.
const TIME_DEFAULTS = {
    hover: true,        // godzina w punkcie pod kursorem + przyjazd do celu
    ends: false,        // kropka dokladnie w punkcie, ktorego dotyczy godzina
    chips: false,       // godzina malym drukiem pod numerkiem w grupce
    headline: true,     // pasek nad mapa: najszybciej tyle, pokazane do tyle
    ride: false,        // ...i w nim sam czas jazdy obok "za ile tam bedziesz"
};

const TIME_PREFS_KEY = 'metal-planner:time-prefs';

function loadTimePrefs() {
    try {
        return JSON.parse(localStorage.getItem(TIME_PREFS_KEY)) || {};
    } catch {
        return {};
    }
}

const timeOpts = {...TIME_DEFAULTS, ...loadTimePrefs()};

// Kropki przystanków i to, gdzie ląduje ich rozkład. Osobny klucz od
// TIME_PREFS_KEY, bo tamto jest eksperymentem na czas strojenia, a to nie.
// Wartości dobrane na żywo, na realnej mapie (2026-08-30).
const DOT_DEFAULTS = {
    size: 8,           // promień kropki na wybranej trasie [px]; wachlarz ma o 1 mniej
    rows: 20,          // ile odjazdów wypisuje tablica pod kropką
    center: true,      // kropka węzła: środek wszystkich słupków zamiast peronu
    start: false,      // wyróżnienie przystanku startowego
    tipCursor: true,   // dymek przy kursorze
    tipPanel: true,    // okienko w rogu ekranu, zostaje po zejściu kursora
    // Godziny SAMEGO przejazdu rowerem - domyślnie zgaszone. Godzina "jesteś
    // przy rowerze" pochodzi z rozkładu i jest pokazywana zawsze; ta druga
    // jest jedyną liczbą na tej mapie, której w żadnym rozkładzie nie ma.
    // Odległość obok mówi to samo, nie udając odczytanej.
    bikeTimes: false,
    // Kreski wybranych przejazdów rowerem i ich stacje końcowe na stałe,
    // a nie tylko pod kursorem - domyślnie zgaszone.
    bikeRides: false,
    // Debug: dlaczego rower i auto przeszły wybór (pole `why` z serwera).
    why: false,
};

const DOT_PREFS_KEY = 'metal-planner:dot-prefs';

function loadDotPrefs() {
    try {
        return JSON.parse(localStorage.getItem(DOT_PREFS_KEY)) || {};
    } catch {
        return {};
    }
}

const dotOpts = {...DOT_DEFAULTS, ...loadDotPrefs()};

function saveDotPrefs() {
    try {
        localStorage.setItem(DOT_PREFS_KEY, JSON.stringify(dotOpts));
    } catch {
        // localStorage niedostepny - przelaczniki dzialaja dalej, tylko sie nie zapamietaja
    }
}

function saveTimePrefs() {
    try {
        localStorage.setItem(TIME_PREFS_KEY, JSON.stringify(timeOpts));
    } catch {
        // localStorage niedostepny - przelaczniki dzialaja dalej, tylko sie nie zapamietaja
    }
}

/** Sekundy -> "8 min" / "1 h 3 min". */
function fmtMins(sec) {
    const total = Math.round(sec / 60);
    if (total < 60) return total + ' min';
    const h = Math.floor(total / 60);
    const m = total % 60;
    return m ? `${h} h ${m} min` : `${h} h`;
}

/** Sekundy od polnocy -> "16:04". Rozklad potrafi przekroczyc dobe (kursy
    nocne licza sie dalej: 25:10), wiec godzina wraca na tarcze modulo 24. */
function fmtClock(sec) {
    const total = Math.round(sec / 60);
    const h = Math.floor(total / 60) % 24;
    const m = total % 60;
    return h + ':' + String(m).padStart(2, '0');
}

// --- interpolacja godziny w dowolnym punkcie linii -------------------------
//
// Serwer podaje godziny tylko dla przystankow. Kursor stoi zwykle miedzy nimi,
// wiec godzine w tym miejscu trzeba wyliczyc - proporcjonalnie do przebytej
// drogi miedzy dwoma SASIEDNIMI przystankami tego kursu (kontrakt p.10).
//
// Miara jest metryczna (metry wzdluz narysowanej linii), nie pikselowa: ta
// sama godzina ma wychodzic niezaleznie od powiekszenia mapy.

/** Liczy raz na kawalek: odleglosci wzdluz linii i to, w ktorym miejscu tej
    linii leza jego przystanki. Wynik wisi na obiekcie kawalka z odpowiedzi,
    wiec przezywa przemalowania mapy. */
function ensurePathMetrics(seg, latlngs) {
    if (seg._cum) return;
    const cum = [0];
    for (let i = 1; i < latlngs.length; i++) {
        cum.push(cum[i - 1] + latlngs[i].distanceTo(latlngs[i - 1]));
    }
    seg._cum = cum;
    // Przystanki ida wzdluz linii po kolei, wiec kazdego szukamy od miejsca
    // poprzedniego - petla ani nawrot trasy nie moga przez to cofnac kolejnosci.
    const at = [];
    let from = 0;
    for (const stop of (seg.stops_t || [])) {
        const point = L.latLng(stop[0], stop[1]);
        let bestI = from, bestD = Infinity;
        for (let i = from; i < latlngs.length; i++) {
            const d = latlngs[i].distanceTo(point);
            if (d < bestD) { bestD = d; bestI = i; }
        }
        at.push(cum[bestI]);
        from = bestI;
    }
    seg._stopAt = at;
}

/** Rzut kursora na narysowana linie: ktory odcinek, jak gleboko w nim (0-1)
    i ile metrow od poczatku linii. */
function projectOnPath(latlngs, cum, containerPoint) {
    let best = null;
    let prev = map.latLngToContainerPoint(latlngs[0]);
    for (let i = 1; i < latlngs.length; i++) {
        const cur = map.latLngToContainerPoint(latlngs[i]);
        const dx = cur.x - prev.x, dy = cur.y - prev.y;
        const lenSq = dx * dx + dy * dy;
        const t = lenSq > 0
            ? Math.max(0, Math.min(1, ((containerPoint.x - prev.x) * dx
                                     + (containerPoint.y - prev.y) * dy) / lenSq))
            : 0;
        const px = prev.x + t * dx, py = prev.y + t * dy;
        const d = Math.hypot(containerPoint.x - px, containerPoint.y - py);
        if (!best || d < best.d) {
            best = {d, i, t, pos: cum[i - 1] + t * (cum[i] - cum[i - 1])};
        }
        prev = cur;
    }
    return best;
}

/** Godzina w punkcie oddalonym o `pos` metrow od poczatku kawalka - liniowo
    miedzy godzinami dwoch sasiednich przystankow, miedzy ktorymi ten punkt
    lezy. Poza skrajnymi przystankami zwraca ich wlasne godziny, bez
    ekstrapolacji w przyszlosc ani w przeszlosc. */
function timeAtPos(seg, pos) {
    const at = seg._stopAt, stops = seg.stops_t;
    if (!at || !stops || stops.length < 2 || at.length !== stops.length) return null;
    if (pos <= at[0]) return stops[0][2];
    for (let i = 1; i < at.length; i++) {
        if (pos <= at[i]) {
            const span = at[i] - at[i - 1];
            const f = span > 0 ? (pos - at[i - 1]) / span : 0;
            return stops[i - 1][2] + f * (stops[i][2] - stops[i - 1][2]);
        }
    }
    return stops[stops.length - 1][2];
}

/** Wszystko, co dymek ma o tym punkcie do powiedzenia - albo null, gdy serwer
    nie podal dla tego kawalka godzin. */
function timeAtHover(hit, containerPoint) {
    const seg = hit && hit.seg;
    if (!seg || !seg.stops_t || !containerPoint) return null;
    ensurePathMetrics(seg, hit.latlngs);
    const on = projectOnPath(hit.latlngs, seg._cum, containerPoint);
    if (!on) return null;
    const now = timeAtPos(seg, on.pos);
    if (now === null) return null;
    const a = hit.latlngs[on.i - 1], b = hit.latlngs[on.i];
    return {
        now,
        arrive: typeof seg.arrive === 'number' ? seg.arrive : null,
        at: L.latLng(a.lat + (b.lat - a.lat) * on.t, a.lng + (b.lng - a.lng) * on.t),
    };
}

let flowLayer = null;
let flowParts = [];       // {layer, opacity, weight} - do przygaszania pod wybraną trasą
let flowHits = [];        // {seg, layer, casing, weight, latlngs} - kursor nad korytarzem
let flowLabelLayer = null;
let lastFlow = null;      // ostatnia odpowiedź /api/flow - do przerysowania bez zapytania
// Warstwy "czasu na mapie" - zadeklarowane razem z resztą warstw wachlarza,
// zanim cokolwiek zdąży je sprzątnąć (clearFlow leci niżej, ale wywołuje się
// też przy starcie).
let flowSpanLayer = null;   // kropki "stąd - dotąd" pod kursorem
let fastestLayer = null;    // najszybsza trasa spod paska nad mapą
let flowDotLayer = null;    // węzły przesiadkowe wachlarza (patrz flowStopDots)
let flowCarLayer = null;    // auta car-sharingu w zasięgu (patrz flowCarMarkers)
let flowBikeLayer = null;   // rowery miejskie w zasięgu (patrz flowBikeMarkers)
let flowBikeRideLayer = null;   // strzałki przejazdów - domyślnie tylko pod kursorem

function clearFlow() {
    if (flowLayer) { map.removeLayer(flowLayer); flowLayer = null; }
    if (flowLabelLayer) { map.removeLayer(flowLabelLayer); flowLabelLayer = null; }
    if (flowDotLayer) { map.removeLayer(flowDotLayer); flowDotLayer = null; }
    if (flowCarLayer) { map.removeLayer(flowCarLayer); flowCarLayer = null; }
    if (flowBikeLayer) { map.removeLayer(flowBikeLayer); flowBikeLayer = null; }
    clearBikeRides();
    hoveredStopDot = null;
    flowParts = [];
    flowHits = [];
    lastFlow = null;
    clearFlowHover();
    hideSidePanel();
    timetableTarget = null;
    renderTimeHeadline();
    setBaseDim(false);
    // Zdjęta mapa = nie ma już pytania, na które odpowiadały tamte auta
    // i rowery. Włączona warstwa wraca wtedy do miejskiego feedu.
    refreshCarLayer();
    refreshBikeLayer();
}

/** Skład korytarza danego kawałka: wszystkie linie jadące tymi samymi,
    kolejnymi przystankami, w jednym globalnym porządku - prosto z rozkładu
    (planner._corridor_lines). Kawałek jadący solo to skład jednoelementowy. */
function corridorOf(seg) {
    return seg.corridor && seg.corridor.length
        ? seg.corridor
        : [{num: seg.num, kind: seg.kind}];
}

function corridorKey(roster) {
    return roster.map(l => l.kind + ' ' + l.num).join('|');
}

function drawFlow(flow, refit) {
    if (flowLayer) map.removeLayer(flowLayer);   // bez clearFlow: przemalowanie
    flowParts = [];                              // wszystkich słupków tam i z powrotem
    flowHits = [];                               // - stare warstwy z podświetlenia i tak znikają
    lastFlow = flow;
    clearFlowHover();
    setBaseDim(true);                            // to przy każdym ruchu suwaka za dużo
    const faint = [], casings = [], bright = [];

    for (const s of flow.segments) {        // posortowane po w rosnąco
        const rel = s.w;
        const color = LINE_COLORS[s.kind] || LINE_COLORS.other;
        const weight = lookWeight(rel);
        const opacity = lookOpacity(rel);
        const latlngs = s.path.map(p => L.latLng(p));
        // Nieinteraktywna: linie wspólnego korytarza leżą dokładnie jedna na
        // drugiej, więc zwykłe hover/click Leaflet trafiałoby zawsze w tę
        // narysowaną na wierzchu. Kursor jest łapany globalnie
        // (handleFlowHover) i rozstrzygany po SKŁADZIE korytarza z rozkładu.
        const line = L.polyline(latlngs, {color, opacity, weight, interactive: false});
        flowParts.push({layer: line, opacity, weight});
        let casing = null;
        if (rel >= look.casingFrom) {
            casing = L.polyline(latlngs, {
                color: '#fff', opacity: 0.9, weight: weight + 2.5, interactive: false,
            });
            flowParts.push({layer: casing, opacity: 0.9, weight: weight + 2.5});
            casings.push(casing);
            bright.push(line);
        } else {
            faint.push(line);
        }
        flowHits.push({
            seg: s, layer: line, casing, weight, latlngs,
            box: L.latLngBounds(latlngs),   // zgrubny odsiew przy szukaniu pod kursorem
        });
    }

    // Kolejność: blade tło -> białe otoczki -> jaskrawe korytarze.
    flowLayer = L.layerGroup([...faint, ...casings, ...bright]).addTo(map);
    // Osobna warstwa, dodana PO korytarzach: kropka ma łapać kursor przed
    // linią, na której leży.
    // Rowery pod autami i pod kropkami: stacja bywa dokładnie przy węźle, a
    // najpierw pod kursor ma trafić to, co opisuje całe miejsce.
    refreshBikeLayer();
    // Auta pod kropkami przesiadek: gdy jedno stoi dokładnie na węźle, kursor
    // ma trafić najpierw w kropkę - ona opisuje całe to miejsce.
    refreshCarLayer();
    if (flowDotLayer) map.removeLayer(flowDotLayer);
    hoveredStopDot = null;
    flowDotLayer = L.layerGroup(flowStopDots(flow.nodes, flow.deadline_sec)).addTo(map);
    placeLineLabels();
    renderTimeHeadline();
    if (selectedJourney !== null) dimFlow(true);
    seedStartPanel();
    // Nowa mapa = inny zestaw linii, więc i inne pojazdy dotyczą tego, co
    // widać (patrz vehiclesFilter). Przy zgaszonej warstwie to nic nie kosztuje.
    renderVehicles();
    if (!refit) return;

    // Kadr: najciaśniejszy sensowny próg jasności, żeby nie skakać do widoku
    // całego województwa przez jedną bladą nitkę... (progi własne - kadr nie
    // ma się ruszać przy strojeniu wyglądu suwakami)
    let points = [];
    for (const threshold of [0.7, 0.45, 0]) {
        points = flow.segments.filter(s => s.w >= threshold)
                              .flatMap(s => s.path);
        if (points.length >= 4) break;
    }
    fitTo([...points, ...endpointPoints()]);   // start i cel zawsze w kadrze
}

// --- pasek nad mapą: ile w ogóle trwa ta podróż ----------------------------
//
// Jedyna liczba na mapie, której nie trzeba szukać kursorem - i jedyna, która
// odpowiada na pytanie zadawane najpierw: "ile to w ogóle zajmuje". Podaje
// dwie granice całego wachlarza: najszybszy dojazd i najpóźniejszy, jaki mapa
// jeszcze rysuje (czyli skutek progu mapy - patrz showMore).
// Najechanie na najszybszy czas pokazuje, KTÓRĄ trasą się go osiąga - obie
// liczby przychodzą z serwera (best_sec/limit_sec/fastest), razem z gotową
// geometrią tej trasy. Obie strony paska podają godzinę i czas jazdy w tej
// samej kolejności, żeby dało się je czytać jednym spojrzeniem.

function showFastest() {
    hideFastest();
    const fastest = lastFlow && lastFlow.fastest;
    if (!fastest || !fastest.legs || !fastest.legs.length) return;
    const halos = [], cores = [];
    for (const leg of fastest.legs) {
        if (!leg.path || leg.path.length < 2) continue;
        const latlngs = leg.path.map(p => L.latLng(p));
        if (leg.kind === 'walk') {
            // Dojscie rysujemy tak, jak wszedzie indziej na tej mapie:
            // kreskowana linia w kolorze marszu, bez czarnej otoczki. Bez
            // tego podswietlona trasa zaczynala sie "w powietrzu", kawalek
            // od zaznaczonego startu - i nic nie tlumaczylo tej dziury.
            cores.push(L.polyline(latlngs, {
                color: WALK_COLOR, weight: 4, opacity: 1,
                dashArray: '1,7', lineCap: 'round', interactive: false,
            }));
            continue;
        }
        halos.push(L.polyline(latlngs, {
            color: '#111', opacity: 0.85, weight: 9,
            lineCap: 'round', lineJoin: 'round', interactive: false,
        }));
        cores.push(L.polyline(latlngs, {
            color: LINE_COLORS[leg.kind] || LINE_COLORS.other, opacity: 1, weight: 5,
            lineCap: 'round', lineJoin: 'round', interactive: false,
        }));
    }
    fastestLayer = L.layerGroup([...halos, ...cores]).addTo(map);
}

function hideFastest() {
    if (fastestLayer) { map.removeLayer(fastestLayer); fastestLayer = null; }
}

// "Pokaż więcej" tuż za granicą mapy (punkt 2 kontraktu): każde kliknięcie
// dokłada jedną WYJŚCIOWĄ gęstość - pierwsze do dwukrotności, drugie do
// trzykrotności, trzecie do czterokrotności. Próg z tego dobiera serwer i to
// on pilnuje sufitu (MAX_MAP_MORE w plannerze), tutaj liczba służy tylko
// temu, żeby przycisk zniknął, gdy nie ma już czego dokładać. Dokładka żyje
// do NASTĘPNEGO wyszukiwania - nowa relacja zaczyna od gęstości z suwaka.
const MAX_MAP_MORE = 3;
let mapMore = 0;                   // ile razy kliknięto "pokaż więcej"
let moreBusy = false;              // klik w locie - drugi klik ma poczekać

function showMore() {
    if (moreBusy) return;
    moreBusy = true;
    mapMore += 1;
    // Kadru NIE przestawiamy: gęstsza mapa dokłada linie, nie zmienia tego,
    // na co user patrzy.
    loadPlan(requestToken, false)
        .catch(() => showError('Nie udało się połączyć z serwerem.'))
        .finally(() => { moreBusy = false; });
}

/** Plakietki w pasku nad mapa - ta sama regula, co na karcie propozycji
    (patrz summaryHtml): dojscie OTWIERAJACE albo ZAMYKAJACE trase dostaje
    wlasny znak, bo inaczej pasek obiecuje wsiadanie na przystanku, ktorego
    nikt nie wskazywal; przejscie miedzy pojazdami zostaje kreska. */
function headlineChips(legs) {
    const parts = [];
    let pendingWalk = false;
    legs.forEach((leg, i) => {
        if (leg.kind === 'walk') {
            if (i === 0 || i === legs.length - 1) {
                parts.push(`<span class="headline-walk" title="Przejście pieszo` +
                           ` · ok. ${leg.minutes} min">${WALK_ICON}${leg.minutes}</span>`);
            } else {
                pendingWalk = true;
            }
            return;
        }
        if (pendingWalk) parts.push('<span class="headline-hop"></span>');
        pendingWalk = false;
        parts.push(`<span class="line-chip ${esc(leg.kind)}">${esc(leg.num)}</span>`);
    });
    return parts.join('');
}

function renderTimeHeadline() {
    const el = $('time-headline');
    if (!el) return;
    const flow = lastFlow;
    if (!timeOpts.headline || !flow || typeof flow.best_sec !== 'number') {
        el.hidden = true;
        el.innerHTML = '';
        hideFastest();
        return;
    }
    const chips = headlineChips((flow.fastest && flow.fastest.legs) || []);
    // Po trzecim kliknięciu i przy suficie skanu (at_ceiling) nie ma już
    // czego dokładać - przycisk, który nic nie robi, nie ma prawa stać.
    const more = flow.more < MAX_MAP_MORE && !flow.at_ceiling
        ? `<button type="button" class="headline-more" title="Rysuj też gorsze `
          + `opcje - mapa ${flow.more + 2}× gęstsza niż wyjściowa">Pokaż więcej</button>`
        : '';
    // Sama jazda to osobna liczba na życzenie: "za ile" zostaje zawsze.
    const ride = timeOpts.ride && typeof flow.ride_sec === 'number'
        ? `, jazda <b>${esc(fmtMins(flow.ride_sec))}</b>` : '';
    el.innerHTML =
        // "za", nie "w": obie liczby są mierzone od godziny z formularza,
        // więc mówią, ZA ILE się tam będzie, a nie ile trwa sama jazda
        // (zgłoszenie #143). Czekanie na pierwszy pojazd jest w nich zawarte.
        // "Wyjeżdżasz o" to najpóźniejszy wyjazd, który wciąż daje najszybszy
        // przyjazd - wcześniej wychodzi się tylko po to, żeby gdzieś czekać.
        `<span class="headline-best" tabindex="0">Najszybciej: wyjeżdżasz o `
        + `<b>${esc(flow.starts)}</b>, dojeżdżasz o <b>${esc(flow.best_arrival)}</b>, `
        + `za <b>${esc(fmtMins(flow.best_sec))}</b>${ride}${chips}</span>`
        + `<span class="headline-sep">·</span>`
        + `<span class="headline-limit">mapa od <b>${esc(flow.map_from)}</b> `
        + `do <b>${esc(flow.deadline)}</b>, za <b>${esc(fmtMins(flow.limit_sec))}</b></span>`
        + more;
    el.hidden = false;
    const best = el.querySelector('.headline-best');
    best.addEventListener('mouseenter', showFastest);
    best.addEventListener('mouseleave', hideFastest);
    best.addEventListener('focus', showFastest);
    best.addEventListener('blur', hideFastest);
    const moreBtn = el.querySelector('.headline-more');
    if (moreBtn) moreBtn.addEventListener('click', showMore);
    // Na dotyku nie ma "mouseenter" - to jedyny sposób, żeby zobaczyć trasę
    // najszybszego dojazdu na telefonie. Toggle, nie show: drugie stuknięcie
    // (albo stuknięcie gdzie indziej, które i tak odpala renderTimeHeadline
    // od nowa) chowa trasę z powrotem.
    best.addEventListener('click', event => {
        event.stopPropagation();
        if (fastestLayer) hideFastest(); else showFastest();
    });
    placeTimeHeadline();
}

// Odstęp paska od krawędzi okna i od tego, co może mu stanąć na drodze.
const HEADLINE_GAP = 16;

/** Dokąd z lewej sięga to, na czym paskowi stawać nie wolno: panel i pływające
    przyciski - ale tylko te, które leżą na jego wysokości. Mierzone, a nie
    wpisane liczbą: szerokość panelu zmienia suwak, a napis na przycisku trybu
    zmienia jego szerokość; wpisana liczba rozjeżdżała się z każdą taką zmianą.
    Schowany panel sam wyjeżdża poza ekran, więc przestaje być przeszkodą bez
    osobnej reguły. */
function headlineGuard(band) {
    let guard = HEADLINE_GAP;
    for (const el of [sidebar, $('sidebar-toggle'), $('mode-toggle')]) {
        if (!el || el.hidden) continue;
        const box = el.getBoundingClientRect();
        if (box.bottom <= band.top || box.top >= band.bottom) continue;
        guard = Math.max(guard, box.right + HEADLINE_GAP);
    }
    return guard;
}

/** Pasek stoi na środku OKNA - tam patrzy oko, a nie na środek wolnego
    skrawka mapy. Gdy wyśrodkowany wszedłby na panel albo na przyciski,
    odsuwa się w prawo dokładnie o tyle, o ile trzeba; gdy i wtedy brakuje mu
    miejsca, zawija się na kolejne linijki (flex-wrap) zamiast wystawać poza
    ekran. Idealny środek jest więc regułą, a nie obietnicą: przy wąskim oknie
    granica wygrywa - ale dopiero wtedy. */
function placeTimeHeadline() {
    const el = $('time-headline');
    if (!el || el.hidden) return;
    // Na telefonie panel jest nakładką na całą szerokość, a pasek schodzi pod
    // niego na sam dół - nie ma tam czego omijać i całe ustawianie oddaje się
    // arkuszowi (patrz RWD w style.css).
    if (!window.matchMedia('(min-width: 761px)').matches) {
        el.style.maxWidth = '';
        el.style.left = '';
        return;
    }
    el.style.maxWidth = '';
    const band = el.getBoundingClientRect();
    const guard = headlineGuard(band);
    const room = window.innerWidth - HEADLINE_GAP - guard;
    el.style.maxWidth = room + 'px';
    // Szerokość po przycięciu: zawinięty pasek jest węższy, więc znów może
    // zmieścić się na środku.
    const width = el.getBoundingClientRect().width;
    el.style.left = Math.max(guard, (window.innerWidth - width) / 2) + 'px';
}

window.addEventListener('resize', placeTimeHeadline);
// Panel zjeżdża z animacją, więc miejsce na pasek zmienia się dopiero po niej.
sidebar.addEventListener('transitionend', placeTimeHeadline);

// --- numery linii: jedna grupka na cały wspólny korytarz -------------------
//
// Numery są JEDYNYM sposobem odróżnienia linii leżących na sobie, więc muszą
// być czytelne - i to one, a nie geometria, dostały tu całą uwagę.
//
// Trzy zasady, każda naprawiająca konkretną wadę poprzednich wersji:
//
// - KONDENSACJA. Wspólny korytarz dostaje JEDNĄ grupkę ze wszystkimi swoimi
//   numerami obok siebie, a nie osobny numer dla każdej linii rozrzucony
//   gdzie indziej. Skład bierze się z rozkładu (seg.corridor), nie z tego, co
//   akurat wpadło w promień kilku pikseli - liczenie "co tu jedzie" po
//   pikselach dawało plakietki "13 linii" tam, gdzie realnie jadą dwie.
// - RÓWNE ODSTĘPY. Kolejne grupki stają co stałą liczbę PIKSELÓW wzdłuż
//   korytarza, nie w ułamkach długości kawałka. Kawałki mają bardzo różne
//   długości (tnie je jasność i skład korytarza), więc "w połowie kawałka"
//   znaczyło na ekranie odstępy losowe: raz gęsto, raz nic na pół mapy.
// - ZERO NACHODZENIA. Kolizje sprawdza się prostokątem o REALNEJ szerokości
//   grupki (grupka pięciu numerów jest kilka razy szersza niż jeden numer),
//   a nie jednym stałym promieniem - dlatego numery nie mają jak na siebie
//   wejść. Pierwszeństwo w zajmowaniu miejsca mają korytarze najjaśniejsze.
//
// Progu jasności tu NIE MA celowo. Kolejność zajmowania miejsca (od
// najjaśniejszych), kolizje i sufit i tak przycinają gęstość, a próg wycinał
// przy tym numery także tam, gdzie było zupełnie pusto: w przybliżonym widoku
// rzadkiej okolicy potrafił zejść z 57% opisanych korytarzy na 14%, nie
// oszczędzając ani procenta ekranu. Blade korytarze dostają więc numer wtedy,
// gdy zostało dla niego miejsce - i tylko trochę bledszy.
// Odstęp grupek i ich wielkość siedzą na suwakach (look.labelStep/labelScale) -
// "co ile numerów" i "jak duże numery" to dokładnie te dwie rzeczy, którymi
// reguluje się, jak natrętne są numery na mapie.
const labelStepPx = () => look.labelStep;
const labelRepeatPx = () => look.labelStep * 1.6;  // ten sam skład nie częściej niż co tyle
const LABEL_EDGE_PX = 14;        // margines kadru - grupka nie może wystawać za mapę
const LABEL_GAP_PX = 4;          // odstęp między sąsiednimi grupkami
const LABEL_MAX = 60;            // sufit, żeby mapa nie zamieniła się w ścianę liczb

const CHIP_CHAR_PX = 6.6;        // szerokość cyfry przy foncie plakietki...
const CHIP_PAD_PX = 10;          // ...plus jej własne obramowanie i wcięcie
const CHIP_GAP_PX = 3;
const CHIP_ROW_PX = 17;
const CLUSTER_PAD_PX = 4;
const CHIP_TIME_ROW_PX = 11;     // dodatkowy wiersz grupki, gdy pod numerem stoi czas
// Najgęstsze korytarze Wrocławia mają po 10 linii - jednym rządkiem to 260 px,
// czyli pasek przez jedną trzecią ekranu, którego i tak nie da się objąć
// wzrokiem. Łamiemy więc grupkę na wiersze: kwadratowa plamka czyta się jako
// jedna rzecz i zajmuje dużo mniej miejsca w poprzek korytarza.
const CLUSTER_MAX_COLS = 5;

function clusterRows(roster) {
    const rows = [];
    for (let i = 0; i < roster.length; i += CLUSTER_MAX_COLS) {
        rows.push(roster.slice(i, i + CLUSTER_MAX_COLS));
    }
    return rows;
}

/** Realny rozmiar grupki na ekranie [szerokość, wysokość] - z niego liczą się
    kolizje, więc grupka pięciu numerów odsuwa sąsiadów pięć razy dalej niż
    pojedynczy numer. */
function clusterBox(roster) {
    const rows = clusterRows(roster);
    const scale = look.labelScale;   // numery rosną razem z suwakiem - i tak samo ich kolizje
    // Czas pod numerkiem powiększa grupkę w obu wymiarach, więc musi wejść
    // do POMIARU, nie tylko do rysowania - inaczej grupki zaczęłyby na siebie
    // wchodzić (kolizje liczą się z tego pudełka, patrz placeLineLabels).
    const chars = l => (timeOpts.chips
        ? Math.max(String(l.num).length, 5)   // "16:04" bywa szersze niż sam numer
        : String(l.num).length);
    const rowPx = CHIP_ROW_PX + (timeOpts.chips ? CHIP_TIME_ROW_PX : 0);
    let width = 0;
    for (const row of rows) {
        let w = 2 * CLUSTER_PAD_PX;
        row.forEach((l, i) => {
            w += CHIP_PAD_PX + CHIP_CHAR_PX * chars(l) + (i ? CHIP_GAP_PX : 0);
        });
        width = Math.max(width, w * scale);
    }
    return [width, (rows.length * rowPx + 2 * CLUSTER_PAD_PX) * scale];
}

/** Punkty na ścieżce co `stepPx` PIKSELÓW EKRANU, pomijając te poza kadrem.
    Pierwszy wypada w połowie kroku (albo w połowie krótkiego kawałka), żeby
    numer nie lądował dokładnie na styku dwóch kawałków tego samego kursu. */
function labelAnchors(latlngs, stepPx) {
    const size = map.getSize();
    const pts = latlngs.map(p => map.latLngToContainerPoint(p));
    const lens = [];
    let total = 0;
    for (let i = 1; i < pts.length; i++) {
        const len = pts[i].distanceTo(pts[i - 1]);
        lens.push(len);
        total += len;
    }
    if (total <= 0) return [];

    const out = [];
    let next = Math.min(stepPx / 2, total / 2);
    let cum = 0;
    for (let i = 1; i < pts.length; i++) {
        const len = lens[i - 1];
        while (len > 0 && next <= cum + len) {
            const t = (next - cum) / len;
            const at = L.point(
                pts[i - 1].x + (pts[i].x - pts[i - 1].x) * t,
                pts[i - 1].y + (pts[i].y - pts[i - 1].y) * t,
            );
            if (at.x >= LABEL_EDGE_PX && at.y >= LABEL_EDGE_PX
                && at.x <= size.x - LABEL_EDGE_PX && at.y <= size.y - LABEL_EDGE_PX) {
                out.push(at);
            }
            next += stepPx;
        }
        cum += len;
    }
    return out;
}

function placeLineLabels() {
    if (flowLabelLayer) { map.removeLayer(flowLabelLayer); flowLabelLayer = null; }
    if (!flowHits.length) return;

    // Duże zapytania to ponad tysiąc kawałków, a numery przeliczają się po
    // każdym ruchu mapy - kawałki spoza kadru odsiewamy więc od razu, na
    // surowych współrzędnych, zamiast rzutować każdy ich punkt na ekran.
    const view = map.getBounds();
    const candidates = [];
    for (const h of flowHits) {
        if (!h.latlngs.some(p => view.contains(p))) continue;
        const roster = corridorOf(h.seg);
        const key = corridorKey(roster);
        for (const at of labelAnchors(h.latlngs, labelStepPx())) {
            candidates.push({at, roster, key, w: h.seg.w});
        }
    }
    candidates.sort((a, b) => b.w - a.w);   // najjaśniejsze zajmują miejsce pierwsze

    const boxes = [];
    const byKey = new Map();
    const markers = [];
    for (const c of candidates) {
        if (markers.length >= LABEL_MAX) break;
        const size = clusterBox(c.roster);
        const half = size[0] / 2 + LABEL_GAP_PX;
        const halfH = size[1] / 2 + LABEL_GAP_PX;
        const box = [c.at.x - half, c.at.y - halfH, c.at.x + half, c.at.y + halfH];
        if (boxes.some(b => b[0] < box[2] && box[0] < b[2] && b[1] < box[3] && box[1] < b[3])) continue;
        const same = byKey.get(c.key);
        if (same && same.some(p => p.distanceTo(c.at) < labelRepeatPx())) continue;
        boxes.push(box);
        if (same) same.push(c.at); else byKey.set(c.key, [c.at]);
        markers.push(clusterMarker(
            map.containerPointToLatLng(c.at), c.roster, c.w,
            timeOpts.chips ? chipTimesAt(c.at, c.roster) : null,
        ));
    }

    flowLabelLayer = L.layerGroup(markers).addTo(map);
    for (const marker of markers) bindCluster(marker);
}

/** Godzina dla KAZDEJ linii grupki z osobna - o ktorej ta linia jest w tym
    miejscu. Grupka opisuje caly wspolny korytarz, a jego linie jada tedy o
    roznych porach, wiec jedna liczba na cala grupke bylaby godzina tylko
    jednej z nich. Liczy sie tylko przy wlaczonym przelaczniku, bo to
    dodatkowe trafienie w geometrie na kazda postawiona grupke. */
/** Kawałek, o którym mówimy, gdy pod kursorem leży kilka kawałków TEJ SAMEJ
    linii. To różne KURSY: ten sam przystanek potrafi wypaść u nich o godzinach
    różniących się o kwadrans. `flowHits` są posortowane po odległości w
    pikselach, a kursy leżą dokładnie jeden na drugim, więc branie pierwszego
    z brzegu (tak było do 2026-08-29) sprawiało, że drgnięcie kursora o piksel
    przestawiało "tu jesteś" z 13:01 na 13:16 - w tym samym miejscu.

    Wygrywa kurs dowożący DO CELU najwcześniej: tą samą miarą mapa liczy
    jasność, więc dymek mówi o tym kursie, który jest tu najlepszą opcją.
    Kawałek bez odczytanej godziny u celu nie wygrywa z takim, który ją ma. */
function hitFor(hits, num, kind) {
    let best = null;
    for (const h of hits) {
        if (h.seg.num !== num || h.seg.kind !== kind) continue;
        if (best === null) { best = h; continue; }
        const mine = h.seg.arrive, its = best.seg.arrive;
        if (mine === undefined) continue;
        if (its === undefined || mine < its) best = h;
    }
    return best;
}

function chipTimesAt(at, roster) {
    const hits = flowHitsAt(at);
    return roster.map(l => {
        const hit = hitFor(hits, l.num, l.kind);
        const when = hit ? timeAtHover(hit, at) : null;
        return when ? when.now : null;
    });
}

function clusterMarker(at, roster, weight, times) {
    let index = 0;
    const rows = clusterRows(roster).map(row =>
        '<span class="line-cluster-row">' + row.map(l => {
            const sec = times ? times[index] : null;
            // Slot jest celem myszy (patrz bindCluster) - dzięki temu wskazanie
            // linii działa tak samo, gdy kursor stoi na czasie pod numerem.
            const html = `<span class="line-chip-slot" data-i="${index++}">`
                + `<span class="line-chip ${esc(l.kind)}">${esc(l.num)}</span>`
                + (times
                    ? `<span class="chip-time">${sec === null ? '' : esc(fmtClock(sec))}</span>`
                    : '')
                + '</span>';
            return html;
        }).join('') + '</span>',
    ).join('');
    const marker = L.marker(at, {
        icon: L.divIcon({
            className: 'line-cluster-anchor',
            html: `<span class="line-cluster">${rows}</span>`,
            iconSize: null,
        }),
        keyboard: false,
        opacity: (flowDimmed ? 0.25 : 0.65 + 0.35 * weight) * look.labelOpacity,
    });
    marker.roster = roster;
    return marker;
}

/** Numer w grupce wskazuje się WYŁĄCZNIE najechaniem: kursor nad numerem
    podświetla DOKŁADNIE tę linię. Bez tego grupka mówiłaby, co tędy jedzie,
    ale nie dałaby tego wskazać. Klik nie robi tu nic - jest tylko wygaszany,
    żeby nie dobił do map.on('click') i nie ustawił punktu trasy. */
function bindCluster(marker) {
    const el = marker.getElement();
    if (!el) return;
    L.DomEvent.on(el, 'mouseover', ev => {
        const slot = ev.target.closest && ev.target.closest('.line-chip-slot');
        if (slot) pickFromCluster(marker, Number(slot.dataset.i));
    });
    L.DomEvent.on(el, 'click', ev => L.DomEvent.stop(ev));
}

let flowDimmed = false;

/** Wybrana trasa musi być czytelna, więc reszta wachlarza schodzi w tło. */
function dimFlow(dim) {
    flowDimmed = dim;
    for (const part of flowParts) {
        part.layer.setStyle({opacity: dim ? part.opacity * look.dimFactor : part.opacity});
    }
    // Wybrana trasa ma własne kropki na swoich przystankach - te z wachlarza
    // leżałyby na nich i pytały o to samo dwa razy.
    if (flowDotLayer) {
        if (dim) { map.removeLayer(flowDotLayer); hoveredStopDot = null; }
        else flowDotLayer.addTo(map);
    }
    if (flowLabelLayer) placeLineLabels();   // grupki przeliczają własną widoczność
}

// Numery stoją co tyle a tyle pikseli KORYTARZA i tylko w kadrze, więc po
// każdym ruchu mapy - przesunięciu i przybliżeniu - muszą powstać na nowo.
map.on('moveend', placeLineLabels);

// --- kursor nad korytarzem: na czym stoję ----------------------------------
//
// Pod kursorem podświetla się WYŁĄCZNIE jedna linia, a podpowiedź podaje jej
// numer wprost. Domyślnie jest to najjaśniejsza linia korytarza (najczęściej
// ta, o którą chodzi); żeby wskazać dowolną inną, najeżdża się na jej numer w
// grupce (bindCluster). Podświetlanie całego korytarza naraz - tak było
// wcześniej - sprawiało, że nic się z niego nie wyróżniało: widać było, że coś
// tędy jedzie, ale nie na czym stoi kursor.
//
// Podświetla się CAŁA LINIA, nie sam kawałek pod kursorem. Jeden fizyczny kurs
// bywa pocięty na kilkanaście kawałków (jasność - punkt 3, skład korytarza -
// punkt 7), więc rozjaśnienie jednego z nich odpowiadało na pytanie "gdzie
// dokładnie stoi kursor" zamiast na to, o które chodzi: "dokąd stąd jedzie ta
// linia". Podświetlenie leży w OSOBNEJ warstwie dokładanej na wierzch
// wszystkiego (ciemna otoczka + pełne krycie), a nie w przemalowaniu warstw na
// miejscu - inaczej "na wierzchu" zależałoby od kolejności rysowania i jasna
// linia obok potrafiła przykryć wskazaną.

let flowTooltip = null;
let flowHighlight = null;   // warstwa podświetlenia całej linii
let flowHighlightKey = null;
let flowPick = null;      // {key, index, options} - wskazana linia korytarza
let flowPickAt = null;    // gdzie stoi kursor - do przerysowania po przełączeniu
let flowPickPoint = null; // ...to samo w pikselach ekranu - do rzutu na linię
const FLOW_HIT_SLACK_PX = 5;   // margines poza grubością linii, na niecelny kursor
const HALO_EXTRA_PX = 6;       // o tyle otoczka podświetlenia szersza od linii
const HIGHLIGHT_EXTRA_PX = 2;  // o tyle sama linia grubsza pod kursorem

function distPointToSegmentPx(p, a, b) {
    const dx = b.x - a.x, dy = b.y - a.y;
    const lenSq = dx * dx + dy * dy;
    if (lenSq === 0) return p.distanceTo(a);
    const t = Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / lenSq));
    return p.distanceTo(L.point(a.x + t * dx, a.y + t * dy));
}

function polylineDistancePx(containerPoint, latlngs) {
    let min = Infinity;
    let prev = map.latLngToContainerPoint(latlngs[0]);
    for (let i = 1; i < latlngs.length; i++) {
        const cur = map.latLngToContainerPoint(latlngs[i]);
        min = Math.min(min, distPointToSegmentPx(containerPoint, prev, cur));
        prev = cur;
    }
    return min;
}

/** Wszystkie kawałki pod danym miejscem na ekranie, od najbliższego.
    Duże zapytania to ponad tysiąc kawałków, a to leci przy każdym ruchu
    myszy - dlatego najpierw zgrubny odsiew po ramce kawałka (na surowych
    współrzędnych), a dokładny pomiar odległości dopiero dla reszty. Margines
    ramki liczy się z aktualnego powiększenia, żeby przy widoku całego miasta,
    gdzie kilka pikseli to ponad sto metrów, nic nie wypadło przedwcześnie. */
function flowHitsAt(containerPoint) {
    const here = map.containerPointToLatLng(containerPoint);
    const away = map.containerPointToLatLng(L.point(containerPoint.x + 16, containerPoint.y + 16));
    const padLat = Math.abs(away.lat - here.lat);
    const padLng = Math.abs(away.lng - here.lng);
    const hits = [];
    for (const h of flowHits) {
        if (here.lat < h.box.getSouth() - padLat || here.lat > h.box.getNorth() + padLat
            || here.lng < h.box.getWest() - padLng || here.lng > h.box.getEast() + padLng) {
            continue;
        }
        const tol = h.weight / 2 + FLOW_HIT_SLACK_PX;
        const dist = polylineDistancePx(containerPoint, h.latlngs);
        if (dist <= tol) hits.push({...h, dist});
    }
    hits.sort((a, b) => a.dist - b.dist);
    return hits;
}

/** Między czym można się w tym miejscu przełączać: skład korytarza NAJBLIŻSZEJ
    linii - z rozkładu - dopasowany do narysowanych kawałków. Zestaw bierze się
    z rozkładu, a nie z tego, co leży w promieniu kilku pikseli, bo przy widoku
    całego miasta kilka pikseli to ponad sto metrów: do wyboru wchodziłyby
    wtedy linie z sąsiednich ulic, którymi wcale się tędy nie jedzie. */
function corridorOptions(hits) {
    return corridorOf(hits[0].seg).map(l => ({
        num: l.num,
        kind: l.kind,
        hit: hitFor(hits, l.num, l.kind),
    }));
}

function brightestOption(options) {
    let best = 0, bestW = -1;
    options.forEach((o, i) => {
        const w = o.hit ? o.hit.seg.w : -1;
        if (w > bestW) { bestW = w; best = i; }
    });
    return best;
}

function flowPickHtml(when) {
    const options = flowPick.options;
    const sel = options[flowPick.index];
    const mode = MODE_LABEL[sel.kind] || MODE_LABEL.other;
    let html = `<span class="flow-tip-line ${esc(sel.kind)}">${esc(mode)} ${esc(sel.num)}</span>`;
    html += flowTipTimeHtml(when);
    if (options.length > 1) {
        html += '<span class="flow-tip-row">' + options.map((o, i) =>
            `<span class="line-chip ${esc(o.kind)}${i === flowPick.index ? ' picked' : ''}">`
            + `${esc(o.num)}</span>`,
        ).join('') + '</span>';
    }
    return html;
}

/** Godziny w dymku - odpowiedz na "o ktorej tu jestem i o ktorej bede u celu".
    Duza liczba to godzina DOKLADNIE w punkcie pod kursorem (interpolowana,
    patrz timeAtPos). Pod nia przyjazd do celu, gdy jedzie sie dalej stad -
    ta sama liczba, z ktorej policzona jest jasnosc tego kawalka, wiec kolor
    i godzina nigdy nie moga powiedziec czegos innego. */
function flowTipTimeHtml(when) {
    if (!timeOpts.hover || !when) return '';
    let html = '<span class="flow-tip-time">'
        + `<b>${esc(fmtClock(when.now))}</b>`
        + '<span class="flow-tip-what">tu jesteś</span>'
        + '</span>';
    // Bez odczytanego przyjazdu do celu (kawalek bez widocznej kontynuacji)
    // nie pokazujemy NICZEGO o dalszej drodze - zgadnieta godzina lamalaby
    // punkt 10 kontraktu.
    if (when.arrive === null) return html;
    const left = when.arrive - when.now;
    html += '<span class="flow-tip-goal">stąd w <b>'
        + `${esc(fmtMins(left))}</b> u celu (<b>${esc(fmtClock(when.arrive))}</b>)</span>`;
    return html;
}

// Godzina w dymku dotyczy JEDNEGO PUNKTU, a pod kursorem swieci sie cala
// linia - ta kropka mowi wiec, ktorego dokladnie punktu. Siedzi na linii, nie
// pod kursorem: kursor bywa kilka pikseli obok, a godzina jest liczona dla
// miejsca NA torze.
function showTimeDot(when) {
    hideTimeDot();
    if (!timeOpts.ends || !when) return;
    flowSpanLayer = L.circleMarker(when.at, {
        radius: 5, color: '#111', weight: 2, opacity: 0.9,
        fillColor: '#fff', fillOpacity: 1, interactive: false,
    }).addTo(map);
}

function hideTimeDot() {
    if (flowSpanLayer) { map.removeLayer(flowSpanLayer); flowSpanLayer = null; }
}

/** Podświetlenie CAŁEJ wskazanej linii: wszystkie jej narysowane kawałki,
    nie tylko ten pod kursorem. Najpierw ciemna otoczka pod spodem, potem
    linia w pełnym kryciu - warstwa idzie na wierzch całej mapy, więc wskazana
    linia wychodzi przed wszystkie inne, także jaśniejsze od siebie. */
function showLineHighlight(num, kind) {
    const key = kind + ' ' + num;
    if (flowHighlightKey === key) return;      // ta sama linia - nie przerysowujemy
    hideLineHighlight();
    const parts = flowHits.filter(h => h.seg.num === num && h.seg.kind === kind);
    if (!parts.length) return;
    const color = LINE_COLORS[kind] || LINE_COLORS.other;
    const halos = parts.map(h => L.polyline(h.latlngs, {
        color: '#111', opacity: 0.85, weight: h.weight + HALO_EXTRA_PX,
        lineCap: 'round', lineJoin: 'round', interactive: false,
    }));
    const cores = parts.map(h => L.polyline(h.latlngs, {
        color, opacity: 1, weight: h.weight + HIGHLIGHT_EXTRA_PX,
        lineCap: 'round', lineJoin: 'round', interactive: false,
    }));
    flowHighlight = L.layerGroup([...halos, ...cores]).addTo(map);
    flowHighlightKey = key;
}

function hideLineHighlight() {
    if (flowHighlight) { map.removeLayer(flowHighlight); flowHighlight = null; }
    flowHighlightKey = null;
}

function renderFlowPick() {
    if (!flowPick || !flowPickAt) return;
    const sel = flowPick.options[flowPick.index];
    if (sel.hit) showLineHighlight(sel.num, sel.kind);
    else hideLineHighlight();
    // Liczone RAZ: ta sama chwila opisuje i dymek, i kropke na linii.
    const when = timeOpts.hover ? timeAtHover(sel.hit, flowPickPoint) : null;
    showTimeDot(when);
    const html = flowPickHtml(when);
    if (dotOpts.tipCursor) {
        if (!flowTooltip) {
            // setLatLng MUSI być przed addTo: Leaflet przy dodawaniu od razu liczy
            // pozycję dymka i bez współrzędnych rzuca wyjątkiem w środku addTo -
            // przez co dymek nigdy nie powstawał (a każdy ruch myszy nad korytarzem
            // próbował go stworzyć od nowa i wysypywał się w tym samym miejscu).
            flowTooltip = L.tooltip({direction: 'top', offset: [0, -6]})
                .setLatLng(flowPickAt).addTo(map);
        }
        flowTooltip.setLatLng(flowPickAt).setContent(html);
    } else if (flowTooltip) {
        map.removeLayer(flowTooltip);
        flowTooltip = null;
    }
    // Okienko w rogu należy do TABLICY ODJAZDÓW i tylko do niej: „tu jesteś,
    // stąd w 14 min u celu" mówi o punkcie pod kursorem, więc czyta się je
    // tam, gdzie stoi kursor, a nie w drugim końcu ekranu. Kropka traci tu
    // jednak prawo do okienka - inaczej spóźniona odpowiedź z jej tablicą
    // wskoczyłaby w róg już po zejściu kursora na linię.
    timetableTarget = null;
}

function clearFlowHover() {
    hideLineHighlight();
    hideTimeDot();
    if (flowTooltip) { map.removeLayer(flowTooltip); flowTooltip = null; }
    flowPick = null;
    flowPickAt = null;
    flowPickPoint = null;
}

function setFlowPick(options, at, index, containerPoint) {
    const key = corridorKey(options);
    if (!flowPick || flowPick.key !== key) {
        flowPick = {key, index: brightestOption(options), options};
    } else {
        flowPick.options = options;
        if (flowPick.index >= options.length) flowPick.index = brightestOption(options);
    }
    if (index !== undefined) flowPick.index = index;
    flowPickAt = at;
    // Godzina liczy sie dla PUNKTU pod kursorem, wiec sam latlng nie wystarcza -
    // rzut na linie robi sie w pikselach ekranu (patrz projectOnPath).
    flowPickPoint = containerPoint || map.latLngToContainerPoint(at);
    renderFlowPick();
}

function handleFlowHover(e) {
    // Nad grupką numerów rządzi grupka (patrz bindCluster): kursor jest wtedy
    // kilka pikseli obok samej linii, więc szukanie po geometrii zgasiłoby
    // dopiero co wskazany numer.
    const target = e.originalEvent && e.originalEvent.target;
    if (target && target.closest && target.closest('.line-cluster')) return;
    // Nad kropką przystanku rządzi kropka: leży na narysowanej linii, więc bez
    // tego tablica odjazdów i dymek "tu jesteś" wychodzą jeden na drugim.
    if (hoveredStopDot) { clearFlowHover(); return; }
    const hits = flowHits.length ? flowHitsAt(e.containerPoint) : [];
    if (!hits.length) { clearFlowHover(); return; }
    setFlowPick(corridorOptions(hits), e.latlng, undefined, e.containerPoint);
}

function pickFromCluster(marker, index) {
    const at = marker.getLatLng();
    const point = map.latLngToContainerPoint(at);
    const hits = flowHitsAt(point);
    if (!hits.length) return;
    const options = marker.roster.map(l => ({
        num: l.num,
        kind: l.kind,
        hit: hitFor(hits, l.num, l.kind),
    }));
    setFlowPick(options, at, index, point);
}

// Klik w narysowany kurs NIE OTWIERA ŻADNEJ PROPOZYCJI (usunięte 2026-08-16).
// Mapa przepływów tylko pokazuje wachlarz, a wskazywanie konkretnej linii jest
// wyłącznie pod kursorem (hover). Propozycje otwiera się z listy obok.

map.on('mousemove', handleFlowHover);
map.on('mouseout', clearFlowHover);

// Prawy przycisk myszy NIE ROBI TU NIC (usunięte 2026-08-15). Przechodził
// kiedyś na następną linię korytarza, ale wybieranie linii jest już w grupce
// numerów - najechanie na numer wskazuje dokładnie tę linię, bez zgadywania,
// ile razy trzeba kliknąć. Menu kontekstowe przeglądarki zostaje nietknięte.

// -------------------------------------------------- rysowanie jednej trasy ----

let journeyLayer = null;
let hoverLayer = null;

// ---------------------------------------- tablica odjazdów pod kropką ----

// Okienko w rogu ekranu - drugie miejsce, w którym może wyjść to samo, co
// w dymku przy kursorze: tablica odjazdów spod kropki albo podpowiedź o linii
// spod kursora na trasie. Różnica jest jedna, ale w niej cały sens: dymek
// znika razem z kursorem, a okienko ZOSTAJE - podmienia je najechanie na coś
// innego, zamyka krzyżyk albo nowe wyszukiwanie. Dzięki temu da się odczytać
// rozkład, nie trzymając myszy nieruchomo nad kropką.
const flowPanel = $('flow-panel');
const flowPanelBody = $('flow-panel-body');

function showSidePanel(html) {
    if (!flowPanel || !dotOpts.tipPanel) return;
    flowPanelBody.innerHTML = html;
    flowPanel.hidden = false;
}

function hideSidePanel() {
    if (flowPanel) flowPanel.hidden = true;
}

/** Okienko w rogu otwiera się z tablicą przystanku, z którego wyruszamy -
    tak, jakby ktoś od razu najechał na jego kropkę.

    To ten jeden rozkład, który interesuje zawsze: pytanie "o której stąd coś
    jedzie" pada, zanim jeszcze spojrzy się na trasę. Reszta działa jak
    dotąd - najechanie na cokolwiek innego podmienia treść, krzyżyk zamyka.

    Kropki startowej szukamy najpierw w wybranej trasie, potem w wachlarzu:
    przy wybranej trasie kropki wachlarza są zdjęte z mapy (patrz dimFlow),
    więc pytanie ich o cokolwiek pokazywałoby rozkład punktu, którego nie
    widać. */
function seedStartPanel() {
    if (!dotOpts.tipPanel || !flowPanel) return;
    // Nie wyrywamy okienka spod ręki: przerysowanie w trakcie najeżdżania
    // (suwaki wyglądu) ma zostawić to, na co użytkownik właśnie patrzy.
    if (hoveredStopDot || flowPick) return;
    const dot = startDotIn(journeyLayer) || startDotIn(flowDotLayer);
    if (!dot) return;
    timetableTarget = dot;
    loadTimetable(dot, dot.where, dot.sec);
}

function startDotIn(layer) {
    if (!layer) return null;
    return layer.getLayers().find(l => l.isStart && l.where) || null;
}

if (flowPanel) $('flow-panel-close').addEventListener('click', hideSidePanel);

// Okienko w rogu jest jedynym miejscem, w którym tę tablicę da się KLIKNĄĆ:
// dymek przy kursorze Leaflet trzyma poza zdarzeniami myszy (pointer-events),
// więc przycisk "trasa" jest tam schowany stylem (patrz style.css).
if (flowPanelBody) flowPanelBody.addEventListener('click', routeClick);

// Odpowiedzi /api/timetable trzymamy pod (przystanek, doba, godzina) - ta
// sama kropka pytana drugi raz (powrót kursorem, przerysowanie trasy po
// suwaku) pokazuje dymek od razu, bez mrugnięcia "Ładowanie...".
const timetableCache = new Map();

// Która kropka jest pod kursorem - patrz handleFlowHover.
let hoveredStopDot = null;

// Promienie idą z ustawień (sekcja „Kropki i rozkład"), więc to nie są stałe,
// tylko wartości czytane przy każdym rysowaniu - stąd funkcje, nie obiekty.
const STOP_DOT_STYLE = {radius: 5, weight: 3, color: '#263238',
                        fillColor: '#fff', fillOpacity: 1};

const journeyDotStyle = () => ({
    ...STOP_DOT_STYLE,
    radius: dotOpts.size,
    weight: Math.min(4, Math.max(2, Math.round(dotOpts.size * 0.5))),
});

const TIP_LOADING = '<div class="tt-note">Ładowanie…</div>';

/** Co się tu dzieje z tą linią - trzy rzeczy, nie jedna (punkt 11 kontraktu,
    liczy je planner._transfer_nodes). Jedna rodzina znaków, czytana zawsze tak
    samo: LEWY koniec mówi, skąd ten pojazd tu jest - kreska "stąd rusza (dla
    Ciebie)", grot "już jedzie"; PRAWY mówi, co dalej - grot "jedzie dalej",
    kreska "tu koniec jazdy".

      |->   start    wsiadasz tu pierwszy raz; wcześniej mapa tą linią nie
                     wiozła, więc nie było jak wsiąść taniej
      ->->  through  tym pojazdem można już jechać - wsiadanie tutaj to jedna
                     z możliwości, a nie jedyna
      ->|   end      tą linią się tu PRZYJEŻDŻA i wysiada; dalej nie wiezie */
const FLOW_ICONS = {
    start: {
        d: ['M2 2.5v7', 'M2 6h11.5', 'M10.5 3.2 13.5 6l-3 2.8'],
        title: 'stąd wsiadasz — wcześniej mapa tą linią nie wiozła',
    },
    through: {
        d: ['M1 6h12.5', 'M4 3.2 7 6l-3 2.8', 'M10.5 3.2 13.5 6l-3 2.8'],
        title: 'tędy przejeżdża — możesz już nim jechać',
    },
    end: {
        d: ['M1 6h12.5', 'M4 3.2 7 6l-3 2.8', 'M13.5 2.5v7'],
        title: 'tu wysiadasz — dalej mapa tą linią nie wiezie',
    },
};

function flowIcon(flow) {
    const icon = FLOW_ICONS[flow];
    // Pusty znacznik, nie brak znacznika: kolumna ma zostać na miejscu, żeby
    // godziny w kolejnych wierszach stały w jednej osi.
    if (!icon) return '<span class="tt-flow"></span>';
    return `<svg class="tt-flow tt-flow-${flow}" viewBox="0 0 15 12" role="img">`
        + `<title>${esc(icon.title)}</title>`
        + icon.d.map(d => `<path d="${d}"/>`).join('')
        + '</svg>';
}

/** `mapSec` to godzina, o której MAPA stawia pasażera na tej kropce. Sama
    tablica liczy od godziny z formularza (patrz timetableAnchor), więc na
    liście bywają odjazdy sprzed tej chwili - i to jest cel zgłoszenia #143.
    Żeby nic się przez to nie zacierało, dzieli je widoczna kreska, a wiersze
    sprzed niej zajmują najwyżej połowę listy: inaczej na ruchliwym węźle
    wypchnęłyby poza suwak dokładnie te odjazdy, po które się tu przyszło. */
function timetableHtml(data, mapSec) {
    if (data.error) return `<div class="tt-note">${esc(data.error)}</div>`;
    const head = `<div class="tip-head"><span class="tip-stop">${esc(data.stop)}</span>` +
                 `<span class="tt-from">od ${esc(data.from_time)}</span></div>`;
    if (!data.departures.length) {
        return head + '<div class="tt-note">Nic już stąd nie odjeżdża tego dnia.</div>';
    }
    // `all_departures` to tablica sprzed odsiewu - stąd bierze się takt
    // linii, patrz summariseRepeats.
    const wszystkie = summariseRepeats(data.departures, data.all_departures);
    const ile = timetableRows();
    const przed = mapSec === undefined ? []
                                       : wszystkie.filter(d => d.sec < mapSec);
    const reszta = mapSec === undefined ? wszystkie
                                        : wszystkie.filter(d => d.sec >= mapSec);
    // Odjazdy sprzed przyjazdu mapy to tło - najwyżej połowa wierszy, bez
    // wymuszonego minimum: przy jednym wierszu zostaje ten, na który się
    // zdąży, bo to o niego pyta tablica (#143).
    const kreskaPo = Math.min(przed.length, Math.floor(ile / 2));
    const list = [...przed.slice(0, kreskaPo), ...reszta].slice(0, ile);
    // Kolumna z ikonką pojawia się tylko wtedy, gdy jest co w niej postawić.
    // Tablica pod kropką WYBRANEJ trasy pyta o cały przystanek, a nie o węzeł
    // mapy, więc nie wie, co się tu z którą linią dzieje - pusta kolumna
    // przesuwałaby jej wiersze bez powodu.
    const flows = list.some(d => FLOW_ICONS[d.flow]);
    // Kreska stoi tam, gdzie kończą się odjazdy sprzed przyjazdu mapy - i mówi
    // wprost, o której mapa cię tu stawia, żeby "za ile" nad nią nie wyglądało
    // na obietnicę, że zdążysz.
    const kreska = kreskaPo
        ? `<li class="tt-here"><span>tu według mapy jesteś ` +
          `${esc(fmtClock(mapSec))}</span></li>`
        : '';
    const rows = list.map((d, i) => (i === kreskaPo ? kreska : '') +
        `<li>` + (flows ? flowIcon(d.flow) : '') +
        `<span class="tt-time">${esc(d.time)}</span>` +
        `<span class="badge ${esc(d.mode)}">${esc(d.num)}</span>` +
        `<span class="tip-dir">${esc(d.headsign)}</span>` +
        // "0 min", nie "teraz": nagłówek mówi "od 16:57", a to nie jest
        // godzina zegarowa, tylko najwcześniejsza, o której da się tu być -
        // "teraz" obok niej znaczyłoby coś innego niż znaczy. Rytm dopisany
        // W TEJ SAMEJ linii, żeby powtarzająca się linia nie miała wiersza
        // wyższego od pozostałych.
        `<span class="tt-in">${esc(d.in_min < 1 ? 0 : d.in_min)} min` +
        (d.every_min ? `<small> · co ${esc(d.every_min)} min</small>` : '') +
        `</span>` + routeButtonHtml(d, 'trasa') + `</li>`
    ).join('');
    return head + `<ul class="tt-rows${flows ? ' has-flow' : ''}">${rows}</ul>`;
}

// Ile wierszy pokazuje dymek - suwak w panelu, sekcja „Kropki i rozkład”.
// Rzecz do dostrojenia PRZY MAPIE, bo o tym, ile wierszy jest za dużo,
// decyduje to, ile z niej zasłaniają - a tego nie widać z pliku konfiguracji.
const TIMETABLE_ROWS_MAX = 20;  // wyżej dymek przykrywa mapę, o którą się pyta
const TIMETABLE_FETCH = 40;     // ...a tyle pobieramy, bo część odsiewamy

/** Suwak ma swój sufit w atrybucie, ale wartość wraca też z localStorage -
    a tam może leżeć cokolwiek, również z czasów, gdy zakres był inny. */
function timetableRows() {
    const rows = Math.round(Number(dotOpts.rows));
    if (!Number.isFinite(rows)) return DOT_DEFAULTS.rows;
    return Math.max(1, Math.min(rows, TIMETABLE_ROWS_MAX));
}

const lineKey = l => `${l.kind} ${l.num} ${l.headsign}`;

/** Zostawia tylko to, w co MAPA pozwala tu wsiąść.

    Węzeł wachlarza niesie swoją listę linii (patrz planner._transfer_nodes),
    bo inaczej dymek na Pilczycach wypisywał wszystko, co przez nie przejeżdża -
    razem z tramwajem jadącym dokładnie tam, skąd się przyjechało. Kierunek
    jest częścią tożsamości linii: sam numer za mało mówi, ta sama trójka mija
    węzeł w obie strony. */
function keepOfferedLines(data, lines) {
    if (!lines) return data;
    // `depart_by` to OSTATNI odjazd tej linii, którym da się jeszcze dojechać
    // do celu w oknie mapy - policzony na serwerze z rozkładu, nie zgadnięty
    // (patrz planner._line_deadlines). Wcześniej front sprawdzał tylko, czy
    // sam odjazd mieści się w oknie: warunek konieczny, nie wystarczający -
    // autobus ruszający minutę przed jego zamknięciem do celu nie dowiezie.
    const limit = new Map(), flow = new Map();
    for (const l of lines) {
        // "end" to linia, KTÓRĄ SIĘ TU PRZYJEŻDŻA, a nie którą się stąd jedzie.
        // Jej wiersz dokłada withArrivals z godziny przyjazdu; zostawiona tutaj
        // dostałaby najbliższy ODJAZD, czyli opcję, której mapa nie proponuje.
        if (l.flow === 'end') continue;
        limit.set(lineKey(l), l.depart_by === undefined ? Infinity : l.depart_by);
        // Bez `flow` (odpowiedź z cache'u sprzed zmiany, tryb awaryjny) wiersz
        // zostaje BEZ ikonki - lepiej nie powiedzieć nic, niż zgadnąć.
        flow.set(lineKey(l), l.flow);
    }
    const kept = [];
    for (const d of data.departures) {
        const key = lineKey({kind: d.mode, num: d.num, headsign: d.headsign});
        // Negacja, nie proste `<=`: oferta bez `depart_by` daje Infinity,
        // a odjazd bez `sec` - NaN. Każde porównanie z NaN jest fałszem, więc
        // przy `<=` wypadłyby wtedy WSZYSTKIE wiersze zamiast żadnego.
        if (!limit.has(key) || d.sec > limit.get(key)) continue;
        kept.push({...d, flow: flow.get(key)});
    }
    return {...data, departures: kept};
}

/** Dokłada wiersze linii, KTÓRYMI SIĘ TU PRZYJEŻDŻA (`flow: "end"`).

    Tablica przystanku odpowiada na pytanie "co stąd odjeżdża", ale człowiek
    stojący pod kropką pyta o coś szerszego: "co się tu ze mną dzieje". Pojazd,
    którym się tu dojechało i z którego się wysiada, nie jest odjazdem i w
    tablicy przystanku go nie ma - a bez niego trzecia ikonka (patrz FLOW_ICONS)
    nie miałaby przy czym stanąć i tablica dalej pokazywałaby tylko odjazdy.

    Godzina bierze się z węzła (`arrive`, patrz planner._transfer_nodes), czyli
    z rozkładu tego samego kursu, z którego narysowano kawałek - nie z
    najbliższego kursu tej linii, bo tym akurat się tu nie przyjechało.

    `fromSec` to ta sama godzina, od której liczy nagłówek ("od 16:06"), więc
    "za ile" znaczy w każdym wierszu to samo. */
function withArrivals(data, lines, fromSec) {
    if (!lines) return data;
    const rows = [];
    for (const l of lines) {
        if (l.flow !== 'end' || l.arrive === undefined) continue;
        rows.push({
            time: fmtClock(l.arrive),
            sec: l.arrive,
            in_min: Math.round((l.arrive - fromSec) / 60),
            num: l.num,
            mode: l.kind,
            headsign: l.headsign,
            flow: 'end',
        });
    }
    if (!rows.length) return data;
    // Kolejność robi summariseRepeats (sortuje po `sec`) - przyjazd ląduje
    // między odjazdami tam, gdzie naprawdę jest na osi czasu.
    return {...data, departures: [...data.departures, ...rows]};
}

/** Wycina odjazdy zza horyzontu mapy.

    Siatka bezpieczeństwa POD odsiewem z keepOfferedLines, nie zamiast niego:
    mocna reguła ("czy tym kursem w ogóle się dojedzie") działa przez
    `depart_by` przy linii, a tu zostaje słabszy, ale zawsze prawdziwy warunek
    na wypadek oferty bez tej liczby - odpowiedzi z cache'u sprzed zmiany albo
    z trybu awaryjnego.

    Bez tego na rzadko obsługiwanym węźle dymek wypisywał odjazdy o 17:51 na
    mapie kończącej się o 15:12 - godziny prawdziwe, tylko bez związku
    z podróżą, o którą pytamy. */
function keepWithinHorizon(data, deadline) {
    if (!deadline) return data;
    return {...data, departures: data.departures.filter(d => d.sec <= deadline)};
}

/** Zwija powtórzenia tej samej linii w JEDEN wiersz z częstotliwością.

    Osiem odjazdów jednej linii to nie osiem opcji, tylko jedna opcja i jej
    rytm - a po odsianiu linii, których mapa stąd nie proponuje, na rzadkim
    węźle zostawała dokładnie taka lista. Zamiast wypisywać je wszystkie albo
    część z nich gubić, zostaje najbliższy odjazd i notka "co X min":
    "za 4 min, potem co 15 min" mówi to samo, w jednym wierszu i bez zgadywania,
    czy pominięte kursy w ogóle istnieją.

    Takt bierze się z `rhythmSource` - PEŁNEJ tablicy przystanku, sprzed
    odsiewu - więc pisze się go także wtedy, gdy następny kurs wypada już poza
    zakresem mapy. To informacja o LINII, nie o oknie: "co 20 min" tak samo
    trzeba wiedzieć, gdy ten kolejny kurs mapa jeszcze rysuje, jak i gdy już
    nie. Liczony z listy po odsiewie znikał dokładnie tam, gdzie był
    najpotrzebniejszy - na rzadkim węźle blisko granicy okna.

    Odstęp to MEDIANA przerw, nie średnia: jeden nocny przeskok o godzinę nie
    ma prawa przesunąć liczby opisującej normalny takt.

    Kierunek jest częścią tożsamości linii - ta sama linia w drugą stronę to
    osobna opcja i osobny wiersz. */
function summariseRepeats(departures, rhythmSource) {
    const rytm = lineRhythms(rhythmSource || departures);
    const groups = new Map();
    for (const d of departures) {
        // `flow` w kluczu, bo przyjazd i odjazd tej samej linii to dwa różne
        // zdarzenia na tym przystanku - zwinięte w jeden wiersz udawałyby
        // rytm kursowania tam, gdzie go nie ma.
        const key = lineKey({kind: d.mode, num: d.num, headsign: d.headsign})
            + '|' + (d.flow || '');
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(d);
    }
    const out = [];
    for (const list of groups.values()) {
        const d = list[0];
        // Wiersz przyjazdu to jedno zdarzenie z mapy, a nie oferta - takt
        // przy nim mówiłby o odjazdach, o które nikt tu nie pyta.
        const every = d.flow === 'end' ? undefined
            : rytm.get(lineKey({kind: d.mode, num: d.num, headsign: d.headsign}));
        out.push(every ? {...d, every_min: every} : d);
    }
    return out.sort((a, b) => a.sec - b.sec);
}

/** Takt każdej linii z tablicy: klucz linii -> mediana przerw w minutach.
    Linie z jednym tylko odjazdem nie trafiają tu wcale - jeden kurs nie ma
    rytmu, a "co 0 min" byłoby zdaniem o niczym. */
function lineRhythms(departures) {
    const groups = new Map();
    for (const d of departures) {
        // Przyjazd nie jest odjazdem: doklejony wiersz "end" tej samej linii
        // stanąłby w rytmie obok jej odjazdów i zrobiłby takt z jednego kursu.
        if (d.flow === 'end') continue;
        const key = lineKey({kind: d.mode, num: d.num, headsign: d.headsign});
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(d);
    }
    const out = new Map();
    for (const [key, list] of groups) {
        if (list.length > 1) out.set(key, medianGapMin(list));
    }
    return out;
}

function medianGapMin(list) {
    const gaps = [];
    for (let i = 1; i < list.length; i++) gaps.push(list[i].sec - list[i - 1].sec);
    gaps.sort((a, b) => a - b);
    return Math.round(gaps[(gaps.length - 1) >> 1] / 60);
}

/** `where` to {name} albo {lat, lon}: trasa zna nazwę przystanku wprost
    z etapu, a mapa przepływów stawia kropki z geometrii kawałków i zna tylko
    położenie słupka (nazwę dopowiada backend, patrz gtfs.stop_at).
    `where.lines` (tylko wachlarz) zawęża tablicę do tego, co mapa proponuje. */
/** Jedno ujście dla gotowego HTML-a tablicy: dymek kropki (jeśli w ogóle
    jest - przy wyłączonym dymku kropka nie dostaje go wcale) i okienko
    w rogu, ale to drugie TYLKO dla kropki, która jest teraz wskazywana.
    Bez tego warunku odpowiedź, która przyszła po zejściu kursora na inną
    kropkę, nadpisywałaby w okienku świeższą treść. */
function emitTimetable(dot, html) {
    const out = dot.isStart && dotOpts.start ? `<div class="tt-start">${html}</div>` : html;
    if (dot.getTooltip()) dot.setTooltipContent(out);
    if (timetableTarget === dot) showSidePanel(out);
}

// Kropka, której tablicę pokazujemy teraz - patrz emitTimetable.
let timetableTarget = null;

/** Od której godziny liczy dymek kropki (zgłoszenie #143).

    Domyślnie od GODZINY Z FORMULARZA, a nie od tej, o której mapa sądzi, że
    pasażer tu stanie: mapa zna tylko to, co sama narysowała, więc jej godzina
    bywa za późna - a wtedy tablica gubiła nie kilka wierszy, lecz całą
    odpowiedź. Kto jest tu wcześniej pieszo, rowerem albo linią spod progu,
    i tak zobaczy, co odjeżdża.

    Przy odpowiedzi z kolejnej doby (punkt 13 kontraktu) zostaje godzina mapy:
    pytanie sprzed doby nie mówi już nic o tamtym dniu. Nigdy później niż
    mapa - wcześniejsza godzina niczego nie ukrywa, późniejsza ukrywa. */
function timetableAnchor(sec) {
    const flow = lastFlow;
    if (!flow || flow.day_offset !== 0 || typeof flow.departure_sec !== 'number') {
        return sec;
    }
    return Math.min(flow.departure_sec, sec);
}

function loadTimetable(dot, where, sec) {
    const date = $('date').value;
    const from = timetableAnchor(sec);
    // Do klucza wchodzi też `flow` i `arrive`: ta sama linia raz jest ofertą
    // do wsiadania, a raz pojazdem, którym się tu przyjechało - i wtedy dymek
    // ma pokazać co innego, choć przystanek i godzina się nie zmieniły.
    const filtr = where.lines
        ? where.lines.map(l => `${lineKey(l)}/${l.flow || ''}/${l.arrive || ''}`).join('|')
        : '';
    const key = `${where.name || where.lat + ',' + where.lon}`
        + `@${date}@${from}@${sec}@${filtr}@${where.deadline || ''}`;
    const cached = timetableCache.get(key);
    if (cached !== undefined) { emitTimetable(dot, cached); return; }

    const query = where.name
        ? {stop: where.name, date, from_sec: from}
        : {lat: where.lat, lon: where.lon, date, from_sec: from};
    // Z zapasem PRZY KAŻDEJ kropce, nie tylko przy węźle wachlarza: przy
    // węźle część odjazdów odsiewamy, a przy kropce wybranej trasy pytamy
    // o tyle, ile suwak w ogóle pozwala pokazać. Bez tego serwerowa domyślna
    // ósemka byłaby cichym sufitem mocniejszym od suwaka.
    query.limit = TIMETABLE_FETCH;
    emitTimetable(dot, TIP_LOADING);
    fetch('/api/timetable?' + new URLSearchParams(query))
        .then(r => r.json())
        .then(data => {
            // Przyjazdy doklejamy PO obu sitach: oba pytają "czy tym odjazdem
            // jeszcze się dojedzie", a wiersz przyjazdu nie jest odjazdem -
            // to fakt z samej mapy, więc nie ma go czym odsiewać.
            // Pełna tablica jedzie przez oba sita nietknięta (dokładamy ją
            // do odpowiedzi, a sita przepisują tylko `departures`): takt linii
            // ma się liczyć z rozkładu, nie z tego, co przeżyło odsiew.
            const pelna = {...data, all_departures: data.departures};
            const html = timetableHtml(data.error ? data : withArrivals(
                keepWithinHorizon(keepOfferedLines(pelna, where.lines), where.deadline),
                where.lines, from), sec);
            // Pustą tablicę zapamiętujemy (to też odpowiedź), ale błędu już
            // nie: offline z service workera wraca jako {error}, a po powrocie
            // sieci kropka miałaby go w pamięci na zawsze.
            if (!data.error) timetableCache.set(key, html);
            emitTimetable(dot, html);
        })
        .catch(() => emitTimetable(dot,
            '<div class="tt-note">Nie udało się pobrać rozkładu.</div>'));
}

/** Kropka przystanku na narysowanej trasie: po najechaniu pokazuje, co stąd
    odjeżdża - bez tego przesiadka jest punktem, o którym wiadomo tylko, że
    się na nim wysiada.

    Godzinę bierzemy z etapu (`sec` na osi doby rozkładowej, nie "HH:MM"),
    więc przesiadka po północy pyta o rozkład swojej doby, a nie o 00:40
    dnia obok (patrz gtfs.load_day).

    Klik NIE przechodzi do mapy, choć klik w to samo miejsce obok kropki
    zamyka trasę: na telefonie nie ma najeżdżania, dotknięcie kropki jest
    jedynym sposobem otwarcia dymka - i nie może przy okazji sprzątać tego,
    czego dotyczy. */
function stopDot(point, where, sec, style) {
    // Że to start, MÓWI ŹRÓDŁO: przy węźle wachlarza flaga z backendu (ten
    // rozwiązał zapytanie do konkretnych słupków, patrz planner._transfer_nodes),
    // przy wybranej trasie - miejsce w niej samej (wsiadanie pierwszego
    // przejazdu). Front niczego tu nie odtwarza z nazw ani z odległości.
    const isStart = !!where.start;
    const dot = L.circleMarker(point, {
        ...(style || STOP_DOT_STYLE),
        ...(isStart && dotOpts.start ? START_DOT_STYLE : {}),
    });
    dot.isStart = isStart;
    // Czym ta kropka jest - żeby dało się ją "najechać" bez kursora.
    dot.where = where;
    dot.sec = sec;
    // Dymek istnieje tylko wtedy, gdy jest włączony: Leaflet otwiera związany
    // dymek sam, na mouseover, więc "nie pokazuj go" nie da się zrobić inaczej
    // niż nie wiążąc go wcale. Przełącznik przerysowuje mapę (patrz applyDots),
    // więc kropki powstają od nowa z aktualnym ustawieniem.
    if (dotOpts.tipCursor) {
        dot.bindTooltip(TIP_LOADING, {
            direction: 'top', offset: [0, -6], opacity: 1,
            className: 'timetable-tip',
        });
    }
    dot.on('mouseover', () => {
        hoveredStopDot = dot;
        timetableTarget = dot;
        clearFlowHover();
        loadTimetable(dot, where, sec);
    });
    dot.on('mouseout', () => {
        if (hoveredStopDot === dot) hoveredStopDot = null;
        // timetableTarget zostaje: okienko w rogu ma przeczekać zejście kursora.
    });
    dot.on('click', e => {
        L.DomEvent.stop(e);
        timetableTarget = dot;
        loadTimetable(dot, where, sec);
        if (dot.getTooltip()) dot.openTooltip();
    });
    return dot;
}


// Przystanek startowy - ta sama zieleń, co marker startu i szyna w formularzu,
// żeby to była oczywiście ta sama rzecz, a nie kolejny kolor do nauczenia.
const START_DOT_STYLE = {color: '#1b5e20', weight: 4, fillColor: '#c8f0cd'};

// Kropki wachlarza są mniejsze od tych na wybranej trasie: jest ich kilkanaście
// naraz i mają nie przykryć samej mapy - a trasa, gdy się ją wybierze, ma być
// tym, co rzuca się w oczy.
const FLOW_DOT_STYLE = {radius: 4, weight: 2, color: '#263238',
                        fillColor: '#fff', fillOpacity: 1};

// Kropka waży tyle, co to, co przy niej leży: jej krycie idzie z jasności
// węzła (backend, patrz planner._transfer_nodes) przez tę samą skalę, co
// krycie kawałków - więc suwak „najbledsza linia" rusza jedno i drugie razem.
// Bez tego blada okolica dostawała kropki tak samo mocne, jak najszybsza
// trasa, i to one niosły ciężar obrazka zamiast linii.
const flowDotStyle = (w = 1) => {
    const krycie = lookOpacity(Math.max(0, Math.min(1, Number(w) || 0)));
    return {
        ...FLOW_DOT_STYLE,
        radius: Math.max(2, dotOpts.size - 1),
        weight: Math.min(3, Math.max(1.5, Math.round(dotOpts.size * 0.4))),
        opacity: krycie,
        fillOpacity: krycie,
    };
};

/** Kropki węzłów wachlarza - jedna na MIEJSCE, nie na słupek.

    Węzły liczy backend (patrz planner._transfer_nodes), a nie front z
    geometrii: plac z trzema peronami dostawał wtedy trzy kropki, każdą z inną
    zawartością, bo każdy peron to inne współrzędne i inna godzina. Grupowanie
    po miejscu jest w rozkładzie (gtfs._build_places), więc front nie ma go
    z czego odtworzyć - i nie powinien zgadywać po odległości na ekranie. */
function flowStopDots(nodes, deadline) {
    return (nodes || []).map(n => stopDot(
        nodePoint(n), {name: n.name, lines: n.lines, deadline, start: n.start},
        // Start nigdy nie blednie: to nie jest jedna z opcji, tylko miejsce,
        // w którym stoisz.
        n.sec, flowDotStyle(n.start ? 1 : n.w)));
}

/** Gdzie postawić kropkę węzła. Obie współrzędne liczy backend (patrz
    planner._transfer_nodes): `lat`/`lon` to słupek, z którego wzięta jest
    godzina, `clat`/`clon` - środek wszystkich słupków tego miejsca.
    Przełącznik tylko wybiera, bo nie ma tu czego dopytywać. */
function nodePoint(node) {
    return dotOpts.center && node.clat !== undefined
        ? [node.clat, node.clon]
        : [node.lat, node.lon];
}


// Auto car-sharingu w zasięgu mapy. Fiolet, bo tym kolorem te auta jeżdżą
// i po nim się je poznaje na ulicy (ten sam powód, co --car w style.css).
const CAR_STYLE = {radius: 5, weight: 1, color: '#6a1b9a',
                   fillColor: '#ab47bc', fillOpacity: 0.9};

// Auto, przy którym jest coś do wzięcia w programie „Ogarniam", nosi złotą
// obwódkę - inaczej trzeba by najeżdżać po kolei na wszystkie, żeby znaleźć
// to jedno, za które Traficar płaci.
const CAR_OGARNIAM_STYLE = {radius: 6, weight: 2.5, color: '#f9a825'};

// Dostawczak (tylko na życzenie, patrz traficar.map_choice) to pusty pierścień
// zamiast pełnej kropki: to osobny wybór, więc ma być widać bez najeżdżania,
// który jest który. Złota obwódka „Ogarniam" kładzie się na nim tak samo.
const CAR_VAN_STYLE = {radius: 6, weight: 2.5, fillColor: '#ffffff'};

function carStyle(car) {
    return {
        ...CAR_STYLE,
        ...(car.van ? CAR_VAN_STYLE : {}),
        ...(car.ogarniam && car.ogarniam.length ? CAR_OGARNIAM_STYLE : {}),
    };
}

function carName(car) {
    return `${esc(car.model)}${car.van ? ' · dostawczy' : ''} · ${esc(car.plate)}`;
}

/** Znaczniki wolnych aut (patrz traficar.map_cars).

    Auto jest MIEJSCEM, do którego mapa dowozi, a nie kursem: nie ma linii,
    nie ma jasności, nie należy do wachlarza (kontrakt p.15). Cała treść
    siedzi w dymku, bo o aucie mówi się to samo, co o przystanku - o której
    się przy nim jest - plus to, czego o nim nie wiemy: ile stąd do celu
    w linii prostej i ani słowa o czasie jazdy. */
function flowCarMarkers(cars) {
    return (cars || []).map(car => L.circleMarker([car.lat, car.lon], carStyle(car))
        .bindTooltip(carTooltipHtml(car), {
        direction: 'top', offset: [0, -4], opacity: 1,
    }));
}

function carTooltipHtml(car) {
    return [
        `<b>${carName(car)}</b>`,
        `Jesteś przy nim ${fmtClock(car.at)} — ${fmtMins(car.walk_sec)} ` +
        `pieszo z „${esc(car.from)}”`,
        `Do celu ${fmtDist(car.to_dest_m)} w linii prostej`,
        `Paliwo ${car.fuel}%, zasięg ${car.range} km`,
        ogarniamText(car.ogarniam),
        ...(dotOpts.why && car.why ? [
            whyText(car.why) + (car.why.group > 1
                ? ` · najlepsze z ${car.why.group} aut spod tego samego miejsca` : ''),
        ] : []),
    ].join('<br>');
}

/** Debug: dlaczego rower albo auto przeszło wybór (pole `why`, patrz
    bikes._why i traficar._why) - w czym jest najlepsze i co je bije. Poziom
    to liczba tych, które je biją: 0 znaczy, że nie bije go nic. */
function whyText(why) {
    const parts = [];
    if (why.records.length) parts.push(`najlepszy: ${why.records.join(', ')}`);
    parts.push(why.beaten
        ? `bije go ${why.beaten} z ${why.of}: ${why.beaten_by.map(esc).join(', ')}` +
          (why.beaten > why.beaten_by.length ? ' …' : '') + ` (poziom ${why.beaten})`
        : `nic go nie bije (z ${why.of})`);
    return `<i>${parts.join(' · ')}</i>`;
}

/** Program „Ogarniam" (w feedzie: `discounts`) - co przy tym aucie jest do
    wzięcia i za ile. Zdanie pada ZAWSZE, także gdy nie ma nic: brak wiersza
    znaczyłby naraz „nic tu nie ma" i „nie wiadomo", a to dwie różne rzeczy. */
function ogarniamText(tasks) {
    if (!tasks || !tasks.length) return 'Ogarniam: nic do wzięcia';
    return 'Ogarniam: ' + tasks
        .map(task => `${esc(task.co.toLowerCase())} ${task.ile} zł`)
        .join(' · ');
}

function fmtDist(metres) {
    return metres < 1000
        ? `${metres} m`
        : `${(metres / 1000).toFixed(1).replace('.', ',')} km`;
}

// Rower miejski na mapie. Pomarańcz jest ten sam, co plakietka etapu
// rowerowego na wybranej trasie - „to jest rower" ma znaczyć jedno, niezależnie
// czy patrzy się na kropkę stacji, czy na kreskę przejazdu.
const BIKE_STYLE = {weight: 1, color: '#e65100', fillColor: '#ffb74d',
                    fillOpacity: 0.9};
// Rower stojący luzem, poza stojakiem: ta sama rodzina koloru, ale pusty
// środek - to jeden rower, a nie miejsce, w którym stoi ich kilka.
const BIKE_LOOSE_STYLE = {radius: 4, weight: 2, color: '#e65100',
                          fillColor: '#fff', fillOpacity: 1};
// Pytanie o inny dzień: stacja stoi tam zawsze, ale ile w niej będzie
// rowerów - nie wiadomo. Szarość mówi to, zanim się przeczyta dymek.
const BIKE_UNKNOWN_STYLE = {radius: 4, weight: 1.5, color: '#9e9e9e',
                            fillColor: '#fff', fillOpacity: 0.35};
// Drugi koniec przejazdu. Blada i bez własnego życia: pojawia się razem ze
// strzałką, pod kursorem, bo opisuje przejazd, a nie miejsce, z którego coś
// się bierze. Stacja, na której da się WSIĄŚĆ na rower, ma własną kropkę.
const BIKE_TARGET_STYLE = {radius: 4, weight: 1.5, color: '#e65100',
                           fillColor: '#ffe0b2', fillOpacity: 0.85};
// Kreska przejazdu kropkowana tak samo, jak etap rowerowy wybranej trasy.
const BIKE_RIDE_STYLE = {color: '#e65100', weight: 2, opacity: 0.8,
                         dashArray: '1, 6', interactive: false};

/** Kropki rowerów w zasięgu mapy (patrz bikes.map_places).

    Kropkę dostaje wyłącznie początek przejazdu, który przeszedł wybór
    (punkt 16). Drugi koniec przejazdu pokazuje się razem z kreską - a jeśli
    sam jest początkiem wybranego przejazdu, stoi na mapie z własnego tytułu.

    Sam przejazd domyślnie nie jest narysowany na stałe: dwie kropki mówią to
    samo, co kreska między nimi. Kreski pojawiają się pod kursorem, a na stałe
    - po zapaleniu przełącznika pod zębatką (dotOpts.bikeRides). */
function flowBikeMarkers(places, live) {
    return (places || []).map(place => {
        const dot = L.circleMarker([place.lat, place.lon],
                                   bikeStyle(place, live))
            .bindTooltip(bikeTooltipHtml(place, live),
                         {direction: 'top', offset: [0, -4], opacity: 1});
        dot.on('mouseover', () => showBikeRides(place));
        dot.on('mouseout', () => {
            if (!dotOpts.bikeRides) clearBikeRides();
            else if (lastFlow) showAllBikeRides(lastFlow.bike_places);
        });
        return dot;
    });
}

function bikeStyle(place, live) {
    if (!live) return BIKE_UNKNOWN_STYLE;
    if (place.loose) return BIKE_LOOSE_STYLE;
    // Wielkość niesie liczbę rowerów, więc pełna i pusta stacja różnią się
    // od siebie bez czytania. Sufit, bo powyżej kilkunastu "więcej" już nic
    // nie zmienia w decyzji, a kropka zaczyna zasłaniać mapę.
    return {...BIKE_STYLE, radius: 4 + Math.min(place.bikes, 16) * 0.4};
}

function bikeTooltipHtml(place, live) {
    const rows = [`<b>${esc(place.name || 'Rower luzem')}</b>`];
    rows.push(`Jesteś przy nim ${fmtClock(place.at)} — ` +
              `${fmtMins(place.walk_sec)} pieszo z „${esc(place.from)}”`);
    rows.push(live ? bikeCountText(place)
                   : '<b>Nie wiadomo, czy będą tu rowery</b> — liczba rowerów ' +
                     'jest z tej chwili, a pytasz o inny dzień');
    if (dotOpts.why && place.why) {
        // Wybór przechodzi STACJA (punkt 16), więc powód stoi przy niej, a pod
        // nim podróże, które stacja pokazuje - każda ze swoimi trzema liczbami.
        rows.push(whyText(place.why));
        for (const ride of place.rides) {
            for (const option of ride.options) {
                rows.push(`→ ${esc(ride.name)}: przy rowerze ` +
                          `${fmtClock(option.bike_at)}, w celu ` +
                          `${fmtClock(option.arrival)}, pojazdów ${option.vehicles}`);
            }
        }
    }
    return rows.join('<br>');
}

function bikeCountText(place) {
    if (place.loose) return place.electric ? 'Jeden rower, elektryczny'
                                           : 'Jeden rower';
    // Przy odhaczonym rodzaju liczba ma mówić o tym, na co MOŻNA tu wsiąść:
    // łączna obiecywałaby rowery, których pasażer właśnie nie szuka.
    const {electric, regular} = bikeKinds();
    const elektryki = place.electric || 0;
    const zwykle = place.bikes - elektryki;
    if (electric && !regular) {
        return `${elektryki} ` + plural(elektryki, 'rower elektryczny',
                                        'rowery elektryczne', 'rowerów elektrycznych');
    }
    if (regular && !electric) {
        return `${zwykle} ` + plural(zwykle, 'rower zwykły',
                                     'rowery zwykłe', 'rowerów zwykłych');
    }
    const bikes = `${place.bikes} ` +
        plural(place.bikes, 'rower', 'rowery', 'rowerów');
    return elektryki
        ? `${bikes} (w tym ${elektryki} ` +
          plural(elektryki, 'elektryczny', 'elektryczne', 'elektrycznych') + ')'
        : bikes;
}

/** Kreski przejazdów z jednej kropki - te, które przeszły wybór.

    Z etykietką przy każdym drugim końcu, bo to jest cała odpowiedź na „dokąd
    tym rowerem": jak daleko to stąd. */
function showBikeRides(place) {
    clearBikeRides();
    flowBikeRideLayer = L.layerGroup(bikeRideLayers(place, true)).addTo(map);
}

/** Przejazdy wszystkich kropek naraz (przełącznik pod zębatką) - z kreskami,
    stacjami końcowymi i etykietkami, tak samo jak pod kursorem. Przejazdów
    jest tyle, ile przeszło wybór (suwak pod zębatką), więc nie robi się z tego
    ściana tekstu. */
function showAllBikeRides(places) {
    clearBikeRides();
    const layers = [];
    for (const place of places || []) layers.push(...bikeRideLayers(place, true));
    flowBikeRideLayer = L.layerGroup(layers).addTo(map);
}

function clearBikeRides() {
    if (flowBikeRideLayer) map.removeLayer(flowBikeRideLayer);
    flowBikeRideLayer = null;
}

function bikeRideLayers(place, labels) {
    const layers = [];
    for (const ride of place.rides) {
        layers.push(L.polyline([[place.lat, place.lon], [ride.lat, ride.lon]],
                               BIKE_RIDE_STYLE));
        if (!labels) continue;
        layers.push(L.circleMarker([ride.lat, ride.lon], BIKE_TARGET_STYLE)
            .bindTooltip(bikeRideTooltipHtml(ride),
                         {direction: 'top', offset: [0, -4], opacity: 1}));
        // Etykietka wisi na własnym, niewidzialnym uchwycie przezroczystym
        // dla kursora: leży dokładnie na kropce wyżej, a to ona ma łapać
        // najechanie.
        layers.push(L.circleMarker([ride.lat, ride.lon],
                                   {...BIKE_TARGET_STYLE, opacity: 0,
                                    fillOpacity: 0, interactive: false})
            .bindTooltip(bikeRideLabel(ride),
                         {permanent: true, direction: 'right', offset: [6, 0],
                          className: 'bike-ride-label', opacity: 1}));
    }
    return layers;
}

/** Etykietka przy drugim końcu. Domyślnie SAMA odległość: godzina przejazdu
    jest policzona, nie odczytana - wraca przełącznikiem pod zębatką. */
function bikeRideLabel(ride) {
    const parts = [];
    if (dotOpts.bikeTimes) parts.push(fmtClock(ride.at));
    parts.push(fmtDist(ride.m));
    return parts.join(' · ');
}

function bikeRideTooltipHtml(ride) {
    const rows = [`<b>${esc(ride.name)}</b>`];
    rows.push(dotOpts.bikeTimes
        ? `Stąd ${fmtDist(ride.m)} w linii prostej — ${fmtMins(ride.sec)} ` +
          `z wypożyczeniem i oddaniem, jesteś tu ${fmtClock(ride.at)}`
        : `Stąd ${fmtDist(ride.m)} w linii prostej`);
    rows.push(`${ride.bikes} ` + plural(ride.bikes, 'rower', 'rowery', 'rowerów') +
              ` na miejscu, ${ride.docks} wolnych stojaków`);
    return rows.join('<br>');
}

function legLayers(legs, {preview}) {
    const casings = [], lines = [], marks = [];
    const rideWeight = preview ? 5 : 7;

    for (const leg of legs) {
        // Etapy kolejowe (patrz pkp.py) nie mają geometrii - słownik stacji
        // PKP nie niesie współrzędnych, więc nie ma czego narysować. Karta
        // na liście propozycji i tak pokazuje pełne godziny i nazwy stacji.
        if (!leg.path || leg.path.length < 2) continue;
        if (leg.kind === 'walk') {
            lines.push(L.polyline(leg.path, {
                color: WALK_COLOR, weight: 3, opacity: preview ? 0.7 : 1,
                dashArray: '1,6', lineCap: 'round', interactive: false,
            }));
            continue;
        }
        if (leg.kind === 'bike') {
            // Przerywana, bo to NIE jest przebieg ulicami: backend zna tylko
            // dwie stacje i odcinek między nimi (patrz planner._bike_ride_leg).
            // Ciągła kreska obiecywałaby trasę, której nikt tu nie policzył.
            lines.push(L.polyline(leg.path, {
                color: LINE_COLORS.bike, weight: preview ? 4 : 5,
                opacity: preview ? 0.75 : 1, dashArray: '9,7',
                lineCap: 'round', interactive: false,
            }));
            if (!preview) {
                marks.push(L.marker(leg.path[Math.floor(leg.path.length / 2)], {
                    icon: L.divIcon({
                        className: 'line-badge solid bike',
                        html: BIKE_ICON, iconSize: null,
                    }),
                    interactive: false,
                }));
            }
            continue;
        }
        // Jazda Traficarem (patrz planner._car_drive_leg). Kreskowana i bez
        // białej otoczki, czyli NIE tak, jak rysuje się kursy: to odcinek
        // prosty od auta do celu, nie przebieg ulicami, bo przebiegu nikt tu
        // nie liczy. Linia ciągła obiecywałaby trasę, której nie ma.
        if (leg.kind === 'drive') {
            lines.push(L.polyline(leg.path, {
                color: LINE_COLORS.car, weight: preview ? 4 : 5,
                opacity: preview ? 0.7 : 0.95, dashArray: '10,8',
                lineCap: 'round', interactive: false,
            }));
            if (!preview) {
                marks.push(L.marker(leg.path[Math.floor(leg.path.length / 2)], {
                    icon: L.divIcon({
                        className: 'line-badge solid car',
                        html: esc(leg.num), iconSize: null,
                    }),
                    interactive: false,
                }));
            }
            continue;
        }
        const color = LINE_COLORS[leg.mode] || LINE_COLORS.other;
        casings.push(L.polyline(leg.path, {
            color: '#fff', weight: rideWeight + 5, opacity: preview ? 0.7 : 0.95,
            lineCap: 'round', lineJoin: 'round', interactive: false,
        }));
        lines.push(L.polyline(leg.path, {
            color, weight: rideWeight, opacity: 1,
            lineCap: 'round', lineJoin: 'round', interactive: false,
        }));
        if (!preview) {
            marks.push(L.marker(leg.path[Math.floor(leg.path.length / 2)], {
                icon: L.divIcon({
                    className: `line-badge solid ${leg.mode}`,
                    html: esc(leg.num), iconSize: null,
                }),
                interactive: false,
            }));
        }
    }

    if (!preview) {
        // Kropki na wsiadaniu i wysiadaniu każdego etapu - widać, gdzie się
        // przesiadamy, bez czytania listy. Każda jest do najechania: dymek
        // pokazuje tablicę odjazdów tego przystanku (patrz stopDot).
        const firstRide = legs.find(l => l.kind === 'ride');
        for (const leg of legs) {
            if (leg.kind !== 'ride' || !leg.path || leg.path.length < 2) continue;
            const style = journeyDotStyle();
            // Wsiadanie do PIERWSZEGO przejazdu to z definicji przystanek,
            // z którego się wyrusza - nie ma tu czego rozpoznawać.
            const start = leg === firstRide;
            marks.push(stopDot(leg.path[0], {name: leg.from, start}, leg.dep_sec, style));
            marks.push(stopDot(leg.path[leg.path.length - 1],
                               {name: leg.to}, leg.arr_sec, style));
        }
    }
    return [...casings, ...lines, ...marks];
}

function drawJourney(index, keepView) {
    clearJourney();
    const journey = journeys[index];
    if (!journey) return;
    journeyLayer = L.layerGroup(legLayers(journey.legs, {preview: false})).addTo(map);
    dimFlow(true);
    seedStartPanel();
    // Przy przerysowaniu w miejscu (suwaki wyglądu) nie wyrywamy widoku -
    // kadrujemy tylko wtedy, gdy trasa i tak nie mieści się w kadrze.
    const points = [...journey.legs.flatMap(leg => leg.path || []), ...endpointPoints()];
    if (!keepView || !map.getBounds().contains(L.latLngBounds(points))) fitTo(points);
}

function clearJourney() {
    if (journeyLayer) { map.removeLayer(journeyLayer); journeyLayer = null; }
    // Zdjęta warstwa nie wyśle już mouseout, a wskaźnik na nieistniejącą
    // kropkę blokowałby dymek przepływów na zawsze (patrz handleFlowHover).
    hoveredStopDot = null;
}

// Podgląd pod kursorem. Indeks pamiętamy, bo mouseover leci z każdego
// elementu karty - bez tego trasa migałaby przy ruchu myszą w jej obrębie.
let previewIndex = null;

function previewJourney(index) {
    if (previewIndex === index) return;
    clearPreview();
    const journey = journeys[index];
    if (!journey || selectedJourney === index) return;
    hoverLayer = L.layerGroup(legLayers(journey.legs, {preview: true})).addTo(map);
    previewIndex = index;
}

function clearPreview() {
    if (hoverLayer) { map.removeLayer(hoverLayer); hoverLayer = null; }
    previewIndex = null;
}

// ------------------------------------------------------ lista propozycji ----

function selectJourney(index) {              // klik w kartę na liście
    if (selectedJourney === index) {         // ponowny klik = pokaż znów cały wachlarz
        deselectJourney();
        return;
    }
    openJourney(index);
}

function deselectJourney() {
    clearPreview();
    selectedJourney = null;
    clearJourney();
    dimFlow(false);
    renderJourneys();
    renderVehicles();
}

/** Otwiera propozycję z listy - w przeciwieństwie do kliknięcia w kartę nigdy
    jej nie zamyka. Mapa nigdy tu nie trafia: klik w mapę nie otwiera tras. */
function openJourney(index) {
    clearPreview();
    // Otwarcie trasy przy schowanym panelu byłoby niewidoczne - także wtedy,
    // gdy klikamy w trasę już wybraną.
    document.body.classList.remove('panel-hidden');
    if (selectedJourney === index) { scrollToSelected(); return; }
    selectedJourney = index;
    drawJourney(index);
    renderJourneys();
    renderVehicles();
}

function badgeHtml(leg) {
    return `<span class="badge ${leg.mode}" title="${esc(leg.line)}">${esc(leg.num)}</span>`;
}

// Plakietka etapu rowerowego na mapie. Sam znak 🚲 zamiast numeru linii,
// bo rower numeru nie ma - a plakietka ma mówić „czym", nie „którym".
const BIKE_ICON = '🚲';

/** Znak trasy: nitka z krańcami na końcach - to samo, co przycisk rysuje na
    mapie, tylko w 11 pikselach. Ta sama rodzina co FLOW_ICONS: kreska bierze
    `currentColor`, więc chodzi za kolorem przycisku (przygaszony w spoczynku,
    akcentowy pod kursorem), zamiast mieć własny, który trzeba by osobno
    pamiętać przy każdej zmianie stanu. */
const ROUTE_ICON =
    '<svg class="tt-route-icon" viewBox="0 0 15 12" aria-hidden="true">'
    + '<path d="M2.5 9.5h2.6l4.8-7h2.6"/>'
    + '<circle cx="2.5" cy="9.5" r="1.5"/>'
    + '<circle cx="12.5" cy="2.5" r="1.5"/></svg>';

/** "A którędy ta linia jedzie w ogóle" - pytanie, na które wyszukiwarka nie
    odpowiada wcale: propozycja pokazuje kawałek od wsiadania do wysiadania,
    a tablica pod słupkiem sam moment odjazdu. Przycisk przeskakuje w rozkład
    linii, tak jak "odjazdy" w rozkładzie linii przeskakuje w tablicę słupka.

    Rysowany TYLKO dla linii, które rozkład zna (patrz timetableMode.hasLine):
    pociągi PKP nie są w bazie rozkładów, więc ich wiersze zostają bez
    przycisku zamiast prowadzić w komunikat o nieznanej linii. Kierunek jedzie
    razem z numerem - rozkład ma się otworzyć na tym wariancie, którym jedzie
    ten kurs, a nie na przeciwnym. */
function routeButtonHtml(line, label) {
    const tt = window.timetableMode;
    if (!tt || !tt.hasLine || !tt.hasLine(line.num, line.mode)) return '';
    return `<button type="button" class="tt-route"
                    data-route-num="${esc(line.num)}"
                    data-route-mode="${esc(line.mode)}"
                    data-route-headsign="${esc(line.headsign || '')}"
                    title="Cała trasa: ${esc(MODE_LABEL[line.mode] || 'Linia')} ${esc(line.num)}"
                    >${ROUTE_ICON}${esc(label)}</button>`;
}

/** Klik w "trasę" nigdy nie ma znaczyć tego, co klik w wiersz pod nią -
    ani rozwinięcia propozycji, ani zamknięcia okienka. */
function routeClick(event) {
    const button = event.target.closest('[data-route-num]');
    if (!button) return false;
    event.preventDefault();
    event.stopPropagation();
    window.timetableMode.openLine({
        num: button.dataset.routeNum,
        mode: button.dataset.routeMode,
        headsign: button.dataset.routeHeadsign,
    });
    return true;
}

/** Ludzik idacy - monochromatyczny SVG, nie emoji: reszta ikon w interfejsie
    (◉ ⚙ ⇅ ✕ ◷) tez jest jednobarwna, a kolorowe 🚶 wygladaloby jak wklejka
    z innego programu i renderowaloby sie inaczej na kazdym systemie. */
const WALK_ICON =
    '<svg class="walk-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false">' +
    '<circle cx="9" cy="2.5" r="1.9" fill="currentColor"/>' +
    '<path d="M9.2 5.4 6.4 7.2 5.1 10.4M9.2 5.4 11.2 7.6 11.6 10.8 12.9 13.6' +
    'M9.2 5.4 7.1 9.8 4.4 13.4" fill="none" stroke="currentColor" ' +
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';


function walkBadgeHtml(leg) {
    const dokad = leg.from === leg.to ? ' na inne stanowisko' : ` do: ${leg.to}`;
    return `<span class="badge walk" title="Przejście pieszo${esc(dokad)}` +
           ` · ok. ${leg.minutes} min">${WALK_ICON}${leg.minutes}</span>`;
}

function hopHtml(walk) {
    if (!walk) return '<span class="hop"></span>';
    return `<span class="hop walk" title="Przesiadka z przejściem pieszo` +
           ` · ok. ${walk.minutes} min"></span>`;
}

/** Rzad plakietek pod godzinami karty: obraz trasy, etap po etapie.

    Przejscie MIEDZY pojazdami zostaje kreska lacznika - jest wlasnoscia
    przesiadki ("tu trzeba przejsc"), a nie osobnym przystankiem podrozy.
    Wersja, w ktorej kazde przejscie dostawalo wlasna plakietke, byla wierna,
    ale nieczytelna: odkad przesiadka miedzy roznymi slupkami niemal zawsze
    kosztuje minimalne trzy minuty, trasa z trzema przesiadkami rozrastala sie
    do "3 - 18 - 3 - 14 - 3 - 4 - 3 - 146" i zawijala do dwoch linii.

    Plakietke dostaje przejscie OTWIERAJACE albo ZAMYKAJACE trase - i to jest
    ta luka, ktora tu naprawiamy. Takie przejscie nie jest przesiadka, tylko
    wlasnym etapem do/od sieci, i nie ma sasiedniego lacznika, ktory moglby je
    ponies: karta "dojdz na stacje i wsiadz w pociag" wygladala przez to jak
    sam pociag. Zglaszone na zywo. */
function summaryHtml(legs) {
    const parts = [];
    let pendingWalk = null;
    legs.forEach((leg, i) => {
        if (leg.kind === 'walk') {
            if (i === 0 || i === legs.length - 1) {
                if (parts.length) parts.push(hopHtml(null));
                parts.push(walkBadgeHtml(leg));
            } else {
                pendingWalk = leg;
            }
            return;
        }
        if (parts.length) parts.push(hopHtml(pendingWalk));
        pendingWalk = null;
        parts.push(badgeHtml(leg));
    });
    return parts.join('');
}

/** Gdzie wysiąść z pojazdu, w którym się siedzi - jedyna rzecz, którą pasażer
    startujący z pokładu MUSI zrobić, a o którą zwykła lista tras nigdy nie
    pytała (pole `onboard` propozycji, patrz onboard.mark_journeys).

    Trzy różne zdania, bo to trzy różne sytuacje: wysiadka od razu, wysiadka
    za kilka przystanków z przesiadką i dojazd tym samym pojazdem pod sam cel.
    Zlanie ich w jedno („wysiądź: X, za N przystanków") czytałoby się przy
    N = 0 jak polecenie wyskoczenia w biegu. */
function exitText(exit) {
    const ile = `${exit.stops} ${plural(exit.stops, 'przystanek', 'przystanki', 'przystanków')}`;
    if (!exit.stops) return ['Wysiądź na najbliższym przystanku', exit.stop];
    if (!exit.transfer) return [`Dojedziesz tym pojazdem — wysiadka za ${ile}`, exit.stop];
    return [`Wysiądź za ${ile}`, exit.stop];
}

function exitHtml(exit) {
    if (!exit) return '';
    const [co, gdzie] = exitText(exit);
    return `<div class="j-exit"><span class="j-exit-what">${esc(co)}</span>`
         + `<span class="j-exit-stop">${esc(prettyStopName(gdzie))}</span>`
         + `<span class="j-exit-time">${esc(exit.time)}</span></div>`;
}

function plural(n, one, few, many) {
    if (n === 1) return one;
    const rest = n % 10, hundreds = n % 100;
    return rest >= 2 && rest <= 4 && (hundreds < 12 || hundreds > 14) ? few : many;
}

function detailHtml(journey) {
    const rows = [];
    const stopRow = (time, name, cls) =>
        `<li class="tl-stop ${cls}"><span class="tl-time">${esc(time)}</span>` +
        `<span class="tl-dot"></span><span class="tl-name">${esc(name)}</span></li>`;

    journey.legs.forEach((leg, i) => {
        if (leg.kind === 'walk') {
            // Trasa OTWARTA dojsciem (patrz planner._origin_walk): os musi
            // zaczac sie od tego, SKAD sie wychodzi i o ktorej - inaczej
            // pierwszy wiersz mowi "przejdz do X", nie mowiac skad ani kiedy,
            // a godzina z naglowka karty nie ma w osi odpowiednika.
            if (i === 0) rows.push(stopRow(journey.departure, leg.from, 'first'));
            // `note` to gotowy opis z backendu - przychodzi tylko z etapów
            // dostawionych przez warstwę rowerową (planner._foot_leg), bo
            // „Dojście do stacji WRM ..." nie da się złożyć z from/to: stacja
            // roweru to nie przystanek.
            //
            // `to_car` to dojście do auta Traficar, nie na inny słupek
            // (patrz planner._car_walk_leg): tam nie ma przystanku, tylko
            // ulica, przy której stoi konkretne auto - i to trzeba napisać.
            // Bez adresu z feedu (`to` puste) zostaje samo "dojście do auta" -
            // KTÓRE to auto mówi i tak następny wiersz.
            //
            // Bez obu rozstrzygają NAZWY, nie miejsce: miejsce potrafi zbierać
            // słupki nazwane różnie (stacja PKP i przystanek MPK przy niej -
            // patrz naming.py), a wtedy "inne stanowisko" nie mówi
            // wysiadającemu z pociągu, dokąd ma iść. Ta sama zasada co
            // w planner._walk_leg - zdanie w osi i zdanie z serwera nie mogą
            // rozstrzygać tego inaczej.
            const dokad = leg.note
                ? esc(leg.note)
                : leg.to_car
                    ? (leg.to ? `Dojście do auta · ${esc(leg.to)}` : 'Dojście do auta')
                    : leg.from === leg.to
                        ? 'Przejście na inne stanowisko'
                        : `Przejście do ${esc(leg.to)}`;
            const ile = leg.to_car && leg.metres ? ` (${leg.metres} m)` : '';
            rows.push(
                `<li class="tl-walk"><span class="tl-time"></span><span class="tl-dot"></span>` +
                `<span class="tl-body">${dokad}${ile} · ok. ${leg.minutes} min</span></li>`,
            );
            // Trasa ZAMKNIETA dojsciem (patrz planner._target_reach): wiersz
            // z przyjazdem do celu nie ma juz skad wyjsc, bo emituje go
            // przejazd, a po tym dojsciu zadnego przejazdu nie ma. Bez tego
            // os konczy sie na "przejdz do X", nie mowiac o ktorej sie tam
            // jest - a to jest godzina z naglowka karty.
            if (i === journey.legs.length - 1) {
                rows.push(stopRow(journey.arrival, leg.to, 'last'));
            }
            return;
        }
        if (leg.kind === 'bike') {
            rows.push(stopRow(leg.from_time, leg.from, i === 0 ? 'first' : ''));
            rows.push(
                `<li class="tl-ride bike"><span class="tl-time"></span>` +
                `<span class="tl-dot"></span><span class="tl-body">` +
                `${badgeHtml(leg)} <span class="tl-headsign">do stacji ` +
                `${esc(leg.to)}</span>` +
                // Rozbicie czasu na trzy części jest tu sednem, nie ozdobą:
                // z 14 minut cztery to stanie przy stojaku (patrz
                // planner._bike_ride_leg), a kto tego nie wie, ten planuje
                // przesiadkę, której nie zdąży.
                // Przecinek, nie kropka - reszta interfejsu jest po polsku.
                `<span class="tl-info">${(leg.distance_m / 1000).toFixed(1).replace('.', ',')} km · ` +
                `${leg.minutes} min (jazda ${leg.ride_minutes} min, ` +
                `wypożyczenie i zwrot ${leg.overhead_minutes} min)<br>` +
                `${esc(leg.from)}: ${leg.bikes_available} ` +
                `${plural(leg.bikes_available, 'rower', 'rowery', 'rowerów')} · ` +
                `${esc(leg.to)}: ${leg.docks_available} ` +
                `${plural(leg.docks_available, 'wolne miejsce', 'wolne miejsca', 'wolnych miejsc')}` +
                `</span></span></li>`,
            );
            const after = journey.legs[i + 1];
            if (!after || after.kind === 'walk') {
                rows.push(stopRow(leg.to_time, leg.to, after ? '' : 'last'));
            }
            return;
        }
        // Ostatni etap propozycji z Traficarem. Wszystkie liczby idą z "ok.":
        // auto nie ma rozkładu, więc czas jazdy jest policzony z odległości,
        // a nie odczytany (patrz traficar.drive_time). Osobny wiersz na sam
        // odbiór auta, bo te pięć minut to nie jazda i nie dojście - a mija.
        if (leg.kind === 'drive') {
            rows.push(
                `<li class="tl-walk"><span class="tl-time"></span><span class="tl-dot"></span>` +
                `<span class="tl-body">Odbiór auta: rezerwacja i start · ` +
                `ok. ${leg.start_min} min</span></li>`,
            );
            rows.push(stopRow(leg.from_time, leg.from, ''));
            rows.push(
                `<li class="tl-ride car"><span class="tl-time"></span>` +
                `<span class="tl-dot"></span><span class="tl-body">` +
                `${badgeHtml(leg)} <span class="tl-headsign">${CAR_ICON}` +
                `${esc(leg.model)} · ${esc(leg.plate)}</span>` +
                `<span class="tl-info">ok. ${leg.minutes} min · ok. ${leg.km} km · ` +
                `paliwo ${leg.fuel}%, zasięg ${leg.range} km</span>` +
                // Skąd te liczby - powiedziane wprost, a nie zostawione do
                // domyślenia się z samego "ok.". Każda inna godzina w tej
                // aplikacji jest odczytana z rozkładu; ta jedna nie ma skąd.
                `<span class="tl-info est">Czas i dystans szacowane z odległości` +
                ` — auto nie ma rozkładu</span></span></li>`,
            );
            rows.push(stopRow(leg.to_time, leg.to, 'last'));
            return;
        }
        const stopWord = leg.mode === 'train'
            ? plural(leg.stops_count, 'stacja', 'stacje', 'stacji')
            : plural(leg.stops_count, 'przystanek', 'przystanki', 'przystanków');
        rows.push(stopRow(leg.from_time, leg.from, i === 0 ? 'first' : ''));
        rows.push(
            `<li class="tl-ride ${esc(leg.mode)}"><span class="tl-time"></span>` +
            `<span class="tl-dot"></span><span class="tl-body">` +
            // Przycisk PRZED liczbą przystanków, choć czyta się go po niej:
            // .tl-info zajmuje całą szerokość (flex-basis: 100%), więc wszystko
            // za nim spada do trzeciej linijki - a to jest akcja tego wiersza,
            // nie osobny wiersz.
            `${badgeHtml(leg)} <span class="tl-headsign">${esc(leg.headsign)}</span>` +
            // Etap, w którym się już siedzi (patrz onboard.mark_journeys),
            // nie jest wsiadaniem - i oś ma to powiedzieć wprost, bo
            // pierwszy wiersz wygląda identycznie jak każde inne wsiadanie.
            (leg.onboard ? '<span class="tl-tag">jedziesz tym pojazdem</span>' : '') +
            routeButtonHtml(leg, 'trasa') +
            `<span class="tl-info">${leg.stops_count} ${stopWord} · ` +
            `${leg.minutes} min</span></span></li>`,
        );
        // Wysiadanie wypisujemy tylko wtedy, gdy nie zaraz po nim następuje
        // wsiadanie do kolejnej linii - inaczej ten sam przystanek byłby
        // w osi dwa razy pod rząd.
        const next = journey.legs[i + 1];
        if (!next || next.kind === 'walk') {
            rows.push(stopRow(leg.to_time, leg.to,
                              `${next ? '' : 'last'}${leg.onboard ? ' exit' : ''}`));
        } else if (leg.onboard) {
            // Przesiadka z naszego pojazdu w inny NA TYM SAMYM słupku: wiersz
            // wysiadania normalnie się nie pojawia (byłby ten sam przystanek
            // dwa razy pod rząd), a akurat tu jest najważniejszym wierszem
            // całej osi - to jest ten moment, w którym trzeba wstać.
            rows.push(stopRow(leg.to_time, leg.to, 'exit'));
        }
    });

    return `<ol class="timeline">${rows.join('')}</ol>
        <p class="j-collapse">
            Kliknij ponownie — albo w mapę obok trasy — żeby wrócić do
            wszystkich wariantów.
        </p>`;
}

/** Dlaczego na liście nie ma roweru, choć warstwa 🚲 jest włączona.

    Bez tego zera nie da się od siebie odróżnić: „policzone, rowerem nie
    dojedziesz tu w oknie mapy", „kanał operatora milczy" i „pytasz o inny
    dzień, a stan stojaków jest żywy" wyglądają identycznie - czyli jak
    zepsuta funkcja. Backend rozróżnia te trzy przypadki (patrz pole `bikes`
    w odpowiedzi /api/flow), więc wystarczy je wypisać. */
function bikeNoteHtml() {
    const info = lastFlow && lastFlow.bikes;
    if (!info || info.journeys > 0) return '';
    const text = !info.live
        ? 'Rower liczymy tylko dla dzisiejszych wyjazdów — stan stacji jest '
          + 'z tej chwili, nie z rozkładu.'
        : info.stations === 0
            ? 'Nie udało się pobrać stanu stacji WRM. Reszta wyników jest '
              + 'kompletna.'
            : 'Żadna trasa ze stacją WRM nie mieści się w oknie czasowym '
              + 'mapy — tutaj rower nic nie daje.';
    // Kartka (.notice), a nie szara linijka jak .results-foot: ten tekst leży
    // nad mapą, gdzie sam cień pod literami czyta się ledwo - a to jedyne
    // miejsce, w którym pada odpowiedź na „czemu nic nie widzę".
    return `<div class="notice bike-note"><p>🚲 ${esc(text)}</p></div>`;
}

/** Nagłówek listy przy starcie z pokładu: czym się jedzie i co jest najbliżej.

    Bez tego lista wygląda jak każda inna - same godziny i linie - a to jest
    jedyne miejsce, które potwierdza, że serwer rozpoznał TEN kurs, o który
    chodziło. Pomyłka w kierunku albo przystanku daje przecież kompletne,
    sensownie wyglądające wyniki, tylko dla kogoś innego. */
function onboardNoteHtml() {
    const kurs = lastFlow && lastFlow.onboard;
    if (!kurs) return '';
    return `<p class="onboard-head">`
        + `<span class="badge ${esc(kurs.mode)}">${esc(kurs.num)}</span>`
        + `<span>w stronę <b>${esc(kurs.headsign)}</b> — najbliższy przystanek `
        + `<b>${esc(prettyStopName(kurs.stop_name))}</b> o ${esc(kurs.at)}</span></p>`;
}

function renderJourneys() {
    if (!journeys.length) return;
    const cards = journeys.map((j, i) => {
        const selected = i === selectedJourney;
        const transfers = j.transfers === 0
            ? 'bez przesiadek'
            : `${j.transfers} ${plural(j.transfers, 'przesiadka', 'przesiadki', 'przesiadek')}`;
        const meta = [
            transfers,
            j.wait_min > 0 ? `odjazd za ${j.wait_min} min` : 'odjazd teraz',
        ];
        // Propozycja z autem kończy się czymś, czego nie ma w rozkładzie -
        // i czym się płaci za przejazd. To ma być widać na karcie, zanim się
        // ją rozwinie, a nie dopiero na osi trasy.
        if (j.traficar) meta.push('ostatni odcinek autem');
        // Etykieta dla czytnika ekranu opowiada te sama trase, co plakietki -
        // razem z przejsciami, bo to one decyduja, czy trasa jest wykonalna.
        const lines = j.legs.filter(
            leg => leg.kind === 'walk' || leg.kind === 'ride'
                || leg.kind === 'bike' || leg.kind === 'drive')
            .map(leg => leg.kind === 'walk'
                ? `pieszo ${leg.minutes} min`
                : leg.line).join(', ');
        // Podróż kończąca się autem ma szacowany ostatni etap, więc i jej
        // łączny czas jest szacunkiem - "ok." stoi przy tej liczbie, którą
        // czyta się pierwszą, a nie dopiero w rozwinięciu karty.
        const czas = j.traficar ? `ok. ${j.duration_min} min` : `${j.duration_min} min`;
        // Wysiadka idzie NA POCZĄTEK etykiety: to pierwsza rzecz do zrobienia
        // i pierwsza, którą ma usłyszeć czytnik ekranu.
        const wysiadka = j.onboard ? exitText(j.onboard).join(': ') + ', ' : '';
        const label = `${wysiadka}${j.departure} – ${j.arrival}, ${czas}, ` +
                      `${transfers}, ${lines}`;
        return `
            <li class="journey${selected ? ' selected' : ''}" data-index="${i}"
                tabindex="0" role="button" aria-expanded="${selected}"
                aria-label="${esc(label)}">
                <div class="j-head">
                    <span class="j-clock">${esc(j.departure)} – ${esc(j.arrival)}</span>
                    <span class="j-duration${j.traficar ? ' est' : ''}"${
                        j.traficar ? ' title="Ostatni odcinek autem - czas jazdy'
                                   + ' szacowany, auto nie ma rozkładu"' : ''
                    }>${esc(czas)}</span>
                </div>
                ${exitHtml(j.onboard)}
                <div class="j-lines">${summaryHtml(j.legs)}</div>
                <div class="j-meta">${meta.join(' · ')}</div>
                ${selected ? detailHtml(j) : ''}
            </li>`;
    }).join('');

    resultsBox.innerHTML = `
        <div class="results-head">
            <h2>Propozycje tras</h2>
            <span class="results-count">${journeys.length}</span>
            <button id="results-toggle" class="icon-button"
                    title="${resultsCollapsed ? 'Pokaż' : 'Ukryj'} propozycje tras"
                    aria-label="${resultsCollapsed ? 'Pokaż' : 'Ukryj'} propozycje tras"
                    aria-expanded="${String(!resultsCollapsed)}">${resultsCollapsed ? '▸' : '▾'}</button>
        </div>
        <div class="results-body">
            ${onboardNoteHtml()}
            <ol class="journeys">${cards}</ol>
            ${bikeNoteHtml()}
            <p class="results-foot">
                Na mapie widać wszystkie sensowne dojazdy — im jaśniejsza linia,
                tym lepsza opcja. Kliknij propozycję albo linię na mapie, żeby
                zobaczyć całą trasę.
            </p>
        </div>`;
    resultsBox.classList.toggle('collapsed', resultsCollapsed);

    setTabCount(journeys.length);
    scrollToSelected();
}

function setTabCount(count) {
    if (!tabCount) return;
    tabCount.textContent = count;
    tabCount.hidden = !count;
}

function scrollToSelected() {
    const card = resultsBox.querySelector('.journey.selected');
    if (card) card.scrollIntoView({block: 'start', behavior: 'smooth'});
}

resultsBox.addEventListener('click', event => {
    if (routeClick(event)) return;

    if (event.target.closest('#results-toggle')) {
        resultsCollapsed = !resultsCollapsed;
        saveUiState({resultsCollapsed});
        renderJourneys();
        return;
    }

    const card = event.target.closest('.journey');
    if (card) { selectJourney(Number(card.dataset.index)); return; }

    // Kliknięcie podpowiedzi ("czy chodziło o…") wstawia nazwę w pierwsze
    // niepasujące pole.
    const name = event.target.dataset && event.target.dataset.name;
    if (!name) return;
    event.preventDefault();
    const known = new Set([...markersByName.keys()].map(n => n.toLowerCase()));
    if (!isPoint(sel.start)
            && !known.has(rawStopName(startInput.value.trim()).toLowerCase())) {
        startInput.value = name;
        sel.start = null;
        updatePointMarker('start', null);
    } else {
        endInput.value = name;
        sel.end = null;
        updatePointMarker('end', null);
    }
    if (startInput.value && endInput.value) search();
});

resultsBox.addEventListener('keydown', event => {
    if (event.target.closest('[data-route-num]')) return;
    const card = event.target.closest('.journey');
    if (card && (event.key === 'Enter' || event.key === ' ')) {
        event.preventDefault();
        selectJourney(Number(card.dataset.index));
    }
});

resultsBox.addEventListener('mouseover', event => {
    const card = event.target.closest('.journey');
    if (card) previewJourney(Number(card.dataset.index));
    else clearPreview();
});
resultsBox.addEventListener('mouseleave', clearPreview);

// ------------------------------------------------- start z pokładu pojazdu ----

// "Skąd" zakłada, że pasażer gdzieś STOI. Siedzący w autobusie nie stoi
// nigdzie - jedzie - i jego pytanie brzmi inaczej: nie "czym dojechać", tylko
// "gdzie wysiąść". Przełącznik nad polami zamienia więc jedno pole na trzy:
// linia, kierunek z czoła pojazdu i przystanek, który ma się przed sobą. Z tych
// trzech serwer rozpoznaje konkretny kurs rozkładu (patrz onboard.py) i od
// niego liczy całą resztę - wysiadkę, przesiadki, rower i pieszo tak samo jak
// zawsze.
//
// Kierunek i przystanek to LISTY, nie pola tekstowe: ich treść jest skończona
// i znana (rozkład tej linii), a wpisywanie nazwy przystanku w trzęsącym się
// autobusie to proszenie się o literówkę.
const obFields = $('onboard-fields');
const obLineInput = $('ob-line');
const obDirSelect = $('ob-dir');
const obStopSelect = $('ob-stop');
const obMsg = $('ob-msg');
const obSummary = $('ob-summary');
const modePlaceButton = $('mode-place');
const modeVehicleButton = $('mode-vehicle');

// Ta sama lista linii, co w trybie rozkładów (#line-names, patrz
// timetables.all_lines) - jedzie w stronie, więc pole "linia" podpowiada bez
// ani jednego zapytania.
const LINE_ENTRIES = obLineInput ? JSON.parse($('line-names').textContent) : [];
const LINE_NUMBERS = LINE_ENTRIES.map(line => line.num);
const LINE_MODE_OF = new Map(LINE_ENTRIES.map(line => [line.num, line.mode]));
const OB_LINE_LIMIT = 8;

let onboardOn = false;
let obDirections = [];     // kierunki wczytanej linii (patrz /api/onboard)
let obToken = 0;           // odsiewa odpowiedzi na nieaktualną już linię
// Czy komplet ma być zwinięty do jednej linijki. Chęć, nie stan: zwija się
// tylko wtedy, gdy wybór jest KOMPLETNY (patrz syncOnboardView), więc zmiana
// linii sama z siebie pokazuje pola z powrotem.
let obCollapsed = false;
let obLineAuto = null;     // uchwyt podpowiedzi linii (patrz attachAutocomplete)

/** Co pojedzie do serwera jako `onboard_*` - albo null, gdy wybór jest jeszcze
    niekompletny. Kierunek jedzie razem z numerem i przystankiem, bo słupek
    rozstrzyga kierunek tylko wtedy, gdy linia mija go raz; na pętli i na
    trasie zawrotnej mija go w obie strony. */
function onboardPick() {
    const num = obLineInput.value.trim();
    const kierunek = obDirection();
    const stop = obStopSelect.value;
    if (!num || !kierunek || !stop) return null;
    return {num, mode: LINE_MODE_OF.get(num) || '', headsign: kierunek.headsign, stop};
}

const onboardReady = () => onboardOn && !!onboardPick();

/** Wybrany kierunek albo null. Pustej wartości pola NIE wolno tu przepuścić
    przez Number(): Number('') to zero, czyli PIERWSZY kierunek z listy -
    a wtedy „nie wybrałem jeszcze kierunku" znaczyłoby „jadę tam, gdzie
    akurat jeździ najwięcej kursów". */
const obDirection = () =>
    obDirSelect.value === '' ? null : obDirections[Number(obDirSelect.value)] || null;

/** Zwinięty komplet albo trzy pola - jedno z dwóch, nigdy oba naraz.

    Zwijamy WYŁĄCZNIE kompletny wybór: niepełny trzeba dokończyć, więc pola
    muszą być widoczne, choćby ktoś wcześniej zwinął poprzedni. Dzięki temu
    nie ma stanu "zwinięte, ale nie wiadomo co" - zmiana linii, wyczyszczenie
    pola albo pusty kierunek rozwijają kartę same. */
function syncOnboardView() {
    const kurs = onboardPick();
    const zwiniete = obCollapsed && !!kurs;
    obFields.hidden = !onboardOn || zwiniete;
    obSummary.hidden = !onboardOn || !zwiniete;
    obSummary.setAttribute('aria-expanded', String(!zwiniete));
    // Zwinięcie zabiera kursor z pola linii i zamyka jego podpowiedzi - razem
    // z kursorem znika klawiatura telefonu, a lista schowana OTWARTA wróciłaby
    // taka po rozwinięciu i zasłoniła kierunek z przystankiem.
    if (zwiniete) { obLineInput.blur(); if (obLineAuto) obLineAuto.close(); }
    if (!kurs) return;
    const kierunek = obDirection();
    const przystanek = prettyStopName(
        obStopSelect.options[obStopSelect.selectedIndex].text);
    obSummary.innerHTML =
        `<span class="badge ${esc(kurs.mode || 'other')}">${esc(kurs.num)}</span>`
        // Przystanek PRZED kierunkiem, choć wybiera się go później: z tej
        // linijki ucina się koniec, a stracić wolno kierunek (kontekst),
        // nie przystanek, przy którym pojazd zaraz stanie. Znaczek słupka
        // mówi, że to przystanek, tak samo jak na liście podpowiedzi - bez
        // niego zwinięta linijka to trzy nazwy bez podpisów.
        + `<span class="ac-pin" aria-hidden="true"></span>`
        + `<span class="ob-summary-text"><b>${esc(przystanek)}</b>`
        + ` · w stronę ${esc(kierunek.headsign)}</span>`
        + `<span class="ob-summary-edit" aria-hidden="true">zmień</span>`;
    obSummary.setAttribute('aria-label',
        `Jadę linią ${kurs.num}, najbliższy przystanek ${przystanek}, `
        + `w stronę ${kierunek.headsign} — zmień`);
}

function obNote(text) {
    obMsg.textContent = text || '';
    obMsg.hidden = !text;
}

/** Przełącznik "Stoję tutaj" / "Jestem w pojeździe". Zmienia PYTANIE, więc
    zabiera poprzednią odpowiedź na nie: start z drugiego trybu przestaje
    obowiązywać (nie da się naraz stać na przystanku i jechać autobusem).
    Cel zostaje - ten się nie zmienia od tego, skąd się wyrusza. */
function setStartMode(vehicle, focus = true) {
    onboardOn = vehicle;
    document.body.classList.toggle('start-onboard', vehicle);
    syncOnboardView();
    for (const [button, on] of [[modeVehicleButton, vehicle], [modePlaceButton, !vehicle]]) {
        button.classList.toggle('active', on);
        button.setAttribute('aria-pressed', String(on));
    }
    const previous = sel.start;
    sel.start = null;
    startInput.value = '';
    updatePointMarker('start', null);
    restyle(previous);
    showLocateMsg('');
    saveUiState({startOnboard: vehicle});
    // Kursor w polu linii tylko wtedy, gdy ktoś sam kliknął przełącznik.
    // Przy wracaniu do zapamiętanego trybu (odświeżenie strony) klawiatura
    // telefonu wyskakiwałaby nad mapę, o nic nie zapytana.
    if (vehicle && focus) obLineInput.focus();
}

/** Kierunki wpisanej linii (/api/onboard). Data z formularza jedzie razem
    z numerem: rozkład linii zależy od dnia, a pytanie "jestem w pojeździe"
    zwykle dotyczy dziś, ale nie musi - pole daty stoi tuż obok. */
function loadDirections() {
    const num = obLineInput.value.trim();
    obDirections = [];
    fillDirections();
    if (!num) { obNote(''); return; }

    const mine = ++obToken;
    obNote('Szukam kierunków…');
    fetch('/api/onboard?' + new URLSearchParams({
        num, mode: LINE_MODE_OF.get(num) || '', date: $('date').value,
    }))
        .then(r => r.json())
        .then(data => {
            if (mine !== obToken) return;
            if (data.error) { obNote(data.error); return; }
            obDirections = data.directions || [];
            fillDirections();
            obNote(obDirections.length ? '' : (data.note || 'Ta linia dziś nie kursuje.'));
        })
        .catch(() => { if (mine === obToken) obNote('Nie udało się pobrać kierunków.'); });
}

/** Lista kierunków, a po niej lista przystanków - w tej kolejności, bo
    przystanki są własnością kierunku. Jeden kierunek (linia okrężna, kurs
    jednokierunkowy) wybiera się sam: nie ma z czego wybierać. */
function fillDirections() {
    obDirSelect.innerHTML = '<option value="">Kierunek</option>'
        + obDirections.map((kierunek, i) =>
            `<option value="${i}">${esc(kierunek.headsign)}</option>`).join('');
    obDirSelect.disabled = !obDirections.length;
    if (obDirections.length === 1) obDirSelect.value = '0';
    fillStops();
}

function fillStops() {
    const kierunek = obDirection();
    const stops = kierunek ? kierunek.stops : [];
    obStopSelect.innerHTML = '<option value="">Przystanek</option>'
        + stops.map(stop =>
            `<option value="${esc(stop.id)}">${esc(prettyStopName(stop.name))}</option>`).join('');
    obStopSelect.disabled = !stops.length;
    syncOnboardView();
}

if (obLineInput) {
    modePlaceButton.addEventListener('click', () => setStartMode(false));
    modeVehicleButton.addEventListener('click', () => setStartMode(true));

    // Podpowiedzi numerów linii - plakietka w kolorze pojazdu, tak samo jak
    // w trybie rozkładów (patrz timetable.js): numer JEST plakietką, więc
    // trafienia w nim nie podświetlamy.
    obLineAuto = attachAutocomplete(obLineInput, () => loadDirections(), {
        suggest: query => suggestionsFor(query, LINE_NUMBERS, null, OB_LINE_LIMIT)
            .map(item => ({...item, mode: LINE_MODE_OF.get(item.name)})),
        render: item =>
            `<span class="badge ${esc(item.mode)}">${esc(item.name)}</span>`
            + `<span class="ac-kind">${esc(MODE_LABEL[item.mode] || 'Linia')}</span>`,
        onEnter: () => loadDirections(),
    });
    // Wpisanie z ręki (bez wybrania podpowiedzi) też ma działać - linia to
    // dwa, trzy znaki, więc lista bywa szybsza do minięcia niż do trafienia.
    obLineInput.addEventListener('change', loadDirections);

    obDirSelect.addEventListener('change', () => { fillStops(); obNote(''); });
    // Wybór przystanku domyka pytanie - jeśli cel już jest, nie ma na co
    // czekać. Ta sama zasada, co przy drugim kliknięciu w mapę. Domyka też
    // samą kartę: komplet mówi jedno zdanie, a zajmuje trzy rzędy panelu.
    obStopSelect.addEventListener('change', () => {
        obCollapsed = true;
        syncOnboardView();
        if (onboardReady() && endInput.value) search();
    });

    // Rozwinięcie bez ustawiania kursora: klikający „zmień" najczęściej
    // poprawia PRZYSTANEK, a kursor w polu linii otwiera nad listami
    // podpowiedzi i zasłania dokładnie to, po co się tu przyszło.
    obSummary.addEventListener('click', () => {
        obCollapsed = false;
        syncOnboardView();
    });

    // Tryb przeżywa odświeżenie strony (patrz saveUiState) - ale sam wybór
    // pojazdu już nie: kurs sprzed odświeżenia zdążył odjechać.
    if (uiState.startOnboard) setStartMode(true, false);
}

// ------------------------------------------------------------ wyszukiwanie ----

function resetResults() {
    // Nowy token porzuca zapytanie w locie: po ✕ nie ma dorysować się wynik
    // relacji, której już nie ma na ekranie (a kółko musi zgasnąć od razu).
    ++requestToken;
    setSearching(false);
    journeys = [];
    selectedJourney = null;
    clearJourney();
    clearPreview();
    clearFlow();
    renderVehicles();
    resultsBox.innerHTML = '';
    setTabCount(0);
}

function showError(message, suggestions) {
    let html = `<div class="notice error"><p>${esc(message)}</p>`;
    if (suggestions && suggestions.length) {
        // I napis, i data-name to ETYKIETA (patrz prettyStopName) - klik
        // wstawia ją wprost do pola, a rawStopName w queryParams zamienia ją
        // z powrotem przy wysyłaniu.
        html += '<p>Czy chodziło o:</p><ul>' + suggestions.map(prettyStopName).map(name =>
            `<li><a href="#" data-name="${esc(name)}">${esc(name)}</a></li>`
        ).join('') + '</ul>';
    }
    resultsBox.innerHTML = html + '</div>';
}

// Tempo marszu to trzy stałe tempa (patrz gtfs.WALK_PACES), więc suwak ma
// trzy pozycje, a obok stoi nazwa, nie numer pozycji.
const WALK_PACES = ['wolno', 'zwykle', 'szybko'];
const WALK_PACE_LABELS = ['wolne', 'zwykłe', 'szybkie'];

function queryParams() {
    const params = new URLSearchParams({
        time: $('time').value,
        date: $('date').value,
        density: $('density').value,
        cars: $('car-count').value,
        bike_count: $('bike-count').value,
        car_groups: $('car-groups').checked ? '1' : '0',
        car_vans: $('car-vans').checked ? '1' : '0',
        bike_electric: bikeElectricOn ? '1' : '0',
        bike_regular: bikeRegularOn ? '1' : '0',
        latest_start: $('latest-start').checked ? '1' : '0',
        transfer_gain_sec: (Number($('transfer-gain').value) * 60).toFixed(0),
        walk_pace: WALK_PACES[$('walk-pace').value],
        bike_kmh: $('bike-kmh').value,
        bike_overhead_sec: (Number($('bike-overhead').value) * 60).toFixed(0),
    });
    // "Pokaż więcej" nad mapą - tylko gdy user je kliknął; bez tego próg
    // wynika z samej gęstości z suwaka.
    if (mapMore) params.set('more', mapMore);
    // Rower dokładamy do zapytania tylko wtedy, gdy pasażer o niego prosi -
    // patrz routes.api_flow. Bez tego odpowiedź jest co do bajtu taka sama
    // jak przed dodaniem warstwy rowerowej.
    if (bikesOn) params.set('bikes', '1');
    const poklad = onboardOn ? onboardPick() : null;
    if (poklad) {
        // Start z pokładu pojazdu (patrz onboard.py): zamiast miejsca jedzie
        // linia, kierunek i słupek, przy którym pojazd zaraz stanie. Serwer
        // rozpoznaje z tego kurs i dopiero on mówi, gdzie i kiedy zaczyna się
        // podróż - dlatego nie ma tu ani `start`, ani godziny "na oko".
        params.set('onboard_num', poklad.num);
        if (poklad.mode) params.set('onboard_mode', poklad.mode);
        if (poklad.headsign) params.set('onboard_headsign', poklad.headsign);
        params.set('onboard_stop', poklad.stop);
    } else if (isPoint(sel.start)) {
        params.set('start_lat', sel.start.lat);
        params.set('start_lon', sel.start.lon);
    } else {
        // rawStopName, nie surowa wartość pola: w polu stoi ETYKIETA
        // (patrz prettyStopName), a wyszukiwarka zna grupę stacji tylko
        // pod jej kanoniczną postacią z myślnikiem.
        params.set('start', rawStopName(startInput.value));
    }
    if (isPoint(sel.end)) {
        params.set('end_lat', sel.end.lat);
        params.set('end_lon', sel.end.lon);
    } else {
        params.set('end', rawStopName(endInput.value));
    }
    return params;
}

/** Kanoniczne nazwy z API: podświetlenie startu/celu działa też przy ręcznym
    wpisaniu, nie tylko przy klikaniu w mapę - ale klikniętego punktu nie
    nadpisujemy nazwą z odpowiedzi. */
function adoptNames(data) {
    const previous = [sel.start, sel.end];
    // Z pokładu "skąd" nie ma pola na ekranie, ale przystanek, przy którym
    // pojazd zaraz stanie, ma się podświetlić na mapie jak każdy inny start.
    if (onboardOn) sel.start = data.start;
    else if (!isPoint(sel.start)) { sel.start = data.start; startInput.value = displayValue(data.start); }
    if (!isPoint(sel.end)) { sel.end = data.end; endInput.value = displayValue(data.end); }
    // Poprzednie końce muszą wrócić do zwykłego stylu. Przemalowanie tylko
    // nowych wystarczało przy PIERWSZYM wyszukiwaniu, bo setBaseDim(true)
    // przechodził wtedy przez wszystkie słupki - przy kolejnych mapa jest
    // już przygaszona, setBaseDim wychodzi od razu i stare podświetlenie
    // zostawało na mapie na zawsze.
    restyle(...previous, sel.start, sel.end);
}

/** Jedno zapytanie do /api/flow niesie teraz i mapę (segments), i listę
    propozycji (journeys) - to ta sama, współdzielona odpowiedź, więc obie
    nie mogą już się rozjechać (patrz planner.plan_flow). */
/** Ostrzeżenie o TRYBIE AWARYJNYM mapy - ten sam wygląd, co pozostałe
    komunikaty błędów, ale dopisywane NAD listą, nie zamiast niej: w tym
    trybie jakaś trasa i tak jest pokazana i ma zostać widoczna.

    Kiedy się pojawia: serwer nie zdołał złożyć wachlarza opcji i przysłał
    samą najszybszą trasę (pole `degraded` w odpowiedzi /api/flow, patrz
    plan_flow). Bez tego komunikatu rzadka mapa wygląda dokładnie tak samo
    jak "tędy naprawdę nic nie jedzie" i nie da się tych dwóch rzeczy
    odróżnić na ekranie.

    Mówi też, CO z tym zrobić. Najczęstsza przyczyna to nie awaria, tylko za
    wąskie okno na rzadkim kierunku: Bielany Wrocławskie - PKP -> Wojszyce
    o 13:29 mieści w oknie z suwaków (12 min naddatku) dokładnie jeden kurs,
    bo następny jedzie pół godziny później - wachlarz nie ma z czego powstać.
    Poszerzenie zakresu jest wtedy jedynym wyjściem i to ono ma stać
    w komunikacie, a nie sam fakt porażki. */
function showDegradedNotice() {
    resultsBox.insertAdjacentHTML('afterbegin',
        '<div class="notice error degraded"><p>Tryb awaryjny: w tym oknie '
        + 'czasowym nie ułożył się wachlarz połączeń — mapa pokazuje samą '
        + 'najszybszą trasę. Zakres poszerzysz przyciskiem „Pokaż więcej” nad mapą.'
        + '</p></div>');
}

/** Mapa narysowana, lista obok pusta. To NIE jest błąd i nie ma go udawać:
    czerwona ramka nad kompletem połączeń mówiła "coś się zepsuło", a mapa
    pod nią była w porządku. Do 2026-09-04 kazała w dodatku zawęzić okno
    czasowe - czyli odwrócić dokładnie to, co użytkownik przed chwilą zrobił
    przyciskiem "+X min". Zostaje sam fakt, neutralnym stylem, bez polecenia
    (tak samo jak showRailOnlyNotice niżej: wyjaśnienie, nie awaria). */
function showWideWindowNotice() {
    resultsBox.innerHTML = '<div class="notice"><p>Mapa pokazuje wszystkie '
        + 'połączenia z tego okna czasowego. Przy tak szerokim oknie nie '
        + 'ułożyła się z nich lista tras obok.</p></div>';
}

/** Informacja przy relacji poza obszarem MPK Wrocławia (pole `rail_only`
    w odpowiedzi /api/flow, patrz routes.py) - lista pokazuje same
    bezpośrednie połączenia kolejowe, bez mapy przepływów (nie ma jej z
    czego złożyć: MPK w ogóle nie zna jednego z dwóch miejsc). To nie błąd
    (styl neutralny, nie czerwony jak showDegradedNotice), tylko wyjaśnienie,
    czemu mapa jest pusta, mimo że lista poniżej ma wyniki. */
function showRailOnlyNotice() {
    resultsBox.insertAdjacentHTML('afterbegin',
        '<div class="notice"><p>Relacja poza obszarem MPK Wrocławia - '
        + 'pokazano tylko bezpośrednie połączenia kolejowe, bez przesiadek.'
        + '</p></div>');
}

/** Informacja, że trasa rusza dopiero po dłuższym czekaniu albo innego dnia
    niż pytanie - czekanie ma być widoczne, nie schowane (punkt 13 kontraktu).

    Godzinę wyjazdu mówi też pasek nad mapą ("wyjeżdżasz o"), ale łatwo ją
    przeoczyć - dłuższe czekanie tego samego dnia dostaje więc i tak osobny
    komunikat (decyzja użytkownika z 2026-09-25, po recenzji #141/#143/#147).

    Styl neutralny, nie czerwony: to nie błąd, tylko odpowiedź na pytanie
    "jak tam dojadę", gdy odpowiedź brzmi "za jakiś czas". */
const WAIT_NOTICE_SEC = 20 * 60;

function waitNoticeHtml(data) {
    if (data.day_offset) {
        const dzien = data.day_offset === 1 ? 'jutro' : `za ${data.day_offset} dni`;
        return `<div class="notice"><p>O tej porze nic już stąd nie jedzie. `
            + `Najbliższy wyjazd ${dzien} o ${esc(data.starts)}.</p></div>`;
    }
    if (!(data.waits_sec > WAIT_NOTICE_SEC)) return '';
    // Tego samego dnia coś stąd zwykle jedzie wcześniej - tylko nie dowozi
    // szybciej. "Nic już nie jedzie" byłoby nieprawdą; prawdą jest, że na
    // najszybszy dojazd trzeba poczekać.
    const ile = Math.round(data.waits_sec / 60);
    return `<div class="notice"><p>Najszybszy dojazd wyjeżdża o `
        + `${esc(data.starts)} — to za ${ile} min.</p></div>`;
}

function showWaitNotice(data) {
    const html = waitNoticeHtml(data);
    if (html) resultsBox.insertAdjacentHTML('afterbegin', html);
}

/** Cała reakcja na gotową odpowiedź /api/flow - wydzielona z loadPlan, żeby
    dało się ją uruchomić bez sieci (patrz tests/js/harness.js). */
function renderPlan(data, refit) {
    adoptNames(data);
    drawFlow(data, refit);

    journeys = data.journeys;
    selectedJourney = null;      // nowa lista = stary wybór nieaktualny
    clearJourney();
    clearPreview();
    dimFlow(false);
    renderVehicles();            // nowa mapa - inne linie, inne pojazdy
    if (!journeys.length) {
        // Pusta lista przy NIEPUSTEJ mapie to nie brak połączeń -
        // mapa pokazuje je tuż obok. Komunikat nie ma prawa temu
        // przeczyć (zdarza się przy szerokim oknie, gdy graf urośnie
        // ponad budżet szukania w _enumerate_journeys).
        if (data.segments.length) showWideWindowNotice();
        else showError('Nie znaleziono żadnego połączenia w tym oknie czasowym.');
    } else {
        renderJourneys();
    }
    showWaitNotice(data);
    if (data.degraded) showDegradedNotice();
    if (data.rail_only) showRailOnlyNotice();
}

function loadPlan(token, refit) {
    const params = queryParams();
    return Promise.all([fetch('/api/flow?' + params).then(r => r.json()), stopsReady])
        .then(([data]) => {
            if (token !== requestToken) return false;
            if (data.error) {
                clearFlow();
                showError(data.error, data.suggestions);
                return false;
            }
            renderPlan(data, refit);
            return true;      // znaleziono - patrz search() i playPipeDrop
        });
}

/** Rower zmienia ODPOWIEDŹ, nie tylko wygląd mapy: rowerowych propozycji nie
    ma w ostatniej odpowiedzi serwera, więc po przełączeniu warstwy trzeba je
    doliczyć. Tą samą drogą co suwaki w ⚙ (bez kadrowania), a nie przez nowe
    wyszukiwanie - relacja się nie zmieniła, więc kadr ma zostać na miejscu
    i nie ma po co znowu odgrywać dźwięku znalezienia trasy. */
function replanForBikes() {
    if (!lastFlow || !startInput.value || !endInput.value) return;
    loadPlan(requestToken, false)
        .catch(() => showError('Nie udało się połączyć z serwerem.'));
}

const LAST_SEARCH_KEY = 'metal-planner:last-search';

function saveLastSearch() {
    // Podróży z pokładu nie zapamiętujemy: kurs, którym się jechało, dawno
    // odjechał, a przywrócona po godzinie linia z przystankiem byłaby
    // odpowiedzią na pytanie, którego już nikt nie zadaje.
    if (onboardOn) { forgetLastSearch(); return; }
    try {
        localStorage.setItem(LAST_SEARCH_KEY, JSON.stringify({
            start: sel.start, end: sel.end,
        }));
    } catch {
        // localStorage niedostępny - wyszukiwanie działa dalej, po prostu się nie zapamięta
    }
}

/** Wołane przy X - zapomniana trasa nie ma wracać po odświeżeniu strony. */
function forgetLastSearch() {
    try {
        localStorage.removeItem(LAST_SEARCH_KEY);
    } catch {
        // localStorage niedostępny - nie ma czego czyścić
    }
}

/** Ostatnie wyszukiwanie (skąd/dokąd) wraca po odświeżeniu strony - tylko
    gdy pola są jeszcze puste (nie nadpisujemy tego, co user już zdążył
    wpisać, zanim ten kod się uruchomił). Godzina wraca sama z siebie do
    "teraz", bo tak ustawia ją serwer przy każdym renderowaniu strony. */
function restoreLastSearch() {
    if (onboardOn || startInput.value || endInput.value) return;
    let saved;
    try {
        saved = JSON.parse(localStorage.getItem(LAST_SEARCH_KEY));
    } catch {
        return;
    }
    if (!saved || !saved.start || !saved.end) return;
    sel.start = saved.start;
    sel.end = saved.end;
    startInput.value = displayValue(sel.start);
    endInput.value = displayValue(sel.end);
    // Godzina zostaje "teraz" (już ustawiona przez serwer przy renderowaniu
    // strony) - nie przywracamy tu starej godziny z poprzedniego wyszukiwania.
    updatePointMarker('start', sel.start);
    updatePointMarker('end', sel.end);
    restyle(sel.start, sel.end);
    search();
}

/** Kółko ładowania w dwóch miejscach naraz, bo w każdym widoku widać co
    innego: w komunikacie pod kartą (szeroki ekran, zakładka „Trasy") i na
    przycisku „Szukaj" (telefon w widoku mapy - tam wyników nie widać, a
    wyszukiwanie odpala się samo po drugim kliknięciu w mapę). */
function setSearching(on) {
    document.body.classList.toggle('searching', on);
    $('search').disabled = on;
    resultsBox.setAttribute('aria-busy', String(on));
}

function search() {
    // Z pokładu pola "skąd" nie ma - kompletem jest linia, kierunek
    // i przystanek (patrz onboardReady).
    if (!endInput.value) return;
    if (onboardOn ? !onboardReady() : !startInput.value) return;
    const token = ++requestToken;
    mapMore = 0;               // nowa relacja zaczyna od gęstości z suwaka
    clearJourney();
    clearPreview();
    setSearching(true);
    resultsBox.innerHTML =
        '<div class="notice loading"><span class="spinner" aria-hidden="true"></span>' +
        'Szukam połączeń…</div>';
    saveLastSearch();
    // Widoku nie przełączamy sami - kto szuka z mapy, ten chce zostać na
    // mapie i zobaczyć na niej przebieg. Że wyniki są, mówi licznik przy
    // zakładce „Trasy".
    loadPlan(token, true)
        // Rura spada tylko po WYSZUKANIU, nie po każdym przeliczeniu: suwaki
        // w panelu ⚙ wołają loadPlan bezpośrednio i mają zostać ciche.
        .then(found => { if (found) playPipeDrop(); })
        .catch(() => showError('Nie udało się połączyć z serwerem.'))
        // Kółko gasi tylko odpowiedź na AKTUALNE zapytanie - przy szybkiej
        // zmianie relacji stare, odsiane zapytanie nie może udawać, że nowe
        // już się doliczyło.
        .finally(() => { if (token === requestToken) setSearching(false); });
}

$('search').addEventListener('click', search);
stopsReady.then(restoreLastSearch);

// Ręczne wpisanie w pole tekstowe wychodzi z trybu "wybrany punkt" - dalej
// liczy się to, co user wpisał, jak przy zwykłym wyszukiwaniu.
startInput.addEventListener('input', () => {
    if (isPoint(sel.start)) { sel.start = null; updatePointMarker('start', null); }
});
endInput.addEventListener('input', () => {
    if (isPoint(sel.end)) { sel.end = null; updatePointMarker('end', null); }
});

// -------------------------------------------- podpowiedzi nazw przystanków ----

// Własna lista zamiast <datalist>: natywna wygląda inaczej w każdej
// przeglądarce, nie da się jej ostylować ani sterować kolejnością trafień,
// a do tego wymaga dokładnych ogonków - "lesnica" nie znajdowało "LEŚNICA".
//
// Serwer daje {name, kind} (patrz routes.py/index) - `kind` jedzie OSOBNO
// od nazwy, nie doklejone do stringa: plakietka "PKP" w podpowiedziach
// (patrz open() niżej) ma tylko odróżnić stację kolejową na oko, a pole
// wyszukiwania i tak dostaje samą nazwę - z doklejonym "PKP" wyszukiwarka
// nie znalazłaby stacji, bo zna ją tylko pod prawdziwą nazwą.
const STOP_ENTRIES = JSON.parse($('stop-names').textContent);
const STOP_NAMES = STOP_ENTRIES.map(e => e.name);
const MAX_SUGGESTIONS = 8;

// Składanie nazwy: bez ogonków i wielkości liter, ale ZNAK W ZNAK - długość
// się nie zmienia, więc pozycja trafienia w wersji złożonej wskazuje ten sam
// fragment oryginalnej nazwy (do podświetlenia).
const fold = text => text.normalize('NFD').replace(/[\u0300-\u036f]/g, '')
                         .toLowerCase().replace(/ł/g, 'l');
// Podpowiedzi żyją w ETYKIETACH (patrz prettyStopName), nie w nazwach
// kanonicznych: to etykieta się pokazuje, to ją wybrany wiersz wpisuje do pola
// i po niej szuka wpisany tekst - więc "dowolna" znajduje grupy stacji, choć
// w kanonicznej nazwie tego słowa nie ma. Na kanoniczną wraca dopiero
// rawStopName przy wysyłaniu (patrz queryParams).
const STOP_LABELS = STOP_NAMES.map(prettyStopName);
const STOP_KIND = new Map(STOP_ENTRIES.map(e => [prettyStopName(e.name), e.kind]));
const FOLDED_LABELS = STOP_LABELS.map(fold);

// Drugie złożenie: bez kropek i z rozwiniętymi skrótami, żeby "Plac
// Grunwaldzki" podpowiadało "PL. GRUNWALDZKI". Tabela przychodzi z serwera
// (patrz naming.ABBREVIATIONS, templates/index.html) - przepisana tutaj
// rozjechałaby się z wyszukiwarką przy pierwszym dopisanym skrócie.
//
// To OSOBNY, ostatni przebieg, a nie zamiennik fold(): rozwinięcie zmienia
// długość ("pl" -> "plac"), więc pozycja trafienia nie wskazuje już tego
// samego fragmentu oryginalnej nazwy i nie ma czego podświetlić.
const ABBREV = new Map(Object.entries(JSON.parse($('stop-abbrev').textContent)));
const expand = folded => folded.replace(/\./g, ' ').split(/\s+/)
                               .filter(Boolean)
                               .map(word => ABBREV.get(word) || word).join(' ');
const ALIAS_LABELS = STOP_LABELS.map(label => expand(fold(label)));

/** Trafienia od początku nazwy przed trafieniami w środku - wpisując "grun"
    chcemy najpierw "Grunwaldzki", a nie "pl. Grunwaldzki" alfabetycznie.
    Trafienia po rozwinięciu skrótu idą na koniec: są najluźniejsze, tak samo
    jak po stronie serwera (patrz gtfs.match_stop). */
function suggestionsFor(query, names = STOP_LABELS, folded, limit = MAX_SUGGESTIONS) {
    const needle = fold(query.trim());
    if (!needle) return [];
    const own = names === STOP_LABELS;
    folded = folded || (own ? FOLDED_LABELS : names.map(fold));
    // Złożenia aliasowe idą tą samą drogą co `folded`: gotowe dla domyślnej
    // listy przystanków, liczone w locie dla każdej innej (tryb rozkładów
    // podaje własną listę numerów linii - patrz timetable.js).
    const aliases = own ? ALIAS_LABELS : folded.map(expand);
    const alias = expand(needle);
    const prefix = [], inside = [], aliased = [];
    names.forEach((name, i) => {
        const at = folded[i].indexOf(needle);
        if (at === 0) prefix.push({name, at, len: needle.length});
        else if (at > 0) inside.push({name, at, len: needle.length});
        // at/len na zero = nic nie podświetlamy (patrz wyżej, dlaczego).
        else if (alias && aliases[i].includes(alias)) aliased.push({name, at: 0, len: 0});
    });
    return [...prefix, ...inside, ...aliased].slice(0, limit);
}

/** Wiersz podpowiedzi: nazwa z podświetlonym trafieniem. Tryb rozkładów
    podstawia własny (plakietka linii albo znaczek przystanku). */
function suggestionHtml(item) {
    // "PKP" tylko jako etykieta wiersza - do pola wpisuje się sama nazwa.
    const tag = STOP_KIND.get(item.name) === 'train'
        ? ' <span class="ac-tag">PKP</span>' : '';
    return esc(item.name.slice(0, item.at))
         + `<mark>${esc(item.name.slice(item.at, item.at + item.len))}</mark>`
         + esc(item.name.slice(item.at + item.len)) + tag;
}

/** Klawiatura, ARIA i zamykanie listy są tu raz; co dokładnie się podpowiada
    i jak wygląda wiersz, wołający może podmienić:
    - `options.suggest(query)` - własne szukanie (rozkłady mieszają w jednej
      liście linie i przystanki, więc nie da się tego opisać jedną tablicą nazw);
    - `options.render(item)`   - własny wiersz;
    - `options.onEnter()`      - co robi Enter poza listą.
    `onPick` dostaje wybraną pozycję, nie sam napis. */
function attachAutocomplete(input, onPick, options = {}) {
    const suggest = options.suggest || (query => suggestionsFor(query));
    const render = options.render || suggestionHtml;
    const onEnter = options.onEnter || search;
    const list = $(input.id + '-list');
    let items = [];
    let active = -1;          // -1 = nic nie wybrane klawiaturą

    function close() {
        list.hidden = true;
        list.innerHTML = '';
        list.classList.remove('kb');
        items = [];
        active = -1;
        input.setAttribute('aria-expanded', 'false');
        input.removeAttribute('aria-activedescendant');
    }

    function open() {
        items = suggest(input.value);
        active = -1;
        if (!items.length) { close(); return; }
        list.innerHTML = items.map((item, i) =>
            `<li class="ac-item" role="option" aria-selected="false"
                 id="${list.id}-${i}" data-index="${i}">${render(item)}</li>`
        ).join('');
        list.hidden = false;
        list.classList.remove('kb');
        input.setAttribute('aria-expanded', 'true');
        input.removeAttribute('aria-activedescendant');
    }

    function setActive(index) {
        const previous = list.children[active];
        if (previous) {
            previous.classList.remove('active');
            previous.setAttribute('aria-selected', 'false');
        }
        active = index;
        const current = list.children[active];
        if (!current) return;
        current.classList.add('active');
        current.setAttribute('aria-selected', 'true');
        current.scrollIntoView({block: 'nearest'});
        list.classList.add('kb');       // klawiatura przejmuje podświetlenie
        input.setAttribute('aria-activedescendant', current.id);
    }

    function move(step) {
        if (list.hidden) open();
        if (!items.length) return;
        setActive(active < 0
            ? (step > 0 ? 0 : items.length - 1)
            : (active + step + items.length) % items.length);
    }

    function choose(index) {
        const item = items[index];
        if (!item) return;
        input.value = item.name;
        close();
        onPick(item);
    }

    input.addEventListener('input', open);
    input.addEventListener('focus', () => { if (input.value) open(); });
    // Wybór myszą leci przez mousedown z preventDefault, więc blur nigdy nie
    // zamknie listy sprzed kliknięcia.
    input.addEventListener('blur', close);

    input.addEventListener('keydown', event => {
        switch (event.key) {
        case 'ArrowDown': event.preventDefault(); move(1); break;
        case 'ArrowUp': event.preventDefault(); move(-1); break;
        case 'Escape': close(); break;
        case 'Tab': close(); break;
        case 'Enter':
            event.preventDefault();
            if (active >= 0) choose(active);
            else { close(); onEnter(); }
            break;
        }
    });

    list.addEventListener('mousedown', event => {
        const option = event.target.closest('.ac-item');
        if (!option) return;
        event.preventDefault();         // pole ma zostać z fokusem
        choose(Number(option.dataset.index));
    });

    // Lista zamyka się sama (blur, Esc, wybór), ale bywa chowana razem z całym
    // polem - a wtedy nie ma komu jej zamknąć i wraca otwarta, gdy pole wróci
    // na ekran (patrz syncOnboardView: zwijanie kompletu w jedną linijkę).
    return {close};
}

// Wybór podpowiedzi kończy tryb "wybrany punkt" i - gdy relacja jest
// kompletna - od razu szuka, tak samo jak klik w mapę.
attachAutocomplete(startInput, () => {
    if (isPoint(sel.start)) { sel.start = null; updatePointMarker('start', null); }
    if (endInput.value) search();
});
attachAutocomplete(endInput, () => {
    if (isPoint(sel.end)) { sel.end = null; updatePointMarker('end', null); }
    if (startInput.value) search();
});

$('swap').addEventListener('click', () => {
    const previous = [sel.start, sel.end];
    [sel.start, sel.end] = [sel.end, sel.start];
    [startInput.value, endInput.value] = [endInput.value, startInput.value];
    updatePointMarker('start', sel.start);
    updatePointMarker('end', sel.end);
    restyle(...previous, sel.start, sel.end);
    search();
});

// „teraz" to CHWILA, nie sama godzina: przy dacie zostawionej na innym dniu
// sama godzina opisywałaby 17:40 w przyszły wtorek, a nie ten moment.
$('time-now').addEventListener('click', () => {
    const now = new Date();
    $('time').value = now.toTimeString().slice(0, 5);
    $('date').value = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
                    + `-${String(now.getDate()).padStart(2, '0')}`;
    if (startInput.value && endInput.value) search();
});

$('clear').addEventListener('click', () => {
    const previous = [sel.start, sel.end];
    sel = {start: null, end: null};
    startInput.value = '';
    endInput.value = '';
    // Pojazdu ✕ nie kasuje: pasażer wciąż siedzi w tym samym autobusie,
    // a zmienia się to, dokąd chce nim dojechać. Kasowanie linii kazałoby
    // wybierać ją od nowa po każdej zmianie celu.
    updatePointMarker('start', null);
    updatePointMarker('end', null);
    showLocateMsg('');
    resetResults();
    restyle(...previous);
    setView('map');       // nową relację wybiera się na mapie
    forgetLastSearch();
});

// Suwaki Ustawień Developerskich: etykieta od razu, mapa i lista propozycji
// po krótkim debounce (odpowiedź z ciepłym cache to ~10 ms, więc działa
// "na żywo"). Jedno wspólne zapytanie (loadPlan) niesie obie rzeczy naraz,
// więc każdy suwak siłą rzeczy odświeża i mapę, i listę - nie ma już
// suwaków "tylko dla mapy".
//
// Wartości suwaków zapamiętujemy w localStorage (jeden klucz, mały JSON) -
// przeżywają odświeżenie strony i nowe wizyty, więc nie trzeba ustawiać
// preferencji od nowa za każdym razem.
const DEV_PREFS_KEY = 'metal-planner:dev-prefs';
const DEV_SLIDER_IDS = ['density', 'car-count', 'bike-count', 'transfer-gain',
                        'walk-pace', 'bike-kmh', 'bike-overhead'];

function loadDevPrefs() {
    try {
        return JSON.parse(localStorage.getItem(DEV_PREFS_KEY)) || {};
    } catch {
        return {};       // localStorage niedostępny (tryb prywatny) albo zepsuty JSON
    }
}

function saveDevPref(id, value) {
    const prefs = loadDevPrefs();
    prefs[id] = value;
    try {
        localStorage.setItem(DEV_PREFS_KEY, JSON.stringify(prefs));
    } catch {
        // localStorage niedostępny - suwak działa dalej, po prostu się nie zapamięta
    }
}

function applyStoredDevPrefs() {
    const prefs = loadDevPrefs();
    for (const id of DEV_SLIDER_IDS) {
        if (prefs[id] === undefined) continue;
        const input = $(id);
        const valueEl = $(id + '-value');
        if (!input) continue;
        input.value = prefs[id];
        if (valueEl) valueEl.textContent = prefs[id];
    }
}

function liveSlider(inputId, valueId, resetsMore) {
    const input = $(inputId);
    const valueEl = $(valueId);
    let timer = null;
    input.addEventListener('input', () => {
        valueEl.textContent = input.value;
        // Ruszenie suwakiem gęstości to nowa wyjściowa gęstość - dokładka
        // z "pokaż więcej" liczyłaby się inaczej od starej i suwak
        // wyglądałby na zepsuty.
        if (resetsMore) mapMore = 0;
        saveDevPref(inputId, input.value);
        clearTimeout(timer);
        timer = setTimeout(() => {
            if (!startInput.value || !endInput.value) return;
            loadPlan(requestToken, false)
                .catch(() => showError('Nie udało się połączyć z serwerem.'));
        }, 200);
    });
}
applyStoredDevPrefs();
liveSlider('density', 'density-value', true);
liveSlider('car-count', 'car-count-value', true);
liveSlider('bike-count', 'bike-count-value', true);
liveSlider('transfer-gain', 'transfer-gain-value');
liveSlider('walk-pace', 'walk-pace-value');
liveSlider('bike-kmh', 'bike-kmh-value');
liveSlider('bike-overhead', 'bike-overhead-value');

function showWalkPace() {
    $('walk-pace-value').textContent = WALK_PACE_LABELS[$('walk-pace').value];
}
$('walk-pace').addEventListener('input', showWalkPace);
showWalkPace();

// Grupowanie aut, dostawczaki i rodzaj roweru zmieniają odpowiedź serwera,
// więc jak suwak: pamiętane w tym samym kluczu i od razu nowe zapytanie.
// Rodzaje roweru są domyślnie WŁĄCZONE - bez ruszania czegokolwiek mapa
// wygląda tak, jak wyglądała przed zgłoszeniem #147.
for (const [id, domyslnie] of [['car-groups', false], ['car-vans', false],
                               ['latest-start', false]]) {
    const input = $(id);
    const zapisane = loadDevPrefs()[id];
    input.checked = zapisane === undefined ? domyslnie : zapisane === true;
    input.addEventListener('change', () => {
        mapMore = 0;
        saveDevPref(id, input.checked);
        if (id === 'car-vans' && !flowOnScreen()) refreshCarLayer();
        if (!startInput.value || !endInput.value) return;
        loadPlan(requestToken, false)
            .catch(() => showError('Nie udało się połączyć z serwerem.'));
    });
}

// --- suwaki wyglądu mapy (schowane, patrz LOOK_TUNING) ---------------------
//
// Te suwaki nie dotykają serwera - kręcą wyłącznie liczbami z LOOK_DEFAULTS,
// więc mapa przemalowuje się natychmiast, z ostatniej odpowiedzi (lastFlow),
// bez ponownego zapytania. Wartości są już dobrane (siedzą w LOOK_DEFAULTS),
// więc cała sekcja jest domyślnie schowana - `LOOK_TUNING = true` przywraca
// ją, gdyby trzeba było stroić od nowa.
const LOOK_KNOBS = {
    'look-min-op': 'minOpacity',
    'look-max-op': 'maxOpacity',
    'look-min-w': 'minWeight',
    'look-max-w': 'maxWeight',
    'look-casing': 'casingFrom',
    'look-dim': 'dimFactor',
    'look-label-step': 'labelStep',
    'look-label-size': 'labelScale',
    'look-label-op': 'labelOpacity',
};

function saveLookPrefs() {
    try {
        localStorage.setItem(LOOK_PREFS_KEY, JSON.stringify(look));
    } catch {
        // localStorage niedostępny - suwaki działają dalej, po prostu się nie zapamiętają
    }
}

/** Przemalowanie z ostatniej odpowiedzi - bez zapytania do serwera. Wybrana
    trasa rysuje się na nowo NA WIERZCHU przemalowanego wachlarza (kolejność
    warstw w canvasie to kolejność dokładania). */
function applyLook() {
    document.documentElement.style.setProperty('--chip-scale', look.labelScale);
    if (lastFlow) drawFlow(lastFlow, false);
    if (selectedJourney !== null) drawJourney(selectedJourney, true);
    const dump = $('look-dump');
    if (dump) {
        dump.textContent = Object.entries(look)
            .map(([k, v]) => `${k}: ${v}`).join(', ');
    }
}

function bindLookSliders() {
    const section = $('look-section');
    if (!section) return;               // sekcja skasowana - wartości zostają domyślne
    if (!LOOK_TUNING) { section.hidden = true; return; }
    section.hidden = false;
    let timer = null;
    const show = id => {
        const input = $(id);
        const out = $(id + '-value');
        if (out) out.textContent = input.value;
    };
    for (const [id, key] of Object.entries(LOOK_KNOBS)) {
        const input = $(id);
        input.value = look[key];        // źródłem prawdy jest LOOK_DEFAULTS + localStorage
        show(id);
        input.addEventListener('input', () => {
            look[key] = Number(input.value);
            show(id);
            saveLookPrefs();
            clearTimeout(timer);        // przeciąganie suwaka: jedno przemalowanie na klatkę
            timer = setTimeout(applyLook, 60);
        });
    }
}
bindLookSliders();
document.documentElement.style.setProperty('--chip-scale', look.labelScale);

// --- dźwięk: spadająca metalowa rura ---------------------------------------
//
// Nagranie, nie synteza - chodzi o TEN konkretny dźwięk, a nie o coś, co
// brzmi podobnie.
//
// Dwa formaty, bo jeden nie wystarcza: Ogg Opus (Chrome, Firefox, Edge)
// i AAC w kontenerze m4a dla Safari, które Ogg umie dopiero od niedawna
// i nie na każdym systemie. Wybiera `canPlayType`, nie zgadywanie po nazwie
// przeglądarki - ta kłamie, a canPlayType odpowiada za konkretny dekoder.
// Oba pliki ważą po ~38 kB, więc wpadają do cache'u service workera razem
// z resztą statyki i działają offline.
//
// Ustawienie jest SCHOWANE (SOUND_TUNING = false) - dźwięk po prostu jest.
// Przełącznik zostaje w kodzie i w panelu, więc pokazanie go to zmiana
// jednej stałej (ten sam układ, co przy LOOK_TUNING).

const SOUND_TUNING = false;      // czy pokazywać sekcję "Dźwięk" w panelu ⚙

const PIPE_SOURCES = [
    ['audio/ogg; codecs=opus', '/static/sounds/metal-pipe.ogg'],
    ['audio/mp4; codecs="mp4a.40.2"', '/static/sounds/metal-pipe.m4a'],
];

// Nagranie jest głośne (szczyt ponad 0 dBFS), a to ma być żart w tle,
// nie alarm.
const PIPE_VOLUME = 0.35;

const SOUND_DEFAULTS = {
    pipe: true,
};

const SOUND_PREFS_KEY = 'metal-planner:sound-prefs';

function loadSoundPrefs() {
    try {
        return JSON.parse(localStorage.getItem(SOUND_PREFS_KEY)) || {};
    } catch {
        return {};
    }
}

// Zapamiętany wybór czytamy tylko wtedy, gdy przełącznik jest widoczny -
// inaczej ktoś, kto wyłączył dźwięk, gdy sekcja była na wierzchu, zostałby
// z ciszą i bez czegokolwiek, czym da się ją cofnąć.
const soundOpts = {...SOUND_DEFAULTS, ...(SOUND_TUNING ? loadSoundPrefs() : {})};

function saveSoundPrefs() {
    try {
        localStorage.setItem(SOUND_PREFS_KEY, JSON.stringify(soundOpts));
    } catch {
        // localStorage niedostępny - przełącznik działa dalej, tylko się nie zapamięta
    }
}

let pipeAudio = null;

/** Element audio powstaje przy pierwszym użyciu i zostaje - jeden na stronę.
    Zwraca null, gdy przeglądarka nie umie żadnego z naszych formatów. */
function pipeElement() {
    if (pipeAudio) return pipeAudio;
    const element = document.createElement('audio');
    if (!element.canPlayType) return null;
    const pick = PIPE_SOURCES.find(([type]) => element.canPlayType(type));
    if (!pick) return null;
    element.src = pick[1];
    element.preload = 'auto';
    element.volume = PIPE_VOLUME;
    pipeAudio = element;
    return pipeAudio;
}

function prefersLessMotion() {
    return !!(window.matchMedia
              && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
}

/** Cicho, gdy przełącznik wyłączony, gdy system prosi o ograniczenie
    animacji albo gdy przeglądarka nie umie żadnego z formatów. */
function playPipeDrop() {
    if (!soundOpts.pipe || prefersLessMotion()) return;
    const audio = pipeElement();
    if (!audio) return;
    // Drugie wyszukiwanie w trakcie pierwszego dźwięku ma zagrać OD NOWA,
    // a nie zostać po cichu pominięte.
    audio.currentTime = 0;
    const started = audio.play();
    // Przeglądarka odmawia, dopóki strona nie dostała gestu. Tu zawsze
    // jesteśmy po kliknięciu, ale odrzucona obietnica nie może wywalić
    // reszty łańcucha.
    if (started && started.catch) started.catch(() => {});
}

function bindSoundToggle() {
    const fold = $('fold-sound');
    if (fold) fold.hidden = !SOUND_TUNING;
    const input = $('sound-pipe');
    if (!input) return;
    input.checked = !!soundOpts.pipe;
    input.addEventListener('change', () => {
        soundOpts.pipe = input.checked;
        saveSoundPrefs();
        // Włączenie od razu gra: inaczej trzeba by szukać trasy, żeby usłyszeć,
        // co się właśnie włączyło.
        if (input.checked) playPipeDrop();
    });
}
bindSoundToggle();

// --- przełączniki "czasu na mapie" -----------------------------------------
//
// Nic tu nie rusza serwera: wszystkie liczby są już w ostatniej odpowiedzi
// (lastFlow), więc przełącznik przemalowuje mapę natychmiast, bez zapytania.
const TIME_TOGGLES = {
    'time-hover': 'hover',
    'time-ends': 'ends',
    'time-chips': 'chips',
    'time-show-headline': 'headline',
    'time-ride': 'ride',
};

function applyTimeOpts() {
    if (lastFlow) drawFlow(lastFlow, false);   // grupki i pasek liczą się od nowa
    else renderTimeHeadline();
    renderFlowPick();                          // dymek pod kursorem, jeśli akurat wisi
}

function bindTimeToggles() {
    for (const [id, key] of Object.entries(TIME_TOGGLES)) {
        const input = $(id);
        if (!input) continue;
        input.checked = !!timeOpts[key];
        input.addEventListener('change', () => {
            timeOpts[key] = input.checked;
            saveTimePrefs();
            applyTimeOpts();
        });
    }
}
bindTimeToggles();

// --- kropki przystanków i miejsce na rozkład -------------------------------
//
// Też bez zapytania do serwera: obie współrzędne węzła i cała tablica odjazdów
// są już w odpowiedziach, więc wystarczy przemalować z lastFlow i przerysować
// wybraną trasę w miejscu (keepView - kadr ma się nie ruszyć).
const DOT_TOGGLES = {
    'dot-center': 'center',
    'dot-start': 'start',
    'tip-cursor': 'tipCursor',
    'tip-panel': 'tipPanel',
    'bike-times': 'bikeTimes',
    'bike-rides': 'bikeRides',
    'debug-why': 'why',
};

function applyDotOpts() {
    if (lastFlow) drawFlow(lastFlow, false);
    if (selectedJourney !== null) drawJourney(selectedJourney, true);
    if (!dotOpts.tipPanel) hideSidePanel();
    if (!dotOpts.tipCursor && flowTooltip) {
        map.removeLayer(flowTooltip);
        flowTooltip = null;
    }
}

function bindDotOpts() {
    const size = $('dot-size');
    const sizeOut = $('dot-size-value');
    if (size) {
        size.value = dotOpts.size;
        if (sizeOut) sizeOut.textContent = size.value;
        let timer = null;
        size.addEventListener('input', () => {
            dotOpts.size = Number(size.value);
            if (sizeOut) sizeOut.textContent = size.value;
            saveDotPrefs();
            clearTimeout(timer);      // przeciąganie suwaka: jedno przemalowanie na klatkę
            timer = setTimeout(applyDotOpts, 60);
        });
    }
    const rows = $('dot-rows');
    const rowsOut = $('dot-rows-value');
    if (rows) {
        rows.value = dotOpts.rows;
        if (rowsOut) rowsOut.textContent = rows.value;
        rows.addEventListener('input', () => {
            dotOpts.rows = Number(rows.value);
            if (rowsOut) rowsOut.textContent = rows.value;
            saveDotPrefs();
            // W pamięci leży GOTOWY html, przycięty do starej liczby wierszy -
            // bez tego suwak działałby dopiero na kropkach jeszcze nietkniętych.
            timetableCache.clear();
            if (timetableTarget) {
                loadTimetable(timetableTarget, timetableTarget.where, timetableTarget.sec);
            }
        });
    }
    for (const [id, key] of Object.entries(DOT_TOGGLES)) {
        const input = $(id);
        if (!input) continue;
        input.checked = !!dotOpts[key];
        input.addEventListener('change', () => {
            dotOpts[key] = input.checked;
            saveDotPrefs();
            applyDotOpts();
        });
    }
}
bindDotOpts();

// --- rozwijane sekcje panelu -----------------------------------------------
//
// Opcji zrobiło się tyle, że panel przewijał się dłużej niż ekran. Sekcje
// pamiętają, czy były rozwinięte - w tym samym kluczu co suwaki.
const DEV_FOLD_IDS = [
    'fold-time', 'fold-window', 'fold-transfer',
    'fold-sound', 'fold-dots', 'fold-bike', 'fold-cars', 'fold-assumptions',
    'fold-experiments', 'fold-debug', 'look-section',
    'fold-version',
];

function bindDevFolds() {
    const prefs = loadDevPrefs();
    for (const id of DEV_FOLD_IDS) {
        const el = $(id);
        if (!el) continue;
        const saved = prefs['fold:' + id];
        if (saved !== undefined) el.open = !!saved;
        el.addEventListener('toggle', () => saveDevPref('fold:' + id, el.open));
    }
}
bindDevFolds();

// --- zmienione ustawienia i powrót do domyślnych ----------------------------
//
// `value`/`checked` z index.html to wartości domyślne (patrz komentarz nad
// panelem), więc opcja różna od nich dostaje znacznik, a nagłówek sekcji -
// liczbę takich opcji, żeby było je widać także przy zwiniętej sekcji.
function markChangedSettings() {
    for (const fold of devPanel.querySelectorAll('.dev-fold')) {
        let changed = 0;
        for (const input of fold.querySelectorAll('input')) {
            const differs = input.type === 'checkbox'
                ? input.checked !== input.defaultChecked
                : Number(input.value) !== Number(input.defaultValue);
            input.closest('.field, .dev-check').classList.toggle('changed', differs);
            if (differs) changed++;
        }
        const summary = fold.querySelector('summary');
        if (changed) summary.dataset.changed = changed;
        else delete summary.dataset.changed;
    }
}
devPanel.addEventListener('input', markChangedSettings);
devPanel.addEventListener('change', markChangedSettings);
markChangedSettings();

// Jeden przycisk na cały panel: kasuje wszystkie zapamiętane ustawienia
// i przeładowuje stronę, więc każda wartość wraca z *_DEFAULTS tą samą drogą,
// co przy pierwszej wizycie - bez osobnego "przywróć" dla każdej sekcji, które
// trzeba by pilnować przy każdej nowej opcji. Ostatnie wyszukiwanie ma własny
// klucz, więc mapa wraca ta sama.
$('dev-reset').addEventListener('click', () => {
    for (const key of [DEV_PREFS_KEY, TIME_PREFS_KEY, DOT_PREFS_KEY,
                       LOOK_PREFS_KEY, SOUND_PREFS_KEY]) {
        try {
            localStorage.removeItem(key);
        } catch {
            // localStorage niedostępny - i tak nie ma czego kasować
        }
    }
    location.reload();
});

// ------------------------------------------------- most do trybu rozkładów ----
//
// Rozkłady (static/timetable.js) to drugi widok TEJ SAMEJ mapy: własny plik,
// żeby ten nie puchł, ale rysuje po tym samym Leaflecie i musi umieć schować
// wachlarz wyszukiwarki na czas swojego panowania. Stąd wąski, jawny most
// zamiast globalnych zmiennych - poza tym, co niżej, nic z app.js nie wycieka.
//
// Schowanie wachlarza NIE kasuje ostatniej odpowiedzi (lastFlow): powrót do
// wyszukiwania odtwarza dokładnie to, co było widać, bez ponownego zapytania.

function suspendPlanner() {
    plannerSuspended = true;
    if (flowLayer) { map.removeLayer(flowLayer); flowLayer = null; }
    if (flowLabelLayer) { map.removeLayer(flowLabelLayer); flowLabelLayer = null; }
    if (flowDotLayer) { map.removeLayer(flowDotLayer); flowDotLayer = null; }
    // Punkty i wyróżnione słupki relacji schodzą razem z wachlarzem: same,
    // bez linii między nimi, mówiłyby o wyszukiwaniu, którego nie widać.
    updatePointMarker('start', null);
    updatePointMarker('end', null);
    restyle(sel.start, sel.end);
    // Wachlarza nie ma, więc warstwy wracają do miejskiego feedu - włącznik
    // zostaje tam, gdzie go zostawiono (patrz refreshCarLayer).
    refreshCarLayer();
    refreshBikeLayer();
    clearBikeRides();
    flowParts = [];
    flowHits = [];
    clearFlowHover();
    clearJourney();
    clearPreview();
    hideFastest();
    setBaseDim(false);          // przystanki wracają do pełnej widoczności - w
    const headline = $('time-headline');   // rozkładach to one są treścią mapy
    if (headline) headline.hidden = true;
    // Okienko w rogu opisuje przystanek na mapie, którą właśnie zdejmujemy -
    // zostawione, wisiałoby nad rozkładami z tablicą sprzed przejścia (a od
    // niedawna również z przyciskiem „trasa", którym się tu weszło).
    hideSidePanel();
}

function resumePlanner() {
    plannerSuspended = false;
    updatePointMarker('start', sel.start);
    updatePointMarker('end', sel.end);
    restyle(sel.start, sel.end);
    if (lastFlow) drawFlow(lastFlow, false);
    // Bez mapy nie ma czego zawężać - a rozkłady właśnie przestały o tym
    // decydować (patrz vehiclesFilter). Auta i rowery wracają wtedy do
    // miejskiego feedu, bo ich włącznik znów ma na czym stać.
    else { renderTimeHeadline(); renderVehicles();
           refreshCarLayer(); refreshBikeLayer(); }
    if (selectedJourney !== null) drawJourney(selectedJourney, true);
}

// Pierwsze postawienie warstw aut i rowerów: zanim padnie jakiekolwiek
// pytanie, pokazują po prostu całe miasto (patrz refreshCarLayer). Tutaj,
// a nie przy samych przyciskach - stamtąd warstwy wachlarza jeszcze nie
// istnieją.
refreshCarLayer();
refreshBikeLayer();

window.plannerBridge = {
    map, esc, fitTo, setView, syncTabs, setBaseDim, renderVehicles,
    attachAutocomplete, suggestionsFor, suggestionHtml,
    // STOP_LABELS, nie STOP_NAMES: na zewnątrz wychodzi to, co się pokazuje
    // i wpisuje do pola (patrz prettyStopName). Dwie prawie identyczne
    // tablice w jednym API to zaproszenie do sięgnięcia po złą.
    LINE_COLORS, MODE_LABEL, STOP_LABELS, ROUTE_ICON,
    prettyStopName, rawStopName,
    suspendPlanner, resumePlanner,
};

}
