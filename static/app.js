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
        if (persist) saveUiState({devOpen: open});
    };
    setDev(!!uiState.devOpen, false);
    devToggle.addEventListener('click', () => setDev(devPanel.classList.contains('hidden')));
    $('dev-close').addEventListener('click', () => setDev(false));
}

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

const LINE_COLORS = {tram: '#c62828', bus: '#1565c0', other: '#6a1b9a'};
const MODE_LABEL = {tram: 'Tramwaj', bus: 'Autobus', other: 'Linia'};

// ------------------------------------------------------- markery na mapie ----

const markersByName = new Map();          // nazwa -> [L.circleMarker, ...]

const BASE_STYLE = {radius: 4, weight: 1, color: '#1565c0',
                    fillColor: '#42a5f5', fillOpacity: 0.8};
// Gdy pokazujemy przepływy, zwykłe przystanki schodzą na dalszy plan.
const DIM_STYLE = {radius: 2.5, weight: 0, color: '#90a4ae',
                   fillColor: '#90a4ae', fillOpacity: 0.25};
let baseDimmed = false;

function styleFor(name) {
    if (name === sel.start) return {radius: 8, weight: 2, color: '#1b5e20',
                                    fillColor: '#4caf50', fillOpacity: 1};
    if (name === sel.end) return {radius: 8, weight: 2, color: '#b71c1c',
                                  fillColor: '#ef5350', fillOpacity: 1};
    return baseDimmed ? DIM_STYLE : BASE_STYLE;
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
            const m = L.circleMarker([s.lat, s.lon], BASE_STYLE).addTo(map);
            m.bindTooltip(s.name);
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

map.on('click', e => {
    if (handleFlowClick(e)) return;   // klik w narysowany kurs otwiera propozycję
    pickEndpoint({lat: e.latlng.lat, lon: e.latlng.lng});
});

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
// Wartości dobrane przez użytkownika na żywo, na realnej mapie (2026-08-15),
// suwakami w panelu deweloperskim. Suwaki zostają w kodzie, ale są UKRYTE -
// jeden przełącznik niżej (LOOK_TUNING) przywraca całą sekcję, gdyby trzeba
// było dostroić to jeszcze raz.
const LOOK_DEFAULTS = {
    minOpacity: 0.1,      // krycie najbledszego kawałka (w=0)
    maxOpacity: 1,        // krycie najjaśniejszego (w=1)
    minWeight: 0.5,       // grubość najbledszego kawałka [px]
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
const LOOK_TUNING = false;
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

let flowLayer = null;
let flowParts = [];       // {layer, opacity, weight} - do przygaszania pod wybraną trasą
let flowHits = [];        // {seg, layer, casing, weight, latlngs} - kursor nad korytarzem
let flowLabelLayer = null;
let lastFlow = null;      // ostatnia odpowiedź /api/flow - do przerysowania bez zapytania

function clearFlow() {
    if (flowLayer) { map.removeLayer(flowLayer); flowLayer = null; }
    if (flowLabelLayer) { map.removeLayer(flowLabelLayer); flowLabelLayer = null; }
    flowParts = [];
    flowHits = [];
    lastFlow = null;
    clearFlowHover();
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
    placeLineLabels();
    if (selectedJourney !== null) dimFlow(true);
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
    let width = 0;
    for (const row of rows) {
        let w = 2 * CLUSTER_PAD_PX;
        row.forEach((l, i) => {
            w += CHIP_PAD_PX + CHIP_CHAR_PX * String(l.num).length + (i ? CHIP_GAP_PX : 0);
        });
        width = Math.max(width, w * scale);
    }
    return [width, (rows.length * CHIP_ROW_PX + 2 * CLUSTER_PAD_PX) * scale];
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
        markers.push(clusterMarker(map.containerPointToLatLng(c.at), c.roster, c.w));
    }

    flowLabelLayer = L.layerGroup(markers).addTo(map);
    for (const marker of markers) bindCluster(marker);
}

function clusterMarker(at, roster, weight) {
    let index = 0;
    const rows = clusterRows(roster).map(row =>
        '<span class="line-cluster-row">' + row.map(l =>
            `<span class="line-chip ${esc(l.kind)}" data-i="${index++}">${esc(l.num)}</span>`,
        ).join('') + '</span>',
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

/** Numer w grupce jest też przyciskiem: najechanie wskazuje DOKŁADNIE tę
    linię (nie trzeba się przełączać), a kliknięcie otwiera jej propozycję.
    Bez tego grupka mówiłaby, co tędy jedzie, ale nie dałaby tego wskazać. */
function bindCluster(marker) {
    const el = marker.getElement();
    if (!el) return;
    L.DomEvent.on(el, 'mouseover', ev => {
        const chip = ev.target.closest && ev.target.closest('.line-chip');
        if (chip) pickFromCluster(marker, Number(chip.dataset.i));
    });
    L.DomEvent.on(el, 'click', ev => {
        const chip = ev.target.closest && ev.target.closest('.line-chip');
        if (!chip) return;
        L.DomEvent.stop(ev);      // inaczej klik ustawiłby jeszcze punkt trasy
        const line = marker.roster[Number(chip.dataset.i)];
        const index = journeyForLine(line.num, line.kind, marker.getLatLng());
        if (index !== null) openJourney(index, true);
    });
}

let flowDimmed = false;

/** Wybrana trasa musi być czytelna, więc reszta wachlarza schodzi w tło. */
function dimFlow(dim) {
    flowDimmed = dim;
    for (const part of flowParts) {
        part.layer.setStyle({opacity: dim ? part.opacity * look.dimFactor : part.opacity});
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
        hit: hits.find(h => h.seg.num === l.num && h.seg.kind === l.kind) || null,
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

function flowPickHtml() {
    const options = flowPick.options;
    const sel = options[flowPick.index];
    const mode = MODE_LABEL[sel.kind] || MODE_LABEL.other;
    let html = `<span class="flow-tip-line ${esc(sel.kind)}">${esc(mode)} ${esc(sel.num)}</span>`;
    if (options.length > 1) {
        html += '<span class="flow-tip-row">' + options.map((o, i) =>
            `<span class="line-chip ${esc(o.kind)}${i === flowPick.index ? ' picked' : ''}">`
            + `${esc(o.num)}</span>`,
        ).join('') + '</span>';
        html += '<span class="flow-tip-hint">najedź na numer w grupce, żeby wskazać inną</span>';
    }
    if (flowPickAt && journeyForLine(sel.num, sel.kind, flowPickAt) !== null) {
        html += '<span class="flow-tip-hint">kliknij, aby otworzyć trasę</span>';
    }
    return html;
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
    if (!flowTooltip) {
        // setLatLng MUSI być przed addTo: Leaflet przy dodawaniu od razu liczy
        // pozycję dymka i bez współrzędnych rzuca wyjątkiem w środku addTo -
        // przez co dymek nigdy nie powstawał (a każdy ruch myszy nad korytarzem
        // próbował go stworzyć od nowa i wysypywał się w tym samym miejscu).
        flowTooltip = L.tooltip({direction: 'top', offset: [0, -6]})
            .setLatLng(flowPickAt).addTo(map);
    }
    flowTooltip.setLatLng(flowPickAt).setContent(flowPickHtml());
}

function clearFlowHover() {
    hideLineHighlight();
    if (flowTooltip) { map.removeLayer(flowTooltip); flowTooltip = null; }
    flowPick = null;
    flowPickAt = null;
}

function setFlowPick(options, at, index) {
    const key = corridorKey(options);
    if (!flowPick || flowPick.key !== key) {
        flowPick = {key, index: brightestOption(options), options};
    } else {
        flowPick.options = options;
        if (flowPick.index >= options.length) flowPick.index = brightestOption(options);
    }
    if (index !== undefined) flowPick.index = index;
    flowPickAt = at;
    renderFlowPick();
}

function handleFlowHover(e) {
    // Nad grupką numerów rządzi grupka (patrz bindCluster): kursor jest wtedy
    // kilka pikseli obok samej linii, więc szukanie po geometrii zgasiłoby
    // dopiero co wskazany numer.
    const target = e.originalEvent && e.originalEvent.target;
    if (target && target.closest && target.closest('.line-cluster')) return;
    const hits = flowHits.length ? flowHitsAt(e.containerPoint) : [];
    if (!hits.length) { clearFlowHover(); return; }
    setFlowPick(corridorOptions(hits), e.latlng);
}

function pickFromCluster(marker, index) {
    const at = marker.getLatLng();
    const hits = flowHitsAt(map.latLngToContainerPoint(at));
    if (!hits.length) return;
    const options = marker.roster.map(l => ({
        num: l.num,
        kind: l.kind,
        hit: hits.find(h => h.seg.num === l.num && h.seg.kind === l.kind) || null,
    }));
    setFlowPick(options, at, index);
}

/** Klik w kurs otwiera pasującą propozycję - najpierw dla linii AKTUALNIE
    wskazanej, żeby klik trafiał tam, gdzie patrzy podpowiedź; dopiero potem
    dla pozostałych kawałków pod kursorem. Klik obok zwraca false, a wywołujący
    ustawia punkt trasy zamiast tego. */
function handleFlowClick(e) {
    const tried = [];
    if (flowPick) tried.push(flowPick.options[flowPick.index]);
    for (const h of flowHitsAt(e.containerPoint)) tried.push({num: h.seg.num, kind: h.seg.kind});
    for (const line of tried) {
        const index = journeyForLine(line.num, line.kind, e.latlng);
        if (index !== null) { openJourney(index, true); return true; }
    }
    return false;
}

map.on('mousemove', handleFlowHover);
map.on('mouseout', clearFlowHover);

// Prawy przycisk myszy NIE ROBI TU NIC (usunięte 2026-08-15). Przechodził
// kiedyś na następną linię korytarza, ale wybieranie linii jest już w grupce
// numerów - najechanie na numer wskazuje dokładnie tę linię, bez zgadywania,
// ile razy trzeba kliknąć. Menu kontekstowe przeglądarki zostaje nietknięte.

// -------------------------------------------------- rysowanie jednej trasy ----

let journeyLayer = null;
let hoverLayer = null;

function legLayers(legs, {preview}) {
    const casings = [], lines = [], marks = [];
    const rideWeight = preview ? 5 : 7;

    for (const leg of legs) {
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
        // przesiadamy, bez czytania listy.
        for (const leg of legs) {
            if (leg.kind !== 'ride') continue;
            for (const point of [leg.path[0], leg.path[leg.path.length - 1]]) {
                marks.push(L.circleMarker(point, {
                    radius: 5, weight: 3, color: '#263238',
                    fillColor: '#fff', fillOpacity: 1, interactive: false,
                }));
            }
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
    // Po kliknięciu w mapę nie wyrywamy widoku spod kursora - kadrujemy
    // tylko wtedy, gdy trasa i tak nie mieści się w kadrze.
    const points = [...journey.legs.flatMap(leg => leg.path), ...endpointPoints()];
    if (!keepView || !map.getBounds().contains(L.latLngBounds(points))) fitTo(points);
}

function clearJourney() {
    if (journeyLayer) { map.removeLayer(journeyLayer); journeyLayer = null; }
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

/** Otwiera propozycję (z listy albo z mapy) - w przeciwieństwie do kliknięcia
    w kartę nigdy jej nie zamyka, więc klik w narysowaną trasę jest bezpieczny. */
function openJourney(index, keepView) {
    clearPreview();
    // Otwarcie trasy przy schowanym panelu byłoby niewidoczne - także wtedy,
    // gdy klikamy w trasę już wybraną.
    document.body.classList.remove('panel-hidden');
    if (selectedJourney === index) { scrollToSelected(); return; }
    selectedJourney = index;
    drawJourney(index, keepView);
    renderJourneys();
}

const NEAR_CLICK_PX = 40;   // "ta linia w tym miejscu" - w pikselach ekranu

/** Propozycja jeżdżąca daną linią: najbliższa klikniętemu miejscu, a gdy
    kliknięcie jest z dala od którejkolwiek - po prostu najlepsza z listy. */
function journeyForLine(num, mode, latlng) {
    const clicked = latlng && map.latLngToContainerPoint(latlng);
    let fallback = null, nearest = null, nearestDist = Infinity;
    journeys.forEach((journey, index) => {
        for (const leg of journey.legs) {
            if (leg.kind !== 'ride' || leg.num !== num || leg.mode !== mode) continue;
            if (fallback === null) fallback = index;
            if (!clicked) continue;
            for (const point of leg.path) {
                const dist = clicked.distanceTo(map.latLngToContainerPoint(point));
                if (dist < nearestDist) { nearestDist = dist; nearest = index; }
            }
        }
    });
    return nearestDist <= NEAR_CLICK_PX ? nearest : fallback;
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
        rows.push(stopRow(leg.from_time, leg.from, i === 0 ? 'first' : ''));
        rows.push(
            `<li class="tl-ride ${esc(leg.mode)}"><span class="tl-time"></span>` +
            `<span class="tl-dot"></span><span class="tl-body">` +
            `${badgeHtml(leg)} <span class="tl-headsign">${esc(leg.headsign)}</span>` +
            `<span class="tl-info">${leg.stops_count} ` +
            `${plural(leg.stops_count, 'przystanek', 'przystanki', 'przystanków')} · ` +
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
        range_m: $('range').value,
        extra_pct: $('extra').value,
        extra_floor_sec: (Number($('extra-floor').value) * 60).toFixed(0),
        extra_cap_sec: (Number($('extra-cap').value) * 60).toFixed(0),
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
function loadPlan(token, refit) {
    const params = queryParams();
    return Promise.all([fetch('/api/flow?' + params).then(r => r.json()), stopsReady])
        .then(([data]) => {
            if (token !== requestToken) return;
            if (data.error) {
                clearFlow();
                showError(data.error, data.suggestions);
                return;
            }
            adoptNames(data);
            drawFlow(data, refit);

            journeys = data.journeys;
            selectedJourney = null;      // nowa lista = stary wybór nieaktualny
            clearJourney();
            clearPreview();
            dimFlow(false);
            if (!journeys.length) {
                showError('Nie znaleziono żadnego połączenia w tym oknie czasowym.');
                return;
            }
            renderJourneys();
        });
}

const LAST_SEARCH_KEY = 'metal-planner:last-search';

function saveLastSearch() {
    try {
        localStorage.setItem(LAST_SEARCH_KEY, JSON.stringify({
            start: sel.start, end: sel.end, time: $('time').value,
        }));
    } catch {
        // localStorage niedostępny - wyszukiwanie działa dalej, po prostu się nie zapamięta
    }
}

/** Ostatnie wyszukiwanie (skąd/dokąd/godzina) wraca po odświeżeniu strony -
    tylko gdy pola są jeszcze puste (nie nadpisujemy tego, co user już
    zdążył wpisać, zanim ten kod się uruchomił). */
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
    if (saved.time) $('time').value = saved.time;
    updatePointMarker('start', sel.start);
    updatePointMarker('end', sel.end);
    restyle(sel.start, sel.end);
    search();
}

function search() {
    if (!startInput.value || !endInput.value) return;
    const token = ++requestToken;
    clearJourney();
    clearPreview();
    resultsBox.innerHTML = '<div class="notice">Szukam połączeń…</div>';
    saveLastSearch();
    loadPlan(token, true)
        // Wyniki są tym, po co się przyszło - na telefonie pokazujemy je od
        // razu (na szerokim ekranie i tak widać wszystko naraz).
        .then(() => { if (token === requestToken && journeys.length) setView('list'); })
        .catch(() => showError('Nie udało się połączyć z serwerem.'));
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
const STOP_NAMES = JSON.parse($('stop-names').textContent);
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
            return `<li class="ac-item" role="option" aria-selected="false"
                        id="${list.id}-${i}" data-index="${i}">` +
                   `${esc(item.name.slice(0, item.at))}<mark>${hit}</mark>` +
                   `${esc(item.name.slice(item.at + item.len))}</li>`;
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

$('clear').addEventListener('click', () => {
    const previous = [sel.start, sel.end];
    sel = {start: null, end: null};
    startInput.value = '';
    endInput.value = '';
    updatePointMarker('start', null);
    updatePointMarker('end', null);
    resetResults();
    restyle(...previous);
    setView('map');       // nową relację wybiera się na mapie
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
const DEV_SLIDER_IDS = ['range', 'extra', 'extra-floor', 'extra-cap'];

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

}
