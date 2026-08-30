/* Planer podróży - cały frontend: mapa Leaflet, panel wyszukiwania,
   lista propozycji tras i panel deweloperski.

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

// Panel deweloperski jest schowany za przyciskiem - normalny użytkownik
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

function setView(view) {
    if (!viewTabs) return;
    document.body.classList.toggle('view-list', view === 'list');
    document.body.classList.toggle('view-map', view !== 'list');
    for (const tab of viewTabs.querySelectorAll('.tab')) {
        const on = tab.dataset.view === view;
        tab.classList.toggle('active', on);
        tab.setAttribute('aria-pressed', String(on));
    }
}

if (viewTabs) {
    viewTabs.addEventListener('click', event => {
        const tab = event.target.closest('.tab');
        if (tab) setView(tab.dataset.view);
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
const displayValue = v => !v ? ''
    : isPoint(v) ? `(${v.lat.toFixed(4)}, ${v.lon.toFixed(4)})` : v;

function esc(text) {
    const div = document.createElement('div');
    div.textContent = text ?? '';
    return div.innerHTML;
}

const LINE_COLORS = {tram: '#c62828', bus: '#1565c0', train: '#2e7d32', other: '#6a1b9a'};
const MODE_LABEL = {tram: 'Tramwaj', bus: 'Autobus', train: 'Pociąg', other: 'Linia'};

// ------------------------------------------------------- markery na mapie ----

const markersByName = new Map();          // nazwa -> [L.circleMarker, ...]
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

function styleFor(name) {
    if (name === sel.start) return {radius: 8, weight: 2, color: '#1b5e20',
                                    fillColor: '#4caf50', fillOpacity: 1};
    if (name === sel.end) return {radius: 8, weight: 2, color: '#b71c1c',
                                  fillColor: '#ef5350', fillOpacity: 1};
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

// Kadrowanie wyniku potrzebuje współrzędnych startu i celu, a te znamy
// dopiero z markerów - kto rysuje, czeka na to zapytanie.
const stopsReady = fetch('/api/stops')
    .then(r => r.json())
    .then(stops => {
        if (stops.error) { showError(stops.error); return; }
        for (const s of stops) {
            stopKind.set(s.name, s.kind);
            const m = L.circleMarker([s.lat, s.lon], styleFor(s.name)).addTo(map);
            // Sama etykieta dymka, w odróżnieniu od podpowiedzi w formularzu
            // (patrz STOP_KIND/attachAutocomplete), nie jedzie nigdzie jako
            // wyszukiwana nazwa - można doklejać "PKP" wprost do tekstu.
            m.bindTooltip(s.kind === 'train' ? `${s.name} PKP` : s.name);
            // Zatrzymujemy zdarzenie - inaczej klik w słupek dobiłby też do
            // map.on('click') i nadpisał wybór punktem.
            m.on('click', e => { L.DomEvent.stop(e); pickEndpoint(s.name); });
            if (!markersByName.has(s.name)) markersByName.set(s.name, []);
            markersByName.get(s.name).push(m);
        }
    });

/** Klik w mapę (pusty punkt albo słupek) uzupełnia brakujący koniec relacji.
    Nigdy nie kasuje gotowego wyszukiwania - od tego jest przycisk ✕; przy
    wybranej trasie pierwszy taki klik po prostu ją odznacza. */
function pickEndpoint(value) {
    if (selectedJourney !== null) { deselectJourney(); return; }
    if (sel.start && sel.end) return;
    const previous = [sel.start, sel.end];
    if (!sel.start) {
        sel.start = value;
    } else if (!samePlace(value, sel.start)) {
        sel.end = value;
    }
    startInput.value = displayValue(sel.start);
    endInput.value = displayValue(sel.end);
    updatePointMarker('start', sel.start);
    updatePointMarker('end', sel.end);
    restyle(...previous, sel.start, sel.end);
    if (sel.start && sel.end) search();
}

map.on('click', e => pickEndpoint({lat: e.latlng.lat, lon: e.latlng.lng}));

// ------------------------------------------- moja lokalizacja jako start ----

// Pozycja z przeglądarki to dla nas zwykły punkt mapy, nie przystanek -
// backend sam znajdzie wokół niego słupki (zasięg z panelu ⚙).
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
// suwakami w panelu deweloperskim - sekcja jest z powrotem WIDOCZNA
// (LOOK_TUNING niżej), żeby dało się stroić dalej.
//
// minWeight = maxWeight to świadomy wybór: grubość jest STAŁA, a różnicę
// zapasu czasu niesie samo krycie (0.4 -> 1).
const LOOK_DEFAULTS = {
    minOpacity: 0.4,      // krycie najbledszego kawałka (w=0)
    maxOpacity: 1,        // krycie najjaśniejszego (w=1)
    minWeight: 3,         // grubość najbledszego kawałka [px]
    maxWeight: 3,         // grubość najjaśniejszego [px]
    casingFrom: 0.45,     // od tej jasności kawałek dostaje białą otoczkę (1 = nigdy)
    dimFactor: 0.22,      // ile zostaje z krycia, gdy wybrana jest jedna trasa
    labelStep: 200,       // co tyle pikseli korytarza staje kolejna grupka numerów
    labelScale: 0.8,      // wielkość numerów (1 = jak w CSS)
    labelOpacity: 1,      // mnożnik krycia grupek numerów
};

// JEDYNY przełącznik strojenia wyglądu: `true` pokazuje sekcję „Wygląd mapy"
// w panelu deweloperskim (i zaczyna pamiętać ustawienia suwaków w
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
// Kazda rzecz siedzi na wlasnym przelaczniku w panelu deweloperskim - to
// wciaz szukanie formy, a nie gotowa decyzja.
const TIME_DEFAULTS = {
    hover: true,        // godzina w punkcie pod kursorem + przyjazd do celu
    bar: false,         // ...razem z paskiem: jaka to czesc najszybszej trasy
    ends: false,        // kropka dokladnie w punkcie, ktorego dotyczy godzina
    chips: false,       // godzina malym drukiem pod numerkiem w grupce
    headline: true,     // pasek nad mapa: najszybciej tyle, pokazane do tyle
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
    center: true,      // kropka węzła: środek wszystkich słupków zamiast peronu
    start: false,      // wyróżnienie przystanku startowego
    tipCursor: true,   // dymek przy kursorze
    tipPanel: true,    // okienko w rogu ekranu, zostaje po zejściu kursora
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

function clearFlow() {
    if (flowLayer) { map.removeLayer(flowLayer); flowLayer = null; }
    if (flowLabelLayer) { map.removeLayer(flowLabelLayer); flowLabelLayer = null; }
    if (flowDotLayer) { map.removeLayer(flowDotLayer); flowDotLayer = null; }
    hoveredStopDot = null;
    flowParts = [];
    flowHits = [];
    lastFlow = null;
    clearFlowHover();
    hideSidePanel();
    timetableTarget = null;
    renderTimeHeadline();
    setBaseDim(false);
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
    if (flowDotLayer) map.removeLayer(flowDotLayer);
    hoveredStopDot = null;
    flowDotLayer = L.layerGroup(flowStopDots(flow.nodes, flow.deadline_sec)).addTo(map);
    placeLineLabels();
    renderTimeHeadline();
    if (selectedJourney !== null) dimFlow(true);
    seedStartPanel();
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
// jeszcze rysuje (czyli dokładnie to, co ustawia suwak okna czasowego).
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
        const latlngs = leg.path.map(p => L.latLng(p));
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
    const chips = ((flow.fastest && flow.fastest.legs) || []).map(leg =>
        `<span class="line-chip ${esc(leg.kind)}">${esc(leg.num)}</span>`).join('');
    el.innerHTML =
        `<span class="headline-best" tabindex="0">Najszybciej o `
        + `<b>${esc(flow.best_arrival)}</b>, w <b>${esc(fmtMins(flow.best_sec))}</b>${chips}</span>`
        + `<span class="headline-sep">·</span>`
        + `<span class="headline-limit">mapa pokazuje do `
        + `<b>${esc(flow.deadline)}</b>, w <b>${esc(fmtMins(flow.limit_sec))}</b></span>`;
    el.hidden = false;
    const best = el.querySelector('.headline-best');
    best.addEventListener('mouseenter', showFastest);
    best.addEventListener('mouseleave', hideFastest);
    best.addEventListener('focus', showFastest);
    best.addEventListener('blur', hideFastest);
}

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
    i godzina nigdy nie moga powiedziec czegos innego.

    Pasek daje "18 min" skale: sama liczba nie mowi, czy to kawalek drogi, czy
    prawie cala. Odniesieniem jest najszybsza trasa (best_sec), nie okno mapy -
    okno rusza sie suwakiem, wiec pasek liczony wzgledem niego zmienialby
    dlugosc przy samym "pokaz wiecej", nic nie mowiac o czasie (ten sam powod,
    dla ktorego jasnosc odnosi sie do best_arr, patrz kontrakt p.9). */
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
    const total = lastFlow && lastFlow.best_sec;
    if (timeOpts.bar && total) {
        // Minimum 2%, zeby bardzo krotka reszta drogi nie wyszla paskiem o
        // zerowej szerokosci - to czyta sie jak "brak danych", nie jak "blisko".
        const pct = Math.max(2, Math.min(100, Math.round((100 * left) / total)));
        html += `<span class="flow-tip-bar"><i style="width:${pct}%"></i></span>`
            + `<span class="flow-tip-share">${pct}% najszybszej trasy `
            + `(${esc(fmtMins(total))})</span>`;
    }
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
    // Korytarz przejmuje okienko od kropki - inaczej po zejściu z kropki na
    // linię w rogu wisiałaby dalej tablica odjazdów sprzed ruchu myszy.
    timetableTarget = null;
    showSidePanel(html);
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

function timetableHtml(data) {
    if (data.error) return `<div class="tt-note">${esc(data.error)}</div>`;
    const head = `<div class="tt-head"><span class="tt-stop">${esc(data.stop)}</span>` +
                 `<span class="tt-from">od ${esc(data.from_time)}</span></div>`;
    if (!data.departures.length) {
        return head + '<div class="tt-note">Nic już stąd nie odjeżdża tego dnia.</div>';
    }
    const rows = summariseRepeats(data.departures).slice(0, TIMETABLE_ROWS).map(d =>
        `<li><span class="tt-time">${esc(d.time)}</span>` +
        `<span class="badge ${esc(d.mode)}">${esc(d.num)}</span>` +
        `<span class="tt-dir">${esc(d.headsign)}</span>` +
        // "0 min", nie "teraz": nagłówek mówi "od 16:57", a to nie jest
        // godzina zegarowa, tylko najwcześniejsza, o której da się tu być -
        // "teraz" obok niej znaczyłoby coś innego niż znaczy. Rytm dopisany
        // W TEJ SAMEJ linii, żeby powtarzająca się linia nie miała wiersza
        // wyższego od pozostałych.
        `<span class="tt-in">${esc(d.in_min < 1 ? 0 : d.in_min)} min` +
        (d.every_min ? `<small> · co ${esc(d.every_min)} min</small>` : '') +
        `</span></li>`
    ).join('');
    return head + `<ul class="tt-rows">${rows}</ul>`;
}

// Ile wierszy pokazuje dymek - z konfigu serwera (TIMETABLE_ROWS w .env,
// patrz config.timetable_rows), bo to rzecz do dostrojenia bez ruszania kodu.
const TIMETABLE_ROWS = Number(document.body.dataset.timetableRows) || 8;
const TIMETABLE_FETCH = 40;     // ...a tyle pobieramy, bo część odsiewamy

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
    const limit = new Map(lines.map(
        l => [lineKey(l), l.depart_by === undefined ? Infinity : l.depart_by]));
    return {...data, departures: data.departures.filter(d => {
        const key = lineKey({kind: d.mode, num: d.num, headsign: d.headsign});
        // Negacja, nie proste `<=`: oferta bez `depart_by` daje Infinity,
        // a odjazd bez `sec` - NaN. Każde porównanie z NaN jest fałszem, więc
        // przy `<=` wypadłyby wtedy WSZYSTKIE wiersze zamiast żadnego.
        return limit.has(key) && !(d.sec > limit.get(key));
    })};
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

    Odstęp to MEDIANA przerw, nie średnia: jeden nocny przeskok o godzinę nie
    ma prawa przesunąć liczby opisującej normalny takt.

    Kierunek jest częścią tożsamości linii - ta sama linia w drugą stronę to
    osobna opcja i osobny wiersz. */
function summariseRepeats(departures) {
    const groups = new Map();
    for (const d of departures) {
        const key = lineKey({kind: d.mode, num: d.num, headsign: d.headsign});
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(d);
    }
    const out = [];
    for (const list of groups.values()) {
        out.push(list.length > 1
            ? {...list[0], every_min: medianGapMin(list)}
            : list[0]);
    }
    return out.sort((a, b) => a.sec - b.sec);
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

function loadTimetable(dot, where, sec) {
    const date = $('date').value;
    const filtr = where.lines ? where.lines.map(lineKey).join('|') : '';
    const key = `${where.name || where.lat + ',' + where.lon}`
        + `@${date}@${sec}@${filtr}@${where.deadline || ''}`;
    const cached = timetableCache.get(key);
    if (cached !== undefined) { emitTimetable(dot, cached); return; }

    const query = where.name
        ? {stop: where.name, date, from_sec: sec}
        : {lat: where.lat, lon: where.lon, date, from_sec: sec};
    if (where.lines) query.limit = TIMETABLE_FETCH;
    emitTimetable(dot, TIP_LOADING);
    fetch('/api/timetable?' + new URLSearchParams(query))
        .then(r => r.json())
        .then(data => {
            const html = timetableHtml(data.error ? data : keepWithinHorizon(
                keepOfferedLines(data, where.lines), where.deadline));
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

const flowDotStyle = () => ({
    ...FLOW_DOT_STYLE,
    radius: Math.max(2, dotOpts.size - 1),
    weight: Math.min(3, Math.max(1.5, Math.round(dotOpts.size * 0.4))),
});

/** Kropki węzłów wachlarza - jedna na MIEJSCE, nie na słupek.

    Węzły liczy backend (patrz planner._transfer_nodes), a nie front z
    geometrii: plac z trzema peronami dostawał wtedy trzy kropki, każdą z inną
    zawartością, bo każdy peron to inne współrzędne i inna godzina. Grupowanie
    po miejscu jest w rozkładzie (gtfs._build_places), więc front nie ma go
    z czego odtworzyć - i nie powinien zgadywać po odległości na ekranie. */
function flowStopDots(nodes, deadline) {
    return (nodes || []).map(n => stopDot(
        nodePoint(n), {name: n.name, lines: n.lines, deadline, start: n.start},
        n.sec, flowDotStyle()));
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
                color: '#455a64', weight: 3, opacity: preview ? 0.7 : 1,
                dashArray: '1,6', lineCap: 'round', interactive: false,
            }));
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
}

function badgeHtml(leg) {
    return `<span class="badge ${leg.mode}" title="${esc(leg.line)}">${esc(leg.num)}</span>`;
}

function summaryHtml(legs) {
    const parts = [];
    let pendingWalk = false;
    for (const leg of legs) {
        if (leg.kind === 'walk') { pendingWalk = true; continue; }
        if (parts.length) parts.push(`<span class="hop${pendingWalk ? ' walk' : ''}"></span>`);
        pendingWalk = false;
        parts.push(badgeHtml(leg));
    }
    return parts.join('');
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
            rows.push(
                `<li class="tl-walk"><span class="tl-time"></span><span class="tl-dot"></span>` +
                `<span class="tl-body">Przejście na inne stanowisko · ok. ${leg.minutes} min</span></li>`,
            );
            return;
        }
        const stopWord = leg.mode === 'train'
            ? plural(leg.stops_count, 'stacja', 'stacje', 'stacji')
            : plural(leg.stops_count, 'przystanek', 'przystanki', 'przystanków');
        rows.push(stopRow(leg.from_time, leg.from, i === 0 ? 'first' : ''));
        rows.push(
            `<li class="tl-ride ${esc(leg.mode)}"><span class="tl-time"></span>` +
            `<span class="tl-dot"></span><span class="tl-body">` +
            `${badgeHtml(leg)} <span class="tl-headsign">${esc(leg.headsign)}</span>` +
            `<span class="tl-info">${leg.stops_count} ${stopWord} · ` +
            `${leg.minutes} min</span></span></li>`,
        );
        // Wysiadanie wypisujemy tylko wtedy, gdy nie zaraz po nim następuje
        // wsiadanie do kolejnej linii - inaczej ten sam przystanek byłby
        // w osi dwa razy pod rząd.
        const next = journey.legs[i + 1];
        if (!next || next.kind === 'walk') {
            rows.push(stopRow(leg.to_time, leg.to, next ? '' : 'last'));
        }
    });

    return `<ol class="timeline">${rows.join('')}</ol>
        <p class="j-collapse">
            Kliknij ponownie — albo w mapę obok trasy — żeby wrócić do
            wszystkich wariantów.
        </p>`;
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
        const lines = j.legs.filter(leg => leg.kind === 'ride')
                            .map(leg => leg.line).join(', ');
        const label = `${j.departure} – ${j.arrival}, ${j.duration_min} min, ` +
                      `${transfers}, ${lines}`;
        return `
            <li class="journey${selected ? ' selected' : ''}" data-index="${i}"
                tabindex="0" role="button" aria-expanded="${selected}"
                aria-label="${esc(label)}">
                <div class="j-head">
                    <span class="j-clock">${esc(j.departure)} – ${esc(j.arrival)}</span>
                    <span class="j-duration">${j.duration_min} min</span>
                </div>
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
        <ol class="journeys">${cards}</ol>
        <p class="results-foot">
            Na mapie widać wszystkie sensowne dojazdy — im jaśniejsza linia,
            tym lepsza opcja. Kliknij propozycję albo linię na mapie, żeby
            zobaczyć całą trasę.
        </p>`;
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
    if (!isPoint(sel.start) && !known.has(startInput.value.trim().toLowerCase())) {
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
    resultsBox.innerHTML = '';
    setTabCount(0);
}

function showError(message, suggestions) {
    let html = `<div class="notice error"><p>${esc(message)}</p>`;
    if (suggestions && suggestions.length) {
        html += '<p>Czy chodziło o:</p><ul>' + suggestions.map(name =>
            `<li><a href="#" data-name="${esc(name)}">${esc(name)}</a></li>`
        ).join('') + '</ul>';
    }
    resultsBox.innerHTML = html + '</div>';
}

function queryParams() {
    const params = new URLSearchParams({
        time: $('time').value,
        date: $('date').value,
        range_m: $('range').value,
        extra_pct: $('extra').value,
        extra_floor_sec: (Number($('extra-floor').value) * 60).toFixed(0),
        extra_cap_sec: (Number($('extra-cap').value) * 60).toFixed(0),
        transfer_gain_sec: (Number($('transfer-gain').value) * 60).toFixed(0),
    });
    if (isPoint(sel.start)) {
        params.set('start_lat', sel.start.lat);
        params.set('start_lon', sel.start.lon);
    } else {
        params.set('start', startInput.value);
    }
    if (isPoint(sel.end)) {
        params.set('end_lat', sel.end.lat);
        params.set('end_lon', sel.end.lon);
    } else {
        params.set('end', endInput.value);
    }
    return params;
}

/** Kanoniczne nazwy z API: podświetlenie startu/celu działa też przy ręcznym
    wpisaniu, nie tylko przy klikaniu w mapę - ale klikniętego punktu nie
    nadpisujemy nazwą z odpowiedzi. */
function adoptNames(data) {
    const previous = [sel.start, sel.end];
    if (!isPoint(sel.start)) { sel.start = data.start; startInput.value = data.start; }
    if (!isPoint(sel.end)) { sel.end = data.end; endInput.value = data.end; }
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
    odróżnić na ekranie. */
function showDegradedNotice() {
    resultsBox.insertAdjacentHTML('afterbegin',
        '<div class="notice error degraded"><p>Tryb awaryjny: nie udało się '
        + 'ułożyć wachlarza połączeń, mapa pokazuje tylko najszybszą trasę.'
        + '</p></div>');
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
    if (!journeys.length) {
        // Pusta lista przy NIEPUSTEJ mapie to nie brak połączeń -
        // mapa pokazuje je tuż obok. Komunikat nie ma prawa temu
        // przeczyć (zdarza się przy szerokim oknie, gdy graf urośnie
        // ponad budżet szukania w _enumerate_journeys).
        showError(data.segments.length
            ? 'Mapa pokazuje połączenia, ale przy tak szerokim oknie nie '
              + 'udało się z nich złożyć listy tras. Zawęź okno czasowe.'
            : 'Nie znaleziono żadnego połączenia w tym oknie czasowym.');
    } else {
        renderJourneys();
    }
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

const LAST_SEARCH_KEY = 'metal-planner:last-search';

function saveLastSearch() {
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
    if (startInput.value || endInput.value) return;
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
    if (!startInput.value || !endInput.value) return;
    const token = ++requestToken;
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
const STOP_KIND = new Map(STOP_ENTRIES.map(e => [e.name, e.kind]));
const MAX_SUGGESTIONS = 8;

// Składanie nazwy: bez ogonków i wielkości liter, ale ZNAK W ZNAK - długość
// się nie zmienia, więc pozycja trafienia w wersji złożonej wskazuje ten sam
// fragment oryginalnej nazwy (do podświetlenia).
const fold = text => text.normalize('NFD').replace(/[\u0300-\u036f]/g, '')
                         .toLowerCase().replace(/ł/g, 'l');
const FOLDED_NAMES = STOP_NAMES.map(fold);

/** Trafienia od początku nazwy przed trafieniami w środku - wpisując "grun"
    chcemy najpierw "Grunwaldzki", a nie "pl. Grunwaldzki" alfabetycznie. */
function suggestionsFor(query) {
    const needle = fold(query.trim());
    if (!needle) return [];
    const prefix = [], inside = [];
    STOP_NAMES.forEach((name, i) => {
        const at = FOLDED_NAMES[i].indexOf(needle);
        if (at === 0) prefix.push({name, at, len: needle.length});
        else if (at > 0) inside.push({name, at, len: needle.length});
    });
    return [...prefix, ...inside].slice(0, MAX_SUGGESTIONS);
}

function attachAutocomplete(input, onPick) {
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
        items = suggestionsFor(input.value);
        active = -1;
        if (!items.length) { close(); return; }
        list.innerHTML = items.map((item, i) => {
            const hit = esc(item.name.slice(item.at, item.at + item.len));
            const tag = STOP_KIND.get(item.name) === 'train'
                ? ' <span class="ac-tag">PKP</span>' : '';
            return `<li class="ac-item" role="option" aria-selected="false"
                        id="${list.id}-${i}" data-index="${i}">` +
                   `${esc(item.name.slice(0, item.at))}<mark>${hit}</mark>` +
                   `${esc(item.name.slice(item.at + item.len))}${tag}</li>`;
        }).join('');
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
        onPick();
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
            else { close(); search(); }
            break;
        }
    });

    list.addEventListener('mousedown', event => {
        const option = event.target.closest('.ac-item');
        if (!option) return;
        event.preventDefault();         // pole ma zostać z fokusem
        choose(Number(option.dataset.index));
    });
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

$('time-now').addEventListener('click', () => {
    $('time').value = new Date().toTimeString().slice(0, 5);
    if (startInput.value && endInput.value) search();
});

$('clear').addEventListener('click', () => {
    const previous = [sel.start, sel.end];
    sel = {start: null, end: null};
    startInput.value = '';
    endInput.value = '';
    updatePointMarker('start', null);
    updatePointMarker('end', null);
    showLocateMsg('');
    resetResults();
    restyle(...previous);
    setView('map');       // nową relację wybiera się na mapie
    forgetLastSearch();
});

// Suwaki panelu deweloperskiego: etykieta od razu, mapa i lista propozycji
// po krótkim debounce (odpowiedź z ciepłym cache to ~10 ms, więc działa
// "na żywo"). Jedno wspólne zapytanie (loadPlan) niesie obie rzeczy naraz,
// więc każdy suwak siłą rzeczy odświeża i mapę, i listę - nie ma już
// suwaków "tylko dla mapy".
//
// Wartości suwaków zapamiętujemy w localStorage (jeden klucz, mały JSON) -
// przeżywają odświeżenie strony i nowe wizyty, więc nie trzeba ustawiać
// preferencji od nowa za każdym razem.
const DEV_PREFS_KEY = 'metal-planner:dev-prefs';
const DEV_SLIDER_IDS = ['range', 'extra', 'extra-floor', 'extra-cap', 'transfer-gain'];

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

function liveSlider(inputId, valueId) {
    const input = $(inputId);
    const valueEl = $(valueId);
    let timer = null;
    input.addEventListener('input', () => {
        valueEl.textContent = input.value;
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
liveSlider('range', 'range-value');
liveSlider('extra', 'extra-value');
liveSlider('extra-floor', 'extra-floor-value');
liveSlider('extra-cap', 'extra-cap-value');
liveSlider('transfer-gain', 'transfer-gain-value');

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
    $('look-reset').addEventListener('click', () => {
        Object.assign(look, LOOK_DEFAULTS);
        for (const [id, key] of Object.entries(LOOK_KNOBS)) {
            $(id).value = look[key];
            show(id);
        }
        saveLookPrefs();
        applyLook();
    });
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
    'time-bar': 'bar',
    'time-ends': 'ends',
    'time-chips': 'chips',
    'time-show-headline': 'headline',
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
    'fold-time', 'fold-window', 'fold-transfer', 'fold-range',
    'fold-sound', 'fold-dots', 'look-section', 'fold-version',
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

}
