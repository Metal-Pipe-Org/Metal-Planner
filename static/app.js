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
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    // Kafelki pobierane z CORS zamiast "no-cors": service worker dostaje
    // wtedy normalną odpowiedź zamiast nieprzejrzystej, a te przeglądarka
    // rozlicza z limitu miejsca po ~7 MB za sztukę niezależnie od rozmiaru.
    crossOrigin: true,
}).addTo(map);

const $ = id => document.getElementById(id);

// Każdy dymek na mapie (auto, rower, przystanek, pojazd) wychodził poza okno,
// gdy kropka stała przy brzegu - Leaflet ustawia go przy kropce i niczego nie
// pilnuje. Jedno miejsce na wszystkie, tak jak robią to biblioteki dymków
// (Floating UI): gdy dymek nie mieści się po swojej stronie kropki, otwiera
// się po przeciwnej (strzałka przechodzi razem z nim), a wzdłuż kropki tylko
// się przesuwa, ze strzałką (CSS) dalej wskazującą kropkę. Przesunięcie w osi
// otwierania oderwałoby strzałkę od kropki, więc go nie ma.
const TOOLTIP_MARGIN = 8;
const TOOLTIP_ARROW = 6;
const TOOLTIP_SIDES = {
    top: {flip: 'bottom', across: ['left', 'right'], away: [0, -1]},
    bottom: {flip: 'top', across: ['left', 'right'], away: [0, 1]},
    left: {flip: 'right', across: ['top', 'bottom'], away: [-1, 0]},
    right: {flip: 'left', across: ['top', 'bottom'], away: [1, 0]},
};
const setTooltipPosition = L.Tooltip.prototype._setPosition;

function tooltipVertical(side) {
    return side === 'top' || side === 'bottom';
}

function tooltipOverflow(box, side) {
    const [lo, hi, size] = tooltipVertical(side)
        ? [box.top, box.bottom, window.innerHeight]
        : [box.left, box.right, window.innerWidth];
    return Math.max(0, TOOLTIP_MARGIN - lo) + Math.max(0, hi - size + TOOLTIP_MARGIN);
}

L.Tooltip.prototype._setPosition = function (pos) {
    const el = this._container;
    el.style.translate = '';
    el.style.removeProperty('--tip-shift');
    setTooltipPosition.call(this, pos);
    let side = Object.keys(TOOLTIP_SIDES).find(s => el.classList.contains(`leaflet-tooltip-${s}`));
    if (!side) return;
    // Jeden odczyt układu na ścieżce bez odbicia - dymek przepływów jest
    // przestawiany przy każdym ruchu myszy.
    let box = el.getBoundingClientRect();
    let overflow = tooltipOverflow(box, side);
    if (overflow) {
        // Odsunięcie od kropki (offset) po zmianie strony musi iść w drugą
        // stronę, inaczej strzałka wjeżdża za kropkę.
        const {direction, offset} = this.options;
        const o = L.point(offset);
        const gap = Math.abs(tooltipVertical(side) ? o.y : o.x);
        const placeOn = s => {
            const [ax, ay] = TOOLTIP_SIDES[s].away;
            this.options.direction = s;
            this.options.offset = [ax * gap, ay * gap];
            setTooltipPosition.call(this, pos);
            this.options.direction = direction;
            this.options.offset = offset;
        };
        let best = side, last = side;
        for (const s of [TOOLTIP_SIDES[side].flip, ...TOOLTIP_SIDES[side].across]) {
            placeOn(s);
            last = s;
            const sOverflow = tooltipOverflow(el.getBoundingClientRect(), s);
            if (sOverflow < overflow) [best, overflow] = [s, sOverflow];
            if (!overflow) break;
        }
        if (best !== last) {
            if (best === side) setTooltipPosition.call(this, pos);
            else placeOn(best);
        }
        side = best;
        box = el.getBoundingClientRect();
    }
    const vertical = tooltipVertical(side);
    const [lo, hi, size, length] = vertical
        ? [box.left, box.right, window.innerWidth, box.width]
        : [box.top, box.bottom, window.innerHeight, box.height];
    const shift = Math.max(TOOLTIP_MARGIN - lo, Math.min(0, size - TOOLTIP_MARGIN - hi));
    if (!shift) return;
    el.style.translate = vertical ? `${shift}px 0` : `0 ${shift}px`;
    // Kropka tuż przy samej krawędzi bywa poza zasięgiem dymka - strzałka
    // staje wtedy na jego rogu zamiast wystawać poza ramkę.
    const reach = Math.max(0, length / 2 - TOOLTIP_ARROW - el.clientLeft
        - parseFloat(getComputedStyle(el).borderTopLeftRadius));
    el.style.setProperty('--tip-shift', `${Math.max(-reach, Math.min(reach, shift))}px`);
};

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

// Czy strona jest w trybie deweloperskim (DEV_MODE, patrz config.dev_mode).
const devMode = document.body.classList.contains('dev-mode');

// Ustawienia są schowane za przyciskiem, a strojenie algorytmu siedzi
// w nich, w sekcjach DEV.
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
        if (open) devPanel.dispatchEvent(new Event('settings-open'));
        if (persist) saveUiState({devOpen: open});
    };
    // Na telefonie panel przykrywa cały ekran - otwarty sam po odświeżeniu
    // zasłaniałby aplikację, o którą nikt jeszcze nie zapytał.
    setDev(!!uiState.devOpen && !phoneLayout.active(), false);
    devToggle.addEventListener('click', () => setDev(devPanel.classList.contains('hidden')));
    $('dev-close').addEventListener('click', () => setDev(false));
}

// „O aplikacji” pod ikoną (zgłoszenie #173).
const aboutDialog = $('about');
$('about-toggle').addEventListener('click', () => aboutDialog.showModal());
// Klik w przyciemnione tło obok karty zamyka ją jak ✕ - samo <dialog> umie
// tylko Esc, a na telefonie Esc nie ma.
aboutDialog.addEventListener('click', e => {
    if (e.target === aboutDialog) aboutDialog.close();
});
// Zgłoszenie od razu z wersją, urządzeniem i adresem: przy każdym błędzie
// pierwsze pytanie brzmiało „na której wersji i na czym?”. Treść składana
// w chwili kliknięcia, bo wersję service worker podaje dopiero po starcie.
// Typu zgłoszenia celowo nie narzucamy - przycisk jest dla wszystkich, nie
// tylko dla zespołu, więc nie wiadomo z góry, co to za zgłoszenie. Jedyne
// automatyczne rozróżnienie to etykieta "external" (workflow issue-triage.yml,
// po uprawnieniach autora, nie po treści zgłoszenia).
$('about-report').addEventListener('click', e => {
    const version = $('sw-version') ? $('sw-version').textContent.trim() : '';
    const body = [
        '**Co się stało:**', '', '', '**Czego się spodziewałem:**', '', '',
        '---',
        `Wersja: ${version && version !== '—' ? version : 'nieznana'}`,
        `Urządzenie: ${phoneLayout.active() ? 'telefon' : 'komputer'} (${navigator.userAgent})`,
        `Adres: ${location.href}`,
    ].join('\n');
    e.currentTarget.href = 'https://github.com/Metal-Pipe-Org/Metal-Planner/issues/new?body='
        + encodeURIComponent(body);
});

// Pole godziny to zwykły tekst, nie <input type="time"> - natywny widget w
// niektórych przeglądarkach (np. Safari) pokazuje AM/PM zależnie od ustawień
// regionalnych systemu i ignoruje atrybut lang strony, więc format 24h nie
// dało się wymusić inaczej niż samodzielnym formatowaniem.
const timeInput = $('time');
// Godzina i data wpisane przez pasażera zostają, jak je ustawił. Wszystko inne
// to „teraz": formularz otwarty od godziny nie ma szukać od godziny temu.
let timeChosen = false;
$('date').addEventListener('input', () => { timeChosen = true; });
timeInput.addEventListener('input', () => {
    timeChosen = true;
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
        // "Mapa" zostawia tryb taki, jaki jest: w rozkładach mapa pokazuje
        // wybrany rozkład (i ma „Zamknij rozkład"). Pusty rozkład nie ma czego
        // pokazać - wtedy "Mapa" wraca od razu do wyszukiwarki, zamiast
        // kazać zamykać coś, w czym nic nie wybrano.
        if (window.timetableMode) {
            if (view !== 'map') window.timetableMode.setMode(view === 'timetable');
            else if (!window.timetableMode.hasContent()) window.timetableMode.setMode(false);
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
// Start ustawiony przez ◎ - ten sam obiekt, nie osobna flaga: każde inne
// ustawienie startu (klik w mapę, wpisanie, zamiana) wstawia INNY obiekt, więc
// samo `sel.start === hereStart` mówi, że start to wciąż „moja pozycja".
let hereStart = null;
let autoRefreshTimer = null;
let autoRefreshOn = false;   // ◎ świeci - patrz syncAutoRefresh
let journeys = [];
// Godzina z formularza to wyjazd albo - po przełączeniu - godzina, na którą
// chce się być na miejscu (zgłoszenie #265). Z pokładu pojazdu przełącznik
// nie działa: tam się już jedzie, więc wyjścia nie ma kiedy wybierać.
let arriveBy = false;
let selectedJourney = null;   // indeks pozycji z listy albo null
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

/** esc() do wartości atrybutu: innerHTML zostawia cudzysłowy, a w atrybucie
    cudzysłów kończy wartość i resztę tekstu czyta się jak kod. */
function escAttr(text) {
    return esc(text).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function escClass(value) {
    return String(value ?? '')
        .split(/\s+/)
        .map(token => token.replace(/[^A-Za-z0-9_-]/g, ''))
        .filter(Boolean)
        .join(' ');
}

// `car` to ostatni etap propozycji z Traficarem (patrz planner._car_drive_leg) -
// fiolet, bo tym kolorem jeżdżą te auta i po nim się je poznaje na ulicy.
// `cable` to Polinka (zgłoszenie #165) - granat Politechniki, która ją prowadzi.
const LINE_COLORS = {tram: '#c62828', bus: '#1565c0', train: '#2e7d32',
                     cable: '#002147',
                     bike: '#ef6c00', car: '#7b2ff2', other: '#6a1b9a'};
const WALK_COLOR = '#455a64';   // dojscie pieszo - patrz --walk w style.css

// Autobusy spoza MPK (na razie siechnickie) mają w prototypie A ustawienia
// „Kolory linii” zieleń pociągów. Rodzaj mają ten sam, co MPK, a przewoźnika
// zna tylko lista linii (patrz timetables.all_lines). Bez bazy rozkładów
// listy nie ma na stronie.
const lineNamesJson = document.getElementById('line-names');
const LINE_LIST = lineNamesJson ? JSON.parse(lineNamesJson.textContent) : [];
const SIECHNICE_LINES = new Set(LINE_LIST.filter(line => line.operator === 'siechnice')
    .map(line => line.num));
const isSiechniceBus = (kind, num) => kind === 'bus' && SIECHNICE_LINES.has(String(num));

// Prototyp B ustawienia „Kolory linii”: każdy tramwaj i autobus ma swój kolor.
// Kolory nie są losowane każdy osobno, bo przy ~150 liniach wiele wypadało
// prawie takich samych: paleta ma tyle kolorów, ile linii na liście, każdy
// kolejny najdalszy (w OKLab, czyli tak, jak oko widzi różnicę) od już
// wybranych, a linie biorą je po kolei w porządku skrótu numeru ze stałym
// ziarnem - ta sama lista daje te same kolory wszędzie i po odświeżeniu.
// Z palety wypada to, co wygląda jak kolor roweru (pomarańcz), Traficara
// (fiolet aż po purpurę) i przejścia pieszo (szarość), oraz kolory za jasne,
// żeby linię było widać na mapie. Numer na kolorowej plakietce jest biały albo
// czarny - ten, który daje co najmniej 4,5:1; numer pisany kolorem linii na
// białym bierze, gdy trzeba, ciemniejszy wariant tego samego odcienia.
// Kolor wychodzi jako #rrggbb, bo schemat robi z niego id grotu.
const LINE_COLOR_SEED = 0x811c9dc5;
const OWN_PALETTE_KINDS = new Set(['tram', 'bus']);
const EXCLUDED_HUES = [[12, 42], [245, 315]];        // odcień HSL: pomarańcz, fiolet i purpura
const MIN_CHROMA = 0.09;                             // OKLCH; niżej to już szarość
// Kolory, do których linia nie może się zbliżyć nawet poza tymi odcieniami.
const RESERVED_COLORS = [LINE_COLORS.bike, LINE_COLORS.car, LINE_COLORS.other, WALK_COLOR];
const MIN_RESERVED_DISTANCE = 0.1;                   // w OKLab
const MAX_LUMINANCE = 0.45;                          // jaśniejszy ginie na mapie
const MIN_TEXT_CONTRAST = 4.5;
const byLineColors = () => setting('line-colors') === 'line';
const paletteColor = kind => byLineColors() && OWN_PALETTE_KINDS.has(kind);

const toLinear = c => c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
const fromLinear = c => c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055;

function hexRgb(hex) {
    return [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255);
}

function luminance(hex) {
    const [r, g, b] = hexRgb(hex).map(toLinear);
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

const contrast = (a, b) => {
    const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
    return (hi + 0.05) / (lo + 0.05);
};

/** OKLCH -> #rrggbb albo null, gdy kolor nie mieści się w sRGB. */
function oklchHex(l, c, h) {
    const a = c * Math.cos(h * Math.PI / 180), b = c * Math.sin(h * Math.PI / 180);
    const l_ = (l + 0.3963377774 * a + 0.2158037573 * b) ** 3;
    const m_ = (l - 0.1055613458 * a - 0.0638541728 * b) ** 3;
    const s_ = (l - 0.0894841775 * a - 1.2914855480 * b) ** 3;
    const rgb = [
        4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
        -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
        -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_,
    ];
    if (rgb.some(v => v < -1e-4 || v > 1 + 1e-4)) return null;
    return '#' + rgb.map(v => Math.round(255 * fromLinear(Math.min(1, Math.max(0, v))))
        .toString(16).padStart(2, '0')).join('');
}

function hexOklab(hex) {
    const [r, g, b] = hexRgb(hex).map(toLinear);
    const l = Math.cbrt(0.4122214708 * r + 0.5363288953 * g + 0.0514459929 * b);
    const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
    const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
    return [0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
            1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s];
}

function hslHue(hex) {
    const [r, g, b] = hexRgb(hex);
    const max = Math.max(r, g, b), d = max - Math.min(r, g, b);
    if (d === 0) return 0;
    const h = max === r ? (g - b) / d : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
    return (h * 60 + 360) % 360;
}

const lineSeedHash = num => {
    let h = LINE_COLOR_SEED;
    for (const ch of String(num)) h = Math.imul(h ^ ch.codePointAt(0), 0x01000193) >>> 0;
    return h;
};

/** Kandydaci z siatki OKLCH, którzy przechodzą wszystkie wykluczenia. */
function paletteCandidates() {
    const reserved = RESERVED_COLORS.map(hexOklab);
    const found = [];
    for (let l = 0.30; l <= 0.86; l += 0.02) {
        for (let c = MIN_CHROMA; c <= 0.32; c += 0.02) {
            for (let h = 0; h < 360; h += 3) {
                const hex = oklchHex(l, c, h);
                if (!hex || luminance(hex) > MAX_LUMINANCE) continue;
                const hue = hslHue(hex);
                if (EXCLUDED_HUES.some(([from, to]) => hue >= from && hue <= to)) continue;
                const lab = hexOklab(hex);
                if (reserved.some(r => Math.hypot(r[0] - lab[0], r[1] - lab[1], r[2] - lab[2]) < MIN_RESERVED_DISTANCE)) continue;
                found.push({hex, lab});
            }
        }
    }
    return found;
}

/** `count` kolorów, każdy następny najdalszy od już wybranych. */
function spreadPalette(count) {
    const pool = paletteCandidates();
    const dist = (p, q) => Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2]);
    const nearest = pool.map(() => Infinity);
    const chosen = [];
    let next = 0;
    while (chosen.length < count) {
        const pick = pool[next];
        chosen.push(pick.hex);
        next = 0;
        for (let i = 0; i < pool.length; i++) {
            nearest[i] = Math.min(nearest[i], dist(pool[i].lab, pick.lab));
            if (nearest[i] > nearest[next]) next = i;
        }
    }
    return {chosen, pool};
}

function textVariant(hex) {
    const [l, a, b] = hexOklab(hex);
    const c = Math.hypot(a, b), h = Math.atan2(b, a) * 180 / Math.PI;
    for (let dark = l; dark > 0; dark -= 0.01) {
        const candidate = oklchHex(dark, c, h) || oklchHex(dark, c * 0.8, h);
        if (candidate && contrast(candidate, '#ffffff') >= MIN_TEXT_CONTRAST) return candidate;
    }
    return '#000000';
}

function paletteEntry(hex) {
    return {
        color: hex,
        ink: contrast(hex, '#ffffff') >= MIN_TEXT_CONTRAST ? '#fff' : '#000',
        text: contrast(hex, '#ffffff') >= MIN_TEXT_CONTRAST ? hex : textVariant(hex),
    };
}

let linePalette = null;

/** {color, ink, text} linii o tym numerze; paleta liczy się raz, przy pierwszym użyciu. */
function paletteLineColor(num) {
    if (!linePalette) {
        const nums = [...new Set(LINE_LIST.filter(line => OWN_PALETTE_KINDS.has(line.mode))
            .map(line => line.num))].sort((a, b) => lineSeedHash(a) - lineSeedHash(b) || (a < b ? -1 : 1));
        const {chosen, pool} = spreadPalette(Math.max(nums.length, 1));
        linePalette = {byNum: new Map(nums.map((n, i) => [n, paletteEntry(chosen[i])])), pool, extra: new Map()};
    }
    const key = String(num);
    if (linePalette.byNum.has(key)) return linePalette.byNum.get(key);
    // Linia spoza listy (np. pojazd na żywo z numerem, którego rozkład nie zna)
    // bierze kolor z całej puli, tylko po skrócie numeru.
    if (!linePalette.extra.has(key)) {
        const {pool} = linePalette;
        linePalette.extra.set(key, paletteEntry(pool[lineSeedHash(key) % pool.length].hex));
    }
    return linePalette.extra.get(key);
}

function lineColor(kind, num) {
    if (paletteColor(kind)) return paletteLineColor(num).color;
    if (isSiechniceBus(kind, num)) return LINE_COLORS.train;
    return LINE_COLORS[kind] || LINE_COLORS.other;
}

/** Dla elementu z klasą rodzaju linii: arkusz bierze z `--lc` kolor linii
    przed kolorem rodzaju, z `--lc-ink` kolor numeru na nim, a z `--lc-text`
    kolor numeru pisanego tym kolorem na białym - więc linia w kolorze swojego
    rodzaju nic tu nie dostaje. */
function lineVar(kind, num) {
    if (paletteColor(kind)) {
        const {color, ink, text} = paletteLineColor(num);
        return `--lc: ${color}; --lc-ink: ${ink}; --lc-text: ${text}; `;
    }
    return isSiechniceBus(kind, num) ? `--lc: ${LINE_COLORS.train}; ` : '';
}

function lineStyle(kind, num) {
    const declared = lineVar(kind, num);
    return declared ? ` style="${escAttr(declared)}"` : '';
}
const MODE_LABEL = {tram: 'Tramwaj', bus: 'Autobus', train: 'Pociąg',
                    cable: 'Kolej linowa',
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
const cityStops = [];                     // {name: miejsce, lat, lon} - nazwy przy przybliżeniu

const BASE_STYLE = {radius: 4, weight: 1, color: '#1565c0',
                    fillColor: '#42a5f5', fillOpacity: 0.8};
// Stacje PKP (patrz pkp.py) - kolor spójny z plakietką linii kolejowej
// (--train w style.css), żeby na pierwszy rzut oka było widać, że to nie
// zwykły słupek MPK, zanim jeszcze ktoś najedzie kursorem na nazwę.
const TRAIN_STYLE = {radius: 5, weight: 1, color: '#1b5e20',
                     fillColor: '#2e7d32', fillOpacity: 0.85};
// Stacja Polinki (#165) - jak stacja kolejowa, w kolorze Polinki.
const CABLE_STYLE = {...TRAIN_STYLE, color: '#000d1f', fillColor: '#002147'};
// Gdy pokazujemy przepływy, zwykłe przystanki schodzą na dalszy plan.
const DIM_STYLE = {radius: 2.5, weight: 0, color: '#90a4ae',
                   fillColor: '#90a4ae', fillOpacity: 0.25};
let baseDimmed = false;
// W rozkładach mapa mówi o linii albo o przystanku, a nie o relacji wpisanej
// w wyszukiwarkę - zieleń startu i czerwień celu opisują wtedy pytanie,
// którego na ekranie nie ma (patrz suspendPlanner).
let plannerSuspended = false;

const ENDPOINT_STYLE = {
    start: {radius: 8, weight: 2, color: '#1b5e20', fillColor: '#4caf50', fillOpacity: 1},
    end: {radius: 8, weight: 2, color: '#b71c1c', fillColor: '#ef5350', fillOpacity: 1},
};

function styleFor(name) {
    // Przy narysowanej mapie start i cel mają po jednej własnej kropce
    // (stopDot, flowEndDots) - słupki powtarzałyby to samo kilka razy obok,
    // więc gasną jak wszystkie inne.
    if (!plannerSuspended && !baseDimmed) {
        if (name === sel.start) return ENDPOINT_STYLE.start;
        if (name === sel.end) return ENDPOINT_STYLE.end;
    }
    if (baseDimmed) return DIM_STYLE;
    const kind = stopKind.get(name);
    return kind === 'train' ? TRAIN_STYLE : kind === 'cable' ? CABLE_STYLE : BASE_STYLE;
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
// obwódka - łatwo odróżnić od słupka. Nieinteraktywne: punkt nie ma tablicy,
// więc nie ma czego otwierać, a zasłaniałby przystanek pod sobą (#363) -
// ani kursor, ani palec (tapCandidates pomija takie warstwy) go nie łapie.
const pointMarkers = {start: null, end: null};
const POINT_STYLE = {
    start: {radius: 9, weight: 3, dashArray: '2,4', color: '#1b5e20',
            fillColor: '#4caf50', fillOpacity: 0.9},
    end: {radius: 9, weight: 3, dashArray: '2,4', color: '#b71c1c',
          fillColor: '#ef5350', fillOpacity: 0.9},
};

function updatePointMarker(slot, value) {
    // Każda zmiana startu przechodzi tędy - tu wiadomo, czy odświeżanie
    // pozycji ma jeszcze o co pytać.
    if (slot === 'start') syncAutoRefresh();
    if (pointMarkers[slot]) {
        map.removeLayer(pointMarkers[slot]);
        pointMarkers[slot] = null;
    }
    if (isPoint(value)) {
        pointMarkers[slot] = L.circleMarker(
            [value.lat, value.lon], {...POINT_STYLE[slot], interactive: false},
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

/** Pasek komunikatów o warstwach mapy. Każda warstwa (źródło) ma własną listę
    komunikatów {level: 'warn' | 'info', text, setting?}; wszystkie idą pod sobą,
    każdy w osobnej linii. `warn` zapala też żółty przycisk warstwy, `info` go
    nie rusza. Klik w komunikat z `setting` otwiera ⚙ przy tym ustawieniu. */
const layerNotesEl = $('layer-notes');
const layerNotes = {};
const layerNoteButtons = {};

function setLayerNotes(source, notes) {
    layerNotes[source] = notes;
    const button = layerNoteButtons[source];
    if (button) button.classList.toggle('warn', notes.some(note => note.level === 'warn'));
    const all = Object.values(layerNotes).flat();
    layerNotesEl.hidden = !all.length;
    layerNotesEl.innerHTML = all.map(note =>
        `<div class="layer-note layer-note-${note.level}"` +
        (note.setting ? ` data-setting="${note.setting}" role="button" tabindex="0"` : '') +
        `>${esc(note.text)}</div>`).join('');
}

layerNotesEl.addEventListener('click', event => {
    const note = event.target.closest('.layer-note[data-setting]');
    if (note) jumpToSetting(note.dataset.setting);
});

layerNotesEl.addEventListener('keydown', event => {
    const note = event.target.closest('.layer-note[data-setting]');
    if (note && (event.key === 'Enter' || event.key === ' ')) {
        event.preventDefault();
        jumpToSetting(note.dataset.setting);
    }
});

const vehiclesToggle = $('vehicles-toggle');
layerNoteButtons.vehicles = vehiclesToggle;
let vehiclesOn = !!uiState.vehiclesOn;
let lastVehicles = [];
let vehiclesTimer = null;
// Portal sam aktualizuje dane co kilkanaście-kilkadziesiąt sekund - częstsze
// odpytywanie tylko obciążałoby serwer bez świeższych danych.
const VEHICLES_REFRESH_MS = 15000;

function vehicleIcon(v) {
    const arrow = v.heading == null ? '' :
        `<div class="vehicle-arrow ${escAttr(v.kind)}" style="${lineVar(v.kind, v.line)}transform: rotate(${v.heading}deg) translateY(-19px)"></div>`;
    return L.divIcon({
        className: 'vehicle-box',
        html: `<div class="vehicle-marker ${escAttr(v.kind)}"${lineStyle(v.kind, v.line)}>${esc(v.line)}</div>${arrow}`,
        iconSize: [36, 36],
        iconAnchor: [18, 18],
    });
}

function vehicleTooltipHtml(v) {
    return `<b>${esc(v.line)} → ${esc(v.to)}</b><br>następny: ${esc(v.next)}`;
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

/** Przystanki, które mapa rysuje każdej linii (klucz linii -> zbiór "lat,lon").
    Linia bez przystanków w odpowiedzi nie trafia do mapy: nie ma jak sprawdzić
    jej kierunku. W rozkładach nie ma podróży, więc nie ma i kierunku. */
function vehicleDrawnStops() {
    const drawn = new Map();
    if (window.timetableMode && window.timetableMode.vehicleLines()) return drawn;
    for (const hit of flowHits) {
        if (!hit.seg.num || !hit.seg.stops_t) continue;
        const key = vehicleKey(hit.seg.kind, hit.seg.num);
        if (!drawn.has(key)) drawn.set(key, new Set());
        for (const [lat, lon] of hit.seg.stops_t) drawn.get(key).add(lat + ',' + lon);
    }
    return drawn;
}

/** Jedzie w naszą stronę, gdy jego dalsza trasa (v.rest) mija przystanek
    narysowany na mapie tej linii. Bez rozpoznanego kursu - nie wiemy, więc
    zostaje. */
function vehicleGoesOurWay(v, drawn) {
    const stops = drawn.get(vehicleKey(v.kind, v.line));
    if (!stops || !v.rest) return true;
    return v.rest.some(([lat, lon]) => stops.has(lat + ',' + lon));
}

let vehicleStatsBox = null;

/** Statystyki warstwy pojazdów (ustawienie developerskie "vehicles-stats"):
    tylko to, co front już wie z odpowiedzi serwera. */
function showVehicleStats(shown, hiddenByDirection) {
    if (!vehiclesOn || !setting('vehicles-stats')) {
        if (vehicleStatsBox) vehicleStatsBox.hidden = true;
        return;
    }
    if (!vehicleStatsBox) {
        vehicleStatsBox = document.createElement('div');
        vehicleStatsBox.className = 'vehicle-stats';
        map.getContainer().appendChild(vehicleStatsBox);
    }
    const count = pick => lastVehicles.filter(pick).length;
    const withArrow = count(v => v.heading != null);
    const lines = [
        `Z kanału: ${lastVehicles.length}`,
        `ze strzałką: ${withArrow} (z kursu ${count(v => v.source === 'trip')}, z ruchu ${count(v => v.source === 'route')})`,
        `bez kierunku: ${lastVehicles.length - withArrow}`,
        `Pokazane: ${shown.length}, w tym ze strzałką ${shown.filter(v => v.heading != null).length}`,
    ];
    if (setting('vehicles-direction')) lines.push(`ukryte filtrem kierunku: ${hiddenByDirection}`);
    vehicleStatsBox.innerHTML = lines.map(esc).join('<br>');
    vehicleStatsBox.hidden = false;
}

function renderVehicles() {
    vehiclesLayer.clearLayers();
    if (!vehiclesOn) { showVehicleStats([], 0); return; }
    const only = vehiclesFilter();
    const byDirection = setting('vehicles-direction');
    const drawn = byDirection ? vehicleDrawnStops() : new Map();
    const shown = [];
    let hiddenByDirection = 0;
    for (const v of lastVehicles) {
        if (only && !only.has(vehicleKey(v.kind, v.line))) continue;
        if (!vehicleGoesOurWay(v, drawn)) { hiddenByDirection++; continue; }
        const marker = L.marker([v.lat, v.lon], {icon: vehicleIcon(v)}).addTo(vehiclesLayer);
        if (v.to) marker.bindTooltip(vehicleTooltipHtml(v));
        marker.tapRank = TAP_RANK.vehicle;
        shown.push(v);
    }
    showVehicleStats(shown, hiddenByDirection);
}

// Po tylu sekundach bez ruchu w danych MPK uznajemy je za nieaktualne. Normalnie
// pozycje zmieniają się co ~20 s; kanał potrafi zamilknąć na kilkanaście minut.
const VEHICLES_STALE_SEC = 30;
// Kiedy (czas przeglądarki) ostatnio przyszły dane, które wciąż się ruszały.
let vehiclesFreshAt = null;

function setVehiclesStale(ageSec) {
    setLayerNotes('vehicles', ageSec === null ? [] : [{
        level: 'warn',
        text: ageSec === undefined
            ? 'Brak danych o pojazdach'
            : `Dane nieaktualne od ${ageSec < 60 ? ageSec + ' s' : Math.round(ageSec / 60) + ' min'}`,
    }]);
}

/** Wiek danych po nieudanym zapytaniu: liczony od ostatnich udanych. */
function vehiclesFailed() {
    setVehiclesStale(vehiclesFreshAt === null
        ? undefined : Math.round((Date.now() - vehiclesFreshAt) / 1000));
}

function loadVehicles() {
    fetch('/api/vehicles').then(r => r.json()).then(data => {
        if (!vehiclesOn) return;
        if (data.error) return vehiclesFailed();   // zostają ostatnie znane pozycje
        lastVehicles = data.vehicles;
        renderVehicles();
        const staleSec = data.stale_sec ?? 0;   // brak pola = dane uznajemy za świeże
        vehiclesFreshAt = Date.now() - staleSec * 1000;
        setVehiclesStale(staleSec > VEHICLES_STALE_SEC ? staleSec : null);
    }).catch(() => {
        if (vehiclesOn) vehiclesFailed();
    });   // kolejna próba za VEHICLES_REFRESH_MS
}

// Karta w tle ma przycinane timery - po powrocie nie czekamy na następny tik.
document.addEventListener('visibilitychange', () => {
    if (vehiclesOn && !document.hidden) loadVehicles();
});

function setVehiclesOn(on) {
    vehiclesOn = on;
    saveUiState({vehiclesOn: on});
    if (vehiclesToggle) {
        vehiclesToggle.classList.toggle('active', on);
        vehiclesToggle.setAttribute('aria-pressed', String(on));
    }
    clearInterval(vehiclesTimer);
    vehiclesTimer = null;
    vehiclesFreshAt = null;
    setVehiclesStale(null);
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
// Włącznik NIGDY nie rusza samego wachlarza - na mapie dokłada i zdejmuje
// wyłącznie kropki. Rusza za to listę propozycji: zgaszony 🚗 zabiera z niej
// trasy kończące się autem, a 🚲 i ⚡ - trasy z rowerem swojego rodzaju
// (zgłoszenie #163). Lista ma mówić o tym samym, co widać na mapie.

const CARS_REFRESH_MS = 20000;    // tyle deklaruje feed Traficara (CARS_TTL_SEC)
const BIKES_REFRESH_MS = 60000;   // tyle deklaruje kanał WRM (`ttl`)

// Auta domyślnie włączone: na narysowanej mapie stały tam od zawsze (kontrakt
// p. 15), a zgaszenie ich przy okazji dokładania włącznika byłoby zabraniem
// czegoś, o co nikt nie prosił. Rower odwrotnie - to ten sam wybór, co dawne
// „mam konto w WRM": domyślnie nie, a kto go odhaczył wcześniej, ten ma go
// dalej (stara pamięć `bikes`). Pojazdy na żywo zostają zgaszone jak dotąd.
let carsOn = uiState.carsOn !== false;
// Pociągi jak auta: domyślnie są, a zgaszone znikają i z trasy, i z mapy.
let trainsOn = uiState.trainsOn !== false;
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
const trainsToggle = $('trains-toggle');
layerNoteButtons.cars = carsToggle;
const bikesToggle = $('bikes-toggle');
const bikesElectricToggle = $('bikes-electric-toggle');

/** Czy na ekranie stoi wachlarz - to on rozstrzyga, z którego źródła biorą
    się auta i rowery. W rozkładach i przed wyszukiwaniem go nie ma. */
const flowOnScreen = () => !!flowLayer;

function cityCarMarkers(cars) {
    return cars.map(car => carMarker(car,
        `<b>${carName(car)}</b><br>` +
        // Opis miejsca postoju bywa w feedzie pusty - pusta linijka w dymku
        // wyglądałaby jak brakująca treść.
        (car.where ? `${esc(car.where)}<br>` : '') +
        `Paliwo ${car.fuel}%, zasięg ${car.range} km<br>` +
        ogarniamText(car.ogarniam),
    ));
}

function cityBikeMarkers(stations, free) {
    const places = stations
        .filter(s => s.renting)
        .map(s => ({...s, loose: false}))
        .concat(free.map(b => ({...b, loose: true, bikes: 1})));
    return places.map(place => {
        const dot = L.circleMarker([place.lat, place.lon], bikeStyle(place, true)).bindTooltip(
            `<b>${esc(place.name || 'Rower luzem')}</b><br>${bikeCountText(place)}`,
            {direction: 'top', offset: [0, -4], opacity: 1},
        );
        dot.tapRank = TAP_RANK.shared;
        return dot;
    });
}

function loadCityCars() {
    fetch('/api/cars').then(r => r.json()).then(data => {
        if (data.error || !carsOn || flowOnScreen()) return;
        if (cityCarLayer) map.removeLayer(cityCarLayer);
        hideCarZone();
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

/** Komunikaty o autach Traficara przy wyniku (zgłoszenie #490). Są tylko przy
    mapie z wynikiem i włączonych autach: ostrzeżenie, gdy czas wyszukiwania jest
    poza zasięgiem od teraz (w przód i wstecz) i aut nie ma, oraz info o celu poza
    strefą - raz na całe wyszukiwanie. */
function paintCarsNote() {
    const notes = [];
    if (carsOn && !plannerSuspended && flowOnScreen()) {
        if (lastFlow.cars_beyond) {
            notes.push({level: 'warn', setting: 'cars-horizon',
                        text: `Auta Traficara tylko do ${setting('cars-horizon')} h od teraz`});
        }
        if (lastFlow.cars_dest_in_zone === false) {
            notes.push({level: 'info', text: 'Cel poza strefą Traficara'});
        }
    }
    setLayerNotes('cars', notes);
}

/** Postawienie warstwy aut od zera - po przełączniku, po nowej mapie i po jej
    zdjęciu. Zawsze zdejmuje obie wersje i stawia najwyżej jedną, więc nie da
    się zostać z autami z wyniku pod autami z całego miasta. */
function refreshCarLayer() {
    clearInterval(carsTimer);
    carsTimer = null;
    if (cityCarLayer) { map.removeLayer(cityCarLayer); cityCarLayer = null; }
    if (flowCarLayer) { map.removeLayer(flowCarLayer); flowCarLayer = null; }
    hideCarZone();
    refreshZoneLayer();
    paintCarsNote();
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
        flowBikeLayer = L.layerGroup([
            ...flowBikeMarkers(lastFlow.bike_places, lastFlow.bike_places_live),
            ...flowBikeSpotMarkers(lastFlow.bike_spots, lastFlow.bike_places,
                                   lastFlow.bike_places_live),
        ]).addTo(map);
        restBikeRides();
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
    replanInPlace();
}

function setTrainsOn(on) {
    trainsOn = on;
    saveUiState({trainsOn: on});
    paintLayerButton(trainsToggle, on);
    paintTrainStops();
    placeStopNames();
    replanInPlace();
}

/** Stacje PKP siedzą w tej samej warstwie co słupki - zgaszony filtr zdejmuje
    z niej tylko je. */
function paintTrainStops() {
    for (const [name, markers] of markersByName) {
        if (stopKind.get(name) !== 'train') continue;
        for (const m of markers) {
            if (trainsOn) m.addTo(stopsLayer); else stopsLayer.removeLayer(m);
        }
    }
}

/** Jeden rodzaj roweru. Warstwa świeci, gdy świeci choć jeden - więc
    zgaszenie ostatniego gasi rower w całości, tak jak dawniej jeden włącznik.

    Zmienia nie tylko mapę, ale i sam wynik z serwera: ten włącznik mówi też
    „mam konto w WRM", a rodzaj rozstrzyga, które miejsca w ogóle są
    kandydatami - na mapie i w propozycjach z rowerem (patrz bikes.ma_rodzaj). */
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
    paintBikeButtons();
    refreshBikeLayer();
    replanInPlace();
}

/** Rower w całości - oba rodzaje naraz. */
function setBikesOn(on) {
    bikeRegularOn = on;
    bikeElectricOn = on;
    saveUiState({bikeRegularOn: on, bikeElectricOn: on, bikesOn: on});
    bikesOn = on;
    paintBikeButtons();
    refreshBikeLayer();
    replanInPlace();
}

if (trainsToggle) {
    trainsToggle.addEventListener('click', () => setTrainsOn(!trainsOn));
    paintLayerButton(trainsToggle, trainsOn);
}

if (carsToggle) {
    carsToggle.addEventListener('click', () => setCarsOn(!carsOn));
    paintLayerButton(carsToggle, carsOn);
}

// Strefa, w której da się oddać auto Traficara (zgłoszenie #157). Na własnym
// panelu pod kropkami: ma być tłem, a nie zasłaniać aut ani łapać najechania.
map.createPane('traficarZone').style.zIndex = 350;
const zoneRenderer = L.canvas({pane: 'traficarZone'});
const ZONE_STYLE = {renderer: zoneRenderer, interactive: false, weight: 1.5,
                    color: '#6a1b9a', fillColor: '#ab47bc', fillOpacity: 0.12};
// Wycięte ze strefy miejsca, gdzie oddać nie wolno (Rynek i podobne).
const NO_ZONE_STYLE = {...ZONE_STYLE, color: '#c62828', fillColor: '#ef5350',
                       fillOpacity: 0.3};
// Dokąd przestawić auto z relokacją w „Ogarniam" - złoto, jak obwódka
// takiego auta (CAR_OGARNIAM_STYLE).
const RELOCATION_STYLE = {...ZONE_STYLE, color: '#f9a825', fillColor: '#fdd835',
                          fillOpacity: 0.35};
let zoneOn = !!uiState.zoneOn;
let zoneLayer = null;       // strefa na stałe, z przycisku 🅿
let carZoneLayer = null;    // strefa pod kursorem, przy jednym aucie
let hoveredCar = null;
let zoneData = null;        // granice zmieniają się rzadko - pobrane raz, starczą
const zoneToggle = $('zone-toggle');

/** Strefa z serwera - za pierwszym razem pobrana, potem z pamięci. */
function withZone(fn) {
    if (zoneData) { fn(zoneData); return; }
    fetch('/api/traficar-zone').then(r => r.json()).then(data => {
        if (data.error) return;
        zoneData = data;
        fn(data);
    }).catch(() => {});
}

function zoneShapes(data) {
    return [
        L.geoJSON({type: 'MultiPolygon', coordinates: data.end}, ZONE_STYLE),
        L.geoJSON({type: 'MultiPolygon', coordinates: data.no_end}, NO_ZONE_STYLE),
    ];
}

/** Przycisk 🅿 jest tylko wtedy, gdy włączono go w ustawieniach (DEV, „Rowery i Traficary”),
    a i wtedy - jak sama strefa - tylko przy zapalonych autach i poza
    rozkładami: strefa bez aut nie ma sensu (patrz refreshCarLayer, który
    to woła). */
function refreshZoneLayer() {
    const available = $('zone-button').checked && carsOn && !plannerSuspended;
    zoneToggle.hidden = !available;
    if (!available || !zoneOn) {
        if (zoneLayer) { map.removeLayer(zoneLayer); zoneLayer = null; }
        return;
    }
    if (zoneLayer) return;
    withZone(data => {
        if (!zoneOn || zoneLayer || zoneToggle.hidden) return;
        zoneLayer = L.layerGroup(zoneShapes(data)).addTo(map);
    });
}

function setZoneOn(on) {
    zoneOn = on;
    saveUiState({zoneOn: on});
    paintLayerButton(zoneToggle, on);
    refreshZoneLayer();
}

if (zoneToggle) {
    zoneToggle.addEventListener('click', () => setZoneOn(!zoneOn));
    paintLayerButton(zoneToggle, zoneOn);
}

/** Najechanie na auto pokazuje, gdzie da się je zostawić - zawsze, bez
    przycisku. Auto z relokacją w „Ogarniam" pokazuje dodatkowo, dokąd trzeba
    je przestawić, żeby zniżka się należała. */
function showCarZone(car) {
    hoveredCar = car;
    withZone(data => {
        if (hoveredCar !== car) return;
        hideCarZone();
        hoveredCar = car;
        const layers = zoneLayer ? [] : zoneShapes(data);
        if ((car.ogarniam || []).some(task => task.co === 'Relokacja')) {
            layers.push(L.geoJSON(
                {type: 'MultiPolygon', coordinates: data.relocation}, RELOCATION_STYLE));
        }
        carZoneLayer = L.layerGroup(layers).addTo(map);
    });
}

function hideCarZone() {
    hoveredCar = null;
    if (carZoneLayer) { map.removeLayer(carZoneLayer); carZoneLayer = null; }
}

/** Kropka auta z dymkiem i strefą pod kursorem - wspólne dla warstwy miasta
    i mapy przepływów. */
function carMarker(car, tooltipHtml) {
    const dot = L.circleMarker([car.lat, car.lon], carStyle(car))
        .bindTooltip(tooltipHtml, {direction: 'top', offset: [0, -4], opacity: 1})
        .on('mouseover', () => showCarOnMap(car, true))
        .on('mouseout', () => showCarOnMap(car, false));
    dot.tapRank = TAP_RANK.shared;
    dot.car = car;
    return dot;
}

/** Auto wskazane na mapie: jego strefa i jazda do niego - a na schemacie
    jego kropka i ta sama jazda (markSchemeCar), skądkolwiek je wskazano. */
function showCarOnMap(car, on) {
    if (on) { showCarZone(car); showCarRide(car); }
    else { hideCarZone(); hideCarRide(); }
    markSchemeCar(car, on);
}

/** To samo z zewnątrz mapy (kropka auta na schemacie): do tego dymek kropki
    auta na mapie. */
function pointCar(car, on) {
    showCarOnMap(car, on);
    if (!flowCarLayer) return;
    flowCarLayer.eachLayer(dot => {
        if (dot.car !== car) return;
        if (on) dot.openTooltip(); else dot.closeTooltip();
    });
}

/** Auto o krótką jazdę od startu (pole `ride`, patrz planner._short_hops):
    po najechaniu mapa rysuje tę jazdę, bo linii, która do niego wiezie, nie
    ma wśród narysowanych - nie prowadzi do celu. */
let carRideLayer = null;

function showCarRide(car) {
    hideCarRide();
    if (!car.ride || !car.ride.path || car.ride.path.length < 2) return;
    const latlngs = car.ride.path.map(p => L.latLng(p));
    carRideLayer = L.layerGroup([
        L.polyline(latlngs, {color: '#111', opacity: 0.85, weight: 9,
                             lineCap: 'round', lineJoin: 'round', interactive: false}),
        L.polyline(latlngs, {color: lineColor(car.ride.kind, car.ride.num), opacity: 1,
                             weight: 5, lineCap: 'round', lineJoin: 'round', interactive: false}),
        L.polyline([latlngs[latlngs.length - 1], L.latLng(car.lat, car.lon)],
                   {color: WALK_COLOR, weight: 4, opacity: 1, dashArray: '1,7',
                    lineCap: 'round', interactive: false}),
    ]).addTo(map);
}

function hideCarRide() {
    if (carRideLayer) map.removeLayer(carRideLayer);
    carRideLayer = null;
}

/** Jeden przycisk rowerów zamiast dwóch (opcja w „Wyglądzie aplikacji”): ⚡ znika, a 🚲
    włącza i gasi oba rodzaje naraz - i świeci, gdy świeci choć jeden. */
function bikesMerged() {
    return $('bikes-merged').checked;
}

function paintBikeButtons() {
    const merged = bikesMerged();
    bikesElectricToggle.hidden = merged;
    bikesToggle.title = merged ? 'Rowery miejskie (WRM)'
        : 'Rowery miejskie zwykłe, bez wspomagania (WRM)';
    bikesToggle.setAttribute('aria-label', merged ? 'Pokaż rowery miejskie'
        : 'Pokaż zwykłe rowery miejskie');
    paintLayerButton(bikesToggle, merged ? bikesOn : bikeRegularOn);
    paintLayerButton(bikesElectricToggle, bikeElectricOn);
}

if (bikesToggle) {
    bikesToggle.addEventListener('click', () => (bikesMerged()
        ? setBikesOn(!bikesOn) : setBikeKind('regular', !bikeRegularOn)));
}

if (bikesElectricToggle) {
    bikesElectricToggle.addEventListener(
        'click', () => setBikeKind('electric', !bikeElectricOn));
}

// Kadrowanie wyniku potrzebuje współrzędnych startu i celu, a te znamy
// dopiero z markerów - kto rysuje, czeka na to zapytanie.
const stopsReady = fetch('/api/stops')
    .then(r => r.json())
    .then(stops => {
        if (stops.error) { showError(stops.error); return; }
        for (const s of stops) {
            stopKind.set(s.name, s.kind);
            cityStops.push({name: s.place || s.name, lat: s.lat, lon: s.lon, kind: s.kind});
            const m = L.circleMarker([s.lat, s.lon], styleFor(s.name));
            m.cityStop = s.name;   // ranga stuknięcia zależy od stylu (patrz tapRank)
            // Sama etykieta dymka, w odróżnieniu od podpowiedzi w formularzu
            // (patrz STOP_KIND/attachAutocomplete), nie jedzie nigdzie jako
            // wyszukiwana nazwa - można doklejać "PKP" wprost do tekstu.
            m.bindTooltip(s.kind === 'train' ? `${s.name} PKP`
                : s.kind === 'cable' ? `${s.name} · kolej linowa` : s.name);
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
        paintTrainStops();
        stopsLayer.addTo(map);
        // Stan włącznika ◉ wraca z localStorage (patrz saveUiState).
        setVehiclesOn(vehiclesOn);
    });

/** Klik w mapę (pusty punkt albo słupek) uzupełnia brakujący koniec relacji.
    Nigdy nie kasuje gotowego wyszukiwania - od tego jest przycisk ✕; przy
    wybranej trasie pierwszy taki klik po prostu ją odznacza.

    O tym, który koniec jest już wybrany, mówią POLA, nie `sel`: nazwa
    wpisana ręcznie albo wzięta z podpowiedzi siedzi tylko w polu (sel zostaje
    null). Patrząc na samo `sel`, klik po wpisaniu celu brał go za pusty i
    kasował (#154). Z tego samego powodu ruszamy wyłącznie pole, które klik
    uzupełnia - drugiego nie przepisujemy z `sel`. */
function pickEndpoint(value) {
    // Okno „mojego miejsca" czeka na wskazanie - ten klik jest dla niego.
    if (placePickTook(value)) return;
    if (selectedJourney !== null) { deselectJourney(); return; }
    const hasStart = startInput.value.trim() !== '';
    const hasEnd = endInput.value.trim() !== '';
    if (hasEnd && (hasStart || onboardOn)) return;
    const previous = [sel.start, sel.end];
    hideRecallOffer();     // pasażer zaczął wskazywać nową relację
    // Z pokładu pojazdu startu się nie klika - startem jest pojazd - więc
    // każdy klik w mapę wskazuje cel.
    if (onboardOn || hasStart) {
        if (!onboardOn && samePlace(value, sel.start)) return;
        sel.end = value;
        endInput.value = displayValue(sel.end);
        updatePointMarker('end', sel.end);
    } else {
        sel.start = value;
        startInput.value = displayValue(sel.start);
        updatePointMarker('start', sel.start);
    }
    restyle(...previous, sel.start, sel.end);
    if (endInput.value && (onboardOn ? onboardReady() : startInput.value)) search();
}

/** Czy tryb rozkładów przejął ten klik. Pusty punkt mapy nie znaczy tam nic -
    rozkład ma przystanek albo linię, nie współrzędne - więc klik poza słupkiem
    jest po prostu ignorowany. */
function timetableTook(stopName) {
    return document.body.classList.contains('mode-timetable')
        && !!(window.timetableMode && window.timetableMode.pickStop(stopName));
}

map.on('click', e => {
    // Leaflet nie wysyła click po przeciągnięciu, więc to jest czyste stuknięcie
    // (phone.js zwija po nim rozwinięty formularz).
    document.dispatchEvent(new Event('planner:map-tap'));
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

// Pozycja tuż przy słupku (#200). Punkt z mapy płaci za dojście jak każde
// inne przejście, minimum trzy minuty - słusznie, bo GPS myli się o
// kilkadziesiąt metrów i nie wie, po której stronie ulicy stoimy. Pasażer to
// wie, więc pytamy jego: "tak" zamienia punkt na sam przystanek, a ten liczy
// wszystkie swoje słupki bez dojścia. Sama reguła dojścia zostaje jedna.
const STANDING_M = 50;
const locateOffer = $('locate-offer');
const locateOfferName = $('locate-offer-name');
let offeredStop = null;   // {name, point} - na który punkt padło pytanie

function hideLocateOffer() {
    offeredStop = null;
    locateOffer.hidden = true;
}

function offerNearestStop(point) {
    const here = L.latLng(point.lat, point.lon);
    let best = null;
    let bestM = STANDING_M;
    for (const [name, markers] of markersByName) {
        for (const m of markers) {
            const d = here.distanceTo(m.getLatLng());
            if (d <= bestM) { best = name; bestM = d; }
        }
    }
    if (!best) return;
    offeredStop = {name: best, point};
    locateOfferName.textContent = best;
    locateOffer.hidden = false;
}

$('locate-offer-yes').addEventListener('click', () => {
    const {name, point} = offeredStop;
    hideLocateOffer();
    // Start mógł się w międzyczasie zmienić (zamiana, wpisanie) - wtedy
    // odpowiedź dotyczy już nieaktualnego pytania.
    if (!samePlace(sel.start, point)) return;
    sel.start = name;
    startInput.value = displayValue(name);
    updatePointMarker('start', null);
    restyle(name);
    if (endInput.value) search();
    else focusEnd();
});

$('locate-offer-no').addEventListener('click', hideLocateOffer);
startInput.addEventListener('input', hideLocateOffer);

/** Gotowy start przy pustym celu: kursor sam przechodzi do „Dokąd" (#275),
    jak w Jakdojade. Wpisanego celu nie ruszamy - wtedy start od razu szuka.
    Klik w mapę tego nie woła: zostawia mapę do wskazania celu, a klawiatura
    telefonu zasłoniłaby jej pół (i pytanie „Stoisz na…"). ◎ woła, bo jest
    wyborem startu jak podpowiedź (#319). */
function focusEnd() {
    if (!endInput.value.trim()) endInput.focus();
}

/** Przycisk ◎ - w przeciwieństwie do kliknięcia w mapę nadpisuje start, który
    już był (o to się prosi, klikając go), ale celu nie rusza. */
function useMyLocation(point, {fromLink = false} = {}) {
    const previous = sel.start;
    hideRecallOffer();
    sel.start = hereStart = point;
    startInput.value = displayValue(point);
    updatePointMarker('start', point);
    restyle(previous, sel.start);
    offerNearestStop(point);
    if (endInput.value) {
        search({fromLink});
    } else {
        map.setView([point.lat, point.lon], 15);   // stąd wybiera się cel
        focusEnd();
    }
}

// Pozycja nie dotyka godziny: nieruszana jest „teraz" przy każdym szukaniu
// (patrz queryParams, #267), a wpisanej ręcznie nikt nie ma kasować po cichu.

const locatePeek = $('locate-peek');
const AUTO_REFRESH_MS = 30000;
const GEO_WAIT_MS = 10000;

// Jedno źródło pozycji dla całej strony (#410, #236): ciągły nasłuch, z którego
// czerpią kropka, ◎, odświeżanie i zapisane miejsca. Jednorazowe pytanie
// przy działającym nasłuchu czeka w Chromium na zmianę pozycji, więc stojący
// pasażer nie dostawał nic; nasłuch za to oddaje każdą zmianę, więc kto klika
// ◎ w drodze, dostaje to, gdzie jest teraz, a nie zapamiętaną pozycję.
const positionSource = {last: null, watching: false, waiting: []};

function watchPosition() {
    if (positionSource.watching) return;
    positionSource.watching = true;
    const watchId = navigator.geolocation.watchPosition(position => {
        positionSource.last = position;
        showYouAreHere(position);
        for (const {onPosition, timer} of positionSource.waiting.splice(0)) {
            clearTimeout(timer);
            onPosition(position);
        }
    }, error => {
        // Brak pomiaru nie jest awarią nasłuchu - czekający odpadną po czasie.
        if (error.code !== error.PERMISSION_DENIED) return;
        navigator.geolocation.clearWatch(watchId);
        positionSource.watching = false;
        // Zgoda odebrana w trakcie: stara pozycja nie może dalej odpowiadać na askPosition.
        positionSource.last = null;
        // Stara kropka wskazywałaby miejsce, którego pasażer już nie udostępnia.
        youAreHere.dot.remove();
        youAreHere.accuracy.remove();
        for (const {onError, timer} of positionSource.waiting.splice(0)) {
            clearTimeout(timer);
            onError(error);
        }
    }, {enableHighAccuracy: true, maximumAge: 5000});
}

/** Aktualna pozycja z nasłuchu od ręki; bez niej (pierwsze kliknięcie, zgoda
    dopiero co udzielona) start nasłuchu woła o zgodę i czeka na pierwszą. */
function askPosition(onPosition, onError) {
    if (positionSource.last) { onPosition(positionSource.last); return; }
    const waiter = {onPosition, onError};
    waiter.timer = setTimeout(() => {
        positionSource.waiting.splice(positionSource.waiting.indexOf(waiter), 1);
        onError({code: 3});
    }, GEO_WAIT_MS);
    positionSource.waiting.push(waiter);
    watchPosition();
}

const hereIsStart = () => sel.start !== null && sel.start === hereStart && !onboardOn;

/** Jeden zegar na całą funkcję: chodzi, dopóki ◎ świeci, start to wciąż
    „moja pozycja" i karta jest widoczna. Odświeżanie ma sens tylko przy takim
    starcie, więc jego utrata gasi też sam przycisk. */
function syncAutoRefresh() {
    // Funkcja ukryta za przełącznikiem DEV (#379); bez niego ◎ tylko ustawia start.
    const allowed = $('location-refresh').checked;
    if (!hereIsStart() || !allowed) autoRefreshOn = false;
    const hint = autoRefreshOn ? 'Wyłącz odświeżanie'
        : hereIsStart() && allowed ? 'Odświeżaj co 30 s' : 'Użyj mojej lokalizacji';
    const label = autoRefreshOn
        ? 'Wyłącz odświeżanie mojej lokalizacji'
        : hereIsStart() && allowed ? 'Odświeżaj moją lokalizację co 30 sekund'
                        : 'Ustaw start na mojej lokalizacji';
    // Drugi ◎ siedzi w zwiniętej karcie telefonu (patrz phone.css) i pokazuje
    // to samo, co pierwszy - tylko przy starcie z mojej pozycji.
    document.body.classList.toggle('here-start', hereIsStart() && allowed);
    for (const button of [locateButton, locatePeek]) {
        button.classList.toggle('active', autoRefreshOn);
        button.setAttribute('aria-pressed', String(autoRefreshOn));
        button.title = hint;
        button.setAttribute('aria-label', label);
    }
    const run = autoRefreshOn && !document.hidden;
    if (run && !autoRefreshTimer) {
        autoRefreshTimer = setInterval(refreshHere, AUTO_REFRESH_MS);
    } else if (!run && autoRefreshTimer) {
        clearInterval(autoRefreshTimer);
        autoRefreshTimer = null;
    }
}

function toggleAutoRefresh() {
    autoRefreshOn = !autoRefreshOn;
    syncAutoRefresh();
    if (autoRefreshOn) refreshHere();
}

/** Ta sama propozycja po odświeżeniu: godziny i linie, bez numeru z listy -
    lista po nowym przeliczeniu ma inną kolejność i długość. */
const journeySignature = j => [j.departure, j.arrival, ...j.legs
    .filter(leg => leg.kind === 'ride').map(leg => leg.line)].join('|');

/** Ciche odświeżenie: nowa pozycja, godzina „teraz" i to samo przeliczenie co
    suwaki w ⚙ (bez kadrowania, bez dźwięku, bez zwijania formularza). Wybrana
    propozycja zostaje, jeśli nowy wynik wciąż ją zawiera. */
function refreshHere() {
    if (!hereIsStart()) return;
    // Kółko wyszukiwania świeci się przy ręcznym szukaniu, a pole godziny w
    // trakcie wpisywania nie ma się zmieniać pod palcem.
    if (document.body.classList.contains('searching')
            || [$('time'), $('date')].includes(document.activeElement)) return;
    const asked = sel.start;
    askPosition(position => {
        // Start mógł się zmienić, gdy GPS mielił.
        if (sel.start !== asked) return;
        sel.start = hereStart = {lat: position.coords.latitude,
                                 lon: position.coords.longitude};
        startInput.value = displayValue(sel.start);
        updatePointMarker('start', sel.start);
        if (!endInput.value) return;
        const kept = selectedJourney === null ? null
            : journeySignature(journeys[selectedJourney]);
        // Wybór wraca zaraz po etapie z mapą i listą, nie po ostatnim etapie:
        // rowery i auta potrafią dojść dużo później.
        loadPlan('quiet', () => {
            if (kept === null) return;
            const index = journeys.findIndex(j => journeySignature(j) === kept);
            if (index < 0) return;
            selectedJourney = index;
            drawJourney(index, true);
            renderJourneys();
            renderVehicles();
        // Chwilowy brak sieci w tle nie jest błędem, o którym trzeba krzyczeć -
        // poprzedni wynik zostaje na ekranie, a następny obrót zegara spróbuje znów.
        }).catch(() => {});
    // Brak sygnału w tle: wynik zostaje, zegar próbuje za 30 s.
    }, () => {});
}

document.addEventListener('visibilitychange', syncAutoRefresh);
locatePeek.addEventListener('click', toggleAutoRefresh);

// Brak API (stara przeglądarka albo strona po http) - przycisk, który i tak
// nic by nie zrobił, lepiej schować.
if (!navigator.geolocation) {
    locateButton.hidden = true;
} else {
    locateButton.addEventListener('click', () => {
        // Drugie kliknięcie, gdy start już jest moją pozycją, nie pyta GPS-a o
        // start, tylko przełącza odświeżanie - pierwsze ma zostać zwykłym ◎.
        if (hereIsStart() && $('location-refresh').checked) {
            toggleAutoRefresh();
            return;
        }
        locateStart();
    });
}

function locateStart(onFail = () => {}, options = {}) {
    showLocateMsg('');
    hideLocateOffer();
    locateButton.disabled = true;      // GPS potrafi mielić kilka sekund
    // Odpowiedź GPS-a może przyjść po tym, jak pasażer sam ruszył „Skąd"
    // (wpisał, wybrał podpowiedź, wyczyścił, kliknął mapę) - jego ręka wygrywa.
    const startBefore = sel.start;
    const textBefore = startInput.value;
    const stale = () => sel.start !== startBefore || startInput.value !== textBefore;
    const finish = () => { locateButton.disabled = false; };
    askPosition(
        position => {
            finish();
            if (stale()) return;
            useMyLocation({lat: position.coords.latitude,
                           lon: position.coords.longitude}, options);
        },
        error => {
            finish();
            if (stale()) return;
            showLocateMsg(GEO_MESSAGES[error.code] || GEO_MESSAGES[2]);
            onFail();
        },
    );
}

/** Cel gotowy przy pustym starcie (#275): jedzie się stąd, czyli to samo co
    ◎ - łącznie z „Stoisz na przystanku…?" i szukaniem. Bez lokalizacji
    kursor idzie do „Skąd", bo bez startu nie ma czego szukać. */
function startFromHere() {
    if (navigator.geolocation) locateStart(() => startInput.focus());
    else startInput.focus();
}

// ------------------------------------------------ moja pozycja na mapie ----

// Kropka „tu jesteś" (#410) jest jednym z odbiorców nasłuchu pozycji (patrz
// positionSource). Nasłuch startuje przy wejściu tylko po udzielonej już zgodzie:
// samo wejście na stronę nie ma wywołać pytania.
const youAreHere = {
    dot: L.marker([0, 0], {
        icon: L.divIcon({className: 'you-are-here', iconSize: [18, 18]}),
        interactive: false, keyboard: false, zIndexOffset: 1000,
    }),
    accuracy: L.circle([0, 0], {radius: 0, interactive: false, color: '#1a73e8', weight: 1, opacity: 0.4,
        fillColor: '#1a73e8', fillOpacity: 0.12}),
};

function showYouAreHere(position) {
    const at = [position.coords.latitude, position.coords.longitude];
    youAreHere.dot.setLatLng(at);
    youAreHere.accuracy.setLatLng(at).setRadius(position.coords.accuracy);
    youAreHere.accuracy.addTo(map);
    youAreHere.dot.addTo(map);
}

if (navigator.geolocation && navigator.permissions) {
    navigator.permissions.query({name: 'geolocation'}).then(status => {
        const sync = () => { if (status.state === 'granted') watchPosition(); };
        sync();
        // Zgoda udzielona później (np. przy pierwszym ◎) włącza kropkę bez przeładowania.
        status.addEventListener('change', sync);
    });
}

// ---------------------------------------------------- kadrowanie widoku ----

function fitTo(points, maxZoom = 16) {
    if (!points.length) return;
    const panelVisible = !document.body.classList.contains('panel-hidden');
    const gutter = 40;
    // maxZoom: krótka trasa nie ma wjeżdżać w widok pojedynczej ulicy.
    const options = {maxZoom};
    if (phoneLayout.active()) {
        const inset = phoneLayout.mapInsets();
        options.paddingTopLeft = [gutter, inset.top];
        options.paddingBottomRight = [gutter, inset.bottom];
    } else {
        options.paddingTopLeft = [panelVisible ? sidebar.offsetWidth + gutter : gutter, gutter];
        // Schemat (gdy włączony) stoi po prawej - trasa ma się
        // zmieścić obok niego, nie pod nim.
        options.paddingBottomRight = [gutter + schemeInset(), gutter];
    }
    map.fitBounds(L.latLngBounds(points), options);
}

function placePoints(endpoint) {
    if (isPoint(endpoint)) return [[endpoint.lat, endpoint.lon]];
    return (markersByName.get(endpoint) || []).map(m => {
        const p = m.getLatLng();
        return [p.lat, p.lng];
    });
}

function endpointPoints() {
    return [sel.start, sel.end].flatMap(placePoints);
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
// --- USTAWIENIA: jeden opis, jeden zapis -------------------------------------
//
// Ustawienie to wiersz `.set-row` w index.html i NIC więcej nie trzeba opisywać:
// rodzaj wynika z kontrolki (przełącznik, suwak, lista wyboru), wartość
// domyślna z jej `checked`/`value`/`selected` w HTML-u, etykiety z `data-labels`,
// "tylko DEV" z klasy `dev-only` (nazwy, opisy, etykiety i DEV pochodzą z
// settings.json, serwer wstawia je do wiersza). Zapamiętywanie, przywracanie, pasek
// "zmienione", kopie pod nagłówkami i statystyki działają na tym opisie
// jednakowo dla każdego rodzaju. Aplikacja czyta wartość przez setting(id) w
// chwili użycia; skutek zmiany (jeśli jest) wpisuje się w SETTING_EFFECTS.
const settingsList = $('settings-list');
const settingsRows = [...settingsList.querySelectorAll('.set-row')];

// JEDYNY przełącznik strojenia wyglądu: `true` pokazuje sekcję „Wygląd mapy"
// w Ustawieniach Developerskich (i zaczyna pamiętać ustawienia suwaków),
// `false` chowa ją w całości i zostawia domyślne. Kod suwaków zostaje w repo
// celowo - patrz znaczniki TYMCZASOWE w index.html i style.css. Dźwięk działa
// tak samo (wiersze z `data-tuning`).
const LOOK_TUNING = true;
const TUNING = {look: LOOK_TUNING};

const SETTINGS_KEY = 'metal-planner:settings';

const readControl = control => (control.type === 'checkbox' ? control.checked
    : control.tagName === 'SELECT' ? control.value : Number(control.value));

function writeControl(control, value) {
    if (control.type === 'checkbox') control.checked = value === true;
    else if (control.tagName !== 'SELECT') control.value = String(value);
    // Zapamiętana opcja mogła zniknąć z listy.
    else if ([...control.options].some(option => option.value === value)) control.value = value;
}

const SETTINGS = new Map();
const SETTING_BY_CONTROL = new Map();
for (const row of settingsRows) {
    // Wybór pliku, przyciski i odnośniki to akcje, nie ustawienia z wartością;
    // wiersz akcji może mieć w środku select (wybór osoby), który też nim nie jest.
    if (row.classList.contains('set-action')) continue;
    const control = row.querySelector('input:not([type=file]), select');
    if (!control) continue;
    const info = {
        id: row.dataset.setting, row, control,
        kind: control.tagName === 'SELECT' ? 'select' : control.type,
        dflt: readControl(control),
        devOnly: row.classList.contains('dev-only'),
        expired: row.dataset.expired !== undefined,
    };
    SETTINGS.set(info.id, info);
    SETTING_BY_CONTROL.set(control, info);
}

const setting = id => readControl(SETTINGS.get(id).control);

// Wartość suwaka podpisana w `<output>`, jeśli ma inną postać niż liczba.
const SETTING_FORMATS = {'walk-pace': value => WALK_PACE_LABELS[value]};

function paintSettingOutput(info) {
    const out = info.row.querySelector('output');
    if (out) out.textContent = (SETTING_FORMATS[info.id] || String)(readControl(info.control));
}

function readStored(key) {
    try {
        return JSON.parse(localStorage.getItem(key)) || {};
    } catch {
        return {};       // localStorage niedostępny (tryb prywatny) albo zepsuty JSON
    }
}

// Dawne klucze, zanim wszystko zeszło się w jednym: klucz -> {pole -> id
// ustawienia}; null znaczy, że pola już nazywają się jak id.
const LEGACY_SETTINGS_KEYS = {
    'metal-planner:dev-prefs': null,
    'metal-planner:time-prefs': {
        hover: 'time-hover', ends: 'time-ends', chips: 'time-chips',
        headline: 'time-show-headline', ride: 'time-ride',
    },
    'metal-planner:dot-prefs': {
        size: 'dot-size', rows: 'dot-rows', ttPast: 'tt-past', center: 'dot-center',
        tipCursor: 'tip-cursor', tipPanel: 'tip-panel', bikeTimes: 'walk-time',
        bikeRides: 'bike-rides', carTimes: 'car-times', why: 'debug-why',
        namesZoom: 'names-zoom',
    },
    'metal-planner:look-prefs': {
        maxWeight: 'look-weight', dimFactor: 'look-dim', labelStep: 'look-label-step',
        labelScale: 'look-label-size', labelOpacity: 'look-label-op',
    },
};

// Jednorazowo: kto miał ustawienia pod dawnymi kluczami, nie traci ich.
function migrateLegacySettings() {
    try {
        if (localStorage.getItem(SETTINGS_KEY) !== null) return;
        const legacy = Object.keys(LEGACY_SETTINGS_KEYS);
        if (legacy.every(key => localStorage.getItem(key) === null)) return;
        const merged = {};
        for (const [key, names] of Object.entries(LEGACY_SETTINGS_KEYS)) {
            for (const [name, value] of Object.entries(readStored(key))) {
                const id = names ? names[name] : name;
                if (id) merged[id] = value;
            }
        }
        localStorage.setItem(SETTINGS_KEY, JSON.stringify(merged));
        for (const key of legacy) localStorage.removeItem(key);
    } catch {
        // localStorage niedostępny - i tak nie ma czego przenosić
    }
}

const storedSettings = () => readStored(SETTINGS_KEY);

function storeSetting(key, value) {
    const stored = storedSettings();
    stored[key] = value;
    try {
        localStorage.setItem(SETTINGS_KEY, JSON.stringify(stored));
    } catch {
        // localStorage niedostępny - ustawienie działa dalej, po prostu się nie zapamięta
    }
}

// Poza trybem deweloperskim (DEV_MODE, patrz config.dev_mode) wiersze DEV są
// schowane, więc z pamięci bierze się tylko to, co użytkownik w panelu widzi -
// schowanej opcji nie miałby czym cofnąć (zgłoszenie #175). Schowana wartość
// jest tylko pomijana, nie kasowana: po ponownym włączeniu DEV_MODE wraca.
// To samo dotyczy strojeń wyłączonych stałymi (TUNING). Ustawienie obsolete,
// które zniknęło z menu (miesiąc po dacie z settings.json), wraca do domyślnego:
// zapamiętana wartość jest pomijana, nie kasowana.
function restoreSettings() {
    const stored = storedSettings();
    for (const info of SETTINGS.values()) {
        const row = info.row;
        if (stored[info.id] === undefined || (info.devOnly && !devMode) || info.expired
            || (row.dataset.tuning && !TUNING[row.dataset.tuning])) continue;
        writeControl(info.control, stored[info.id]);
    }
}
migrateLegacySettings();
restoreSettings();

const differsFromDefault = info => readControl(info.control) !== info.dflt;

const look = {
    minOpacity: 0.3,      // krycie najbledszego kawałka (w=0)
    maxOpacity: 1,        // krycie najjaśniejszego (w=1)
    minWeight: 2,         // grubość najbledszego kawałka [px]
    casingFrom: 0.45,     // od tej jasności kawałek dostaje białą otoczkę (1 = nigdy)
};

const lookOpacity = rel => look.minOpacity + (look.maxOpacity - look.minOpacity) * rel;
const lookWeight = rel => look.minWeight + (setting('look-weight') - look.minWeight) * rel;

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
        stop: nearestStopName(seg, on.pos),
    };
}

/** Nazwa przystanku najbliższego punktowi pod kursorem - mierzona wzdłuż
    linii, nie w prostej: przy pętli i nawrocie najbliższy w prostej bywa
    przystankiem drugiego kierunku. */
function nearestStopName(seg, pos) {
    const at = seg._stopAt, names = seg.stops_n;
    if (!at || !names || names.length !== at.length) return null;
    let best = 0;
    for (let i = 1; i < at.length; i++) {
        if (Math.abs(at[i] - pos) < Math.abs(at[best] - pos)) best = i;
    }
    return names[best];
}

let flowLayer = null;
let flowParts = [];       // {layer, opacity, weight} - do przygaszania pod wybraną trasą
let flowHits = [];        // {seg, layer, casing, weight, latlngs} - kursor nad korytarzem
let flowLabelLayer = null;
let lastFlow = null;      // ostatnia odpowiedź /api/flow - do przerysowania bez zapytania
// To, o czym mówi pasek nad mapą: zwykle lastFlow, a w pierwszym etapie
// wyszukiwania sama najszybsza trasa, zanim jest jakakolwiek mapa.
let headlineFlow = null;
// Warstwy "czasu na mapie" - zadeklarowane razem z resztą warstw wachlarza,
// zanim cokolwiek zdąży je sprzątnąć (clearFlow leci niżej, ale wywołuje się
// też przy starcie).
let flowSpanLayer = null;   // kropki "stąd - dotąd" pod kursorem
let fastestLayer = null;    // najszybsza trasa spod paska nad mapą
let flowDotLayer = null;    // węzły przesiadkowe wachlarza (patrz flowStopDots)
let flowEndDots = null;     // kropki startu i celu (patrz endDots)
let flowCarLayer = null;    // auta car-sharingu w zasięgu (patrz flowCarMarkers)
let flowBikeLayer = null;   // rowery miejskie w zasięgu (patrz flowBikeMarkers)
let flowBikeRideLayer = null;   // strzałki przejazdów - domyślnie tylko pod kursorem
let flowLineStopLayer = null;   // przystanki linii wskazanej kursorem (patrz showLineHighlight)
let flowStopsLayer = null;      // przystanki narysowanych linii przy dużym przybliżeniu
let stopNameLayer = null;       // nazwy przystanków przy dużym przybliżeniu (placeStopNames)
let hoveredLineStop = null;     // kropka przystanku linii pod kursorem
let flowLineStops = null;       // {flow, stops} - przystanki całego wachlarza (patrz flowStopsOf)
let clusterBoxes = [];          // gdzie stoją grupki numerów - nazwy im ustępują

/** `cityLayers` false: mapa nowej relacji jest już w drodze, więc auta
    i rowery nie wracają na tę chwilę do całego miasta - zeszłyby zaraz
    znowu, przy mapie. */
function clearFlow(cityLayers = true) {
    if (flowLayer) { map.removeLayer(flowLayer); flowLayer = null; }
    if (flowLabelLayer) { map.removeLayer(flowLabelLayer); flowLabelLayer = null; }
    if (flowDotLayer) { map.removeLayer(flowDotLayer); flowDotLayer = null; }
    if (flowEndDots) { map.removeLayer(flowEndDots); flowEndDots = null; }
    if (flowCarLayer) { map.removeLayer(flowCarLayer); flowCarLayer = null; }
    if (flowBikeLayer) { map.removeLayer(flowBikeLayer); flowBikeLayer = null; }
    clearBikeRides();
    hoveredStopDot = null;
    flowParts = [];
    flowHits = [];
    lastFlow = null;
    headlineFlow = null;
    clearFlowHover();
    hideSidePanel();
    timetableTarget = null;
    renderTimeHeadline();
    setBaseDim(false);
    if (!cityLayers) return;
    // Zdjęta mapa = nie ma już pytania, na które odpowiadały tamte auta
    // i rowery. Włączona warstwa wraca wtedy do miejskiego feedu.
    refreshCarLayer();
    refreshBikeLayer();
    placeStopNames();
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
    headlineFlow = flow;
    clearFlowHover();
    setBaseDim(true);                            // to przy każdym ruchu suwaka za dużo
    const faint = [], casings = [], bright = [];

    for (const s of flow.segments) {        // posortowane po w rosnąco
        const rel = s.w;
        const color = lineColor(s.kind, s.num);
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
    if (flowEndDots) map.removeLayer(flowEndDots);
    flowEndDots = endDots(flow).addTo(map);
    placeLineLabels();
    renderTimeHeadline();
    if (selectedJourney !== null) dimFlow(true);
    // Okienko w rogu otwiera się samo przy nowej relacji. Przy przerysowaniu
    // tej samej (suwaki, kolejne etapy wyszukiwania) zostaje
    // to, co user w nim zostawił - także zamknięte.
    if (refit || !timetableTarget) seedStartPanel();
    // Nowa mapa = inny zestaw linii, więc i inne pojazdy dotyczą tego, co
    // widać (patrz vehiclesFilter). Przy zgaszonej warstwie to nic nie kosztuje.
    renderVehicles();
    if (!refit) return;

    let framing = setting('map-after-search');
    if (framing === 'auto') framing = phoneLayout.active() ? 'start' : 'all';
    if (framing === 'none') return;
    // Przybliżenie jak po wskazaniu startu ◎ (useMyLocation).
    if (framing === 'start') {
        fitTo(placePoints(sel.start), 15);
        return;
    }
    const points = flow.segments.flatMap(s => s.path);
    fitTo([...points, ...endpointPoints()]);   // start i cel zawsze w kadrze
}

// --- pasek nad mapą: ile w ogóle trwa ta podróż ----------------------------
//
// Jedyna liczba na mapie, której nie trzeba szukać kursorem - i jedyna, która
// odpowiada na pytanie zadawane najpierw: "ile to w ogóle zajmuje". Podaje
// dwie granice całego wachlarza: najszybszy dojazd i najpóźniejszy, jaki mapa
// jeszcze rysuje.
// Najechanie na najszybszy czas pokazuje, KTÓRĄ trasą się go osiąga - obie
// liczby przychodzą z serwera (best_sec/limit_sec/fastest), razem z gotową
// geometrią tej trasy. Obie strony paska podają godzinę i czas jazdy w tej
// samej kolejności, żeby dało się je czytać jednym spojrzeniem.

function showFastest() {
    hideFastest();
    const fastest = headlineFlow && headlineFlow.fastest;
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
            color: lineColor(leg.kind, leg.num), opacity: 1, weight: 5,
            lineCap: 'round', lineJoin: 'round', interactive: false,
        }));
    }
    fastestLayer = L.layerGroup([...halos, ...cores]).addTo(map);
}

function hideFastest() {
    if (fastestLayer) { map.removeLayer(fastestLayer); fastestLayer = null; }
}

/** Plakietki w pasku nad mapa - ta sama regula, co na karcie propozycji
    (patrz summaryHtml): dojscie OTWIERAJACE albo ZAMYKAJACE trase dostaje
    wlasny znak, bo inaczej pasek obiecuje wsiadanie na przystanku, ktorego
    nikt nie wskazywal; przejscie miedzy pojazdami zostaje kreska. Kazdy znak
    ma wlasny dymek, bo sam numer nie mowi, czy to autobus, czy tramwaj, ani
    gdzie sie przesiada. */
function headlineChips(legs) {
    const parts = [];
    const piece = (cls, tip, html, style = '') =>
        `<span class="${escClass(cls)}"${style} data-tip="${escAttr(tip)}">${html}</span>`;
    let pendingWalk = null;
    legs.forEach((leg, i) => {
        if (leg.kind === 'walk') {
            const first = i === 0, last = i === legs.length - 1;
            if (first || last) {
                const tip = first && last ? `Cała droga pieszo, ok. ${leg.minutes} min.`
                    : first ? `Dojście pieszo do pierwszego przystanku, ok. ${leg.minutes} min.`
                    : `Dojście pieszo od ostatniego przystanku do celu, ok. ${leg.minutes} min.`;
                parts.push(piece('headline-walk', tip, `${WALK_ICON}${leg.minutes}`));
            } else {
                pendingWalk = leg;
            }
            return;
        }
        if (pendingWalk) {
            parts.push(piece('headline-hop', 'Przesiadka'
                + (pendingWalk.to ? ` na przystanku ${pendingWalk.to}` : '')
                + `, ok. ${pendingWalk.minutes} min pieszo.`, ''));
        }
        pendingWalk = null;
        parts.push(piece(`line-chip ${leg.kind}`,
                         `${MODE_LABEL[leg.kind] || MODE_LABEL.other} ${leg.num}, ok. `
                         + `${fmtMins(leg.sec)} jazdy.`, esc(leg.num), lineStyle(leg.kind, leg.num)));
    });
    return parts.join('');
}

// Piktogramy paska - jednobarwne i na currentColor, z tej samej rodziny co
// WALK_ICON: emoji wyglądałyby na każdym systemie inaczej.
const HEADLINE_RIDE_ICON =
    '<svg class="headline-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false">'
    + '<rect x="3" y="1.8" width="10" height="10.4" rx="2" fill="none" stroke="currentColor"'
    + ' stroke-width="1.6"/><path d="M3.4 7.4h9.2M5.4 12.2v2M10.6 12.2v2" fill="none"'
    + ' stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';
const HEADLINE_MAP_ICON =
    '<svg class="headline-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false">'
    + '<path d="M1.8 3.6 5.8 2l4.4 1.8 4-1.6v10.2l-4 1.6-4.4-1.8-4 1.6z M5.8 2v10.2'
    + 'M10.2 3.8V14" fill="none" stroke="currentColor" stroke-width="1.4"'
    + ' stroke-linejoin="round"/></svg>';

function renderTimeHeadline() {
    const el = $('time-headline');
    if (!el) return;
    hideHeadlineTip();
    const flow = headlineFlow;
    if (!setting('time-show-headline') || !flow || typeof flow.best_sec !== 'number') {
        el.hidden = true;
        el.innerHTML = '';
        hideFastest();
        return;
    }
    const chips = headlineChips((flow.fastest && flow.fastest.legs) || []);
    // Kawałek paska z dymkiem. Opis jest zwykłym tekstem i do atrybutu idzie
    // przez escAttr w jednym miejscu, a nie sklejany z esc() po kawałku.
    const item = (cls, tip, html) =>
        `<span class="headline-item${cls}" data-tip="${escAttr(tip)}">${html}</span>`;
    // Zanim przyjdzie mapa, pasek zna tylko najszybszą trasę - zakresu mapy
    // jeszcze nie ma.
    const mapped = typeof flow.limit_sec === 'number';
    const showMapRange = $('headline-map-range').checked;
    // Sama jazda to osobna liczba na życzenie.
    const ride = setting('time-ride') && typeof flow.ride_sec === 'number'
        ? item('', `Sama jazda: ${fmtMins(flow.ride_sec)}, od odjazdu pierwszego pojazdu`
                   + ' do celu, bez czekania na niego.',
               `${HEADLINE_RIDE_ICON}<b>${esc(fmtMins(flow.ride_sec))}</b>`)
        : '';
    // Słowa zeszły do dymków (zgłoszenie #247): w pasku zostają godziny,
    // czasy i plakietki, czytane jednym spojrzeniem jak podsumowanie trasy
    // w Google Maps czy Jakdojade, a każdy kawałek tłumaczy się sam po
    // najechaniu albo stuknięciu.
    el.innerHTML =
        '<span class="headline-best" tabindex="0" aria-label="'
        + escAttr(flow.arrive_by
            ? `Najpóźniej: wyjeżdżasz o ${flow.starts}, dojeżdżasz o ${flow.best_arrival}`
              + ` - na miejscu do ${flow.arrive_by}`
            : `Najszybciej: wyjeżdżasz o ${flow.starts}, dojeżdżasz`
              + ` o ${flow.best_arrival}, za ${fmtMins(flow.best_sec)}`) + '">'
        // Plakietki na początku (decyzja użytkownika, #247): najpierw czym się
        // jedzie, potem kiedy.
        + (chips ? `<span class="headline-item headline-chips">${chips}</span>` : '')
        // "Za ile" tylko w dymku (uwaga do #247): na wierzchu każdy czytał
        // tę liczbę jako czas jazdy, a jest mierzona od godziny z formularza,
        // razem z czekaniem na pierwszy pojazd (zgłoszenie #143). Wyjazd to
        // najpóźniejszy, który wciąż daje najszybszy przyjazd - wcześniej
        // wychodzi się tylko po to, żeby gdzieś czekać.
        + item('', flow.arrive_by
                   ? `Wyjazd ${flow.starts}, przyjazd ${flow.best_arrival} - najpóźniejszy`
                     + ` wyjazd, z którym jesteś na miejscu do ${flow.arrive_by}.`
                   : `Wyjazd ${flow.starts}, przyjazd ${flow.best_arrival} - na miejscu`
                     + ` za ${fmtMins(flow.best_sec)}, licząc od ${flow.departure} (godzina`
                     + ' wyszukania).',
               `<b>${esc(flow.starts)}</b><span class="headline-arrow" aria-hidden="true">→</span>`
               + `<b>${esc(flow.best_arrival)}</b>`)
        + ride
        + '</span>'
        // Zakres mapy i jego przycisk to jedna całość: zawinięty pasek przenosi
        // je razem, a nie zostawia samotnego przycisku w drugiej linijce.
        // Sam zakres jest za ustawieniem deweloperskim; znacznik .headline-map
        // zostaje także bez niego, bo mówi, że mapa już przyszła.
        + (mapped
            ? '<span class="headline-map">'
              + (showMapRange
                  ? '<span class="headline-sep" aria-hidden="true"'
                    + ' data-tip="Z lewej najszybsza trasa, z prawej godziny, które obejmuje mapa."></span>'
                    + item(' headline-limit', `Mapa rysuje dojazdy wyjeżdżające od ${flow.map_from}`
                                              + ` i dojeżdżające najpóźniej o ${flow.deadline}`
                                              + (flow.arrive_by ? '.'
                                                 : `, czyli za ${fmtMins(flow.limit_sec)} od wyszukania.`),
                           `${HEADLINE_MAP_ICON}<b>${esc(flow.map_from)}</b>–<b>${esc(flow.deadline)}</b>`)
                  : '')
              + '</span>'
            : '');
    el.hidden = false;
    const best = el.querySelector('.headline-best');
    best.addEventListener('mouseenter', showFastest);
    best.addEventListener('mouseleave', hideFastest);
    best.addEventListener('mouseenter', highlightSchemeFastest);
    best.addEventListener('mouseleave', clearSchemeHighlight);
    best.addEventListener('focus', showFastest);
    best.addEventListener('blur', hideFastest);
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

// Dymki paska: ten sam ciemny dymek, co przy ⓘ w ustawieniach. Wisi na
// <body>, bo pasek jest przycięty do pigułki i przyklejony do krawędzi okna.
// Na telefonie nie ma najechania, więc dymek otwiera też stuknięcie -
// łapane w fazie przechwytywania, bo klik w najszybszą trasę przerywa
// bąbelkowanie (stopPropagation wyżej), a i tak ma pokazać swój opis.
const headlineTip = document.createElement('div');
headlineTip.className = 'headline-tip';
headlineTip.hidden = true;
document.body.appendChild(headlineTip);

function showHeadlineTip(item) {
    headlineTip.textContent = item.dataset.tip;
    headlineTip.hidden = false;
    const at = item.getBoundingClientRect();
    const tip = headlineTip.getBoundingClientRect();
    // Na komputerze pasek stoi u góry, na telefonie w doku na dole - dymek
    // idzie tam, gdzie jest miejsce.
    const below = at.bottom + 8 + tip.height <= innerHeight;
    headlineTip.style.top = `${below ? at.bottom + 8 : at.top - 8 - tip.height}px`;
    const centred = (at.left + at.right - tip.width) / 2;
    headlineTip.style.left = `${Math.max(8, Math.min(centred, innerWidth - tip.width - 8))}px`;
}

function hideHeadlineTip() {
    headlineTip.hidden = true;
}

{
    const bar = $('time-headline');
    const itemOf = event => event.target.closest('[data-tip]');
    bar.addEventListener('pointerover', event => {
        const item = itemOf(event);
        if (event.pointerType === 'mouse' && item) showHeadlineTip(item);
    });
    bar.addEventListener('pointerout', event => {
        if (event.pointerType === 'mouse' && itemOf(event)) hideHeadlineTip();
    });
    bar.addEventListener('focusin', event => {
        const item = itemOf(event);
        if (item) showHeadlineTip(item);
    });
    bar.addEventListener('focusout', hideHeadlineTip);
    bar.addEventListener('click', event => {
        const item = itemOf(event);
        if (item) showHeadlineTip(item);
    }, true);
    document.addEventListener('click', event => {
        if (!bar.contains(event.target)) hideHeadlineTip();
    });
    document.addEventListener('scroll', hideHeadlineTip, true);
}

// Odstęp paska od krawędzi okna i od tego, co może mu stanąć na drodze.
const HEADLINE_GAP = 16;

/** Dokąd z lewej i z prawej sięga to, na czym paskowi stawać nie wolno - ale
    tylko to, co leży na jego wysokości. Z lewej panel i pływające przyciski,
    z prawej panel ⚙ (okienko z rozkładem pasku nie przeszkadza - samo staje
    pod nim, patrz placeTimeHeadline). Mierzone, a nie wpisane liczbą: szerokość panelu
    zmienia suwak, a napis na przycisku trybu zmienia jego szerokość; wpisana
    liczba rozjeżdżała się z każdą taką zmianą. Schowany panel sam wyjeżdża
    poza ekran, więc przestaje być przeszkodą bez osobnej reguły. */
function headlineGuard(band) {
    const onBand = el => {
        const box = el.getBoundingClientRect();
        return box.bottom > band.top && box.top < band.bottom ? box : null;
    };
    let left = HEADLINE_GAP;
    for (const el of [sidebar, $('sidebar-toggle'), $('mode-toggle')]) {
        if (!el || el.hidden) continue;
        const box = onBand(el);
        if (box) left = Math.max(left, box.right + HEADLINE_GAP);
    }
    let right = window.innerWidth - HEADLINE_GAP;
    if (devPanel && !devPanel.hidden && !devPanel.classList.contains('hidden')) {
        const box = onBand(devPanel);
        if (box) right = Math.min(right, box.left - HEADLINE_GAP);
    }
    return {left, right};
}

/** Pasek stoi na środku OKNA - tam patrzy oko, a nie na środek wolnego
    skrawka mapy. Gdy wyśrodkowany wszedłby na panel, przyciski albo okienko
    z prawej, odsuwa się w bok dokładnie o tyle, o ile trzeba; gdy i wtedy brakuje mu
    miejsca, zawija się na kolejne linijki (flex-wrap) zamiast wystawać poza
    ekran. Idealny środek jest więc regułą, a nie obietnicą: przy wąskim oknie
    granica wygrywa - ale dopiero wtedy. */
function placeTimeHeadline() {
    const el = $('time-headline');
    if (flowPanel) flowPanel.style.top = '';
    if (!el || el.hidden) return;
    // Na telefonie pasek stoi w doku nad zakładkami - nie ma tam czego
    // omijać i całe ustawianie oddaje się arkuszowi (patrz phone.css).
    if (phoneLayout.active()) {
        el.style.maxWidth = '';
        el.style.left = '';
        return;
    }
    el.style.maxWidth = '';
    const guard = headlineGuard(el.getBoundingClientRect());
    el.style.maxWidth = (guard.right - guard.left) + 'px';
    // Szerokość po przycięciu: zawinięty pasek jest węższy, więc znów może
    // zmieścić się na środku.
    const width = el.getBoundingClientRect().width;
    const centred = (window.innerWidth - width) / 2;
    el.style.left = Math.max(guard.left, Math.min(centred, guard.right - width)) + 'px';
    // Pasek ma pierwszeństwo w górnym rzędzie: okienko z rozkładem schodzi
    // pod niego, a nie pasek pod okienko.
    if (flowPanel) flowPanel.style.top = el.getBoundingClientRect().bottom + HEADLINE_GAP + 'px';
}

window.addEventListener('resize', placeTimeHeadline);
// Panele zjeżdżają z animacją, więc miejsce na pasek zmienia się dopiero po niej.
sidebar.addEventListener('transitionend', placeTimeHeadline);
if (devPanel) devPanel.addEventListener('transitionend', placeTimeHeadline);

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
// Odstęp grupek i ich wielkość siedzą na suwakach (look-label-step, look-label-size) -
// "co ile numerów" i "jak duże numery" to dokładnie te dwie rzeczy, którymi
// reguluje się, jak natrętne są numery na mapie.
const labelStepPx = () => setting('look-label-step');
const labelRepeatPx = () => setting('look-label-step') * 1.6;  // ten sam skład nie częściej niż co tyle
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
    const scale = setting('look-label-size');   // numery rosną razem z suwakiem - i tak samo ich kolizje
    // Czas pod numerkiem powiększa grupkę w obu wymiarach, więc musi wejść
    // do POMIARU, nie tylko do rysowania - inaczej grupki zaczęłyby na siebie
    // wchodzić (kolizje liczą się z tego pudełka, patrz placeLineLabels).
    const chars = l => (setting('time-chips')
        ? Math.max(String(l.num).length, 5)   // "16:04" bywa szersze niż sam numer
        : String(l.num).length);
    const rowPx = CHIP_ROW_PX + (setting('time-chips') ? CHIP_TIME_ROW_PX : 0);
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
    clusterBoxes = [];
    // Nazwy przystanków liczą się PO grupkach, bo to one mają pierwszeństwo
    // (punkt 7 kontraktu) - stąd wołane tutaj, a nie z osobnego moveend.
    if (!flowHits.length) { placeStopNames(); return; }

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
            setting('time-chips') ? chipTimesAt(c.at, c.roster) : null,
        ));
    }

    flowLabelLayer = L.layerGroup(markers).addTo(map);
    for (const marker of markers) bindCluster(marker);
    clusterBoxes = boxes;
    placeStopNames();
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

/** Grupka numerów - TA SAMA na mapie i na plakietkach schematu: ten sam
    wygląd i to samo wskazywanie linii najechaniem na numer. */
function clusterHtml(roster, times) {
    let index = 0;
    const rows = clusterRows(roster).map(row =>
        '<span class="line-cluster-row">' + row.map(l => {
            const sec = times ? times[index] : null;
            // Slot jest celem myszy (patrz bindCluster) - dzięki temu wskazanie
            // linii działa tak samo, gdy kursor stoi na czasie pod numerem.
            const html = `<span class="line-chip-slot" data-i="${index++}" `
                + `data-num="${escAttr(l.num)}" data-kind="${escAttr(l.kind)}">`
                + `<span class="line-chip ${escClass(l.kind)}"${lineStyle(l.kind, l.num)}>${esc(l.num)}</span>`
                + (times
                    ? `<span class="chip-time">${sec === null ? '' : esc(fmtClock(sec))}</span>`
                    : '')
                + '</span>';
            return html;
        }).join('') + '</span>',
    ).join('');
    return `<span class="line-cluster">${rows}</span>`;
}

function clusterMarker(at, roster, weight, times) {
    const marker = L.marker(at, {
        icon: L.divIcon({
            className: 'line-cluster-anchor',
            html: clusterHtml(roster, times),
            iconSize: null,
        }),
        keyboard: false,
        opacity: (flowDimmed ? 0.25 : 0.65 + 0.35 * weight) * setting('look-label-op'),
    });
    marker.roster = roster;
    return marker;
}

/** Numer w grupce wskazuje się WYŁĄCZNIE najechaniem: kursor nad numerem
    podświetla DOKŁADNIE tę linię. Bez tego grupka mówiłaby, co tędy jedzie,
    ale nie dałaby tego wskazać. Klik nie robi tu nic - jest tylko wygaszany,
    żeby nie dobił do map.on('click') i nie ustawił punktu trasy. Palcem
    numer wskazuje stuknięcie (tapMap). */
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
        part.layer.setStyle({opacity: dim ? part.opacity * setting('look-dim') : part.opacity});
    }
    // Wybrana trasa ma własne kropki na swoich przystankach - te z wachlarza
    // leżałyby na nich i pytały o to samo dwa razy.
    if (flowDotLayer) {
        if (dim) { map.removeLayer(flowDotLayer); hoveredStopDot = null; }
        else flowDotLayer.addTo(map);
    }
    if (flowLabelLayer) placeLineLabels();   // grupki przeliczają własną widoczność
    else placeStopNames();                   // ...a nazwy liczą się po nich
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
// Palec zasłania to, w co celuje, i trafia o kilkanaście pikseli obok - przy
// marginesie kursora w cienką linię nie dało się stuknąć wcale (#274). Razem
// z grubością linii pas do stuknięcia ma ok. 40 px, tyle co zwykły przycisk.
const FLOW_TAP_SLACK_PX = 16;
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
function flowHitsAt(containerPoint, slack = FLOW_HIT_SLACK_PX) {
    const reach = 11 + slack;
    const here = map.containerPointToLatLng(containerPoint);
    const away = map.containerPointToLatLng(L.point(containerPoint.x + reach, containerPoint.y + reach));
    const padLat = Math.abs(away.lat - here.lat);
    const padLng = Math.abs(away.lng - here.lng);
    const hits = [];
    for (const h of flowHits) {
        if (here.lat < h.box.getSouth() - padLat || here.lat > h.box.getNorth() + padLat
            || here.lng < h.box.getWest() - padLng || here.lng > h.box.getEast() + padLng) {
            continue;
        }
        const tol = h.weight / 2 + slack;
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
    if (sel.hit && sel.hit.seg.tip) return segTipHtml(sel.hit.seg.tip);
    const moveTip = mapMoveTip(sel, when);
    if (moveTip) return moveTip;
    const mode = MODE_LABEL[sel.kind] || MODE_LABEL.other;
    let html = `<span class="flow-tip-line ${escClass(sel.kind)}"${lineStyle(sel.kind, sel.num)}>${esc(mode)} ${esc(sel.num)}</span>`;
    html += flowTipTimeHtml(when);
    if (options.length > 1) {
        html += '<span class="flow-tip-row">' + options.map((o, i) =>
            `<span class="line-chip ${escClass(o.kind)}${i === flowPick.index ? ' picked' : ''}"${lineStyle(o.kind, o.num)}>`
            + `${esc(o.num)}</span>`,
        ).join('') + '</span>';
    }
    return html;
}

/** Kawałek z własnym dymkiem (`tip`) - przeprawa Polinką, która rysuje się
    jak linia, ale linią nie jest (planner: polinka.map_crossings). Dymek jest
    zbudowany jak dymek auta (carTooltipHtml): nagłówek, przyjazd do celu
    w tym samym zapisie, a pod nim to, co trzeba wiedzieć przed wyruszeniem. */
function segTipHtml(tip) {
    return [
        `<b>${esc(tip.title)}</b>`,
        ...tip.rides.flatMap(r => [
            `${esc(r.from)} → ${esc(r.to)}:`,
            `Jesteś przy niej ${fmtClock(r.at)} — ` +
            `${fmtMins(r.walk_sec)} pieszo z „${esc(r.walk_from)}”`,
            `U celu ok. ${fmtClock(r.arrival)} — ${fmtMins(r.sec)} przeprawy`,
        ]),
        ...tip.lines.map(esc),
    ].join('<br>');
}

/** Godziny w dymku - odpowiedz na "o ktorej tu jestem i o ktorej bede u celu".
    Duza liczba to godzina DOKLADNIE w punkcie pod kursorem (interpolowana,
    patrz timeAtPos). Pod nia przyjazd do celu, gdy jedzie sie dalej stad -
    ta sama liczba, z ktorej policzona jest jasnosc tego kawalka, wiec kolor
    i godzina nigdy nie moga powiedziec czegos innego. */
function flowTipTimeHtml(when) {
    if (!setting('time-hover') || !when) return '';
    let html = '<span class="flow-tip-time">'
        + `<b>${esc(fmtClock(when.now))}</b>`
        + '<span class="flow-tip-what">tu jesteś'
        + (when.stop ? ` · najbliżej <b>${esc(when.stop)}</b>` : '')
        + '</span></span>';
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
    if (!setting('time-ends') || !when) return;
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
/** Styl podświetlonej linii - jeden dla mapy i schematu: ciemna otoczka
    pod spodem, a na niej linia w pełnym kryciu, obie grubsze od zwykłej. */
function lineHighlightStyles(kind, num, weight) {
    return [
        {color: '#111', opacity: 0.85, weight: weight + HALO_EXTRA_PX,
         lineCap: 'round', lineJoin: 'round', interactive: false},
        {color: lineColor(kind, num), opacity: 1,
         weight: weight + HIGHLIGHT_EXTRA_PX, lineCap: 'round', lineJoin: 'round',
         interactive: false},
    ];
}

function showLineHighlight(num, kind) {
    const key = kind + ' ' + num;
    if (flowHighlightKey === key) return;      // ta sama linia - nie przerysowujemy
    hideLineHighlight();
    // Wskazana linia jest wskazana wszędzie naraz: na mapie, na schemacie
    // i w każdej grupce numerów (wypełniony numer).
    flowHighlightKey = key;
    markPickedChips(num, kind);
    schemeLineHighlight(num, kind);
    const parts = flowHits.filter(h => h.seg.num === num && h.seg.kind === kind);
    if (!parts.length) return;
    const color = lineColor(kind, num);
    const halos = parts.map(h => L.polyline(h.latlngs, lineHighlightStyles(kind, num, h.weight)[0]));
    const cores = parts.map(h => L.polyline(h.latlngs, lineHighlightStyles(kind, num, h.weight)[1]));
    flowHighlight = L.layerGroup([...halos, ...cores]).addTo(map);
    // Przystanki tej linii - tylko dopóki jest wskazana (zgłoszenie #252).
    // Na wierzchu podświetlenia, bo ono przykryłoby je tak samo jak linię.
    flowLineStopLayer = L.layerGroup(lineStopsOf(parts.map(h => h.seg))
        .filter(st => !atTransfer(st))
        .map(st => lineStopDot(st, color))).addTo(map);
}

function hideLineHighlight() {
    if (flowHighlightKey) {
        markPickedChips(null);
        schemeLineHighlight(null);
    }
    if (flowHighlight) { map.removeLayer(flowHighlight); flowHighlight = null; }
    if (flowLineStopLayer) { map.removeLayer(flowLineStopLayer); flowLineStopLayer = null; }
    hoveredLineStop = null;
    flowHighlightKey = null;
}

function markPickedChips(num, kind) {
    for (const chip of document.querySelectorAll('.line-chip-slot .line-chip.picked')) {
        chip.classList.remove('picked');
    }
    if (num === null) return;
    for (const slot of document.querySelectorAll('.line-chip-slot')) {
        if (slot.dataset.num === num && slot.dataset.kind === kind) {
            slot.querySelector('.line-chip').classList.add('picked');
        }
    }
}

function renderFlowPick() {
    if (!flowPick || !flowPickAt) return;
    const sel = flowPick.options[flowPick.index];
    if (sel.hit) showLineHighlight(sel.num, sel.kind);
    else hideLineHighlight();
    // Liczone RAZ: ta sama chwila opisuje i dymek, i kropke na linii.
    const when = setting('time-hover') ? timeAtHover(sel.hit, flowPickPoint) : null;
    showTimeDot(when);
    const html = flowPickHtml(when);
    if (setting('tip-cursor')) {
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
    // Nad przystankiem wskazanej linii linia zostaje wskazana - inaczej jej
    // przystanki znikałyby spod kursora, który właśnie po nie sięga.
    if (hoveredLineStop) return;
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

// --- przystanki narysowanych linii i ich nazwy (zgłoszenia #252, #253) -----
//
// Mijane przystanki nie są przesiadkami (punkt 11 kontraktu), więc ich
// kropki są mniejsze i lżejsze od kropek przesiadek i nie mają tablicy
// odjazdów - tylko nazwę i godzinę, jak przystanki linii w rozkładach.
// Widać je pod kursorem na wskazanej linii, a od przybliżenia
// ustawienia „Nazwy przystanków od przybliżenia" - wszystkie, razem z nazwami. Pozostałe słupki miasta
// zostają blade i bez nazw: mówią o czymś innym niż ta mapa (ta sama zasada,
// co przy ◉). Nic z tego nie rusza samych linii.

const LINE_STOP_STYLE = {radius: 4.5, weight: 2, opacity: 1, fillColor: '#fff', fillOpacity: 1};
const LINE_STOP_GREY = '#546e7a';   // kropka kilku linii naraz nie ma jednego koloru
const PLACE_SPAN_M = 400;           // słupki jednej nazwy dalej niż tyle to inne miejsca
                                    // (ten sam promień co gtfs.PLACE_MAX_SPAN_M)
const NAME_UPPER_PX = 8.6;          // szerokość wielkiej litery przy foncie nazwy...
const NAME_CHAR_PX = 6.9;           // ...i każdego innego znaku
const NAME_PAD_PX = 8;              // razem z białą otoczką liter
const NAME_ROW_PX = 17;
const NAME_OFFSET_PX = 8;           // odstęp nazwy od kropki
const NAME_DOT_PX = 7;              // ile wokół kropki nazwa nie zasłania

/** Przystanki narysowanych kawałków, jeden na słupek: nazwa miejsca, nazwa
    słupka (gdy inna - peron kierunkowy węzła) i godzina każdej linii, która
    tędy jedzie. Godzina jest z rozkładu kawałka (stops_t), jak w dymku linii. */
function lineStopsOf(segs) {
    const byPole = new Map();
    for (const seg of segs) {
        if (!seg.stops_t || !seg.stops_n) continue;
        seg.stops_t.forEach(([lat, lon, sec], i) => {
            const key = lat + ',' + lon;
            let st = byPole.get(key);
            if (!st) {
                st = {lat, lon, name: seg.stops_n[i],
                      pole: seg.stops_pf ? seg.stops_pf[i] : null, lines: new Map()};
                byPole.set(key, st);
            }
            const line = seg.kind + ' ' + seg.num;
            const prev = st.lines.get(line);
            if (!prev || sec < prev.sec) st.lines.set(line, {num: seg.num, kind: seg.kind, sec});
        });
    }
    return [...byPole.values()];
}

/** Czy przystanek leży w miejscu, które ma już kropkę przesiadki - tam
    druga, mniejsza kropka pytałaby o to samo i zasłaniała tablicę odjazdów. */
function atTransfer(st) {
    const nodes = (lastFlow && lastFlow.nodes) || [];
    const here = L.latLng(st.lat, st.lon);
    return nodes.some(n => n.place === st.name
        && here.distanceTo(L.latLng(n.lat, n.lon)) <= PLACE_SPAN_M);
}

/** Przystanki całego wachlarza, liczone raz na odpowiedź /api/flow, a nie
    przy każdym ruchu mapy: duże zapytania mają ponad tysiąc kawałków, a przy
    dużym przybliżeniu placeStopNames biegnie po każdym przesunięciu. Zmienia
    się to tylko z nową mapą, więc ruch mapy przesiewa już gotową listę. */
function flowStopsOf(flow) {
    if (!flowLineStops || flowLineStops.flow !== flow) {
        const stops = lineStopsOf(flow.segments);
        for (const st of stops) st.transfer = atTransfer(st);
        flowLineStops = {flow, stops};
    }
    return flowLineStops.stops;
}

function lineStopTipHtml(st) {
    const lines = [...st.lines.values()].sort((a, b) => a.sec - b.sec);
    let html = lines.length === 1 ? `${esc(fmtClock(lines[0].sec))} · ` : '';
    html += `<b>${esc(st.name)}</b>`;
    if (st.pole) html += `<span class="tip-platform">${esc(st.pole)}</span>`;
    if (lines.length > 1) {
        html += '<span class="tip-line-times">' + lines.map(l =>
            `<span><span class="badge ${escAttr(l.kind)}"${lineStyle(l.kind, l.num)}>${esc(l.num)}</span>`
            + `${esc(fmtClock(l.sec))}</span>`).join('') + '</span>';
    }
    return html;
}

/** Klik nie przechodzi do mapy (jak przy kropce przesiadki): na telefonie
    dotknięcie kropki jest jedynym sposobem, żeby przeczytać jej nazwę. */
function lineStopDot(st, color) {
    const dot = L.circleMarker([st.lat, st.lon], {...LINE_STOP_STYLE, color});
    dot.bindTooltip(lineStopTipHtml(st), {direction: 'top', offset: [0, -4], opacity: 1});
    dot.on('mouseover', () => {
        hoveredLineStop = dot;
        // Dymek linii i dymek przystanku stałyby jeden na drugim.
        hideTimeDot();
        if (flowTooltip) { map.removeLayer(flowTooltip); flowTooltip = null; }
    });
    dot.on('mouseout', () => { if (hoveredLineStop === dot) hoveredLineStop = null; });
    dot.on('click', e => { L.DomEvent.stop(e); dot.openTooltip(); });
    return dot;
}

/** Jedno miejsce na nazwę, nie jeden słupek: słupki tej samej nazwy stojące
    obok siebie dostają jedną nazwę pośrodku. */
function placesOf(stops) {
    const groups = new Map();
    for (const s of stops) {
        const list = groups.get(s.name) || [];
        const here = L.latLng(s.lat, s.lon);
        let g = list.find(g => here.distanceTo(g.first) <= PLACE_SPAN_M);
        if (!g) {
            g = {name: s.name, first: here, pts: []};
            list.push(g);
            groups.set(s.name, list);
        }
        g.pts.push(s);
    }
    return [...groups.values()].flat().map(g => ({
        name: g.name,
        lat: g.pts.reduce((sum, p) => sum + p.lat, 0) / g.pts.length,
        lon: g.pts.reduce((sum, p) => sum + p.lon, 0) / g.pts.length,
        poles: g.pts.length,
        pts: g.pts,
    }));
}

function nameWidthPx(text) {
    let w = NAME_PAD_PX;
    for (const ch of text) w += ch !== ch.toLowerCase() ? NAME_UPPER_PX : NAME_CHAR_PX;
    return w;
}

/** Pudełko, w które nazwa się zmieści: obok kropki - po prawej, po lewej,
    nad nią, pod nią albo po skosie - najpierw przy środku miejsca, potem przy
    każdym jego słupku. Null, gdy nigdzie: nazwa, która się nie mieści, nie
    jest rysowana. Grupki numerów i inne nazwy mają pierwszeństwo zawsze.

    Własne kropki miejsca są przeszkodą tylko w pierwszym podejściu. Słupki
    jednego miejsca stoją po dwóch stronach ulicy, kilka pikseli od siebie,
    a środek miejsca leży między nimi - każde pudełko przy nim zahaczało
    o któryś z nich, więc nazwy nie dostawał akurat największy węzeł w kadrze
    (pl. Grunwaldzki, 2026-10-09). Nazwa leżąca na kropce, którą opisuje, nie
    zasłania niczego obcego. */
function nameBox(anchors, width, taken, own) {
    const size = map.getSize();
    const h = NAME_ROW_PX, off = NAME_OFFSET_PX, d = NAME_OFFSET_PX * 0.7;
    const around = at => [
        [at.x + off, at.y - h / 2],
        [at.x - off - width, at.y - h / 2],
        [at.x - width / 2, at.y - off - h],
        [at.x - width / 2, at.y + off],
        [at.x + d, at.y - d - h],
        [at.x + d, at.y + d],
        [at.x - d - width, at.y - d - h],
        [at.x - d - width, at.y + d],
    ];
    const hits = (list, box) =>
        list.some(b => b[0] < box[2] && box[0] < b[2] && b[1] < box[3] && box[1] < b[3]);
    for (const strict of [true, false]) {
        for (const at of anchors) {
            for (const [x, y] of around(at)) {
                const box = [x, y, x + width, y + h];
                if (box[0] < 0 || box[1] < 0 || box[2] > size.x || box[3] > size.y) continue;
                if (hits(taken, box) || (strict && hits(own, box))) continue;
                return box;
            }
        }
    }
    return null;
}

function placeStopNames() {
    if (flowStopsLayer) { map.removeLayer(flowStopsLayer); flowStopsLayer = null; }
    if (stopNameLayer) { map.removeLayer(stopNameLayer); stopNameLayer = null; }
    if (hoveredLineStop && !flowLineStopLayer) hoveredLineStop = null;
    if (map.getZoom() < setting('names-zoom')) return;
    // Wybrana trasa ma własne przystanki po drodze - nazwy wachlarza
    // opisywałyby to, co jest właśnie przygaszone.
    if (lastFlow && flowDimmed) return;
    const view = map.getBounds();
    let named;
    if (lastFlow) {
        const stops = flowStopsOf(lastFlow).filter(st => view.contains([st.lat, st.lon]));
        flowStopsLayer = L.layerGroup(stops.filter(st => !st.transfer)
            .map(st => lineStopDot(st, LINE_STOP_GREY))).addTo(map);
        named = stops;
    } else {
        named = cityStops.filter(s => view.contains([s.lat, s.lon])
            && (trainsOn || s.kind !== 'train'));
    }
    const dotBox = at => [at.x - NAME_DOT_PX, at.y - NAME_DOT_PX,
                          at.x + NAME_DOT_PX, at.y + NAME_DOT_PX];
    const places = placesOf(named).map(p => {
        const pins = p.pts.map(s => map.latLngToContainerPoint([s.lat, s.lon]));
        return {...p, anchors: [map.latLngToContainerPoint([p.lat, p.lon]), ...pins],
                dots: pins.map(dotBox)};
    }).sort((a, b) => (b.poles - a.poles) || a.name.localeCompare(b.name));
    // Kropki też są przeszkodą: nazwa jednego przystanku nie zasłania drugiego.
    const taken = [...clusterBoxes];
    const markers = [];
    for (const p of places) {
        const text = p.name;
        const width = nameWidthPx(text);
        const others = places.filter(o => o !== p).flatMap(o => o.dots);
        const box = nameBox(p.anchors, width, [...taken, ...others], p.dots);
        if (!box) continue;
        taken.push(box);
        markers.push(L.marker(map.containerPointToLatLng(L.point(box[0], box[1])), {
            icon: L.divIcon({className: 'stop-name', html: esc(text),
                             iconSize: [width, NAME_ROW_PX], iconAnchor: [0, 0]}),
            interactive: false, keyboard: false,
        }));
    }
    stopNameLayer = L.layerGroup(markers).addTo(map);
}

// Klik w narysowany kurs NIE OTWIERA ŻADNEJ PROPOZYCJI (usunięte 2026-08-16).
// Mapa przepływów tylko pokazuje wachlarz, a wskazywanie konkretnej linii jest
// wyłącznie pod kursorem (hover), a na telefonie stuknięciem. Propozycje
// otwiera się z listy obok.

map.on('mousemove', handleFlowHover);
map.on('mouseout', clearFlowHover);

// --- stuknięcie palcem: kto je dostaje (#274) ------------------------------
//
// Leaflet daje klik temu, co leży DOKŁADNIE pod punktem i jest narysowane na
// wierzchu. Pod kursorem to działa, pod palcem nie: palec trafia kilkanaście
// pikseli obok, kropki mają po kilka pikseli, a co jest na wierzchu, zależy
// od kolejności rysowania - linia potrafiła zabrać stuknięcie przesiadce,
// która na niej leży. Na dotyku stuknięcie rozstrzyga więc tapMap: każda
// kropka i każdy znak ma cel ok. 44 px (tyle, ile palec), a gdy jedno
// stuknięcie sięga kilku rzeczy, wygrywa ważniejsza - kolejność niżej -
// a przy równej randze ta bliżej palca. Zwycięzca dostaje dokładnie to, co
// dostałby od Leafleta: najechanie i klik, więc robi to samo co dotąd.
//
// Udawane zdarzenia myszy, które przeglądarka dosyła po stuknięciu, nie
// dochodzą do Leafleta wcale: inaczej trafiałyby dalej po staremu, obok
// rozstrzygnięcia. Czy to palec, mówi ostatnie pointerdown, a nie szerokość
// ekranu: laptop z ekranem dotykowym ma i mysz, i palec. Mysz nie przechodzi
// tędy nigdy.
const TAP_RANK = {
    endpoint: 1,     // start i cel
    transfer: 2,     // przesiadki (kropki z tablicą odjazdów)
    shared: 3,       // auta, rowery, Traficar
    stop: 4,         // przystanki
    vehicle: 5,      // pojazdy na żywo
    number: 6,       // numery w grupkach
    line: 7,         // linie
    // Słupki miasta przygaszone pod narysowaną mapą to tło (styleFor): nie
    // dostają powiększonego celu ani pierwszeństwa przed linią, inaczej
    // przy gęstej siatce przystanków w linię nie dałoby się stuknąć wcale.
    background: 8,
};
const TAP_TARGET_PX = 44;

let touchInput = false;
let tapHovered = null;   // co dostało ostatnie "najechanie" palcem
$('map').addEventListener('pointerdown', e => {
    touchInput = e.pointerType !== 'mouse';
}, true);
$('map').addEventListener('pointermove', e => {
    if (e.pointerType === 'mouse') touchInput = false;
}, true);
for (const type of ['mouseover', 'mouseout', 'mousemove', 'mousedown', 'mouseup', 'click']) {
    $('map').addEventListener(type, e => {
        if (!touchInput || e.target.closest('.leaflet-control-container')) return;
        e.stopImmediatePropagation();
        if (type !== 'click') return;
        if (!map.dragging.moved()) {
            // Stuknięcie w linię albo kropkę nie dochodzi do map.on('click'),
            // a też jest stuknięciem w mapę.
            document.dispatchEvent(new Event('planner:map-tap'));
            tapMap(e);
        }
        // Klik zatrzymany na mapie nie dochodzi też do dokumentu, a tam
        // wiszą okienka zamykane kliknięciem obok (dymek paska z czasem).
        $('map').parentNode.dispatchEvent(new MouseEvent('click', {bubbles: true}));
    }, true);
}

function tapRank(layer) {
    if (layer.cityStop !== undefined) {
        const style = styleFor(layer.cityStop);
        if (style === DIM_STYLE) return TAP_RANK.background;
        if (style === ENDPOINT_STYLE.start || style === ENDPOINT_STYLE.end) return TAP_RANK.endpoint;
        return TAP_RANK.stop;
    }
    if (layer.tapRank) return layer.tapRank;
    if (layer instanceof L.CircleMarker) return TAP_RANK.stop;
    return TAP_RANK.shared;
}

/** Odległość punktu od prostokąta, null - gdy poza celem palca wokół niego. */
function tapBoxAway(at, rect, origin) {
    const box = {left: rect.left - origin.left, right: rect.right - origin.left,
                 top: rect.top - origin.top, bottom: rect.bottom - origin.top};
    const dx = Math.max(box.left - at.x, 0, at.x - box.right);
    const dy = Math.max(box.top - at.y, 0, at.y - box.bottom);
    const growX = Math.max(0, (TAP_TARGET_PX - (box.right - box.left)) / 2);
    const growY = Math.max(0, (TAP_TARGET_PX - (box.bottom - box.top)) / 2);
    return dx <= growX && dy <= growY ? Math.hypot(dx, dy) : null;
}

/** Wszystko, czego sięga stuknięcie w `at`: {rank, away, ...co trafione}. */
function tapCandidates(at) {
    const origin = map.getContainer().getBoundingClientRect();
    const found = [];
    map.eachLayer(layer => {
        if (!layer.tapVia && layer.options.interactive === false) return;
        const rank = tapRank(layer);
        const target = layer.tapVia || layer;
        if (layer instanceof L.CircleMarker) {
            const size = layer.getRadius() + (layer.options.stroke === false ? 0 : layer.options.weight / 2);
            const reach = rank === TAP_RANK.background ? size : Math.max(size, TAP_TARGET_PX / 2);
            const d = at.distanceTo(map.latLngToContainerPoint(layer.getLatLng()));
            if (d <= reach) found.push({rank, away: Math.max(0, d - size), layer: target});
        } else if (layer.roster) {
            // Grupka to kilka celów naraz - każdy numer wskazuje swoją linię.
            for (const slot of layer.getElement().querySelectorAll('.line-chip-slot')) {
                const away = tapBoxAway(at, slot.getBoundingClientRect(), origin);
                if (away !== null) {
                    found.push({rank: TAP_RANK.number, away, cluster: layer,
                                index: Number(slot.dataset.i)});
                }
            }
        } else if (layer instanceof L.Marker && layer.getElement()) {
            const away = tapBoxAway(at, layer.getElement().getBoundingClientRect(), origin);
            if (away !== null) found.push({rank, away, layer: target});
        }
    });
    // Linie mapy przepływów są nieinteraktywne (patrz drawFlow) - ich
    // trafienie liczy się tak samo jak pod kursorem, z szerszym marginesem.
    const hits = flowHits.length ? flowHitsAt(at, FLOW_TAP_SLACK_PX) : [];
    if (hits.length) {
        found.push({rank: TAP_RANK.line,
                    away: Math.max(0, hits[0].dist - hits[0].weight / 2), hits});
    }
    return found;
}

/** Stuknięcie robi to, co kursor: najechanie na zwycięzcę i klik w niego.
    Zjechanie z poprzedniego zwycięzcy idzie przed wszystkim innym - to ono
    zamyka jego dymek, jak zejście kursora. Stuknięcie w pustą mapę, gdy
    wskazana jest linia, tylko ją gasi (jak zjechanie z niej kursorem) - nie
    stawia przy okazji punktu trasy. */
function tapMap(e) {
    const at = map.mouseEventToContainerPoint(e);
    const best = tapCandidates(at).sort((a, b) => a.rank - b.rank || a.away - b.away)[0];
    const mouse = type => new MouseEvent(type, {clientX: e.clientX, clientY: e.clientY});
    if (tapHovered && !(best && best.layer === tapHovered)) {
        map._fireDOMEvent(mouse('mouseout'), 'mouseout', [tapHovered]);
        tapHovered = null;
    }
    if (best && best.hits) {
        setFlowPick(corridorOptions(best.hits), map.containerPointToLatLng(at), undefined, at);
        return;
    }
    if (best && best.cluster) {
        pickFromCluster(best.cluster, best.index);
        return;
    }
    if (!best) {
        if (flowPick) clearFlowHover();
        else map._fireDOMEvent(mouse('click'), 'click');
        return;
    }
    if (best.layer !== tapHovered) {
        map._fireDOMEvent(mouse('mouseover'), 'mouseover', [best.layer]);
        tapHovered = best.layer;
    }
    map._fireDOMEvent(mouse('click'), 'click', [best.layer]);
}

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
    if (!flowPanel || !setting('tip-panel')) return;
    flowPanelBody.innerHTML = html;
    flowPanel.hidden = false;
    placeTimeHeadline();    // okienko stoi obok paska i zmienia wysokość z treścią
}

function hideSidePanel() {
    if (flowPanel) flowPanel.hidden = true;
    placeTimeHeadline();
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
    if (!setting('tip-panel') || !flowPanel) return;
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
if (flowPanelBody) flowPanelBody.addEventListener('click', pushwardClick);

// Odpowiedzi /api/timetable trzymamy pod (przystanek, doba, godzina) - ta
// sama kropka pytana drugi raz (powrót kursorem, przerysowanie trasy po
// suwaku) pokazuje dymek od razu, bez mrugnięcia "Ładowanie...".
const timetableCache = new Map();

// Która kropka jest pod kursorem - patrz handleFlowHover.
let hoveredStopDot = null;

// Promienie idą z ustawień (sekcja „Wygląd mapy"), więc to nie są stałe,
// tylko wartości czytane przy każdym rysowaniu - stąd funkcje, nie obiekty.
const STOP_DOT_STYLE = {radius: 5, weight: 3, color: '#263238',
                        fillColor: '#fff', fillOpacity: 1};

const journeyDotStyle = () => ({
    ...STOP_DOT_STYLE,
    radius: setting('dot-size'),
    weight: Math.min(4, Math.max(2, Math.round(setting('dot-size') * 0.5))),
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

/** `mapSec` to godzina, o której MAPA stawia pasażera na tej kropce - od
    niej tablica szarzy wcześniejsze odjazdy (patrz timetableLinesHtml). */
function timetableHtml(data, mapSec) {
    if (data.error) return `<div class="tt-note">${esc(data.error)}</div>`;
    if (data.departures.length) return timetableLinesHtml(data, mapSec);
    return `<div class="tip-head"><span class="tip-stop">${esc(data.stop)}</span>` +
           `<span class="tt-from">od ${esc(data.from_time)}</span></div>` +
           '<div class="tt-note">Nic już stąd nie odjeżdża tego dnia.</div>';
}

/** Tablica odjazdów: jeden wiersz na linię i kierunek, a w nim same
    godziny odjazdów po kolei - do ostatniej, którą przepuściło sito mapy,
    czyli do ostatniego kursu, którym jeszcze się dojedzie (keepOfferedLines).
    Bez "za ile", bez "co N min" i bez godziny w nagłówku: takt z trzech
    kursów pod rząd mylił ("co 2 min"), a rząd godzin mówi to samo bez
    uogólniania. Godziny sprzed chwili z mapy są szare, a bez nich (ustawienie
    „Szare godziny w tablicy") tablica zaczyna się od tej
    chwili. */
function timetableLinesHtml(data, mapSec) {
    const head = `<div class="tip-head"><span class="tip-stop">${esc(data.stop)}</span></div>`;
    const groups = new Map();
    for (const d of [...data.departures].sort((a, b) => a.sec - b.sec)) {
        const key = lineKey({kind: d.mode, num: d.num, headsign: d.headsign})
            + '|' + (d.flow || '');
        if (!groups.has(key)) groups.set(key, {d, secs: [], byTime: new Map()});
        const group = groups.get(key);
        group.secs.push(d.sec);
        group.byTime.set(d.sec, d);
    }
    const rows = [];
    for (const {d, secs, byTime} of groups.values()) {
        const przed = mapSec === undefined ? [] : secs.filter(s => s < mapSec);
        const po = mapSec === undefined ? secs : secs.filter(s => s >= mapSec);
        // Z szarych tylko ostatnia - "uciekło ci o 3 minuty" - bo starsze
        // zajmowały miejsca godzinom, na które się zdąży, i wypychały je
        // z wiersza (Lutosławskiego: sześć szarych 310, żadnej na czas).
        // Spóźniony kurs (#238, patrz withLate) to też szara godzina - z obu
        // rodzajów razem zostaje najpóźniejsza.
        const szare = setting('tt-past') ? przed.slice(-1) : [];
        const miejsce = TT_TIMES_MAX - szare.length;
        const ciete = po.length > miejsce;
        const pokazane = [...szare, ...(ciete ? po.slice(0, miejsce - 1) : po)];
        if (!pokazane.length) continue;
        rows.push({d, byTime, pokazane, ostatni: ciete ? po[po.length - 1] : null,
                   zdazy: po.length > 0, first: po.length ? po[0] : pokazane[0]});
    }
    // Linie, na które według mapy już się nie zdąży (same szare godziny), na
    // koniec - przy ciasnym suwaku nie mogą wypchnąć tych, w które się wsiądzie.
    rows.sort((a, b) => (b.zdazy - a.zdazy) || (a.first - b.first));
    const list = rows.slice(0, timetableRows());
    const flows = list.some(r => FLOW_ICONS[r.d.flow]);
    // Nadmiar zwija się do końca okna: godzina ostatniego kursu, którym się
    // zdąży, a nie kolejne pozycje. Tylko gdy lista sięga końca mapy
    // (`horizon`) - kropka wybranej trasy ma listę uciętą po sztukach,
    // więc jej ostatnia godzina niczego nie znaczy.
    const doKonca = ostatni => ostatni === null ? ''
        : `<span class="tt-until">… ${data.horizon ? 'do ' + esc(fmtClock(ostatni)) : ''}</span>`;
    const html = list.map(({d, byTime, pokazane, ostatni}) =>
        '<li>' + (flows ? flowIcon(d.flow) : '') +
        `<span class="badge ${escClass(d.mode)}"${lineStyle(d.mode, d.num)}>${esc(d.num)}</span>` +
        `<span class="tip-dir">${esc(d.headsign)}${platformHtml(d.platform)}</span>` +
        '<span class="tt-times">' + pokazane.map(s =>
            `<span${mapSec !== undefined && s < mapSec ? ' class="tt-past"' : ''}` +
            (d.flow === 'end' ? '' : departureAttrs(byTime.get(s), data.stop, s)) + '>' +
            `${esc(fmtClock(s))}</span>`).join('') + doKonca(ostatni) +
            // Zegar opóźnienia pociągu (#71) - wypełnia go fillBoardDelays.
            (d.trip ? `<span class="tt-live" data-trip="${escAttr(d.trip)}"`
                      + ` data-stop="${escAttr(d.trip_stop)}">${boardLiveHtml(d.trip, d.trip_stop)}</span>` : '')
            + '</span>' +
        routeButtonHtml(d, 'trasa') + '</li>',
    ).join('');
    return head + `<ul class="tt-rows tt-lines${flows ? ' has-flow' : ''}">${html}</ul>`;
}

// Ile godzin najwyżej w jednym wierszu tablicy (timetableLinesHtml) - więcej
// nie mieści się w dymku obok numeru i kierunku.
const TT_TIMES_MAX = 6;

// Ile wierszy pokazuje dymek - suwak w panelu, sekcja „Przystanki, rowery i Traficary”.
// Rzecz do dostrojenia PRZY MAPIE, bo o tym, ile wierszy jest za dużo,
// decyduje to, ile z niej zasłaniają - a tego nie widać z pliku konfiguracji.
const TIMETABLE_ROWS_MAX = 20;  // wyżej dymek przykrywa mapę, o którą się pyta
const TIMETABLE_FETCH = 40;     // ...a tyle pobieramy, bo część odsiewamy

/** Suwak ma swój sufit w atrybucie, ale ten tutaj bywa niższy. */
function timetableRows() {
    return Math.max(1, Math.min(Math.round(setting('dot-rows')), TIMETABLE_ROWS_MAX));
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
    najbliższego kursu tej linii, bo tym akurat się tu nie przyjechało. */
function withArrivals(data, lines) {
    if (!lines) return data;
    const rows = [];
    for (const l of lines) {
        if (l.flow !== 'end' || l.arrive === undefined) continue;
        rows.push({
            time: fmtClock(l.arrive),
            sec: l.arrive,
            num: l.num,
            mode: l.kind,
            headsign: l.headsign,
            flow: 'end',
        });
    }
    if (!rows.length) return data;
    // Kolejność robi timetableLinesHtml (sortuje po `sec`) - przyjazd ląduje
    // między odjazdami tam, gdzie naprawdę jest na osi czasu.
    return {...data, departures: [...data.departures, ...rows]};
}

/** Spóźnione kursy (zgłoszenie #238): na tablicy każdego przystanku mapy
    każda linia - także ta, której mapa tam nie rysuje - dostaje swój
    poprzedni kurs, jeśli spóźniony i wciąż stojący na przystanku dowiózłby
    wcześniej niż najszybsza trasa (`late` przy węźle, patrz
    planner._late_departures). O 8:11 z pl. Wróblewskiego mapa nie znała
    czwórki z 8:10, która spóźniona stała na przystanku.

    To zwykła szara godzina w tablicy: z niej i ze zwykłych szarych linia
    pokazuje tylko najpóźniejszą (patrz timetableLinesHtml), a ten sam kurs
    nie wchodzi drugi raz. Podlega temu samemu ustawieniu, co szare godziny.

    Wiersz dostaje `flow` linii z mapy, jeśli ją mapa proponuje - inaczej
    stanąłby jako osobny wiersz obok jej odjazdów. */
function withLate(data, where) {
    if (!setting('tt-past') || !where.late) return data;
    const flow = new Map((where.lines || []).filter(l => l.flow !== 'end')
        .map(l => [lineKey(l), l.flow]));
    const key = d => `${lineKey({kind: d.mode, num: d.num, headsign: d.headsign})}@${d.sec}`;
    const known = new Set(data.departures.map(key));
    const rows = where.late.map(l => ({
        time: fmtClock(l.sec),
        sec: l.sec,
        num: l.num,
        mode: l.kind,
        headsign: l.headsign,
        stop_id: l.stop_id,
        ...('siechnice_stop' in l ? {siechnice_stop: l.siechnice_stop} : {}),
        flow: flow.get(lineKey(l)),
    })).filter(d => !known.has(key(d)));
    return {...data, departures: [...rows, ...data.departures]};
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
    return {...data, horizon: deadline,
            departures: data.departures.filter(d => d.sec <= deadline)};
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
    if (dot.getTooltip()) dot.setTooltipContent(html);
    if (timetableTarget === dot) showSidePanel(html);
    fillBoardDelays();
}

// ------------------------------- opóźnienia pociągów w tablicy odjazdów (#71) --
//
// Przy odjeździe pociągu mały zegar: zielony, gdy PLK wie, że jedzie o czasie,
// czerwony, gdy jest spóźniony (minuty w dymku). Tablica rysuje się od razu
// z rozkładu, a zegary dochodzą, gdy przyjdzie odpowiedź - jak na osi trasy
// (loadTrainDelays). Serwer pilnuje limitów zapytań do PLK (pkp_live.py), więc
// tu tylko pamiętamy odpowiedzi przez minutę, żeby każde najechanie na kropkę
// nie pytało od nowa.
const BOARD_DELAYS_TTL_MS = 60000;
const boardDelays = new Map();     // kurs -> {at, stops: {stacja: {arr, dep}} | null}
const boardPending = new Set();
const CLOCK_ICON = '<svg viewBox="0 0 12 12" aria-hidden="true">'
    + '<circle cx="6" cy="6" r="4.75"/><path d="M6 3.4V6l1.8 1.2"/></svg>';

function boardLiveHtml(trip, stop) {
    const known = boardDelays.get(trip);
    const at = known && known.stops && known.stops[stop];
    const late = at ? at.dep : null;
    if (typeof late !== 'number') return '';
    return late > 0
        ? `<span class="tt-clock late" title="Spóźniony ${late} min">${CLOCK_ICON}</span>`
        : `<span class="tt-clock ontime" title="O czasie">${CLOCK_ICON}</span>`;
}

function fillBoardDelays() {
    const slots = [...document.querySelectorAll('.tt-live[data-trip]')];
    const now = Date.now();
    const missing = new Set();
    for (const slot of slots) {
        const known = boardDelays.get(slot.dataset.trip);
        if (known && now - known.at < BOARD_DELAYS_TTL_MS) {
            slot.innerHTML = boardLiveHtml(slot.dataset.trip, slot.dataset.stop);
        } else if (!boardPending.has(slot.dataset.trip)) {
            missing.add(slot.dataset.trip);
        }
    }
    const trips = [...missing].slice(0, 10);
    if (!trips.length) return;
    trips.forEach(trip => boardPending.add(trip));
    fetch('/api/train_delays?' + new URLSearchParams({trips: trips.join(','), date: $('date').value}))
        .then(r => r.json())
        .then(data => {
            const at = Date.now();
            for (const trip of trips) boardDelays.set(trip, {at, stops: (data.delays || {})[trip] || null});
        })
        .catch(() => {})
        .finally(() => {
            trips.forEach(trip => boardPending.delete(trip));
            for (const slot of document.querySelectorAll('.tt-live[data-trip]')) {
                if (trips.includes(slot.dataset.trip)) {
                    slot.innerHTML = boardLiveHtml(slot.dataset.trip, slot.dataset.stop);
                }
            }
        });
}

// Kropka, której tablicę pokazujemy teraz - patrz emitTimetable.
let timetableTarget = null;

/** Od której godziny liczy dymek kropki (zgłoszenie #143).

    Domyślnie od GODZINY Z FORMULARZA, a nie od tej, o której mapa sądzi, że
    pasażer tu stanie: mapa zna tylko to, co sama narysowała, więc jej godzina
    bywa za późna - a wtedy tablica gubiła nie kilka wierszy, lecz całą
    odpowiedź. Kto jest tu wcześniej pieszo, rowerem albo linią spod progu,
    i tak zobaczy, co odjeżdża. Odpowiedź z kolejnej doby nie jest wyjątkiem:
    jej mapa liczy od północy tamtego dnia i tablica też. Nigdy później niż
    mapa - wcześniejsza godzina niczego nie ukrywa, późniejsza ukrywa. */
function timetableAnchor(sec) {
    const flow = lastFlow;
    if (!flow || typeof flow.departure_sec !== 'number') return sec;
    return Math.min(flow.departure_sec, sec);
}

/** Dzień, którego dotyczy mapa: data z formularza, a przy odpowiedzi
    z kolejnej doby (punkt 13) - tamten dzień. Godziny kropek są na jego osi,
    więc dopiero z nim sekunda odjazdu staje się prawdziwą chwilą. */
function mapDate() {
    const date = $('date').value;
    const offset = (lastFlow && lastFlow.day_offset) || 0;
    if (!offset) return date;
    const [y, m, d] = date.split('-').map(Number);
    const day = new Date(Date.UTC(y, m - 1, d + offset));
    return day.toISOString().slice(0, 10);
}

function loadTimetable(dot, where, sec) {
    // Doba mapy tak, jak mapa ją wyznaczyła: data z formularza i przesunięcie
    // z odpowiedzi, a nie sam dzień z mapDate - doba poprzednia przy „być na"
    // po północy ma doklejony poranek dnia z pytania, więc to nie ten sam
    // rozkład, co pytanie wprost o tamten dzień.
    const date = $('date').value;
    const dayOffset = (lastFlow && lastFlow.day_offset) || 0;
    const from = timetableAnchor(sec);
    // Do klucza wchodzi też `flow` i `arrive`: ta sama linia raz jest ofertą
    // do wsiadania, a raz pojazdem, którym się tu przyjechało - i wtedy dymek
    // ma pokazać co innego, choć przystanek i godzina się nie zmieniły.
    const filtr = where.lines
        ? where.lines.map(l => `${lineKey(l)}/${l.flow || ''}/${l.arrive || ''}`).join('|')
        : '';
    const key = `${where.name || where.lat + ',' + where.lon}`
        + `@${date}@${dayOffset}@${from}@${sec}@${filtr}@${where.deadline || ''}@${trainsOn}`;
    const cached = timetableCache.get(key);
    if (cached !== undefined) { emitTimetable(dot, cached); return; }

    const query = where.name
        ? {stop: where.name, date, day_offset: dayOffset, from_sec: from}
        : {lat: where.lat, lon: where.lon, date, day_offset: dayOffset, from_sec: from};
    // Z zapasem PRZY KAŻDEJ kropce, nie tylko przy węźle wachlarza: przy
    // węźle część odjazdów odsiewamy, a przy kropce wybranej trasy pytamy
    // o tyle, ile suwak w ogóle pozwala pokazać. Bez tego serwerowa domyślna
    // ósemka byłaby cichym sufitem mocniejszym od suwaka.
    // Kropka mapy pyta do końca mapy, nie o sztuki: na ruchliwym węźle
    // czterdzieści odjazdów wszystkich linii kończyło się przed godziną,
    // o której pasażer tu staje, i kursy z mapy wypadały przed odsiewem
    // (Śliczna: 113 o 15:59 i 16:14 znikały za 15:55).
    if (!trainsOn) query.trains = '0';
    if (where.deadline) query.until_sec = where.deadline;
    else query.limit = TIMETABLE_FETCH;
    emitTimetable(dot, TIP_LOADING);
    fetch('/api/timetable?' + new URLSearchParams(query))
        .then(r => r.json())
        .then(data => {
            // Przyjazdy doklejamy PO obu sitach: oba pytają "czy tym odjazdem
            // jeszcze się dojedzie", a wiersz przyjazdu nie jest odjazdem -
            // to fakt z samej mapy, więc nie ma go czym odsiewać.
            const html = timetableHtml(data.error ? data : withArrivals(
                withLate(keepWithinHorizon(keepOfferedLines(data, where.lines),
                                           where.deadline), where),
                where.lines), sec);
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
        ...(isStart ? ENDPOINT_STYLE.start : where.end ? ENDPOINT_STYLE.end : {}),
    });
    dot.isStart = isStart;
    dot.tapRank = isStart ? TAP_RANK.endpoint : TAP_RANK.transfer;
    // Czym ta kropka jest - żeby dało się ją "najechać" bez kursora.
    dot.where = where;
    dot.sec = sec;
    // Dymek istnieje tylko wtedy, gdy jest włączony: Leaflet otwiera związany
    // dymek sam, na mouseover, więc "nie pokazuj go" nie da się zrobić inaczej
    // niż nie wiążąc go wcale. Przełącznik przerysowuje mapę (patrz applyDots),
    // więc kropki powstają od nowa z aktualnym ustawieniem.
    if (setting('tip-cursor')) {
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

/** Przystanek po drodze na wybranej trasie: obwódka z nazwą i godziną
    w dymku - tak samo jak przystanki linii w rozkładach (timetable.js).
    Tablicy odjazdów nie ma: tu się nie wsiada ani nie wysiada. */
function viaDot(stop, color) {
    const dot = L.circleMarker([stop.lat, stop.lon], {
        radius: 5, weight: 2, color, fillColor: '#fff', fillOpacity: 1,
    });
    dot.bindTooltip(stop.t ? `${esc(stop.t)} · ${esc(stop.name)}` : esc(stop.name));
    // Pierwszeństwo przed dymkiem przepływów, jak przy kropkach przesiadek
    // (patrz handleFlowHover) - leży na tej samej linii.
    dot.on('mouseover', () => { hoveredStopDot = dot; clearFlowHover(); });
    dot.on('mouseout', () => { if (hoveredStopDot === dot) hoveredStopDot = null; });
    return dot;
}


// Kropki wachlarza są mniejsze od tych na wybranej trasie: jest ich kilkanaście
// naraz i mają nie przykryć samej mapy - a trasa, gdy się ją wybierze, ma być
// tym, co rzuca się w oczy.
const FLOW_DOT_STYLE = {radius: 4, weight: 2, color: '#263238',
                        fillColor: '#fff', fillOpacity: 1};

// Kropka waży tyle, co to, co przy niej leży: jej krycie idzie z jasności
// węzła (backend, patrz planner._transfer_nodes) przez tę samą skalę, co
// krycie kawałków - więc blade kropki są tam, gdzie blade linie.
// Bez tego blada okolica dostawała kropki tak samo mocne, jak najszybsza
// trasa, i to one niosły ciężar obrazka zamiast linii.
const flowDotStyle = (w = 1) => {
    const krycie = lookOpacity(Math.max(0, Math.min(1, Number(w) || 0)));
    return {
        ...FLOW_DOT_STYLE,
        radius: Math.max(2, setting('dot-size') - 1),
        weight: Math.min(3, Math.max(1.5, Math.round(setting('dot-size') * 0.4))),
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
    // Cel stoi w endDots - ma zostać widać także pod wybraną trasą.
    return (nodes || []).filter(n => !n.end).map(n => nodeDot(n, deadline));
}

function nodeDot(n, deadline) {
    return stopDot(
        nodePoint(n),
        // `late` - spóźnione kursy z tego przystanku (#238, patrz withLate).
        {name: n.name, lines: n.lines, deadline, start: n.start, end: n.end, late: n.late},
        // Start i cel nigdy nie blednieją: to nie są opcje, tylko miejsca,
        // z którego i do którego się jedzie.
        n.sec, flowDotStyle(n.start || n.end ? 1 : n.w));
}

/** Kropki startu i celu - po jednej w środku ich słupków, w miejsce ich
    wszystkich (patrz styleFor). Cel to zwykła kropka węzła (nodeDot), tylko
    czerwona: ta sama tablica, co na każdym innym przystanku mapy, z tym, czym
    się tu przyjeżdża (zgłoszenie #266). Tablicę ma tylko jako węzeł celu: gdy
    żadna podróż z mapy nie kończy się przejazdem do samego celu, węzła nie
    ma i czerwona kropka jest samym znacznikiem, bez tablicy.
    Start ma kropkę z węzła wachlarza, ale tylko wtedy, gdy dotyka go któryś
    narysowany kawałek - gdy każda droga zaczyna się dojściem pieszym na inny
    przystanek, węzła startu nie ma i bez tej kropki start znikał z mapy
    (zgłoszenie #276). Taki start wygląda jak kliknięty punkt (POINT_STYLE):
    też jest miejscem, z którego się dopiero dochodzi do przystanku.
    Osobno od flowDotLayer, bo wybrana trasa chowa tamte kropki, a start
    i cel mają zostać widać. Cel z punktu na mapie ma tylko swój znacznik
    (updatePointMarker) - to nie przystanek, więc i tablicy nie ma. */
function endDots(flow) {
    const nodes = flow.nodes || [];
    const endNode = nodes.find(n => n.end);
    const dots = [endNode ? nodeDot(endNode, flow.deadline_sec)
        : endpointDot(sel.end, ENDPOINT_STYLE.end)];
    if (!nodes.some(n => n.start)) {
        dots.push(endpointDot(sel.start, POINT_STYLE.start));
    }
    return L.layerGroup(dots.filter(Boolean));
}

function endpointDot(name, style) {
    const poles = !isPoint(name) && markersByName.get(name);
    if (!poles) return null;
    const center = L.latLngBounds(poles.map(m => m.getLatLng())).getCenter();
    const dot = L.circleMarker(center, {...style, interactive: false});
    // Sama kropka kursora nie łapie - pod nią leżą słupki tego przystanku.
    // Palec, który w nią trafi, dostaje to samo co słupek, ale z rangą celu:
    // inaczej zabrałaby go linia albo przesiadka leżąca obok.
    dot.tapVia = poles[0];
    dot.tapRank = TAP_RANK.endpoint;
    return dot;
}

/** Gdzie postawić kropkę węzła. Obie współrzędne liczy backend (patrz
    planner._transfer_nodes): `lat`/`lon` to słupek, z którego wzięta jest
    godzina, `clat`/`clon` - środek wszystkich słupków tego miejsca.
    Przełącznik tylko wybiera, bo nie ma tu czego dopytywać. */
function nodePoint(node) {
    return setting('dot-center') && node.clat !== undefined
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
    return (cars || []).map(car => carMarker(car, carTooltipHtml(car)));
}

function carTooltipHtml(car) {
    return [
        `<b>${carName(car)}</b>`,
        // Gdzie wysiąść, pokazuje mapa (showCarRide).
        reachLineHtml(car, 'przy aucie'),
        `Do celu ${fmtDist(car.to_dest_m)} w linii prostej`,
        // Auta i tak zostają na mapie - oddać je na granicy strefy bywa
        // sensowne, byle wiedzieć przed wyruszeniem (zgłoszenie #157).
        ...(lastFlow.cars_dest_in_zone === false ? [
            '<b>⚠ Cel poza strefą Traficara — tam auta nie oddasz</b>',
        ] : []),
        ...(setting('car-times') ? [
            `W celu ok. ${fmtClock(car.arrival)} — ${fmtMins(car.drive_sec)} ` +
            'jazdy z ruszeniem i parkowaniem',
        ] : []),
        `Paliwo ${car.fuel}%, zasięg ${car.range} km`,
        ogarniamText(car.ogarniam),
        ...(setting('debug-why') && car.why ? [
            whyText(car.why),
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
    if (!tasks || !tasks.length) return '<span class="ogarniam-none">Ogarniam: nic do wzięcia</span>';
    return 'Ogarniam: ' + tasks
        .map(task => `${esc(task.co.toLowerCase())} ${task.ile} zł`)
        .join(' · ');
}

function fmtDist(metres) {
    return metres < 1000
        ? `${metres} m`
        : `${(metres / 1000).toFixed(1).replace('.', ',')} km`;
}

// Stacja ma tylko dwie wielkości (decyzja użytkownika z 2026-11-24): duża
// kropka tam, gdzie na rower da się liczyć - przez kwadrans nikt nie wybierze
// wszystkich, a jeden zepsuty nie zostawi z niczym - i mniejsza, ale dalej
// dobrze widoczna, przy rowerze luzem i stacji z ledwie kilkoma.
const BIKE_SURE_COUNT = 4;
const BIKE_BIG_RADIUS = 8;
const BIKE_SMALL_RADIUS = 5.5;

function bikeRadius(bikes) {
    return bikes >= BIKE_SURE_COUNT ? BIKE_BIG_RADIUS : BIKE_SMALL_RADIUS;
}

// Rower miejski na mapie. Pomarańcz jest ten sam, co plakietka etapu
// rowerowego na wybranej trasie - „to jest rower" ma znaczyć jedno, niezależnie
// czy patrzy się na kropkę stacji, czy na kreskę przejazdu.
const BIKE_STYLE = {weight: 1, color: '#e65100', fillColor: '#ffb74d',
                    fillOpacity: 0.9};
// Rower stojący luzem, poza stojakiem: ta sama rodzina koloru, ale pusty
// środek - to jeden rower, a nie miejsce, w którym stoi ich kilka.
const BIKE_LOOSE_STYLE = {radius: BIKE_SMALL_RADIUS, weight: 2, color: '#e65100',
                          fillColor: '#fff', fillOpacity: 1};
// Pytanie o inny dzień: stacja stoi tam zawsze, ale ile w niej będzie
// rowerów - nie wiadomo. Szarość mówi to, zanim się przeczyta dymek.
const BIKE_UNKNOWN_STYLE = {radius: BIKE_SMALL_RADIUS, weight: 1.5, color: '#9e9e9e',
                            fillColor: '#fff', fillOpacity: 0.35};
// Kreska przejazdu kropkowana tak samo, jak etap rowerowy wybranej trasy.
const BIKE_RIDE_STYLE = {color: '#e65100', weight: 2, opacity: 0.8,
                         dashArray: '1, 6', interactive: false};

/** Kropki rowerów w zasięgu mapy (patrz bikes.map_places).

    Kropkę dostaje wyłącznie początek przejazdu, który przeszedł wybór
    (punkt 16). Drugi koniec przejazdu pokazuje się razem z kreską - a jeśli
    sam jest początkiem wybranego przejazdu, stoi na mapie z własnego tytułu.

    Sam przejazd domyślnie nie jest narysowany na stałe: dwie kropki mówią to
    samo, co kreska między nimi. Kreski pojawiają się pod kursorem, a na stałe
    - po zapaleniu przełącznika pod zębatką. */
function flowBikeMarkers(places, live) {
    return (places || []).map(place => {
        const dot = L.circleMarker([place.lat, place.lon],
                                   bikeStyle(place, live))
            .bindTooltip(bikeTooltipHtml(place, live),
                         {direction: 'top', offset: [0, -4], opacity: 1});
        dot.tapRank = TAP_RANK.shared;
        dot.on('mouseover', () => {
            markSchemeBike(place.id, true);
        });
        dot.on('mouseout', () => {
            markSchemeBike(place.id, false);
            restBikeRides();
        });
        return dot;
    });
}

/** Stacje, na których kończą się przejazdy (planner: bike_spots) - kropki
    jak każda inna stacja, bez kresek. Koniec przejazdu ze schematu ma już
    swoją kropkę przy kresce. */
function flowBikeSpotMarkers(spots, places, live) {
    const drawn = new Set((places || []).flatMap(place => place.rides)
        .filter(ride => ride.move).map(ride => String(ride.id)));
    return (spots || []).filter(spot => !drawn.has(String(spot.id))).map(spot => {
        const dot = L.circleMarker([spot.lat, spot.lon], bikeStyle(spot, live))
            .bindTooltip(bikeSpotTooltipHtml(spot, live), {direction: 'top', offset: [0, -4], opacity: 1});
        dot.tapRank = TAP_RANK.shared;
        dot.on('mouseover', () => markSchemeBike(spot.id, true));
        dot.on('mouseout', () => markSchemeBike(spot.id, false));
        return dot;
    });
}

function bikeSpotTooltipHtml(spot, live) {
    return [`<b>${esc(spot.name)}</b>`, bikeCountLine(spot, live)].join('<br>');
}

/** Przejazdy rowerem, gdy nic nie jest wskazane: stale te, które schemat
    pokazuje osobno (z kreską i drugim końcem). */
function restBikeRides() {
    if (!lastFlow) clearBikeRides();
    else {
        showAllBikeRides((lastFlow.bike_places || [])
            .map(place => ({...place, rides: place.rides.filter(ride => ride.move)})));
    }
}

function bikeStyle(place, live) {
    if (!live) return BIKE_UNKNOWN_STYLE;
    if (place.loose) return BIKE_LOOSE_STYLE;
    return {...BIKE_STYLE, radius: bikeRadius(place.bikes)};
}

/** Jak się dostać do auta albo roweru - jedna linijka, ta sama w dymku auta
    i roweru: jazda pokazaną podróżą i godzina przy nim w jednym zdaniu
    („Dojazd: [10] z „…” o 19:05, przy aucie 19:12”) albo dojście pieszo. */
function reachLineHtml(item, near) {
    return item.ride
        ? `Dojazd: <span class="badge ${escClass(item.ride.kind)}"${lineStyle(item.ride.kind, item.ride.num)}>${esc(item.ride.num)}</span> ` +
          `z „${esc(item.ride.from)}” o ${fmtClock(item.ride.dep)}, ${near} ${fmtClock(item.at)}`
        : `Jesteś przy nim ${fmtClock(item.at)} — ${walkAmount(item.walk_m, item.walk_sec)} ` +
          `pieszo z „${esc(item.from)}”`;
}

function bikeTooltipHtml(place, live) {
    const rows = [`<b>${esc(place.name || 'Rower luzem')}</b>`];
    rows.push(reachLineHtml(place, 'przy rowerze'));
    rows.push(bikeCountLine(place, live));
    if (setting('debug-why') && place.why) {
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

function bikeCountLine(place, live) {
    return live ? bikeCountText(place)
                : '<b>Nie wiadomo, czy będą tu rowery</b> — liczba rowerów ' +
                  'jest z tej chwili, a pytasz o inny dzień';
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
    const live = !lastFlow || lastFlow.bike_places_live;
    for (const ride of place.rides) {
        layers.push(L.polyline([[place.lat, place.lon], [ride.lat, ride.lon]],
                               BIKE_RIDE_STYLE));
        if (!labels) continue;
        const target = L.circleMarker([ride.lat, ride.lon], bikeStyle(ride, live))
            .bindTooltip(bikeRideTooltipHtml(ride),
                         {direction: 'top', offset: [0, -4], opacity: 1});
        target.tapRank = TAP_RANK.shared;
        layers.push(target);
        // Etykietka wisi na własnym, niewidzialnym uchwycie przezroczystym
        // dla kursora: leży dokładnie na kropce wyżej, a to ona ma łapać
        // najechanie.
        layers.push(L.circleMarker([ride.lat, ride.lon],
                                   {...bikeStyle(ride, live), opacity: 0,
                                    fillOpacity: 0, interactive: false})
            .bindTooltip(bikeRideLabel(ride),
                         {permanent: true, direction: 'right', offset: [6, 0],
                          className: 'bike-ride-label', opacity: 1}));
    }
    return layers;
}

/** Etykietka przy drugim końcu: odległość, a czas przejazdu (policzony, nie
    odczytany) tylko pod jednym ustawieniem dla całej aplikacji (walkAmount). */
function bikeRideLabel(ride) {
    return walkAmount(ride.m, ride.sec);
}

function bikeRideTooltipHtml(ride) {
    const rows = [`<b>${esc(ride.name)}</b>`];
    if (setting('walk-time'))
        rows.push(`Stąd ${fmtMins(ride.sec)} z wypożyczeniem i oddaniem, jesteś tu ${fmtClock(ride.at)}`);
    rows.push(bikeCountLine(ride, !lastFlow || lastFlow.bike_places_live));
    if (ride.arrival) rows.push(`${GOAL_ICON}<b>${esc(fmtClock(ride.arrival))}</b> u celu`);
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
        const color = lineColor(leg.mode, leg.num);
        casings.push(L.polyline(leg.path, {
            color: '#fff', weight: rideWeight + 5, opacity: preview ? 0.7 : 0.95,
            lineCap: 'round', lineJoin: 'round', interactive: false,
        }));
        lines.push(L.polyline(leg.path, {
            color, weight: rideWeight, opacity: 1,
            lineCap: 'round', lineJoin: 'round', interactive: false,
        }));
        if (!preview) {
            const mark = L.marker(leg.path[Math.floor(leg.path.length / 2)], {
                icon: L.divIcon({
                    className: `line-badge solid ${leg.mode}`,
                    html: esc(leg.num), iconSize: null,
                }),
                interactive: false,
            });
            // divIcon nie przyjmuje stylu - kolor linii dostaje dopiero postawiony element.
            const declared = lineVar(leg.mode, leg.num);
            if (declared) mark.on('add', () => { mark.getElement().style.cssText += declared; });
            marks.push(mark);
        }
    }

    if (!preview) {
        // Przystanki po drodze (zgłoszenie #169) - mniejsze obwódki w kolorze
        // linii, jak przebieg linii w rozkładach, żeby nie udawały przesiadek.
        // Idą przed kropki wsiadania i wysiadania, czyli pod nie.
        for (const leg of legs) {
            if (leg.kind !== 'ride' || !leg.path || leg.path.length < 2) continue;
            const color = lineColor(leg.mode, leg.num);
            for (const stop of leg.via || []) {
                if (stop.lat == null || stop.lon == null) continue;
                marks.push(viaDot(stop, color));
            }
        }
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

/** Peron i tor pociągu (zgłoszenie #271): „peron 2, tor 4" - tak, jak piszą
    tablice PKP. Z rozkładu planowego PLK (patrz pkp.augment_day), więc bez
    dzisiejszych zmian. Pusty napis, gdy rozkład nie podaje żadnego.

    Stoi tam, gdzie się go szuka: na osi trasy przy stacji wsiadania
    i wysiadania (nie przy mijanych - tam nic się nie robi) i w tablicy
    odjazdów stacji. „BUS" (komunikacja zastępcza według PLK) idzie
    dosłownie, jako „peron BUS". */
function platformText(p) {
    if (!p) return '';
    return [p.platform && `peron ${p.platform}`, p.track && `tor ${p.track}`]
        .filter(Boolean).join(', ');
}

function platformHtml(p) {
    const text = platformText(p);
    return text ? `<span class="platform">${esc(text)}</span>` : '';
}

function badgeHtml(leg) {
    // Pojazd, w którym się już siedzi (start z pokładu, patrz
    // onboard.mark_journeys), ma plakietkę z obwódką - na karcie propozycji
    // „4 › D" nie mówi inaczej, czy w 4 trzeba dopiero wsiąść, czy jedzie się
    // nią dalej (zgłoszenie #224).
    if (leg.onboard) {
        return `<span class="badge ${leg.mode} current"${lineStyle(leg.mode, leg.num)} title="${escAttr(leg.line)}`
             + ` — jedziesz tym pojazdem">${esc(leg.num)}</span>`;
    }
    return `<span class="badge ${leg.mode}"${lineStyle(leg.mode, leg.num)} title="${escAttr(leg.line)}">${esc(leg.num)}</span>`;
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
function routeButtonHtml(line, label, trip) {
    const tt = window.timetableMode;
    if (!tt || !tt.hasLine || !tt.hasLine(line.num, line.mode)) return '';
    return `<button type="button" class="tt-route"
                    data-route-num="${escAttr(line.num)}"
                    data-route-mode="${escAttr(line.mode)}"
                    data-route-headsign="${escAttr(line.headsign || '')}"
                    data-route-trip="${escAttr(trip || '')}"
                    title="Cała trasa: ${escAttr(MODE_LABEL[line.mode] || 'Linia')} ${escAttr(line.num)}"
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
        trip: button.dataset.routeTrip,
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
    // Z pociągu wysiada się na stacji (#167) - pasażer liczy stacje, nie przystanki.
    const train = lastFlow && lastFlow.onboard && lastFlow.onboard.mode === 'train';
    const ile = `${exit.stops} ${train
        ? plural(exit.stops, 'stację', 'stacje', 'stacji')
        : plural(exit.stops, 'przystanek', 'przystanki', 'przystanków')}`;
    if (!exit.stops) return [`Wysiądź na najbliższ${train ? 'ej stacji' : 'ym przystanku'}`, exit.stop];
    if (!exit.transfer) return [`Dojedziesz tym pojazdem — wysiadka za ${ile}`, exit.stop];
    return [`Wysiądź za ${ile}`, exit.stop];
}

function exitHtml(exit) {
    if (!exit) return '';
    const [co, gdzie] = exitText(exit);
    return `<div class="j-exit"><span class="j-exit-what">${esc(co)}</span>`
         + `<span class="j-exit-stop">${esc(gdzie)}</span>`
         + `<span class="j-exit-time">${esc(exit.time)}</span></div>`;
}

function plural(n, one, few, many) {
    if (n === 1) return one;
    const rest = n % 10, hundreds = n % 100;
    return rest >= 2 && rest <= 4 && (hundreds < 12 || hundreds > 14) ? few : many;
}

// Rozwinięcia przystanków po drodze przełączone kliknięciem. Kluczem jest sam
// etap: przy przerysowaniu listy to ten sam obiekt, a nowa odpowiedź serwera
// przynosi nowe - i te zaczynają od ustawienia z ⚙.
let viaShown = new WeakMap();

/** Czy przystanki po drodze tego przejazdu są rozwinięte: wybór z kliknięcia,
    a bez niego - opcja „Zawsze pokazuj przystanki po drodze" z ⚙. */
function viaOpen(leg) {
    if (viaShown.has(leg)) return viaShown.get(leg);
    const always = $('via-open');
    return !!(always && always.checked);
}

function toggleVia(button) {
    const card = button.closest('.journey');
    const journey = card && journeys[Number(card.dataset.index)];
    if (!journey) return;
    const legIndex = button.dataset.leg;
    const open = button.getAttribute('aria-expanded') !== 'true';
    viaShown.set(journey.legs[Number(legIndex)], open);
    button.setAttribute('aria-expanded', String(open));
    for (const row of card.querySelectorAll(`[data-via-of="${legIndex}"]`)) row.hidden = !open;
}

function detailHtml(journey) {
    const rows = [];
    // `late` - opóźnienie pociągu przy tej stacji w minutach (#71, patrz
    // loadTrainDelays): planowa godzina przekreślona, pod nią rzeczywista.
    // Zero albo mniej to „o czasie" - ale tylko gdy PLK ten czas zna; bez
    // danych (`late` null/undefined) nie mówimy nic, bo nie wiemy.
    const stopRow = (time, name, cls, platform, late) =>
        `<li class="tl-stop ${cls}">` +
        (late > 0
            ? `<span class="tl-time late"><s>${esc(time)}</s>${esc(addMinutes(time, late))}</span>`
            : `<span class="tl-time">${esc(time)}</span>`) +
        `<span class="tl-dot"></span><span class="tl-name">${esc(name)}` +
        `${platformHtml(platform)}` +
        (late > 0 ? `<span class="delay">+${late} min</span>`
            : typeof late === 'number' ? '<span class="ontime">o czasie</span>' : '') +
        `</span></li>`;

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
        const stopWord = leg.mode === 'train' || leg.mode === 'cable'
            ? plural(leg.stops_count, 'stacja', 'stacje', 'stacji')
            : plural(leg.stops_count, 'przystanek', 'przystanki', 'przystanków');
        const ile = `${leg.stops_count} ${stopWord} · ${leg.minutes} min`;
        // Przystanki po drodze (zgłoszenie #169): liczba przystanków rozwija
        // je w osi, między wsiadaniem a wysiadaniem. Przejazd o jeden
        // przystanek nie ma czego rozwijać i zostaje zwykłym napisem.
        const via = leg.via || [];
        const viaOn = via.length > 0 && viaOpen(leg);
        const late = trainDelays[leg.trip] || {};
        const depLate = (late[leg.from] || {}).dep;
        const arrLate = (late[leg.to] || {}).arr;
        rows.push(stopRow(leg.from_time, leg.from, i === 0 ? 'first' : '',
                          leg.from_platform, depLate));
        rows.push(
            `<li class="tl-ride ${escClass(leg.mode)}"${lineStyle(leg.mode, leg.num)}><span class="tl-time"></span>` +
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
            routeButtonHtml(leg, 'trasa', leg.trip) +
            (via.length
                ? `<button type="button" class="tl-info tl-via-toggle" data-leg="${i}"` +
                  ` aria-expanded="${viaOn}">${ile}</button>`
                : `<span class="tl-info">${ile}</span>`) +
            `</span></li>`,
        );
        // Wiersze są zawsze w osi, a zwinięte tylko schowane: rozwinięcie
        // przełącza je w miejscu (toggleVia), bez przerysowania całej listy,
        // które przewijałoby ją do początku karty.
        for (const stop of via) {
            rows.push(
                `<li class="tl-via ${escClass(leg.mode)}"${lineStyle(leg.mode, leg.num)} data-via-of="${i}"${viaOn ? '' : ' hidden'}>` +
                `<span class="tl-time">${esc(stop.t)}</span><span class="tl-dot"></span>` +
                `<span class="tl-name">${esc(stop.name)}</span></li>`,
            );
        }
        // Wysiadanie wypisujemy tylko wtedy, gdy nie zaraz po nim następuje
        // wsiadanie do kolejnej linii - inaczej ten sam przystanek byłby
        // w osi dwa razy pod rząd.
        const next = journey.legs[i + 1];
        if (!next || next.kind === 'walk') {
            rows.push(stopRow(leg.to_time, leg.to,
                              `${next ? '' : 'last'}${leg.onboard ? ' exit' : ''}`,
                              leg.to_platform, arrLate));
        } else if (leg.onboard) {
            // Przesiadka z naszego pojazdu w inny NA TYM SAMYM słupku: wiersz
            // wysiadania normalnie się nie pojawia (byłby ten sam przystanek
            // dwa razy pod rząd), a akurat tu jest najważniejszym wierszem
            // całej osi - to jest ten moment, w którym trzeba wstać.
            rows.push(stopRow(leg.to_time, leg.to, 'exit', leg.to_platform, arrLate));
        }
        const missed = missedTransfer(journey.legs, i, arrLate);
        if (missed) {
            rows.push(`<li class="tl-warn">⚠️ Przez opóźnienie ${esc(leg.num)} `
                + `(+${arrLate} min) możesz nie zdążyć na ${esc(missed.num)} `
                + `o ${esc(missed.from_time)}</li>`);
        }
    });

    return `<ol class="timeline">${rows.join('')}</ol>`;
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
    // Kartka (.notice), a nie szara linijka: ten tekst leży
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
        + `<span class="badge ${escClass(kurs.mode)}"${lineStyle(kurs.mode, kurs.num)}>${esc(kurs.num)}</span>`
        + `<span>w stronę <b>${esc(kurs.headsign)}</b> — najbliższy przystanek `
        + `<b>${esc(kurs.stop_name)}</b> o ${esc(kurs.at)}</span></p>`;
}

function renderJourneys() {
    if (!journeys.length) return;
    const cards = journeys.map((j, i) => {
        const selected = i === selectedJourney;
        const transfers = j.transfers === 0
            ? 'bez przesiadek'
            : `${j.transfers} ${plural(j.transfers, 'przesiadka', 'przesiadki', 'przesiadek')}`;
        const meta = [transfers];
        // W trybie „być na" godzina z formularza jest przyjazdem, więc „odjazd
        // za" nie miałby od czego liczyć - godzinę wyjazdu karta ma i tak.
        if (!(headlineFlow && headlineFlow.arrive_by)) {
            meta.push(j.wait_min > 0 ? `odjazd za ${j.wait_min} min` : 'odjazd teraz');
        }
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
                : leg.onboard ? `${leg.line} (obecny pojazd)` : leg.line).join(', ');
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

    // Rozwinięcie przystanków po drodze to nie klik w kartę - ta by się
    // przy okazji zwinęła (patrz selectJourney).
    const viaToggle = event.target.closest('.tl-via-toggle');
    if (viaToggle) { toggleVia(viaToggle); return; }

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
            && !known.has(startInput.value.trim().toLowerCase())) {
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
    if (event.target.closest('[data-route-num], .tl-via-toggle')) return;
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
// Rodzaj wybrany z podpowiedzi - numer „113" bywa i autobusem, i pociągiem,
// więc o rodzaju rozstrzyga wybrany wiersz, a nie sam napis (#167).
let obPickedMode = null;
// Pociągi pod wpisane cyfry, z serwera (#167, patrz onboard.trains) -
// klucz to data i cyfry, bo lista pociągów zależy od dnia.
const obTrainCache = new Map();

/** Co pojedzie do serwera jako `onboard_*` - albo null, gdy wybór jest jeszcze
    niekompletny. Kierunek jedzie razem z numerem i przystankiem, bo słupek
    rozstrzyga kierunek tylko wtedy, gdy linia mija go raz; na pętli i na
    trasie zawrotnej mija go w obie strony. */
function onboardPick() {
    const num = obLineInput.value.trim();
    const kierunek = obDirection();
    const stop = obStopSelect.value;
    if (!num || !kierunek || !stop) return null;
    return {num, mode: obLineMode(num), headsign: kierunek.headsign, stop};
}

// Numer pociągu tak, jak się go wpisuje: sam („60221") albo z przewoźnikiem
// („KD 60221", „kd60221") - przewoźnik to litery przed cyframi (#167).
const TRAIN_NUMBER = /^([a-ząćęłńóśźż]*)\s*(\d+)$/i;

/** Rodzaj wpisanej linii: wybrany z podpowiedzi, a bez wyboru - linia
    z rozkładu albo, gdy takiej nie ma, a jest numer pociągu (co najmniej
    trzy cyfry, z przewoźnikiem albo bez), pociąg (zgłoszenie #167). */
function obLineMode(num) {
    if (obPickedMode) return obPickedMode;
    if (LINE_MODE_OF.has(num)) return LINE_MODE_OF.get(num);
    const train = TRAIN_NUMBER.exec(num);
    return train && train[2].length >= 3 ? 'train' : '';
}

const obTrainKey = query => `${$('date').value}|${query.replace(/\D/g, '')}`;

/** Pociągi, których numer zaczyna się od wpisanych cyfr - z pamięci, a gdy
    ich tam nie ma, dociągane z serwera; lista podpowiedzi przelicza się sama,
    kiedy przyjdą. */
function obTrainSuggestions(query) {
    const train = TRAIN_NUMBER.exec(query.trim());
    if (!trainsOn || !train || train[2].length < 2) return [];
    const [, carrier, digits] = train;
    // Wpisany przewoźnik zawęża listę: „KD 602" bez pociągów PR 602…
    const pick = list => list.filter(
        item => item.label.toUpperCase().startsWith(carrier.toUpperCase()));
    const key = obTrainKey(query);
    const known = obTrainCache.get(key);
    if (known) return pick(known);
    obTrainCache.set(key, []);
    fetch('/api/onboard/trains?' + new URLSearchParams({q: digits, date: $('date').value}))
        .then(r => r.json())
        .then(data => {
            // Do pola idzie pełny numer z przewoźnikiem („KD 60221") - tak,
            // jak stoi na plakietce i na tablicy; rozpoznanie i tak patrzy
            // tylko na cyfry (onboard._matches).
            obTrainCache.set(key, (data.trains || []).map(train => ({
                name: train.label, mode: 'train', label: train.label,
                headsign: train.headsign})));
            if (obLineAuto && obLineInput.value.trim() === query.trim()) obLineAuto.refresh();
        })
        .catch(() => obTrainCache.delete(key));
    return [];
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
    const przystanek = obStopSelect.options[obStopSelect.selectedIndex].text;
    obSummary.innerHTML =
        `<span class="badge ${escClass(kurs.mode || 'other')}"${lineStyle(kurs.mode || 'other', kurs.num)}>${esc(kurs.num)}</span>`
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

// Tryb względny (#231, patrz onboard.find_ride): rozpoznany kurs jest według
// rozkładu przy przystanku wyraźnie nie teraz, więc mapa liczy go od teraz -
// przesuniętego o `late_sec`, z resztą pojazdów przy rozkładzie. Nic poza tym
// komunikatem tego nie zdradza, a to ważne w obie strony: spóźnienie
// tłumaczy, czemu godziny tego pojazdu nie zgadzają się z tablicą, a kurs
// dopiero przed nami to najczęściej pomyłka w linii, kierunku albo
// przystanku. Mapy nie wstrzymujemy: pasażer może wiedzieć lepiej.
const obWarn = $('ob-warn');

function hideObWarn() {
    obWarn.hidden = true;
}

function warnOnboardRide(kurs) {
    if (!kurs || !kurs.late_sec) { hideObWarn(); return; }
    const przystanek = esc(kurs.stop_name);
    const rozklad = `<b>${esc(kurs.at_sched)}</b>`;
    const ile = `${Math.round(Math.abs(kurs.late_sec) / 60)} min`;
    // Dwie krótkie linijki - fakt i co z nim zrobić - czytane w biegu, nad
    // polem celu. Pełne zdanie zostaje w dymku i dla czytnika ekranu.
    const [ikona, fakt, ikona2, rada, pelne] = kurs.late_sec > 0
        // Bez nazwy przystanku: „(JAGODNO (P+R) 21:01)" czytało się jak kierunek.
        ? ['🕒', `${esc(kurs.num)} prawdopodobnie spóźniony ~${ile} (rozkład ${rozklad})`,
           '⏩', 'Godziny pojazdu przesunięte',
           `${kurs.num} jedzie prawdopodobnie ok. ${ile} po rozkładzie — według rozkładu był `
           + `na przystanku ${kurs.stop_name} o ${kurs.at_sched}. `
           + `Godziny tego pojazdu na mapie są przesunięte o ${ile}.`]
        : ['⚠️', `${esc(kurs.num)} na ${przystanek} dopiero o ${rozklad} (za ${ile})`,
           '🔎', 'Sprawdź linię i przystanek',
           `Najbliższy kurs ${kurs.num} jest według rozkładu na przystanku `
           + `${kurs.stop_name} dopiero o ${kurs.at_sched}, za `
           + `${ile}. Liczę tak, jakby jechał teraz — sprawdź linię i przystanek.`];
    $('ob-warn-text').innerHTML =
        `<span aria-hidden="true">${ikona}</span> ${fakt}`
        + `\n<span aria-hidden="true">${ikona2}</span> ${rada}`;
    obWarn.title = pelne;
    obWarn.setAttribute('aria-label', pelne);
    obWarn.hidden = false;
}

/** Przełącznik "Stoję tutaj" / "Jestem w pojeździe". Zmienia PYTANIE, więc
    zabiera poprzednią odpowiedź na nie: start z drugiego trybu przestaje
    obowiązywać (nie da się naraz stać na przystanku i jechać autobusem).
    Cel zostaje - ten się nie zmienia od tego, skąd się wyrusza. */
function setStartMode(vehicle, focus = true) {
    onboardOn = vehicle;
    document.body.classList.toggle('start-onboard', vehicle);
    document.dispatchEvent(new Event('planner:startmode'));
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
    hideObWarn();
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
        num, mode: obLineMode(num), date: $('date').value,
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
            `<option value="${escAttr(stop.id)}">${esc(stop.name)}</option>`).join('');
    obStopSelect.disabled = !stops.length;
    hideObWarn();
    syncOnboardView();
}

if (obLineInput) {
    modePlaceButton.addEventListener('click', () => setStartMode(false));
    modeVehicleButton.addEventListener('click', () => setStartMode(true));

    // Podpowiedzi numerów linii - plakietka w kolorze pojazdu, tak samo jak
    // w trybie rozkładów (patrz timetable.js): numer JEST plakietką, więc
    // trafienia w nim nie podświetlamy.
    obLineAuto = attachAutocomplete(obLineInput, item => {
        obPickedMode = item.mode || null;
        loadDirections();
    }, {
        // Linie z rozkładu, a pod nimi pociągi dnia po numerze (#167) - to
        // samo pole, bo pasażer pociągu też widzi przed sobą tylko numer.
        suggest: query => [
            ...suggestionsFor(query, LINE_NUMBERS, null, OB_LINE_LIMIT)
                .map(item => ({...item, mode: LINE_MODE_OF.get(item.name)})),
            ...obTrainSuggestions(query),
        ].slice(0, OB_LINE_LIMIT),
        render: item => item.label
            ? `<span class="badge train">${esc(item.label)}</span>`
              + `<span class="ac-kind">→ ${esc(item.headsign)}</span>`
            : `<span class="badge ${escClass(item.mode)}"${lineStyle(item.mode, item.name)}>${esc(item.name)}</span>`
              + `<span class="ac-kind">${esc(MODE_LABEL[item.mode] || 'Linia')}</span>`,
        onEnter: () => loadDirections(),
    });
    // Ręczne wpisywanie kasuje wybór z podpowiedzi - rodzaj ma wtedy wynikać
    // z tego, co jest w polu (patrz obLineMode).
    obLineInput.addEventListener('input', () => { obPickedMode = null; });
    // Wpisanie z ręki (bez wybrania podpowiedzi) też ma działać - linia to
    // dwa, trzy znaki, więc lista bywa szybsza do minięcia niż do trafienia.
    obLineInput.addEventListener('change', loadDirections);

    obDirSelect.addEventListener('change', () => { fillStops(); obNote(''); });
    // Wybór przystanku domyka pytanie - jeśli cel już jest, nie ma na co
    // czekać. Ta sama zasada, co przy drugim kliknięciu w mapę. Domyka też
    // samą kartę: komplet mówi jedno zdanie, a zajmuje trzy rzędy panelu.
    obStopSelect.addEventListener('change', () => {
        obCollapsed = true;
        hideObWarn();
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

    $('ob-warn-close').addEventListener('click', hideObWarn);

    // Tryb przeżywa odświeżenie strony (patrz saveUiState) - ale sam wybór
    // pojazdu już nie: kurs sprzed odświeżenia zdążył odjechać.
    if (uiState.startOnboard) setStartMode(true, false);
}

// ------------------------------------------------------------ wyszukiwanie ----

function resetResults() {
    // Po ✕ nie ma dorysować się wynik relacji, której już nie ma na ekranie
    // (a kółko musi zgasnąć od razu).
    if (planRun) { planRun.abort.abort(); planRun = null; }
    showBusy(new Set());
    journeys = [];
    selectedJourney = null;
    clearJourney();
    clearPreview();
    clearFlow();
    hideObWarn();
    renderVehicles();
    renderScheme(null);
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

// Tempo marszu to trzy stałe tempa (patrz gtfs.WALK_PACES), więc suwak ma
// trzy pozycje, a obok stoi nazwa, nie numer pozycji.
const WALK_PACES = ['wolno', 'zwykle', 'szybko'];
const WALK_PACE_LABELS = ['wolne', 'zwykłe', 'szybkie'];

function queryParams() {
    if (!timeChosen) setTimeNow();
    const params = new URLSearchParams({
        time: $('time').value,
        date: $('date').value,
        cars: $('car-count').value,
        bike_count: $('bike-count').value,
        car_vans: $('car-vans').checked ? '1' : '0',
        car_horizon_h: $('cars-horizon').value,
        walking: $('walking').checked ? '1' : '0',
        bike_electric: bikeElectricOn ? '1' : '0',
        bike_regular: bikeRegularOn ? '1' : '0',
        transfer_gain_sec: (Number($('transfer-gain').value) * 60).toFixed(0),
        walk_pace: WALK_PACES[$('walk-pace').value],
        bike_kmh: $('bike-kmh').value,
        bike_overhead_sec: (Number($('bike-overhead').value) * 60).toFixed(0),
        car_kmh: $('car-kmh').value,
        car_overhead_sec: (Number($('car-overhead').value) * 60).toFixed(0),
    });
    // Wyłączonych propozycji serwer nie składa wcale - patrz routes.api_flow.
    if (!$('routes-on').checked) params.set('routes', '0');
    // „Być na" - godzina to przyjazd do celu (patrz routes.api_flow).
    if (arriveBy && !onboardOn) params.set('arrive', '1');
    // Rower dokładamy do zapytania tylko wtedy, gdy pasażer o niego prosi -
    // patrz routes.api_flow. Bez tego odpowiedź jest co do bajtu taka sama
    // jak przed dodaniem warstwy rowerowej.
    if (bikesOn) params.set('bikes', '1');
    // Auto odwrotnie: propozycje z Traficarem są domyślnie, a zgaszony 🚗
    // zdejmuje je z listy (zgłoszenie #163) - jak przy `routes`, prosi się
    // tylko o ich brak.
    if (!carsOn) params.set('traficar', '0');
    // Pociągi tak samo jak auta: prosi się tylko o ich brak.
    if (!trainsOn) params.set('trains', '0');
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
        // Od jakiego rozjazdu z rozkładem pojazd jedzie „od teraz" (tryb
        // względny, zgłoszenie #231) - suwak w ⚙, patrz onboard.find_ride.
        params.set('onboard_slack_sec', (Number($('onboard-slack').value) * 60).toFixed(0));
    } else if (isPoint(sel.start)) {
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
    syncAddress();
}

/** Jedno zapytanie do /api/flow niesie teraz i mapę (segments), i listę
    propozycji (journeys) - to ta sama, współdzielona odpowiedź, więc obie
    nie mogą już się rozjechać (patrz planner.plan_flow_stages). */
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
    (styl neutralny, nie czerwony jak błąd), tylko wyjaśnienie,
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
    // Doba wcześniej (-1) to „być na" tuż po północy: wyjście wieczorem
    // przed dniem z pytania to odpowiedź, a nie czekanie.
    if (data.day_offset > 0) {
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

// ---------------------------------------- opóźnienia pociągów na żywo (#71) --
//
// Trasy liczą się z rozkładu i tak zostaje: opóźnienia przychodzą PO
// narysowaniu wyniku, osobnym zapytaniem, i tylko dopisują się na osi trasy
// (patrz detailHtml). Serwer pyta PLK pociąg po pociągu i pilnuje własnych
// limitów (pkp_live.py) - gdy ich zabraknie, opóźnień po prostu nie ma.
let trainDelays = {};      // kurs -> {stacja: {arr, dep}} (minuty)
let delaysToken = 0;       // odsiewa odpowiedź na listę, której już nie ma

function loadTrainDelays() {
    trainDelays = {};
    const mine = ++delaysToken;
    const trips = [...new Set(journeys.flatMap(j => j.legs)
        .filter(leg => leg.kind === 'ride' && leg.mode === 'train'
            && String(leg.trip || '').startsWith('PKP:'))
        .map(leg => leg.trip))];
    if (!trips.length) return;
    fetch('/api/train_delays?' + new URLSearchParams({
        trips: trips.join(','), date: $('date').value,
    }))
        .then(r => r.json())
        .then(data => {
            if (mine !== delaysToken || !data.delays) return;
            trainDelays = data.delays;
            if (Object.keys(trainDelays).length) renderJourneys();
        })
        .catch(() => {});      // bez opóźnień trasa jest ta sama, co z rozkładu
}

/** "15:15" + 7 -> "15:22" (przez północ też). */
function addMinutes(hhmm, minutes) {
    const [h, m] = hhmm.split(':').map(Number);
    const total = ((h * 60 + m + minutes) % 1440 + 1440) % 1440;
    return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
}

/** Następny przejazd, na który przez opóźnienie `late` (min) etapu `i` się
    nie zdąży - albo null. Ta sama minuta zapasu co w planerze
    (TRANSFER_SEC) plus przejścia pieszo po drodze. */
function missedTransfer(legs, i, late) {
    if (!(late > 0)) return null;
    let walkSec = 0;
    for (const next of legs.slice(i + 1)) {
        if (next.kind === 'walk') { walkSec += (next.minutes || 0) * 60; continue; }
        if (next.kind !== 'ride') return null;
        const ready = legs[i].arr_sec + late * 60 + walkSec + 60;
        return ready > next.dep_sec ? next : null;
    }
    return null;
}

/** Mapa z listą - z kompletnej odpowiedzi albo z jej etapu "map", który ma
    już wszystko poza autami i rowerami. Wydzielona z loadPlan, żeby dało się
    ją uruchomić bez sieci (patrz tests/js/harness.js).

    Wybrana trasa zostaje wybrana, jeśli nowa lista ją ma (ta sama trasa po
    suwaku) - inaczej przerysowanie zabierałoby ją spod
    ręki. */
function renderPlan(data, refit) {
    adoptNames(data);
    drawFlow(data, refit);
    pickJourneys(data.journeys);
    renderVehicles();            // nowa mapa - inne linie, inne pojazdy
    renderList(data);
    warnOnboardRide(data.onboard);
    renderScheme(data);
}

// ------------------------------------------- schemat obok mapy (#367) --
//
// Ta sama mapa zdjęta z geografii (za ustawieniem „Schemat obok mapy”) - te
// same kropki przesiadek, start na górze, cel na dole, jedna linia to jedna
// jazda od wsiadania do wysiadania. Graf składa serwer (pole `scheme`, patrz
// planner._scheme), bo tylko on wie, gdzie podróże z mapy wysiadają i dokąd
// dochodzą pieszo. Układ i kreski liczymy sami (layoutLayers): linia, która
// przez węzeł tylko przejeżdża, ma iść przez jego pudełko jedną ciągłą kreską.
let lastSchemeData = null;
let schemeLast = null;
let schemeCollapsed = !!uiState.schemeCollapsed;
// Przybliżenie i przesunięcie rysunku w panelu: rysunek
// bywa kilka razy szerszy niż panel, więc trzeba go dać obejrzeć z bliska.
const schemeView = {scale: 1, x: 0, y: 0};
const SCHEME_MIN_SCALE = 0.1;
const SCHEME_MAX_SCALE = 4;
// Węzeł, którego tablica jest teraz w dymku (patrz showSchemeTip).
let schemeTipOwner = null;

const SCHEME_TRIALS = 24;     // ile układów sprawdzamy (patrz layoutLayers)
const SCHEME_BOX_H = 38;
const SCHEME_LANE = 30;       // odstęp między sąsiednimi liniami przy pudełku
const SCHEME_MARGIN = 28;

let schemeMeasure = null;
function schemeTextWidth(text) {
    schemeMeasure = schemeMeasure || document.createElement('canvas').getContext('2d');
    schemeMeasure.font = '600 13px system-ui, sans-serif';
    return schemeMeasure.measureText(text).width;
}

const schemeKey = line => `${line.kind}|${line.num}|${line.run === undefined ? '' : line.run}`;

/** Jedna kreska na jednym odcinku między dwoma węzłami: wszystkie linie
    jadące tym odcinkiem naraz (tak jak jedna grupka numerków na mapie).
    Linie, które w węźle jadą dalej (serwer oznacza to `chain` po obu stronach
    węzła), idą przez jego pudełko prostą kreską do następnego odcinka -
    także gdy skład grupy się tam zmienia (`through`). Linie, które w tym
    węźle wysiadają, odchodzą od kreski krótką odnogą ze strzałką w pudełko
    (`alight`, patrz schemeBranches); strzałką kończy się cały odcinek tylko
    wtedy, gdy nic nie jedzie dalej. */
function schemeStrands(scheme) {
    // Linie między tymi samymi dwoma pudełkami to jedna kreska, także gdy
    // jedna z nich w pudełku wysiada, a druga jedzie dalej: pudełka leżą nad
    // kreskami, więc jadąca dalej po prostu wychodzi z niego dołem.
    const strands = scheme.edges.map(edge => ({
        from: edge.from, to: edge.to, lines: edge.lines, next: null, prev: null,
        label: edge.lines.map(line => line.num).join(' '),
    }));
    const pairs = [];
    for (const a of strands) {
        for (const b of strands) {
            if (b.from !== a.to) continue;
            const shared = a.lines.filter(line => line.chain
                && b.lines.some(o => o.chain && schemeKey(o) === schemeKey(line)));
            if (shared.length) pairs.push({a, b, shared});
        }
    }
    // Najwięcej wspólnych linii pierwsze: to ona jest „główną” kreską przez węzeł.
    pairs.sort((p, q) => q.shared.length - p.shared.length);
    for (const strand of strands) strand.merges = [];
    for (const {a, b, shared} of pairs) {
        if (a.next || b.prev) {
            // Druga linia „na wprost” przez ten sam węzeł (dwie grupy zlewają
            // się albo rozchodzą): krótkie łączenie przez pudełko.
            a.merges.push({to: b, lines: shared});
            continue;
        }
        a.next = b;
        b.prev = a;
        a.through = shared;
    }
    for (const strand of strands) {
        strand.alight = strand.lines.filter(line => !(line.chain && strands.some(other =>
            other.from === strand.to
            && other.lines.some(o => o.chain && schemeKey(o) === schemeKey(line)))));
        strand.arrow = !strand.next && !strand.merges.length && strand.alight.length > 0;
    }
    return strands;
}

function schemeBoxes(scheme, strands, data) {
    const boxes = new Map();
    for (const node of scheme.nodes) {
        const label = node.role === 'start' ? 'Start: ' + displayValue(data.start)
            : node.role === 'end' ? 'Cel: ' + displayValue(data.end)
            : node.place;
        // Przejazd przez węzeł zajmuje jedno miejsce na górze i na dole naraz.
        const lanes = strands.filter(s => s.to === node.id && !s.next).length
            + strands.filter(s => s.from === node.id).length;
        const slots = Math.max(1, lanes);
        // Odstęp kresek przy pudełku z najszerszego numeru, który przy nim stoi.
        const lane = Math.max(SCHEME_LANE, ...strands.filter(s => s.from === node.id)
            .map(s => schemeTextWidth(s.label) * 0.9 + 16));
        boxes.set(node.id, {node, label, lane, w: Math.ceil(Math.max(schemeTextWidth(label) + 28,
                                                                     slots * lane + 16)),
                            h: SCHEME_BOX_H, x: 0, y: 0});
    }
    return boxes;
}

/** Miejsca linii przy pudełkach: u góry kończące się i przejeżdżające, u dołu
    zaczynające się i przejeżdżające - przejazd ma ten sam x z obu stron,
    żeby kreska szła przez pudełko prosto. Kolejność od lewej wg tego, gdzie
    linia dalej biegnie, żeby kreski się nie plątały. */
function assignPorts(boxes, strands, refine = false) {
    const center = id => boxes.get(id).x + boxes.get(id).w / 2;
    // Po pierwszym przydziale znamy miejsca po drugiej stronie kresek, więc
    // kolejność przy pudełku idzie za nimi, a nie za środkami pudełek: dwie
    // kreski do tego samego pudełka też mają swoją kolejność.
    const from = s => refine && s.xs !== undefined ? s.xs : center(s.from);
    const to = s => refine && s.xt !== undefined ? s.xt : center(s.to);
    for (const [id, box] of boxes) {
        const items = [];
        for (const s of strands) {
            if (s.to === id) {
                items.push({top: s, bottom: s.next, key: s.next ? (from(s) + to(s.next)) / 2 : from(s)});
            } else if (s.from === id && !s.prev) {
                items.push({top: null, bottom: s, key: to(s)});
            }
        }
        items.sort((a, b) => a.key - b.key);
        // Miejsca jak najbliżej tego, dokąd kreska dalej biegnie (a nie równo
        // rozłożone na całą szerokość pudełka), z zachowaniem odstępu: kreski
        // nie pętlą się w bok. Najmniejsze kwadraty przy zachowanej kolejności
        // (izotoniczna regresja po odjęciu i * lane).
        const place = (list, set) => {
            const q = list.map((item, i) => item.key - i * box.lane);
            const blocks = [];
            for (const value of q) {
                blocks.push({sum: value, n: 1});
                while (blocks.length > 1) {
                    const last = blocks[blocks.length - 1], prev = blocks[blocks.length - 2];
                    if (last.sum / last.n >= prev.sum / prev.n) break;
                    blocks.splice(-2, 2, {sum: prev.sum + last.sum, n: prev.n + last.n});
                }
            }
            const xs = blocks.flatMap(block => Array(block.n).fill(block.sum / block.n))
                .map((value, i) => value + i * box.lane);
            // Każde miejsce wciśnięte w pudełko, z zachowaniem odstępu.
            const left = box.x + 10, right = box.x + box.w - 10, n = xs.length;
            list.forEach((item, i) => set(item, Math.min(Math.max(xs[i], left + i * box.lane),
                                                         right - (n - 1 - i) * box.lane)));
        };
        // Bez przejazdu przez pudełko wejścia i wyjścia nie muszą się
        // zgadzać co do x, więc każda strona rozkłada się sama, wyśrodkowana.
        if (items.some(item => item.top && item.bottom)) {
            place(items, (item, x) => {
                if (item.top) item.top.xt = x;
                if (item.bottom) item.bottom.xs = x;
            });
        } else {
            place(items.filter(item => item.top), (item, x) => { item.top.xt = x; });
            place(items.filter(item => item.bottom), (item, x) => { item.bottom.xs = x; });
        }
    }
}

/** Kolumny pudełek (warstwy) po ich górnej krawędzi: indeks warstwy węzła
    i pas wolny od pudełek pod każdą warstwą, w którym biegną poziome
    odcinki kresek. */
function schemeLayers(boxes) {
    const tops = [...new Set([...boxes.values()].map(b => Math.round(b.y)))].sort((a, b) => a - b);
    const layerOf = new Map();
    const gaps = tops.map(() => ({top: -Infinity, bottom: Infinity, used: 0, seen: 0}));
    for (const [id, box] of boxes) {
        const layer = tops.indexOf(Math.round(box.y));
        layerOf.set(id, layer);
        gaps[layer].top = Math.max(gaps[layer].top, box.y + box.h);
        if (layer > 0) gaps[layer - 1].bottom = Math.min(gaps[layer - 1].bottom, box.y);
    }
    return {layerOf, gaps};
}

/** Droga linii od dołu pudełka źródła do góry pudełka celu: w dół, poziomy
    skok w pasie pod źródłem, w dół korytarzem wolnym od pudełek, poziomy
    skok w pasie nad celem, w dół. Linie jednej strzałki (te same dwa węzły)
    idą razem jak tęcza - ta, która ma dalej do celu, skacze najniżej - więc
    nie przecinają się nawzajem. Poziomy skoków przydziela schemeRoutes,
    gdy wiadomo, ile ich jest w pasie. */
function planRoutes(boxes, strands, hintX) {
    const {layerOf, gaps} = schemeLayers(boxes);
    const all = [...boxes.values()];
    const MARGIN = 10, STEP = 7;
    const clear = (x, from, to) => all.every(b => b.y + b.h + 2 < from || b.y - 2 > to
        || b.x - MARGIN > x || b.x + b.w + MARGIN < x);
    const groups = new Map();
    for (const s of strands) {
        const key = `${s.from}>${s.to}`;
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(s);
    }
    const taken = [];
    const mean = values => values.reduce((a, b) => a + b, 0) / values.length;
    for (const group of groups.values()) {
        group.sort((p, q) => p.xs - q.xs);
        const src = boxes.get(group[0].from), dst = boxes.get(group[0].to);
        const gapA = layerOf.get(group[0].from), gapB = layerOf.get(group[0].to) - 1;
        const meanS = mean(group.map(s => s.xs)), meanT = mean(group.map(s => s.xt));
        const spread = (group.length - 1) / 2 * STEP;
        let base = meanT;
        if (gapB > gapA) {
            const from = gaps[gapA].top, to = gaps[gapB].bottom;
            const candidates = [meanS, meanT, ...group.flatMap(s => hintX.get(s) || [])];
            for (const b of all) candidates.push(b.x - MARGIN - 8, b.x + b.w + MARGIN + 8);
            candidates.sort((p, q) => Math.abs(p - meanS) + Math.abs(p - meanT)
                                    - Math.abs(q - meanS) - Math.abs(q - meanT));
            const fits = x => clear(x - spread, from, to) && clear(x + spread, from, to);
            // Gdy w gęstym rysunku żaden korytarz nie jest wolny, kreska idzie
            // najbliższym (przez pudełko), a nie znika w NaN.
            base = candidates.find(fits) ?? candidates[0];
            // Dwie grupy jednym korytarzem nie mogą leżeć na sobie.
            for (const shift of [0, 40, -40, 80, -80]) {
                if (fits(base + shift) && !taken.some(t => Math.abs(t.x - (base + shift)) < 2 * spread + 10
                                                         && t.from < to && t.to > from)) {
                    base += shift;
                    break;
                }
            }
            taken.push({x: base, from, to});
        }
        group.forEach((s, i) => {
            s.start = [s.xs, src.y + src.h];
            s.end = [s.xt, dst.y];
            s.gapA = gapA;
            s.gapB = gapB;
            s.xc = base + (i - (group.length - 1) / 2) * STEP;
            s.rank = meanT >= meanS ? -i : i;
            s.groupX = meanS;
        });
    }
    return gaps;
}

function schemeRoutes(boxes, strands, hintX) {
    const gaps = planRoutes(boxes, strands, hintX);
    // Skoki w jednym pasie od góry do dołu: grupy od lewej, a w grupie tak,
    // żeby się nie przecinały (patrz planRoutes).
    const jogs = gaps.map(() => []);
    for (const s of strands) {
        const [xs] = s.start, [xt] = s.end;
        if (s.gapB > s.gapA) {
            jogs[s.gapA].push({s, which: 'yA'});
            jogs[s.gapB].push({s, which: 'yB'});
        } else if (Math.abs(xs - xt) >= 0.5) {
            jogs[s.gapA].push({s, which: 'yA'});
        }
    }
    jogs.forEach((list, g) => {
        list.sort((p, q) => p.s.groupX - q.s.groupX || p.s.rank - q.s.rank);
        const top = gaps[g].top + 26, bottom = gaps[g].bottom - 14;
        list.forEach(({s, which}, k) => { s[which] = top + (bottom - top) * (k + 1) / (list.length + 1); });
    });
    for (const s of strands) {
        const [xs, ys] = s.start, [xt, yt] = s.end;
        let points;
        if (s.gapB > s.gapA) {
            points = [[xs, ys], [xs, s.yA], [s.xc, s.yA], [s.xc, s.yB], [xt, s.yB], [xt, yt]];
        } else if (Math.abs(xs - xt) < 0.5) {
            points = [[xs, ys], [xt, yt]];
        } else {
            points = [[xs, ys], [xs, s.yA], [xt, s.yA], [xt, yt]];
        }
        s.points = points.filter((p, i) => i === 0 || p[0] !== points[i - 1][0] || p[1] !== points[i - 1][1]);
    }
}

/** Krzywa przez punkty, pionowa w każdym z nich. */
function curvePath(points) {
    let d = `M${points[0][0].toFixed(1)} ${points[0][1].toFixed(1)}`;
    for (let i = 1; i < points.length; i++) {
        const [x0, y0] = points[i - 1], [x1, y1] = points[i];
        const mid = (y0 + y1) / 2;
        d += `C${x0.toFixed(1)} ${mid.toFixed(1)} ${x1.toFixed(1)} ${mid.toFixed(1)} ${x1.toFixed(1)} ${y1.toFixed(1)}`;
    }
    return d;
}

/** Punkty kreski gęsto, do liczenia drogi po niej: krzywa jak w curvePath,
    łamana jak jest. */
function strandSamples(s, steps = 24) {
    if (!s.curved) return s.points;
    const out = [s.points[0]];
    for (let i = 1; i < s.points.length; i++) {
        const [x0, y0] = s.points[i - 1], [x1, y1] = s.points[i];
        const mid = (y0 + y1) / 2;
        for (let k = 1; k <= steps; k++) {
            const t = k / steps, u = 1 - t;
            out.push([u * u * u * x0 + 3 * u * u * t * x0 + 3 * u * t * t * x1 + t * t * t * x1,
                      u * u * u * y0 + 3 * u * u * t * mid + 3 * u * t * t * mid + t * t * t * y1]);
        }
    }
    return out;
}

/** Koszt układu schematu, po którym go wybieramy: przecięcia kresek (te
    w pudełkach liczą się czterokrotnie), kreski wchodzące w pudełka, przez
    które nie przejeżdżają, i boczne łączenia w pudełkach. Liczone na tym, co
    naprawdę rysujemy: kreskach, prostych przejazdach przez pudełka,
    łączeniach i odnogach. */
function schemeCost(boxes, strands) {
    const boxList = [...boxes.values()];
    const items = [];
    const add = (points, owner, skip, kind) => items.push({points, owner, skip, kind});
    for (const s of strands) {
        add(strandSamples(s, 8), s, [s.from, s.to], 'strand');
        const [x, top] = s.points[s.points.length - 1];
        if (s.next) add([[x, top], [x, s.up ? boxes.get(s.to).y : boxes.get(s.to).y + boxes.get(s.to).h]], s, [s.to], 'through');
        for (const merge of s.merges) {
            const target = merge.to.points[0];
            add(strandSamples({curved: true, points: [[x, top], target]}, 8), s, [s.to], 'merge');
        }
    }
    for (const branch of schemeBranches(boxes, strands)) {
        add(strandSamples(branch, 8), branch.owner, [branch.owner.to], 'branch');
    }
    const orient = (a, b, c) => Math.sign((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]));
    const inside = (x, y) => boxList.some(b => x > b.x + 1 && x < b.x + b.w - 1
        && y > b.y + 1 && y < b.y + b.h - 1);
    const cost = {cross: 0, inBox: 0, foreign: 0, merges: 0, total: 0};
    for (let i = 0; i < items.length; i++) {
        for (let j = i + 1; j < items.length; j++) {
            const p = items[i], q = items[j];
            if (p.owner === q.owner || p.owner.next === q.owner || q.owner.next === p.owner) continue;
            for (let a = 1; a < p.points.length; a++) {
                for (let b = 1; b < q.points.length; b++) {
                    const [p1, p2, q1, q2] = [p.points[a - 1], p.points[a], q.points[b - 1], q.points[b]];
                    if (orient(p1, p2, q1) * orient(p1, p2, q2) >= 0
                        || orient(q1, q2, p1) * orient(q1, q2, p2) >= 0) continue;
                    const d = (q2[0] - q1[0]) * (p2[1] - p1[1]) - (q2[1] - q1[1]) * (p2[0] - p1[0]);
                    const t = ((q1[0] - p1[0]) * (q2[1] - q1[1]) - (q1[1] - p1[1]) * (q2[0] - q1[0])) / d;
                    if (inside(p1[0] + t * (p2[0] - p1[0]), p1[1] + t * (p2[1] - p1[1]))) cost.inBox++;
                    else cost.cross++;
                }
            }
        }
        const item = items[i];
        if (item.kind === 'merge') {
            const [x0] = item.points[0], [x1] = item.points[item.points.length - 1];
            cost.merges += 2 + Math.abs(x1 - x0) / 30;
        }
        if (item.kind === 'strand' || item.kind === 'branch') {
            for (const b of boxList) {
                if (item.skip.includes([...boxes].find(([, box]) => box === b)[0])) continue;
                if (item.points.some(([x, y]) => x > b.x - 2 && x < b.x + b.w + 2
                                                 && y > b.y - 2 && y < b.y + b.h + 2)) cost.foreign++;
            }
        }
    }
    cost.total = cost.cross + 4 * cost.inBox + 6 * cost.foreign + cost.merges;
    return cost;
}

function pointAlong(samples, fraction) {
    const lengths = samples.slice(1).map((p, i) => Math.hypot(p[0] - samples[i][0], p[1] - samples[i][1]));
    let left = lengths.reduce((a, b) => a + b, 0) * fraction;
    for (let i = 0; i < lengths.length; i++) {
        if (left <= lengths[i] || i === lengths.length - 1) {
            const k = lengths[i] ? Math.min(1, left / lengths[i]) : 0;
            return [samples[i][0] + (samples[i + 1][0] - samples[i][0]) * k,
                    samples[i][1] + (samples[i + 1][1] - samples[i][1]) * k];
        }
        left -= lengths[i];
    }
    return samples[0];
}

/** Numery każdej grupy jako biała plakietka NA kresce, jak grupka numerów na
    mapie (.line-cluster): numer stoi na swojej kresce, więc nie da się go
    pomylić z sąsiednią. Plakietka szuka miejsca wzdłuż kreski tak, żeby nie
    leżała na pudełku ani na innej plakietce. */
function schemeBadges(boxes, strands, branches) {
    const PAD = 5, GAP = 4, H = 19;
    const placed = [];
    const all = [...boxes.values()];
    const hits = (a, b, margin) => a.x - margin < b.x + b.w && a.x + a.w + margin > b.x
        && a.y - margin < b.y + b.h && a.y + a.h + margin > b.y;
    let out = '';
    // Odnogi pierwsze: mają najmniej miejsca do wyboru.
    for (const s of [...branches, ...strands].filter(item => !item.noBadge)) {
        const widths = s.lines.map(line => schemeBadgeWidth(line.num));
        const w = widths.reduce((a, b) => a + b, 0) + GAP * (widths.length - 1) + 2 * PAD;
        const samples = strandSamples(s);
        const rectAt = fraction => {
            const [cx, cy] = pointAlong(samples, fraction);
            return {x: cx - w / 2, y: cy - H / 2, w, h: H, cx, cy};
        };
        const others = [...strands, ...branches].filter(o => o !== s).flatMap(strandSamples);
        const onOther = r => others.some(([x, y]) => x > r.x - 3 && x < r.x + r.w + 3
            && y > r.y - 3 && y < r.y + r.h + 3);
        const free = r => !all.some(b => hits(r, b, 2)) && !placed.some(b => hits(r, b, 2));
        const fractions = [0.5, 0.4, 0.6, 0.3, 0.7, 0.2, 0.8, 0.1, 0.9, 0.05, 0.95, 0.35, 0.65, 0.25, 0.75];
        // Najpierw miejsce bez cudzych kresek pod plakietką, potem choćby wolne od pudełek.
        const spot = fractions.map(rectAt).find(r => free(r) && !onOther(r))
            || fractions.map(rectAt).find(free) || rectAt(0.5);
        placed.push(spot);
        s.badge = spot;
        // Ta sama grupka numerów co na mapie (clusterHtml), wyśrodkowana na
        // kresce - ten sam wygląd i to samo wskazywanie numerem.
        const fw = w + 40, fh = H + 30;
        out += `<foreignObject class="scheme-badge" data-lines="${escAttr(s.label)}" `
            + `data-from="${escAttr(s.from || '')}" data-to="${escAttr(s.to || '')}" `
            + `data-cx="${spot.cx.toFixed(1)}" data-cy="${spot.cy.toFixed(1)}" `
            + `x="${(spot.cx - fw / 2).toFixed(1)}" y="${(spot.cy - fh / 2).toFixed(1)}" `
            + `width="${fw.toFixed(1)}" height="${fh}"><div xmlns="http://www.w3.org/1999/xhtml" `
            + `class="scheme-cluster-anchor">${clusterHtml(s.lines)}</div></foreignObject>`;
    }
    return out;
}

function schemeBadgeWidth(text) {
    schemeMeasure = schemeMeasure || document.createElement('canvas').getContext('2d');
    schemeMeasure.font = '700 11px system-ui, sans-serif';
    return schemeMeasure.measureText(text).width + 6;
}

/** Odnoga linii, które wysiadają w pudełku, przez które reszta grupy jedzie
    dalej: odchodzi od kreski kawałek nad pudełkiem w bok i kończy się strzałką
    w jego górną krawędź. Ma własną plakietkę z numerami wysiadających. */
function schemeBranches(boxes, strands) {
    // Odnóg nie ma: linie między tymi samymi pudełkami są jedną kreską, która
    // wchodzi w pudełko cała, a jadące dalej wychodzą z niego dołem. Osobna
    // odnoga z drugą grupką numerków powtarzała to samo.
    return [];
}

/** Które przejścia pieszo schemat rysuje: te, które wybrał serwer z powodu
    najszybszej podróży i dojścia (patrz planner._scheme_pairs). */
function schemeWalksOf(pairs) {
    return pairs.filter(p => p.why !== 'extra');
}

/** Odległość do napisu: do 10 m poniżej kilometra, do 0,1 km powyżej. */
function fmtWalkDistance(m) {
    const rounded = Math.round(m / 10) * 10;
    if (rounded < 1000) return `${rounded} m`;
    return `${(Math.round(m / 100) / 10).toFixed(1).replace('.', ',')} km`;
}

/** Ile przejścia pieszo albo przejazdu rowerem - wszędzie w aplikacji tak
    samo: odległość, a czas (szacunek z odległości) tylko pod ustawieniem. */
function walkAmount(m, sec) {
    return setting('walk-time') || m === undefined ? fmtMins(sec) : fmtWalkDistance(m);
}

/** Przejście pieszo: przerywana kreska w dół od jednego pudełka do drugiego
    z małą etykietą minut (czas z odległości w linii prostej, liczy serwer).
    Przejście dłuższe niż jeden rząd idzie krzywą przez punkty, które układ
    zostawił wolne od pudełek (`routes`). Leży pod pudełkami i kreskami jazd. */
function schemeWalkLayer(boxes, walks, routes = new Map()) {
    const out = {svg: '', paths: [], minX: Infinity, maxX: -Infinity, minY: Infinity, maxY: -Infinity};
    for (const pair of walks) {
        let a = boxes.get(pair.from), b = boxes.get(pair.to);
        if (a.y > b.y) [a, b] = [b, a];
        const points = [[a.x + a.w / 2, a.y + a.h], ...(routes.get(pair) || []), [b.x + b.w / 2, b.y]];
        const d = points.length > 2 ? curvePath(points) : `M${points[0][0]} ${points[0][1]}L${points[1][0]} ${points[1][1]}`;
        const [x1, y1] = points[0], [x2, y2] = points[points.length - 1];
        const bike = pair.why === 'bike';
        // Odległość, a czas tylko na życzenie (⚙): minuty marszu i jazdy
        // rowerem to szacunek, odległość - nie.
        const amount = walkAmount(pair.m, pair.min * 60);
        const text = bike ? `${BIKE_ICON} ${amount}` : amount;
        const w = text.length * 5.8 + 14;
        out.paths.push(strandSamples({curved: points.length > 2, points}, 8));
        const [cx, cy] = pointAlong(strandSamples({curved: points.length > 2, points}), 0.6);
        out.minX = Math.min(out.minX, x1, x2, cx - w / 2); out.maxX = Math.max(out.maxX, x1, x2, cx + w / 2);
        out.minY = Math.min(out.minY, y1); out.maxY = Math.max(out.maxY, y2);
        out.svg += `<g class="scheme-walk scheme-walk-brace${pair.why === 'extra' ? ' scheme-walk-extra' : ''}`
            + `${bike ? ' scheme-walk-bike' : ''}" data-walk="${escAttr(`${pair.from}>${pair.to}`)}"`
            + `${bike ? ` data-station="${escAttr(pair.station_id)}"` : ''}>`
            + (bike ? '' : `<title>Przejście pieszo, ${walkAmount(pair.m, pair.min * 60)}</title>`)
            + `<path d="${d}"/>${bike ? `<path class="scheme-hit" d="${d}"/>` : ""}<circle cx="${x1}" cy="${y1}" r="2.5"/><circle cx="${x2}" cy="${y2}" r="2.5"/>`
            + `<rect class="scheme-walk-pill" x="${cx - w / 2}" y="${cy - 9}" width="${w}" height="18" rx="9"/>`
            + `<text class="scheme-walk-text" x="${cx}" y="${cy}">${text}</text></g>`;
    }
    return out;
}

function schemeSvg(boxes, strands, pairs, routes = new Map(), cars = [], bikePlaces = []) {
    const branches = schemeBranches(boxes, strands);
    const labels = schemeBadges(boxes, strands, branches);
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const b of boxes.values()) {
        minX = Math.min(minX, b.x); maxX = Math.max(maxX, b.x + b.w);
        minY = Math.min(minY, b.y); maxY = Math.max(maxY, b.y + b.h);
    }
    for (const s of [...strands, ...branches]) {
        for (const [x, y] of s.points) {
            minX = Math.min(minX, x - 30); maxX = Math.max(maxX, x + 30);
        }
        if (s.badge) { minX = Math.min(minX, s.badge.x); maxX = Math.max(maxX, s.badge.x + s.badge.w); }
    }
    // Przejścia pieszo leżą pod pudełkami i kreskami jazd, żeby nie zasłaniały
    // linii.
    const walkLayer = schemeWalkLayer(boxes, schemeWalksOf(pairs).filter(p => boxes.has(p.from) && boxes.has(p.to)), routes);
    minX = Math.min(minX, walkLayer.minX); maxX = Math.max(maxX, walkLayer.maxX);
    minY = Math.min(minY, walkLayer.minY); maxY = Math.max(maxY, walkLayer.maxY);
    const carLayer = schemeCarLayer(boxes, strands, branches, cars, walkLayer.paths);
    minX = Math.min(minX, carLayer.minX); maxX = Math.max(maxX, carLayer.maxX);
    minY = Math.min(minY, carLayer.minY); maxY = Math.max(maxY, carLayer.maxY);
    const width = maxX - minX + 2 * SCHEME_MARGIN, height = maxY - minY + 2 * SCHEME_MARGIN;
    const colors = new Set();
    const DASH = 9;
    let rects = '', lines = '', names = '';
    for (const [id, b] of boxes) {
        rects += `<g class="node" data-node="${id}"><rect class="scheme-box" `
            + `x="${b.x}" y="${b.y}" width="${b.w}" height="${b.h}" rx="12"/></g>`;
        names += `<text class="scheme-name" x="${b.x + b.w / 2}" y="${b.y + b.h / 2}">${esc(b.label)}</text>`;
        // Stacje roweru spod tego przystanku (te same co kropki na mapie) - bez
        // osobnych odnóg, w rogu pudełka; odnogą jest tylko konkretny
        // przejazd, który zastępuje czekanie albo przesiadkę.
        const stations = bikePlaces.filter(place => place.box === id);
        if (stations.length) {
            const text = `${BIKE_ICON} ${stations.length}`;
            const w = text.length * 5.8 + 12, x = b.x + b.w - w + 6, y = b.y - 9;
            names += `<g class="scheme-bike-corner" data-node="${id}" `
                + `data-stations="${escAttr(stations.map(place => place.id).join(' '))}">`
                + `<rect x="${x.toFixed(1)}" y="${y}" width="${w.toFixed(1)}" height="16" rx="8"/>`
                + `<text x="${(x + w / 2).toFixed(1)}" y="${y + 8}">${text}</text></g>`;
        }
    }
    // Jedna kreska dla grupy linii; z różnych pojazdów jest w paski: po jednej
    // kresce na rodzaj, każda kreskowana tak, że wypełnia przerwy pozostałych.
    const stroke = (group, label, d, arrow, extra = '', from = '', edge = '', to = '') => {
        // Przy kolorze na linię pasek jest na linię, nie na rodzaj.
        const tints = [...new Set([...group].sort((a, b) => a.kind.localeCompare(b.kind))
            .map(line => lineColor(line.kind, line.num)))];
        const tint = tints.length === 1 ? tints[0] : WALK_COLOR;
        const marker = arrow ? ` marker-end="url(#scheme-arrow-${tint.slice(1)})"` : '';
        if (arrow) colors.add(tint);
        const strokes = tints.map((color, i) => tints.length === 1
            ? `<path class="scheme-line" stroke="${color}"${marker} d="${d}"/>`
            : `<path class="scheme-line" stroke="${color}" stroke-linejoin="miter" `
              + `stroke-dasharray="${DASH} ${DASH * (tints.length - 1)}" stroke-dashoffset="${-i * DASH}"`
              + `${i === tints.length - 1 ? marker : ''} d="${d}"/>`).join('');
        return `<g class="scheme-stroke${extra}" data-lines="${escAttr(label)}" data-from="${escAttr(from)}" data-to="${escAttr(to)}" data-edge="${escAttr(edge)}">`
            + `${strokes}<path class="scheme-hit" d="${d}"/></g>`;
    };
    const pathOf = (curved, points) => curved ? curvePath(points)
        : points.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`).join('');
    for (const s of strands) {
        lines += stroke(s.lines, s.label, pathOf(s.curved, s.points), s.arrow, '', s.from, `${s.from}>${s.to}`, s.to);
        if (s.next) {
            // Linie jadące dalej przechodzą przez pudełko jedną prostą kreską.
            const [x, top] = s.points[s.points.length - 1];
            const box = boxes.get(s.to);
            lines += stroke(s.through, s.through.map(line => line.num).join(' '),
                            `M${x.toFixed(1)} ${top.toFixed(1)}L${x.toFixed(1)} ${(s.up ? box.y : box.y + box.h).toFixed(1)}`,
                            false, ' scheme-through', s.to, `${s.from}>${s.to}`);
        }
        for (const merge of s.merges) {
            const [x, top] = s.points[s.points.length - 1];
            const [x2, bottom] = merge.to.points[0];
            lines += stroke(merge.lines, merge.lines.map(line => line.num).join(' '),
                            curvePath([[x, top], [x2, bottom]]), false, ' scheme-through scheme-merge', s.to,
                            `${s.from}>${s.to}`);
        }
    }
    for (const branch of branches) {
        lines += stroke(branch.lines, branch.label, curvePath(branch.points), true, ' scheme-branch',
                        branch.owner.from, `${branch.owner.from}>${branch.owner.to}`, branch.owner.to);
    }
    const markers = [...colors].map(color => `<marker id="scheme-arrow-${color.slice(1)}" `
        + `viewBox="0 0 10 10" refX="10" refY="5" markerWidth="11" markerHeight="11" `
        + `markerUnits="userSpaceOnUse" orient="auto"><path d="M0 0L10 5L0 10z" fill="${color}"/></marker>`).join('');
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${minX - SCHEME_MARGIN} ${minY - SCHEME_MARGIN} `
        + `${width} ${height}" width="${width}" height="${height}"><defs>${markers}</defs>`
        + `<g id="scheme-walk-layer">${walkLayer.svg}</g>${lines}<g id="scheme-pick-layer"></g>${carLayer.svg}${rects}${names}`
        + `${carLayer.labels}${labels}</svg>`;
}

const SCHEME_LINE_PX = 3;     // grubość kreski schematu (.scheme-line)

/** Auta z mapy na schemacie: nie kreska do celu (ile potrwa jazda autem, nie
    wiadomo), tylko kropka auta w jego stylu z mapy (carStyle) na krótkiej,
    przerywanej odnodze od miejsca, w którym się wysiada - przy pudełku, przy
    kresce w miejscu mijanego przystanku, albo na końcu krótkiej jazdy linią,
    której schemat poza tym nie rysuje (planner._car_spots). Przystanku
    wysiadania nie ma: mówi o nim dymek kropki. */
function schemeCarLayer(boxes, strands, branches, spots, walkPaths = []) {
    const out = {svg: '', labels: '', minX: Infinity, maxX: -Infinity, minY: Infinity, maxY: -Infinity};
    const cars = new Map(((lastFlow && lastFlow.cars) || []).map(car => [car.plate, car]));
    const groups = new Map();
    for (const spot of spots) {
        if (!cars.has(spot.plate)) continue;
        const key = spot.hop ? `hop|${spot.node}|${spot.hop.kind}|${spot.hop.num}`
            : spot.edge ? `edge|${spot.edge.join('>')}|${spot.at}` : `node|${spot.node}`;
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(spot);
    }
    const grow = (x, y) => {
        out.minX = Math.min(out.minX, x - 10); out.maxX = Math.max(out.maxX, x + 10);
        out.minY = Math.min(out.minY, y - 10); out.maxY = Math.max(out.maxY, y + 10);
    };
    // Odnoga i kropki nie mogą zasłaniać grupki numerów (plakietek kresek,
    // z zapasem na grupkę, która bywa szersza od wyliczonej), cudzych pudełek
    // ani innych kropek - i nie przecinają bez potrzeby żadnej kreski:
    // jazdy, przejazdu przez pudełko, łączenia ani przejścia pieszo.
    const taken = [...strands, ...branches].map(s => s.badge).filter(Boolean)
        .map(r => ({x: r.x - 8, y: r.y - 6, w: r.w + 16, h: r.h + 12}));
    const lines = [];
    for (const s of [...strands, ...branches]) {
        lines.push({owner: s, points: strandSamples(s, 8)});
        const [x, top] = s.points[s.points.length - 1];
        if (s.next) {
            const box = boxes.get(s.to);
            lines.push({owner: s, points: [[x, top], [x, s.up ? box.y : box.y + box.h]]});
        }
        for (const merge of s.merges || []) {
            lines.push({owner: s, points: strandSamples({curved: true, points: [[x, top], merge.to.points[0]]}, 8)});
        }
    }
    for (const points of walkPaths) lines.push({owner: null, points});
    const orient = (a, b, c) => Math.sign((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]));
    const crosses = (p1, p2, q1, q2) => orient(p1, p2, q1) * orient(p1, p2, q2) < 0
        && orient(q1, q2, p1) * orient(q1, q2, p2) < 0;
    const inRect = (p1, p2, r) => {
        const x1 = Math.min(p1[0], p2[0]) - 9, x2 = Math.max(p1[0], p2[0]) + 9;
        const y1 = Math.min(p1[1], p2[1]) - 9, y2 = Math.max(p1[1], p2[1]) + 9;
        return x1 < r.x + r.w && x2 > r.x && y1 < r.y + r.h && y2 > r.y;
    };
    // Ile przeszkód ma droga od kreski (pudełka) do ostatniej kropki: odcinki
    // `path`, pomijając kreskę `own`, od której odnoga odchodzi, i pudełko
    // `home`, z którego wychodzi.
    const near = (p, a, b) => {
        const dx = b[0] - a[0], dy = b[1] - a[1];
        const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy || 1)));
        return Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy);
    };
    const conflicts = (path, own, home) => {
        let n = 0;
        // Kropki (co 16 px od końca odnogi) nie leżą na żadnej kresce.
        const [ox, oy] = path[path.length - 2], [lx] = path[path.length - 1];
        const step = Math.sign(lx - ox) * 16;
        for (let x = lx; step && Math.abs(x - ox) >= 21; x -= step) {
            for (const line of lines) {
                if (own && line.owner === own) continue;
                for (let k = 1; k < line.points.length; k++) {
                    if (near([x, oy], line.points[k - 1], line.points[k]) < 13) { n += 10; break; }
                }
            }
        }
        for (let i = 1; i < path.length; i++) {
            const [p1, p2] = [path[i - 1], path[i]];
            for (const line of lines) {
                if (own && line.owner === own) continue;
                for (let k = 1; k < line.points.length; k++) {
                    if (crosses(p1, p2, line.points[k - 1], line.points[k])) n += 10;
                }
            }
            n += taken.filter(r => inRect(p1, p2, r)).length * 5;
            n += [...boxes.values()].filter(b => b !== home && inRect(p1, p2, b)).length * 5;
        }
        return n;
    };
    const reach = (n, side) => side * (22 + (n - 1) * 16);
    const dots = (x, y, group, side = 1) => {
        let svg = '';
        group.forEach((spot, i) => {
            const style = carStyle(cars.get(spot.plate));
            const cx = x + side * (22 + i * 16);
            svg += `<g class="scheme-car" data-plate="${escAttr(spot.plate)}">`
                + `<circle cx="${cx.toFixed(1)}" cy="${y.toFixed(1)}" r="${style.radius + 1}" `
                + `fill="${style.fillColor || style.color}" fill-opacity="${style.fillOpacity === undefined ? 1 : style.fillOpacity}" `
                + `stroke="${style.color}" stroke-width="${style.weight}"/>`
                + `<circle class="scheme-car-hit" cx="${cx.toFixed(1)}" cy="${y.toFixed(1)}" r="9"/></g>`;
            grow(cx, y);
        });
        const end = x + reach(group.length, side);
        taken.push({x: Math.min(x, end) - 9, y: y - 9, w: Math.abs(end - x) + 18, h: 18});
        return `<path class="scheme-car-stub" d="M${x.toFixed(1)} ${y.toFixed(1)}L${end.toFixed(1)} ${y.toFixed(1)}"/>` + svg;
    };
    // Pierwsza droga bez przeszkód z kolejnych prób, a gdy każda jakieś ma -
    // ta z najmniejszą liczbą.
    const best = (tries, pathOf, own, home) => {
        let pick = null, score = Infinity;
        for (const t of tries) {
            const n = conflicts(pathOf(t), own, home);
            if (n < score) { pick = t; score = n; }
            if (!n) break;
        }
        return pick;
    };
    for (const group of groups.values()) {
        const first = group[0];
        if (first.hop) {
            const b = boxes.get(first.node);
            if (!b) continue;
            // Odnoga wychodzi z dołu albo z boku pudełka tam, gdzie nie ma
            // innych kresek; najpierw jak dotąd, z prawego dolnego rogu.
            const tries = [
                ...[b.w - 14, 14, b.w * 0.75, b.w * 0.25, b.w / 2]
                    .flatMap(dx => [[b.x + dx, b.y + b.h, 22, 34], [b.x + dx, b.y + b.h, -22, 34]]),
                [b.x + b.w, b.y + b.h / 2, 30, 18], [b.x, b.y + b.h / 2, -30, 18],
            ];
            const pathOf = ([sx, sy, dx, dy]) => [[sx, sy], [sx + dx, sy + dy],
                                                  [sx + dx + reach(group.length, Math.sign(dx)), sy + dy]];
            const [sx, sy, dx, dy] = best(tries, pathOf, null, b);
            const ex = sx + dx, ey = sy + dy;
            const plates = escAttr(group.map(spot => spot.plate).join(' '));
            out.svg += `<path class="scheme-line scheme-hop-line" data-plates="${plates}" `
                + `stroke="${lineColor(first.hop.kind, first.hop.num)}" `
                + `d="M${sx.toFixed(1)} ${sy.toFixed(1)}L${ex.toFixed(1)} ${ey.toFixed(1)}"/>`
                + dots(ex, ey, group, Math.sign(dx));
            // Numer na środku odnogi, jak plakietka na każdej kresce.
            const mx = (sx + ex) / 2, my = (sy + ey) / 2;
            out.labels += `<foreignObject class="scheme-hop-badge" data-plates="${plates}" `
                + `x="${(mx - 30).toFixed(1)}" y="${(my - 15).toFixed(1)}" width="60" height="30">`
                + `<div xmlns="http://www.w3.org/1999/xhtml" class="scheme-cluster-anchor">`
                + `${clusterHtml([first.hop])}</div></foreignObject>`;
            taken.push({x: mx - 20, y: my - 13, w: 40, h: 26});
            grow(sx, sy); grow(ex, ey); grow(mx - 20, my); grow(mx + 20, my);
        } else if (first.edge) {
            const s = strands.find(st => st.from === first.edge[0] && st.to === first.edge[1]);
            if (!s) continue;
            const samples = strandSamples(s);
            // Najpierw miejsce mijanego przystanku, potem coraz dalej od niego,
            // po obu stronach kreski.
            const tries = [0, 0.06, -0.06, 0.12, -0.12, 0.18, -0.18, 0.24, -0.24]
                .map(d => Math.min(0.95, Math.max(0.05, first.at + d)))
                .flatMap(at => [[at, 1], [at, -1]]);
            const pathOf = ([at, side]) => {
                const [x, y] = pointAlong(samples, at);
                return [[x, y], [x + reach(group.length, side), y]];
            };
            const [at, side] = best(tries, pathOf, s, null);
            const [x, y] = pointAlong(samples, at);
            out.svg += dots(x, y, group, side);
            grow(x, y);
        } else {
            const b = boxes.get(first.node);
            if (!b) continue;
            const tries = [[b.x + b.w, 1], [b.x, -1]];
            const side = best(tries, ([x, side]) => [[x, b.y + b.h / 2],
                                                     [x + reach(group.length, side), b.y + b.h / 2]], null, b)[1];
            out.svg += dots(side > 0 ? b.x + b.w : b.x, b.y + b.h / 2, group, side);
        }
    }
    return out;
}

/** Auto wskazane (na mapie albo na schemacie): na schemacie jego kropka
    i jazda do niego - odnoga z numerem albo narysowana linia, którą się do
    niego dojeżdża (ten sam styl co wskazana linia). */
function markSchemeCar(car, on) {
    const canvas = $('scheme-canvas');
    const layer = canvas && canvas.querySelector('#scheme-pick-layer');
    if (!layer) return;
    for (const dot of canvas.querySelectorAll('g.scheme-car')) {
        dot.classList.toggle('picked', on && dot.dataset.plate === car.plate);
    }
    if (!on) {
        layer.innerHTML = '';
        markPickedChips(null);
        return;
    }
    if (!car.ride) return;
    const hop = [...canvas.querySelectorAll('path.scheme-hop-line')]
        .find(path => path.dataset.plates.split(' ').includes(car.plate));
    if (hop) {
        layer.innerHTML = lineHighlightStyles(car.ride.kind, car.ride.num, SCHEME_LINE_PX).map(style =>
            `<path d="${hop.getAttribute('d')}" fill="none" stroke="${style.color}" `
            + `stroke-opacity="${style.opacity}" stroke-width="${style.weight}" stroke-linecap="round"/>`).join('');
    } else {
        schemeLineHighlight(car.ride.num, car.ride.kind);
    }
    markPickedChips(car.ride.num, car.ride.kind);
}

/** Stacja roweru wskazana na mapie: na schemacie jej przejazd i róg
    pudełka, w którym stoi. */
function markSchemeBike(id, on) {
    const canvas = $('scheme-canvas');
    if (!canvas) return;
    for (const element of canvas.querySelectorAll('g.scheme-walk-bike, g.scheme-bike-corner')) {
        const ids = element.dataset.station ? [element.dataset.station] : element.dataset.stations.split(' ');
        if (ids.includes(String(id))) element.classList.toggle('picked', on);
    }
}

let schemeMarkLayer = null;

/** Róg pudełka ze stacjami roweru wskazany na schemacie: na mapie ten
    przystanek i te stacje. */
function markBikeCorner(node, ids, on) {
    if (schemeMarkLayer) { map.removeLayer(schemeMarkLayer); schemeMarkLayer = null; }
    if (!on || !lastFlow) return;
    const layers = [...(lastFlow.bike_places || []), ...(lastFlow.bike_spots || [])]
        .filter(place => ids.includes(String(place.id)))
        .map(place => L.circleMarker([place.lat, place.lon],
                                     {radius: 11, color: '#e65100', weight: 3, fill: false, interactive: false}));
    const mapNode = node && (lastFlow.nodes || []).find(n => (node.role === 'start' ? n.start : n.place === node.place));
    if (mapNode) {
        layers.push(L.circleMarker(nodePoint(mapNode),
                                   {radius: 11, color: '#111', weight: 3, fill: false, interactive: false}));
    }
    schemeMarkLayer = L.layerGroup(layers).addTo(map);
}

/** Pod schematem nie ma już listy ruchów: godziny są w samym rysunku (patrz
    bindSchemeLines). */
let schemeMoves = [];
function renderSchemeMoves(moves) {
    schemeMoves = moves;
    const list = $('scheme-moves');
    list.hidden = true;
    list.innerHTML = '';
}

/** Okienko po najechaniu na linię - JEDNO dla mapy i schematu: (1) linia
    i godzina odjazdu, (2) godzina u celu. Obie
    strony różni tylko to, skąd bierze się odjazd (patrz mapMoveTip i
    schemeMoveTip). Dane to ruchy ze schematu (planner._scheme_moves). */
const GOAL_ICON =
    '<svg class="move-tip-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false">'
    + '<path d="M3.5 14V2.2M3.5 2.8h8.6l-2 3 2 3H3.5" fill="none" stroke="currentColor"'
    + ' stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';

function moveTipHtml(line, depSec, depName, move) {
    // Pierwszy wiersz to wiersz tablicy odjazdów (patrz timetableLinesHtml):
    // ta sama plakietka, kierunek i godzina.
    return '<div class="move-tip">'
        + '<ul class="tt-rows tt-lines"><li>'
        + `<span class="badge ${escClass(line.mode)}"${lineStyle(line.mode, line.num)}>${esc(line.num)}</span>`
        + `<span class="tip-dir">${esc(line.headsign || '')}</span>`
        + `<span class="tt-times"><span>${esc(depSec === null ? line.dep : fmtClock(depSec))}</span>`
        + (line.last && depName === line.from ? `<span class="tip-last">ostatni ${esc(line.last)}</span>` : '')
        + '</span></li></ul>'
        + `<div class="move-tip-row move-tip-goal">${GOAL_ICON}<b>${esc(move.arrival)}</b> u celu</div>`
        + '</div>';
}

/** Mapa: odjazd z przystanku, który leży najbliżej (z tyłu) punktu pod
    kursorem - poprzedniego realnego przystanku tej linii. */
function mapMoveTip(sel, when) {
    if (!when) return null;
    for (const move of schemeMoves) {
        for (const line of move.lines) {
            if (line.num !== sel.num || !line.stops) continue;
            const last = line.stops[line.stops.length - 1];
            if (when.now < line.stops[0][1] - 30 || when.now > last[1] + 30) continue;
            let at = line.stops[0];
            for (const stop of line.stops) {
                if (stop !== last && stop[1] <= when.now + 30) at = stop;
            }
            return moveTipHtml(line, at[1], at[0], move);
        }
    }
    return null;
}

/** Schemat: odjazd z przystanku, od którego zaczyna się ten odcinek kreski,
    kursem, który jedzie tym odcinkiem W TĘ STRONĘ: jego przystanek końca
    odcinka leży za przystankiem początku (`toPlace` null - odcinek wewnątrz
    pudełka, 'end' - cel, do którego kurs dojeżdża końcem jazdy). Sam numer
    i początek nie rozróżniają kierunków tej samej linii. */
function schemeMoveTip(num, fromPlace, toPlace) {
    for (const move of schemeMoves) {
        for (const line of move.lines) {
            if (line.num !== num || !line.stops) continue;
            const i = line.stops.findIndex(stop => stop[0] === fromPlace);
            if (i < 0 || i === line.stops.length - 1) continue;
            if (toPlace && toPlace !== 'end'
                && !line.stops.some((stop, j) => j > i && stop[0] === toPlace)) continue;
            return moveTipHtml(line, line.stops[i][1], fromPlace, move);
        }
    }
    return null;
}

/** Najechanie na linię (dotknięcie na telefonie) pokazuje okienko ruchu. */
function bindSchemeLines(canvas) {
    const placeOf = id => {
        const box = id && schemeLast && schemeLast.boxes.get(id);
        return box ? (box.node.role === 'end' ? 'end' : box.node.place) : null;
    };
    // Wskazanie linii na schemacie to to samo wskazanie co na mapie
    // (showLineHighlight) i ten sam dymek (moveTipHtml) w tym samym stylu.
    const pick = (num, kind, from, to, e) => {
        showLineHighlight(num, kind);
        const html = schemeMoveTip(num, placeOf(from), placeOf(to));
        if (html) showSchemeLineTip(html, e.clientX, e.clientY);
        else hideSchemeTip();
    };
    const unpick = () => {
        hideSchemeTip();
        hideLineHighlight();
    };
    for (const element of canvas.querySelectorAll('g.scheme-stroke')) {
        const strand = schemeLast.strands.find(s => `${s.from}>${s.to}` === element.dataset.edge);
        const nums = element.dataset.lines.split(' ');
        const line = strand && strand.lines.find(l => nums.includes(l.num));
        if (!line) continue;
        const show = e => pick(line.num, line.kind, element.dataset.from, element.dataset.to, e);
        element.addEventListener('pointerenter', e => { if (e.pointerType === 'mouse') show(e); });
        element.addEventListener('pointerleave', e => { if (e.pointerType === 'mouse') unpick(); });
        element.addEventListener('click', show);
    }
    for (const badge of canvas.querySelectorAll('foreignObject.scheme-badge')) {
        const cluster = badge.querySelector('.line-cluster');
        const slotPick = e => {
            const slot = e.target.closest && e.target.closest('.line-chip-slot');
            if (slot) pick(slot.dataset.num, slot.dataset.kind, badge.dataset.from, badge.dataset.to, e);
        };
        cluster.addEventListener('mouseover', slotPick);
        cluster.addEventListener('click', slotPick);
        cluster.addEventListener('mouseleave', unpick);
    }
    // Auto i rower: ten sam dymek co ich kropka na mapie, a na mapie samo
    // auto (stacja roweru) wskazane tak, jak przy najechaniu na kropkę.
    for (const dot of canvas.querySelectorAll('g.scheme-car')) {
        const car = (lastFlow && lastFlow.cars || []).find(c => c.plate === dot.dataset.plate);
        if (!car) continue;
        const show = e => {
            showSchemeLineTip(carTooltipHtml(car), e.clientX, e.clientY);
            pointCar(car, true);
        };
        dot.addEventListener('pointerenter', e => { if (e.pointerType === 'mouse') show(e); });
        dot.addEventListener('pointerleave', () => { hideSchemeTip(); pointCar(car, false); });
        dot.addEventListener('click', show);
    }
    // Numer na odnodze auta: ta jazda - na mapie i na schemacie - i dymek
    // auta, które się nią dojeżdża.
    for (const badge of canvas.querySelectorAll('foreignObject.scheme-hop-badge')) {
        const car = (lastFlow && lastFlow.cars || []).find(c => c.plate === badge.dataset.plates.split(' ')[0]);
        if (!car) continue;
        const show = e => {
            showSchemeLineTip(carTooltipHtml(car), e.clientX, e.clientY);
            showCarOnMap(car, true);
        };
        const cluster = badge.querySelector('.line-cluster');
        cluster.addEventListener('mouseover', show);
        cluster.addEventListener('click', show);
        cluster.addEventListener('mouseleave', () => { hideSchemeTip(); showCarOnMap(car, false); });
    }
    for (const corner of canvas.querySelectorAll('g.scheme-bike-corner')) {
        const box = schemeLast && schemeLast.boxes.get(corner.dataset.node);
        const ids = corner.dataset.stations.split(' ');
        corner.addEventListener('pointerenter', () => markBikeCorner(box && box.node, ids, true));
        corner.addEventListener('pointerleave', () => markBikeCorner(null, ids, false));
        corner.addEventListener('click', () => markBikeCorner(box && box.node, ids, true));
    }
    for (const walk of canvas.querySelectorAll('g.scheme-walk-bike')) {
        const place = (lastFlow && lastFlow.bike_places || []).find(p => p.id === walk.dataset.station);
        if (!place) continue;
        const show = e => {
            showSchemeLineTip(bikeTooltipHtml(place, lastFlow.bike_places_live), e.clientX, e.clientY);
            showBikeRides(place);
        };
        walk.addEventListener('pointerenter', e => { if (e.pointerType === 'mouse') show(e); });
        walk.addEventListener('pointerleave', () => {
            hideSchemeTip();
            restBikeRides();
        });
        walk.addEventListener('click', show);
    }
}

/** Podświetlenie linii na schemacie - ten sam styl co na mapie
    (lineHighlightStyles), w osobnej warstwie nad kreskami. */
function schemeLineHighlight(num, kind) {
    const canvas = $('scheme-canvas');
    const layer = canvas && canvas.querySelector('#scheme-pick-layer');
    if (!layer) return;
    if (num === null) { layer.innerHTML = ''; return; }
    let out = '';
    for (const element of canvas.querySelectorAll('g.scheme-stroke')) {
        if (!element.dataset.lines.split(' ').includes(num)) continue;
        const d = element.querySelector('.scheme-hit').getAttribute('d');
        for (const style of lineHighlightStyles(kind, num, SCHEME_LINE_PX)) {
            out += `<path d="${d}" fill="none" stroke="${style.color}" stroke-opacity="${style.opacity}" `
                + `stroke-width="${style.weight}" stroke-linecap="round" stroke-linejoin="round"/>`;
        }
    }
    layer.innerHTML = out;
}

/** Dymek linii, auta albo roweru na schemacie - ten sam wygląd co dymek na
    mapie (klasy dymka Leafletu), nad kursorem. */
function showSchemeLineTip(html, x, y) {
    const tip = $('scheme-tip');
    schemeTipOwner = null;
    tip.className = 'scheme-tip scheme-map-tip leaflet-tooltip leaflet-tooltip-top';
    tip.innerHTML = html;
    tip.hidden = false;
    const box = tip.getBoundingClientRect();
    tip.style.left = `${Math.max(8, Math.min(innerWidth - box.width - 8, x - box.width / 2))}px`;
    tip.style.top = `${Math.max(8, y - box.height - 12)}px`;
}

/** Najechanie na najszybszą trasę na pasku podświetla w schemacie CAŁĄ tę
    podróż: tylko jej odcinki (skąd, dokąd, numer), nie każde wystąpienie
    numeru linii na innych ruchach i gałęziach. */
function highlightSchemeFastest() {
    const canvas = $('scheme-canvas');
    clearSchemeHighlight();
    const edges = lastSchemeData && lastSchemeData.scheme && lastSchemeData.scheme.fastest_edges;
    if (!edges) return;
    const keys = new Set(edges.map(([from, to, num]) => `${from}>${to}|${num}`));
    // Przejścia tej podróży: dojście ze startu do pierwszego przystanku, każde
    // przejście między jazdami i dojście do celu - tak jak kropki na mapie.
    const nodes = lastSchemeData.scheme.nodes;
    const start = (nodes.find(n => n.role === 'start') || {}).id;
    const end = (nodes.find(n => n.role === 'end') || {}).id;
    const stops = [start, ...edges.flatMap(([from, to]) => [from, to]), end];
    const walks = new Set();
    for (let i = 0; i + 1 < stops.length; i += 2) {
        if (stops[i] !== stops[i + 1]) walks.add(`${stops[i]}>${stops[i + 1]}`);
    }
    for (const element of canvas.querySelectorAll('g.scheme-walk')) {
        if (walks.has(element.dataset.walk)) element.classList.add('scheme-pick');
    }
    let any = false;
    for (const element of canvas.querySelectorAll('g.scheme-stroke')) {
        if (element.dataset.lines.split(' ').some(num => keys.has(`${element.dataset.edge}|${num}`))) {
            element.classList.add('scheme-pick');
            any = true;
        }
    }
    canvas.classList.toggle('has-pick', any);
}

function clearSchemeHighlight() {
    const canvas = $('scheme-canvas');
    canvas.classList.remove('has-pick');
    for (const element of canvas.querySelectorAll('g.scheme-pick')) element.classList.remove('scheme-pick');
}

/** Schemat do panelu obok mapy - albo panel schowany, gdy przełącznik jest
    wyłączony albo odpowiedź schematu nie ma (null: mapa zeszła z ekranu). */
function renderScheme(data) {
    lastSchemeData = data;
    const panel = $('scheme-panel');
    hideSchemeTip();
    if (!data || !data.scheme || !setting('scheme-on')) {
        panel.hidden = true;
        $('scheme-canvas').innerHTML = '';
        $('scheme-moves').innerHTML = '';
        return;
    }
    const scheme = data.scheme;
    renderSchemeMoves(scheme.moves || []);
    const strands = schemeStrands(scheme);
    const boxes = schemeBoxes(scheme, strands, data);
    const finish = (boxes, strands, routes = new Map()) => {
        schemeLast = {boxes, strands, scheme, routes};
        window.schemeLast = schemeLast;
        const canvas = $('scheme-canvas');
        canvas.innerHTML = schemeSvg(boxes, strands, scheme.pairs, routes, scheme.cars || [],
                                     [...(data.bike_places || []), ...(data.bike_spots || [])]);
        panel.hidden = false;
        setSchemeCollapsed(schemeCollapsed);
        bindSchemeNodes(canvas, data);
        bindSchemeLines(canvas);
        fitScheme();
    };
    const walkPairs = schemeWalksOf(scheme.pairs).filter(p => boxes.has(p.from) && boxes.has(p.to));
    let best = null;
    for (let seed = 0; seed < SCHEME_TRIALS; seed++) {
        const trialStrands = schemeStrands(scheme);
        const trialBoxes = schemeBoxes(scheme, trialStrands, data);
        const routes = layoutLayers(scheme, trialBoxes, trialStrands, walkPairs, seed);
        const cost = schemeCost(trialBoxes, trialStrands).total;
        if (!best || cost < best.cost) best = {boxes: trialBoxes, strands: trialStrands, cost, routes};
        if (!cost) break;
    }
    finish(best.boxes, best.strands, best.routes);
}

/** Kreski tych samych dwóch węzłów, które nie mają nic do powiedzenia po
    drodze, zbiegają się w połowie odcinka w jedną z plakietką wszystkich
    numerów (N i 145 pod Morwową). */
function schemeMergeSiblings(strands) {
    const groups = new Map();
    for (const s of strands) {
        const key = `${s.from}>${s.to}`;
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(s);
    }
    for (const group of groups.values()) {
        if (group.length < 2 || group.some(s => s.up || s.points.length !== 2 || s.next || s.merges.length)) continue;
        const xt = group[0].points[1][0], y1 = group[0].points[1][1];
        const top = Math.max(...group.map(s => s.points[0][1]));
        const ym = top + 0.55 * (y1 - top);
        const lines = group.flatMap(s => s.lines);
        strands.push({from: group[0].from, to: group[0].to, lines,
                      label: lines.map(line => line.num).join(' '), next: null, prev: null,
                      merges: [], alight: [], arrow: group.some(s => s.arrow), curved: true,
                      points: [[xt, ym], [xt, y1]]});
        for (const s of group) {
            s.points = [s.points[0], [xt, ym]];
            s.arrow = false;
            s.noBadge = true;
        }
    }
}

/** Układ warstwowy schematu: start sam w pierwszym rzędzie, cel sam w ostatnim,
    przepływ tylko z góry w dół. Rząd węzła to najdłuższa droga do niego
    (jazdą albo dojściem), więc kolejne rzędy to kolejne przesiadki; kolejność
    w rzędzie dobiera barycentrum (kilka przebiegów góra-dół i dół-góra), z tym,
    że kreska dłuższa niż jeden rząd ma w każdym rzędzie pośrednim punkt
    przejścia, który też się układa (i omija pudełka). Zwraca trasy przejść
    pieszych dłuższych niż jeden rząd (para -> punkty pośrednie). */
function layoutLayers(scheme, boxes, strands, walkPairs, seed = 0) {
    let rng = seed * 2654435761 + 1;
    const random = () => { rng = (rng * 1664525 + 1013904223) >>> 0; return rng / 4294967296; };
    const startId = [...boxes.values()].find(b => b.node.role === 'start').node.id;
    const endId = [...boxes.values()].find(b => b.node.role === 'end').node.id;
    const links = new Map();
    const link = (from, to) => {
        if (from === to || from === endId || to === startId || !boxes.has(from) || !boxes.has(to)) return;
        const key = `${from}>${to}`;
        if (!links.has(key)) links.set(key, {from, to});
    };
    for (const s of strands) link(s.from, s.to);
    for (const [from, to] of scheme.order || []) link(from, to);
    for (const pair of walkPairs) {
        if (pair.to === startId || pair.from === endId) link(pair.to, pair.from);
        else link(pair.from, pair.to);
    }
    // Rzędy: najdłuższa droga od startu, krawędzie zawracające (cykl) pomijamy.
    const ids = [...boxes.keys()];
    const out = new Map(ids.map(id => [id, []]));
    for (const l of links.values()) out.get(l.from).push(l);
    const rank = new Map(), seen = new Map(), topo = [];
    const visit = id => {
        seen.set(id, 1);
        const outs = out.get(id);
        if (seed) outs.sort(() => random() - 0.5);
        for (const l of outs) {
            if (seen.get(l.to) === 1) l.back = true;
            else if (!seen.has(l.to)) visit(l.to);
        }
        seen.set(id, 2);
        topo.push(id);
    };
    for (const id of [startId, ...ids]) if (!seen.has(id)) visit(id);
    for (const id of ids) rank.set(id, id === startId ? 0 : 1);
    for (const id of topo.reverse()) {
        for (const l of out.get(id)) {
            if (!l.back && l.to !== endId) rank.set(l.to, Math.max(rank.get(l.to), rank.get(id) + 1));
        }
    }
    let last = 0;
    for (const id of ids) if (id !== endId) last = Math.max(last, rank.get(id));
    rank.set(endId, last + 1);
    // Warstwy z punktami przejścia dla kresek dłuższych niż rząd.
    const layers = Array.from({length: last + 2}, () => []);
    const items = new Map();
    for (const id of ids) {
        const item = {id, w: boxes.get(id).w, up: [], down: []};
        items.set(id, item);
        layers[rank.get(id)].push(item);
    }
    const chains = new Map();
    const segments = [];
    for (const l of links.values()) {
        const from = rank.get(l.from), to = rank.get(l.to);
        if (to <= from) continue;
        const chain = [items.get(l.from)];
        for (let r = from + 1; r < to; r++) {
            const dummy = {id: null, w: 22, up: [], down: []};
            layers[r].push(dummy);
            chain.push(dummy);
        }
        chain.push(items.get(l.to));
        chains.set(`${l.from}>${l.to}`, chain);
        chain.slice(1).forEach((item, i) => {
            segments.push([chain[i], item]);
            chain[i].down.push(item);
            item.up.push(chain[i]);
        });
    }
    if (seed) layers.forEach(layer => layer.sort(() => random() - 0.5));
    const crossings = () => {
        let n = 0;
        for (let r = 0; r + 1 < layers.length; r++) {
            const idx = new Map();
            layers[r].forEach((item, i) => idx.set(item, i));
            layers[r + 1].forEach((item, i) => idx.set(item, i));
            const segs = segments.filter(([a]) => idx.has(a) && layers[r].includes(a));
            for (let i = 0; i < segs.length; i++) {
                for (let j = i + 1; j < segs.length; j++) {
                    const d = (idx.get(segs[i][0]) - idx.get(segs[j][0])) * (idx.get(segs[i][1]) - idx.get(segs[j][1]));
                    if (d < 0) n++;
                }
            }
        }
        return n;
    };
    const sweep = (r, side) => {
        const layer = layers[r];
        const pos = new Map();
        layers[r + (side === 'up' ? -1 : 1)].forEach((item, i) => pos.set(item, i));
        const key = new Map(layer.map((item, i) => {
            const near = item[side === 'up' ? 'up' : 'down'];
            return [item, near.length ? near.reduce((sum, o) => sum + pos.get(o), 0) / near.length : i];
        }));
        layer.sort((a, b) => key.get(a) - key.get(b));
    };
    let bestOrder = layers.map(layer => [...layer]), best = crossings();
    for (let pass = 0; pass < 12 && best; pass++) {
        if (pass % 2 === 0) for (let r = 1; r < layers.length; r++) sweep(r, 'up');
        else for (let r = layers.length - 2; r >= 0; r--) sweep(r, 'down');
        const now = crossings();
        if (now < best) { best = now; bestOrder = layers.map(layer => [...layer]); }
    }
    bestOrder.forEach((layer, r) => { layers[r] = layer; });
    // Położenie w poziomie: kolejność zachowana, stały odstęp, środek jak
    // najbliżej środków sąsiadów (regresja izotoniczna po odjęciu odstępów).
    const GAP = 34;
    const cx = new Map();
    layers.forEach(layer => {
        let x = 0;
        for (const item of layer) { cx.set(item, x + item.w / 2); x += item.w + GAP; }
    });
    const settle = (layer, want) => {
        const offsets = [];
        let at = 0;
        for (const item of layer) { offsets.push(at); at += item.w + GAP; }
        const q = layer.map((item, i) => want(item) - item.w / 2 - offsets[i]);
        const blocks = [];
        for (const value of q) {
            blocks.push({sum: value, n: 1});
            while (blocks.length > 1) {
                const tail = blocks[blocks.length - 1], prev = blocks[blocks.length - 2];
                if (tail.sum / tail.n >= prev.sum / prev.n) break;
                blocks.splice(-2, 2, {sum: prev.sum + tail.sum, n: prev.n + tail.n});
            }
        }
        const fit = blocks.flatMap(block => Array(block.n).fill(block.sum / block.n));
        layer.forEach((item, i) => cx.set(item, fit[i] + offsets[i] + item.w / 2));
    };
    const mean = (list, fallback) => list.length ? list.reduce((sum, o) => sum + cx.get(o), 0) / list.length : fallback;
    for (let pass = 0; pass < 16; pass++) {
        const dir = pass % 4;
        const order = dir < 2 ? layers.map((_, r) => r) : layers.map((_, r) => layers.length - 1 - r);
        for (const r of order) {
            settle(layers[r], item => {
                const here = cx.get(item);
                if (dir === 0 || dir === 2) return item.up.length ? mean(item.up, here) : mean(item.down, here);
                return item.down.length ? mean(item.down, here) : mean(item.up, here);
            });
        }
    }
    // Start i cel stoją na środku reszty rysunku.
    const inner = [...cx].filter(([item]) => item.id !== startId && item.id !== endId);
    const lo = Math.min(...inner.map(([item, x]) => x - item.w / 2));
    const hi = Math.max(...inner.map(([item, x]) => x + item.w / 2));
    cx.set(items.get(startId), (lo + hi) / 2);
    cx.set(items.get(endId), (lo + hi) / 2);
    const ROW = SCHEME_BOX_H + 100;
    for (const [id, box] of boxes) {
        box.x = Math.round(cx.get(items.get(id)) - box.w / 2 - lo);
        box.y = rank.get(id) * ROW;
    }
    const rowMid = r => r * ROW + SCHEME_BOX_H / 2;
    const viaOf = key => {
        const chain = chains.get(key) || [];
        return chain.slice(1, -1).map((item, i) => [Math.round(cx.get(item) - lo), rowMid(rank.get(chain[0].id) + 1 + i)]);
    };
    assignPorts(boxes, strands);
    for (let pass = 0; pass < 3; pass++) assignPorts(boxes, strands, true);
    for (const s of strands) {
        const src = boxes.get(s.from), dst = boxes.get(s.to);
        // Kreska w górę (linia jedzie w stronę przeciwną do przepływu rysunku,
        // np. ta sama linia w obu kierunkach między dwoma przystankami)
        // zaczyna się na górnej krawędzi swojego pudełka i kończy na dolnej
        // krawędzi pudełka celu: obie końcówki leżą na pudełkach.
        s.up = rank.get(s.from) >= rank.get(s.to);
        s.points = s.up ? [[s.xs, src.y], [s.xt, dst.y + dst.h]]
                        : [[s.xs, src.y + src.h], ...viaOf(`${s.from}>${s.to}`), [s.xt, dst.y]];
        s.curved = true;
    }
    schemeMergeSiblings(strands);
    const routes = new Map();
    for (const pair of walkPairs) {
        const [a, b] = rank.get(pair.from) <= rank.get(pair.to) ? [pair.from, pair.to] : [pair.to, pair.from];
        const via = viaOf(`${a}>${b}`);
        if (via.length) routes.set(pair, via);
    }
    return routes;
}

function setSchemeCollapsed(collapsed) {
    schemeCollapsed = collapsed;
    const panel = $('scheme-panel');
    panel.classList.toggle('collapsed', collapsed);
    const toggle = $('scheme-toggle');
    const label = `${collapsed ? 'Pokaż' : 'Ukryj'} schemat`;
    toggle.textContent = collapsed ? '▸' : '▾';
    toggle.title = label;
    toggle.setAttribute('aria-label', label);
    toggle.setAttribute('aria-expanded', String(!collapsed));
    if (collapsed) hideSchemeTip();
}

setSchemeCollapsed(schemeCollapsed);

/** Ile miejsca po prawej zabiera schemat na szerokim ekranie - ta sama
    szerokość co .scheme-panel w style.css: min(560px, 45vw) i 16 px od
    brzegu. Liczona z reguły, a nie z panelu, bo przy pierwszym wyszukaniu
    mapa kadruje się, zanim schemat zdąży się pokazać. */
function schemeInset() {
    if (!setting('scheme-on') || $('scheme-panel').classList.contains('collapsed')) return 0;
    return Math.min(560, innerWidth * 0.45) + 16;
}

$('scheme-toggle').addEventListener('click', () => {
    setSchemeCollapsed(!schemeCollapsed);
    saveUiState({schemeCollapsed});
    if (!schemeCollapsed) fitScheme();
});
$('scheme-fit').addEventListener('click', fitScheme);

function applySchemeView() {
    const svg = $('scheme-canvas').querySelector('svg');
    if (!svg) return;
    svg.style.transform = `translate(${schemeView.x}px, ${schemeView.y}px) scale(${schemeView.scale})`;
}

/** Cały schemat w oknie panelu, ale nie większy niż naturalny - mały
    schemat nie puchnie, a duży da się potem przybliżyć. */
function fitScheme() {
    const viewport = $('scheme-viewport');
    const svg = viewport.querySelector('svg');
    if (!svg || schemeCollapsed) return;
    const box = svg.viewBox.baseVal;
    svg.setAttribute('width', box.width);
    svg.setAttribute('height', box.height);
    svg.style.maxWidth = 'none';
    const scale = Math.min(1, viewport.clientWidth / box.width, viewport.clientHeight / box.height);
    schemeView.scale = scale;
    schemeView.x = (viewport.clientWidth - box.width * scale) / 2;
    schemeView.y = Math.max(0, (viewport.clientHeight - box.height * scale) / 2);
    applySchemeView();
}

function zoomSchemeAt(px, py, factor) {
    const scale = Math.min(SCHEME_MAX_SCALE,
                           Math.max(SCHEME_MIN_SCALE, schemeView.scale * factor));
    const k = scale / schemeView.scale;
    schemeView.x = px - (px - schemeView.x) * k;
    schemeView.y = py - (py - schemeView.y) * k;
    schemeView.scale = scale;
    applySchemeView();
}

(() => {
    const viewport = $('scheme-viewport');
    const local = e => {
        const rect = viewport.getBoundingClientRect();
        return [e.clientX - rect.left, e.clientY - rect.top];
    };
    viewport.addEventListener('wheel', e => {
        e.preventDefault();
        hideSchemeTip();
        zoomSchemeAt(...local(e), Math.exp(-e.deltaY * 0.0015));
    }, {passive: false});

    // Przeciąganie jednym palcem (myszą) przesuwa, dwoma - przybliża. Kursor
    // łapiemy dopiero po kilku pikselach ruchu: inaczej dotknięcie węzła,
    // które ma otworzyć tablicę, stawałoby się początkiem przeciągania.
    const pointers = new Map();
    let dragging = false;
    viewport.addEventListener('pointerdown', e => {
        pointers.set(e.pointerId, local(e));
        dragging = false;
    });
    viewport.addEventListener('pointermove', e => {
        const last = pointers.get(e.pointerId);
        if (!last) return;
        const now = local(e);
        if (pointers.size === 1) {
            const dx = now[0] - last[0], dy = now[1] - last[1];
            if (!dragging && Math.hypot(dx, dy) < 4) return;
            if (!dragging) {
                dragging = true;
                viewport.setPointerCapture(e.pointerId);
                hideSchemeTip();
            }
            schemeView.x += dx;
            schemeView.y += dy;
            applySchemeView();
        } else if (pointers.size === 2) {
            const other = [...pointers.entries()].find(([id]) => id !== e.pointerId)[1];
            const before = Math.hypot(last[0] - other[0], last[1] - other[1]);
            const after = Math.hypot(now[0] - other[0], now[1] - other[1]);
            dragging = true;
            hideSchemeTip();
            if (before > 0) {
                zoomSchemeAt((now[0] + other[0]) / 2, (now[1] + other[1]) / 2, after / before);
            }
        }
        pointers.set(e.pointerId, now);
    });
    const release = e => { pointers.delete(e.pointerId); };
    viewport.addEventListener('pointerup', release);
    viewport.addEventListener('pointercancel', release);
    // Klik po przeciągnięciu to koniec przeciągania, nie wybór węzła.
    viewport.addEventListener('click', e => {
        if (dragging) { e.stopPropagation(); dragging = false; return; }
        if (!e.target.closest('g.node')) hideSchemeTip();
    }, true);
})();

// --- tablica odjazdów węzła ---------------------------------------------
//
// Ta sama tablica co pod kropką przesiadki na mapie (loadTimetable) - węzeł
// schematu JEST tą kropką. Pokazuje się po najechaniu, a na telefonie po
// dotknięciu, tak jak na mapie.

function hideSchemeTip() {
    schemeTipOwner = null;
    $('scheme-tip').hidden = true;
}

function showSchemeTip(element, where, sec) {
    const tip = $('scheme-tip');
    tip.className = 'timetable-tip scheme-tip';
    const owner = {
        // Udaje kropkę mapy na tyle, ile loadTimetable jej potrzebuje: dymek,
        // do którego wpisuje się tablica, gdy dojdzie z sieci.
        getTooltip: () => true,
        setTooltipContent: html => {
            if (schemeTipOwner !== owner) return;
            tip.innerHTML = html;
            placeSchemeTip(element);
        },
    };
    schemeTipOwner = owner;
    tip.hidden = false;
    loadTimetable(owner, where, sec);
}

/** Pod węzłem, a gdy tam brak miejsca - nad nim; zawsze w oknie. */
function placeSchemeTip(element) {
    const tip = $('scheme-tip');
    const node = element.getBoundingClientRect();
    const box = tip.getBoundingClientRect();
    const left = Math.max(8, Math.min(innerWidth - box.width - 8,
                                      node.left + node.width / 2 - box.width / 2));
    let top = node.bottom + 6;
    if (top + box.height > innerHeight - 8) top = Math.max(8, node.top - box.height - 6);
    tip.style.left = `${left}px`;
    tip.style.top = `${top}px`;
}

function bindSchemeNodes(canvas, data) {
    const byId = new Map(data.scheme.nodes.map(node => [node.id, node]));
    const mapNodes = new Map((data.nodes || []).map(node => [node.place, node]));
    const endNode = (data.nodes || []).find(node => node.end);
    for (const element of canvas.querySelectorAll('g.node')) {
        const node = byId.get(element.dataset.node);
        const mapNode = node && (node.role === 'end' ? endNode : node.place && mapNodes.get(node.place));
        if (!mapNode) continue;
        const where = {name: mapNode.name, lines: mapNode.lines, deadline: data.deadline_sec,
                       start: mapNode.start, end: mapNode.end, late: mapNode.late};
        const show = () => showSchemeTip(element, where, mapNode.sec);
        // Najechanie i zjechanie tylko myszą: palec po dotknięciu też
        // „zjeżdża” z węzła i tablica znikałaby, zanim się pokaże.
        element.addEventListener('pointerenter', e => { if (e.pointerType === 'mouse') show(); });
        element.addEventListener('pointerleave', e => {
            if (e.pointerType === 'mouse') hideSchemeTip();
        });
        element.addEventListener('click', show);
    }
}

/** Tożsamość trasy na liście: te same przejazdy tymi samymi kursami i te
    same przejścia. */
function journeyKey(journey) {
    return journey.legs.map(leg => [leg.kind, leg.line, leg.from, leg.to,
                                    leg.from_time, leg.to_time].join('|')).join('/');
}

/** Podmienia listę, zostawiając wybór na tej samej trasie, o ile ona na niej
    jest. Wybrana trasa leży wtedy na wierzchu nowej mapy, tak jak po
    kliknięciu. */
function pickJourneys(list) {
    const chosen = selectedJourney === null ? null : journeyKey(journeys[selectedJourney]);
    journeys = list;
    clearPreview();
    const index = chosen === null ? -1 : journeys.findIndex(j => journeyKey(j) === chosen);
    if (index < 0) {
        selectedJourney = null;
        clearJourney();
        dimFlow(false);
        return;
    }
    selectedJourney = index;
    if (journeyLayer) { map.removeLayer(journeyLayer); journeyLayer.addTo(map); }
}

function renderList(data) {
    if (!journeys.length) {
        // Pusta lista przy NIEPUSTEJ mapie to nie brak połączeń -
        // mapa pokazuje je tuż obok. Komunikat nie ma prawa temu
        // przeczyć (zdarza się przy szerokim oknie, gdy graf urośnie
        // ponad budżet szukania w _enumerate_journeys). Przy wyłączonych
        // propozycjach listy nie ma z wyboru, więc nie ma czego tłumaczyć.
        if (data.segments.length && !$('routes-on').checked) resultsBox.innerHTML = '';
        else if (data.segments.length) showWideWindowNotice();
        else showError('Nie znaleziono żadnego połączenia w tym oknie czasowym.');
    } else {
        renderJourneys();
    }
    loadTrainDelays();
    showWaitNotice(data);
    if (data.rail_only) showRailOnlyNotice();
}

/** Pierwszy etap nowego wyszukiwania: sam pasek „Najszybciej…" (decyzja
    użytkownika). Poprzednia relacja schodzi z mapy razem ze swoimi autami
    i rowerami, a mapa i lista pokazują się dopiero z prawdziwą mapą
    przepływów - do tego czasu lista mówi, że szuka. */
function renderHeadlineOnly(data) {
    clearFlow(false);
    renderScheme(null);
    headlineFlow = data;
    renderTimeHeadline();
}

// Pola z autami i rowerami przy mapie - przychodzą etapem po mapie.
const LIVE_FIELDS = {cars: [], cars_dest_in_zone: null, cars_ahead_sec: null,
                     cars_beyond: false, bike_places: [], bike_spots: [],
                     bike_places_live: false};

/** Mapa przychodzi bez aut i rowerów, a te liczą się po niej. Do tego czasu
    zostają te, które już są na ekranie, i podmieniają się naraz, gdy dojdą
    nowe - przerysowanie nie zostawia na chwilę mapy bez nich (decyzja
    użytkownika). Nowa relacja startuje bez nich: poprzednie zeszły
    z poprzednią mapą (patrz renderHeadlineOnly). */
function keepLive(flow, shown) {
    for (const [key, none] of Object.entries(LIVE_FIELDS)) {
        if (!(key in flow)) flow[key] = shown ? shown[key] : none;
    }
}

/** Auta, rowery i propozycje z nimi - dokładane do mapy, która już stoi:
    linie, wybrana trasa i okienko w rogu zostają. Nowe propozycje idą na
    koniec listy, żeby te, które już się czyta, nie skakały (decyzja
    użytkownika: lista będzie jeszcze przebudowana, na razie ma po prostu
    działać); znikają tylko te, których serwer już nie proponuje. */
function renderLive(data) {
    refreshBikeLayer();
    refreshCarLayer();
    // Auta i rowery mają leżeć pod kropkami i pod wybraną trasą, tak jak po
    // drawFlow - kursor ma trafić najpierw w to, co opisuje całe miejsce.
    for (const layer of [flowDotLayer, flowEndDots, journeyLayer]) {
        if (layer && map.hasLayer(layer)) { map.removeLayer(layer); layer.addTo(map); }
    }
    const fresh = new Map(data.journeys.map(j => [journeyKey(j), j]));
    const shown = journeys.map(journeyKey).filter(key => fresh.has(key));
    const order = [...shown, ...[...fresh.keys()].filter(key => !shown.includes(key))];
    pickJourneys(order.map(key => fresh.get(key)));
    renderList(data);
}

// Wyszukiwanie w toku: {abort, busy, search, acc}. Jedno naraz - nowe
// przerywa poprzednie, a serwer kończy przerwane na najbliższym etapie.
let planRun = null;

/** Zapytanie o mapę, czytane etapami (patrz routes.api_flow): każdy etap
    rysuje się, gdy tylko przyjdzie. `kind` mówi, kto pyta: 'search' - nowe
    wyszukiwanie (kadruje, gra rurę, zwija formularz na telefonie), 'quiet' -
    suwaki i przełączniki. Kończy się, gdy dojdzie
    ostatni etap albo gdy zastąpi je następne zapytanie. `onMapped` woła się
    po narysowaniu mapy z listą - tylko gdy to zapytanie wciąż jest aktualne. */
function loadPlan(kind, onMapped) {
    const prev = planRun;
    if (prev) prev.abort.abort();
    const run = planRun = {
        abort: new AbortController(),
        // Kółka przerwanego zapytania kręcą się dalej, aż skończy to, które
        // je zastąpiło - suwak ruszony w trakcie wyszukiwania nie może
        // zgasić „Szukaj", zanim cokolwiek się pokaże.
        busy: new Set([...(prev ? prev.busy : []), kind]),
        // Zapytanie, które przerwało wyszukiwanie przed dojściem mapy, samo
        // jest tym wyszukiwaniem: nowa relacja ma się wykadrować i tak dalej.
        search: kind === 'search' ? {found: false, mapped: false}
            : prev && prev.search && !prev.search.mapped ? prev.search : null,
        acc: {},
        onMapped,
    };
    showBusy(run.busy);
    syncAddress();
    return Promise.all([fetch('/api/flow?' + queryParams(), {signal: run.abort.signal}),
                        stopsReady])
        .then(([response]) => readLines(response, data => {
            if (planRun === run) applyStage(run, data);
        }))
        .catch(error => {
            if (!run.abort.signal.aborted) throw error;
        })
        .finally(() => {
            if (planRun !== run) return;
            planRun = null;
            showBusy(new Set());
        });
}

function applyStage(run, data) {
    if (data.error) {
        clearFlow();
        hideObWarn();
        renderScheme(null);
        showError(data.error, data.suggestions);
        return;
    }
    const {stage, ...delta} = data;
    // Etapy dopisują się do jednej odpowiedzi; mapa trzyma do niej
    // odwołanie (lastFlow), więc późniejsze etapy widzi od razu.
    Object.assign(run.acc, delta);
    const search = run.search && !run.search.mapped ? run.search : null;
    if (stage === 'fastest') {
        if (search) renderHeadlineOnly(run.acc);
    } else if (stage === 'map') {
        keepLive(run.acc, lastFlow);
        renderPlan(run.acc, !!search);
        if (search) search.mapped = true;
        if (run.onMapped) run.onMapped();
    } else if (lastFlow === run.acc) {
        renderLive(run.acc);
        // Auta z danych na żywo stoją też na schemacie - przychodzi z nimi
        // jego nowa wersja.
        if ('scheme' in delta) renderScheme(run.acc);
    }
    if (search && !search.found) {
        search.found = true;
        // Formularz na telefonie zwija się dopiero przy wyniku - przy
        // błędzie zostaje rozwinięty do poprawki (patrz phone.js).
        const at = whenMoment();
        document.dispatchEvent(new CustomEvent('planner:found', {detail: {
            arrive: arriveBy && !onboardOn,
            // Dzień tak jak w pastylce („jutro", „sb 28.11"); dziś bez dnia.
            day: isoDay(at) === isoDay(new Date()) ? '' : whenDayLabel(at).toLowerCase(),
        }}));
        playPipeDrop();
    }
}

/** Linijki NDJSON z odpowiedzi, każda zaraz po przyjściu. Odpowiedź bez
    końca linii (błąd offline z service workera) to po prostu jedna linijka. */
async function readLines(response, onLine) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    for (;;) {
        const {done, value} = await reader.read();
        buffer += decoder.decode(value, {stream: !done});
        const lines = buffer.split('\n');
        buffer = lines.pop();
        for (const line of lines) if (line.trim()) onLine(JSON.parse(line));
        if (done) break;
    }
    if (buffer.trim()) onLine(JSON.parse(buffer));
}

/** Kółko w toku: „Szukaj" (i linijka zwiniętego formularza na telefonie)
    przy wyszukiwaniu. */
function showBusy(busy) {
    setSearching(busy.has('search'));
}

/** Rower i auto zmieniają ODPOWIEDŹ, nie tylko wygląd mapy: propozycje
    z rowerem i z Traficarem składa serwer (patrz queryParams), więc po
    przełączeniu warstwy trzeba je doliczyć albo zdjąć. Tą samą drogą co
    suwaki w ⚙ (bez kadrowania), a nie przez nowe wyszukiwanie - relacja się
    nie zmieniła, więc kadr ma zostać na miejscu i nie ma po co znowu
    odgrywać dźwięku znalezienia trasy. Start z pokładu nie ma pola „skąd",
    więc o komplecie pytania mówi tam wybór pojazdu (patrz onboardReady). */
function replanInPlace() {
    if (!lastFlow || !endInput.value) return;
    if (onboardOn ? !onboardReady() : !startInput.value) return;
    loadPlan('quiet').catch(() => showError('Nie udało się połączyć z serwerem.'));
}

const LAST_SEARCH_KEY = 'metal-planner:last-search';

// Jedno miejsce na przedawnienie ostatniej trasy RAZEM z jej godziną.
// Infinity - dziś trasa nie wygasa; skończony czas w ms wygasza obie naraz.
const LAST_SEARCH_MAX_AGE_MS = Infinity;

function saveLastSearch() {
    // Podróży z pokładu nie zapamiętujemy: kurs, którym się jechało, dawno
    // odjechał, a przywrócona po godzinie linia z przystankiem byłaby
    // odpowiedzią na pytanie, którego już nikt nie zadaje.
    if (onboardOn) { forgetLastSearch(); return; }
    try {
        // Godzina i data wracają z trasą tylko wtedy, gdy pasażer sam je wpisał -
        // „teraz" z formularza po godzinie przestałoby nim być.
        localStorage.setItem(LAST_SEARCH_KEY, JSON.stringify({
            start: sel.start, end: sel.end, savedAt: Date.now(),
            ...(timeChosen ? {time: $('time').value, date: $('date').value,
                              ...(arriveBy ? {arrive: true} : {})} : {}),
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

// Ostatnie wyszukiwanie nie wraca samo, tylko pyta (#237): po otwarciu
// strony to często już nie ta podróż, a na telefonie samo wyszukanie zwija
// formularz, który trzeba by rozwinąć, żeby zacząć od nowa.
const recallOffer = $('recall-offer');
let recalled = null;      // {start, end} - o którą trasę padło pytanie

function hideRecallOffer() {
    recalled = null;
    recallOffer.hidden = true;
}

/** Po odświeżeniu strony pyta o ostatnie wyszukiwanie (skąd/dokąd) - tylko
    gdy pola są jeszcze puste (nie nadpisujemy tego, co user już zdążył
    wpisać, zanim ten kod się uruchomił). */
function restoreLastSearch() {
    if (onboardOn || startInput.value || endInput.value) return;
    let saved;
    try {
        saved = JSON.parse(localStorage.getItem(LAST_SEARCH_KEY));
    } catch {
        return;
    }
    if (!saved || !saved.start || !saved.end) return;
    if (Date.now() - (saved.savedAt || 0) > LAST_SEARCH_MAX_AGE_MS) return;
    recalled = saved;
    // Każdy koniec w osobnym kawałku, który się nie łamie - linia może pęknąć
    // tylko przed strzałką, a nie w środku nazwy ("Iwiny -" / "nr" / "127").
    const end = text => {
        const span = document.createElement('span');
        span.className = 'recall-offer-end';
        span.textContent = span.title = text;
        return span;
    };
    $('recall-offer-name').replaceChildren(
        end(displayValue(saved.start)), ' ', end('→ ' + displayValue(saved.end)),
        ...(savedWhenFresh(saved) ? [' ', end('· ' + recalledTimeLabel(saved))] : []));
    recallOffer.hidden = false;
}

/** Godzina zapamiętana z trasą wraca tylko, dopóki nie minęła - inaczej
    przywrócona trasa szukałaby połączeń, które już odjechały. */
function savedWhenFresh(saved) {
    if (!saved.time || !saved.date) return false;
    const [hh, mm] = saved.time.split(':').map(Number);
    const day = dayOfIso(saved.date);
    return !momentPassed(new Date(day.getFullYear(), day.getMonth(), day.getDate(), hh, mm));
}

/** „8:00" dla dzisiaj, „jutro 8:00" albo „24.10 8:00" - co wróci z trasą. */
function recalledTimeLabel(saved) {
    const clock = (saved.arrive ? 'na ' : '') + saved.time.replace(/^0/, '');
    const day = new Date();
    const iso = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
                   + `-${String(d.getDate()).padStart(2, '0')}`;
    if (saved.date === iso(day)) return clock;
    day.setDate(day.getDate() + 1);
    if (saved.date === iso(day)) return `jutro ${clock}`;
    const [, month, date] = saved.date.split('-');
    return `${Number(date)}.${month} ${clock}`;
}

function applyLastSearch(saved) {
    sel.start = saved.start;
    sel.end = saved.end;
    startInput.value = displayValue(sel.start);
    endInput.value = displayValue(sel.end);
    // Wraca tylko godzina wpisana ręcznie i jeszcze aktualna; inaczej „teraz".
    if (savedWhenFresh(saved)) {
        timeInput.value = saved.time;
        $('date').value = saved.date;
        timeChosen = true;
        setArriveBy(!!saved.arrive);
        paintWhen();
    }
    updatePointMarker('start', sel.start);
    updatePointMarker('end', sel.end);
    restyle(sel.start, sel.end);
    search();
}

$('recall-offer-yes').addEventListener('click', () => {
    const saved = recalled;
    hideRecallOffer();
    // Pasażer mógł już zacząć nową relację - pytanie dotyczy wtedy pustego
    // formularza, którego już nie ma.
    if (onboardOn || startInput.value || endInput.value) return;
    applyLastSearch(saved);
});
// "Nie" zapomina trasę jak ✕ - inaczej pytanie wracałoby przy każdym otwarciu.
$('recall-offer-no').addEventListener('click', () => {
    hideRecallOffer();
    forgetLastSearch();
});
startInput.addEventListener('input', hideRecallOffer);
endInput.addEventListener('input', hideRecallOffer);

/** Kółko ładowania w kilku miejscach naraz, bo w każdym widoku widać co
    innego: w komunikacie pod kartą (szeroki ekran, zakładka „Trasy"), na
    przycisku „Szukaj" (telefon w widoku mapy - tam wyników nie widać, a
    wyszukiwanie odpala się samo po drugim kliknięciu w mapę) i w linijce
    zwiniętego formularza na telefonie (patrz phone.css). Kręci się, aż
    dojdzie ostatni etap - także wtedy, gdy pierwsze wyniki już widać.
    Przycisk zostaje klikalny: nowe wyszukiwanie przerywa to w toku. */
function setSearching(on) {
    document.body.classList.toggle('searching', on);
    resultsBox.setAttribute('aria-busy', String(on));
}

/** `fromLink`: to wyszukiwanie wywołał link (patrz applyLink). Idzie tylko
    jako argument, nigdy jako stan modułu - inaczej wyszukiwanie, którego link
    nie uruchomił (np. `from=gps` bez `to`), odziedziczyłoby je po późniejszym
    ręcznym szukaniu. */
function search({fromLink = false} = {}) {
    // Z pokładu pola "skąd" nie ma - kompletem jest linia, kierunek
    // i przystanek (patrz onboardReady).
    if (!endInput.value) return;
    if (onboardOn ? !onboardReady() : !startInput.value) return;
    // Rozwinięty przez „zmień” wybór pojazdu wraca po szukaniu do jednej
    // linijki tak samo jak po wybraniu przystanku - inaczej trzy pola
    // zasłaniają wynik.
    if (onboardOn) { obCollapsed = true; syncOnboardView(); }
    hideRecallOffer();
    selectedJourney = null;
    clearJourney();
    clearPreview();
    resultsBox.innerHTML =
        '<div class="notice loading"><span class="spinner" aria-hidden="true"></span>' +
        'Szukam połączeń…</div>';
    // Trasa z cudzego linku nie jest wyborem pasażera: nie wypiera jego
    // ostatniej trasy ani nie trafia do jego podpowiedzi.
    if (!fromLink) {
        saveLastSearch();
        if (!onboardOn) rememberPlace('start', startInput.value);
        rememberPlace('end', endInput.value);
    }
    // Widoku nie przełączamy sami - kto szuka z mapy, ten chce zostać na
    // mapie i zobaczyć na niej przebieg. Że wyniki są, mówi licznik przy
    // zakładce „Trasy". Rura spada przy pierwszym wyniku WYSZUKANIA, nie po
    // każdym przeliczeniu (patrz applyStage).
    loadPlan('search').catch(() => showError('Nie udało się połączyć z serwerem.'));
}

$('search').addEventListener('click', search);

// ------------------------------------------------------------------ link ----
//
// Wyszukiwanie jako zwykły tekst po „?": ?from=Katedra&to=Wojszyce&time=17:40.
// Ten sam adres leży w pasku przeglądarki po każdym wyszukaniu (syncAddress),
// więc kopiowanie z paska działa jak udostępnianie.
// Czego link NIE niesie, a czemu: patrz linkParams.

const POINT_PARAM = /^(-?\d{1,3}\.\d+),(-?\d{1,3}\.\d+)$/;
const pointParam = point => `${point.lat.toFixed(5)},${point.lon.toFixed(5)}`;

function placeFromParam(text) {
    const match = text.match(POINT_PARAM);
    return match ? {lat: Number(match[1]), lon: Number(match[2])} : text;
}

const todayIso = () => {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
         + `-${String(now.getDate()).padStart(2, '0')}`;
};

/** Parametry linku dla tego, co jest teraz w formularzu.

    Niesie: skąd i dokąd, godzinę i datę (tylko gdy wybrane ręcznie - „teraz"
    u nadawcy znaczy „teraz" u odbiorcy). Nic więcej: ani filtrów, ani
    ustawień z ⚙, ani startu z pokładu pojazdu (kurs, którym się jechało, odjechał).
    Start z ◎ (też z zapisanego miejsca) to `from=gps`: odbiorca szuka ze SWOJEJ
    pozycji, a pozycja nadawcy nie jest nikomu do udostępnienia. */
function linkParams() {
    const params = new URLSearchParams();
    if (onboardOn || (!startInput.value && !endInput.value)) return params;
    if (hereIsStart()) params.set('from', 'gps');
    else if (startInput.value) {
        params.set('from', isPoint(sel.start) ? pointParam(sel.start) : startInput.value);
    }
    if (endInput.value) {
        params.set('to', isPoint(sel.end) ? pointParam(sel.end) : endInput.value);
    }
    if (timeChosen) {
        params.set('time', $('time').value);
        if ($('date').value !== todayIso()) params.set('date', $('date').value);
        if (arriveBy) params.set('arrive', '1');
    }
    return params;
}

// Dwukropek i przecinek są w zapytaniu legalne, a "17%3A40" nie jest czytelne.
const readableQuery = params => params.toString().replace(/%3A/gi, ':').replace(/%2C/gi, ',');

function syncAddress() {
    const params = linkParams();
    const query = params ? readableQuery(params) : '';
    history.replaceState(null, '', query ? '?' + query : location.pathname);
}

const SHARE_DONE_MS = 1500;
let shareDoneTimer = null;

/** Arkusz udostępniania tam, gdzie system go ma (telefony, Safari), a poza
    tym schowek - z cichym potwierdzeniem na samej ikonie. */
$('share').addEventListener('click', async event => {
    const button = event.currentTarget;
    if (navigator.share) {
        try {
            await navigator.share({title: 'Metal Planner', url: location.href});
            return;
        } catch (error) {
            if (error.name === 'AbortError') return;
        }
    }
    try {
        await navigator.clipboard.writeText(location.href);
    } catch {
        // schowek zablokowany (np. strona po http) - zostaje ręczne kopiowanie
        window.prompt('Skopiuj link:', location.href);
        return;
    }
    button.textContent = '✓';
    button.title = 'Skopiowano';
    clearTimeout(shareDoneTimer);
    shareDoneTimer = setTimeout(() => {
        button.textContent = '⤴';
        button.title = 'Udostępnij link do tej trasy';
    }, SHARE_DONE_MS);
});

/** Wchodząc z linku: formularz wypełnia się tak, jakby pasażer to wpisał,
    a gdy są oba końce, od razu szuka. Brakujący koniec zostaje pusty z kursorem
    w nim; jedynym wyjątkiem jest `from=gps` - lokalizacja odbiorcy. */
function applyLink() {
    const query = new URLSearchParams(location.search);
    const from = query.get('from');
    // Cel z lokalizacji nie istnieje, więc `to=gps` zostawia „Dokąd" puste.
    const to = query.get('to') === 'gps' ? null : query.get('to');
    if (!from && !to) return;
    const time = /^\d{1,2}:\d{2}$/.test(query.get('time')) ? query.get('time').padStart(5, '0') : null;
    const date = /^\d{4}-\d{2}-\d{2}$/.test(query.get('date')) ? query.get('date') : null;
    // Dzień sprzed rozkładu nie ma odpowiedzi, więc zostaje „teraz". Dzień za
    // rozkładem zostaje: wyszukiwanie powie, że rozkładu jeszcze nie opublikowano.
    if (!(date && date < firstDayIso())) {
        if (time) {
            $('time').value = time;
            timeChosen = true;
            setArriveBy(query.get('arrive') === '1');
        }
        if (date) {
            $('date').value = date;
            timeChosen = true;
        }
    }
    paintWhen();
    const fill = (side, text) => {
        const place = placeFromParam(text);
        if (isPoint(place)) { sel[side] = place; updatePointMarker(side, place); }
        (side === 'start' ? startInput : endInput).value = displayValue(place);
    };
    if (from && from !== 'gps') fill('start', from);
    if (to) fill('end', to);
    restyle(sel.start, sel.end);
    // Puste pole zostaje puste: link nie szuka za pasażera ani nie sięga
    // po lokalizację bez `from=gps`.
    if (from === 'gps') {
        // Szuka sam useMyLocation, gdy cel jest wpisany.
        if (navigator.geolocation) locateStart(undefined, {fromLink: true});
        else startInput.focus();
        focusEnd();
    } else if (from && to) {
        search({fromLink: true});
    } else if (to) startInput.focus();
    else focusEnd();
}

stopsReady.then(applyLink);
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
const STOP_KIND = new Map(STOP_ENTRIES.map(e => [e.name, e.kind]));
const FOLDED_LABELS = STOP_NAMES.map(fold);

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
const ALIAS_LABELS = STOP_NAMES.map(label => expand(fold(label)));

/** Trafienia od początku nazwy przed trafieniami w środku - wpisując "grun"
    chcemy najpierw "Grunwaldzki", a nie "pl. Grunwaldzki" alfabetycznie.
    Trafienia po rozwinięciu skrótu idą na koniec: są najluźniejsze, tak samo
    jak po stronie serwera (patrz gtfs.match_stop). */
function suggestionsFor(query, names = STOP_NAMES, folded, limit = MAX_SUGGESTIONS) {
    const needle = fold(query.trim());
    if (!needle) return [];
    const own = names === STOP_NAMES;
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
    return [...prefix, ...inside, ...aliased]
        .filter(item => trainsOn || !own || STOP_KIND.get(item.name) !== 'train')
        .slice(0, limit);
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

// Ostatnie miejsca, OSOBNO dla "skąd" i "dokąd" (#142): wpisując "p" w starcie
// ma wyjść najpierw pl. Grunwaldzki, z którego się zawsze jeździ, a nie
// pl. Legionów, który jest wyżej alfabetycznie. Same nazwy, bez całych relacji
// - wersja lekka. Tylko w tej przeglądarce, więc telefon ma swoją listę.
const RECENT_PLACES_KEY = 'metal-planner:recent-places';
const RECENT_PLACES_MAX = 10;

function loadRecentPlaces() {
    try {
        return JSON.parse(localStorage.getItem(RECENT_PLACES_KEY)) || {};
    } catch {
        return {};
    }
}

/** Kliknięty punkt ("(51.0720, 17.0921)") nie jest nazwą, którą dałoby się
    wpisać, więc go nie zapamiętujemy - zapamiętujemy tylko etykiety z listy
    przystanków. */
function rememberPlace(side, label) {
    if (!STOP_KIND.has(label)) return;
    const recent = loadRecentPlaces();
    recent[side] = [label, ...(recent[side] || []).filter(name => name !== label)]
        .slice(0, RECENT_PLACES_MAX);
    try {
        localStorage.setItem(RECENT_PLACES_KEY, JSON.stringify(recent));
    } catch {
        // localStorage niedostępny - podpowiedzi działają dalej, tylko bez historii
    }
}

/** Trafienia z ostatnich miejsc na początek, od najświeższego; reszta
    w swojej dotychczasowej kolejności. */
function recentFirst(items, recent) {
    const rank = name => {
        const i = recent.indexOf(name);
        return i < 0 ? recent.length : i;
    };
    return items.map((item, i) => ({item, i}))
        .sort((a, b) => rank(a.item.name) - rank(b.item.name) || a.i - b.i)
        .map(({item}) => item);
}

// Ostatnie miejsca szukane osobno w ich krótkiej liście, żeby przy jednej
// literze nie sortować tysięcy trafień ze wszystkich przystanków.
const suggestRecentFirst = side => query => {
    const recent = (loadRecentPlaces()[side] || []).filter(
        name => STOP_KIND.has(name) && (trainsOn || STOP_KIND.get(name) !== 'train'));
    const hits = recentFirst(suggestionsFor(query, recent, undefined, Infinity), recent);
    const rest = suggestionsFor(query, STOP_NAMES, undefined, MAX_SUGGESTIONS + hits.length)
        .filter(item => !recent.includes(item.name));
    return [...hits, ...rest].slice(0, MAX_SUGGESTIONS);
};

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
    // `refresh` przelicza otwartą listę, gdy podpowiedzi doszły później
    // (numery pociągów idą z serwera, patrz obTrainSuggestions).
    return {close, refresh: () => { if (document.activeElement === input) open(); }};
}

// Wybór podpowiedzi kończy tryb "wybrany punkt" i - gdy relacja jest
// kompletna - od razu szuka, tak samo jak klik w mapę.
attachAutocomplete(startInput, () => {
    if (isPoint(sel.start)) { sel.start = null; updatePointMarker('start', null); }
    if (endInput.value) search();
    else focusEnd();
}, {suggest: suggestRecentFirst('start'),
    onEnter: () => (startInput.value.trim() && !endInput.value.trim() ? focusEnd() : search())});
attachAutocomplete(endInput, () => {
    if (isPoint(sel.end)) { sel.end = null; updatePointMarker('end', null); }
    if (startInput.value) search();
    else if (!onboardOn) startFromHere();
}, {suggest: suggestRecentFirst('end'),
    onEnter: () => (endInput.value.trim() && !startInput.value.trim() && !onboardOn
                    ? startFromHere() : search())});

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
function setTimeNow() {
    timeChosen = false;
    const now = new Date();
    $('time').value = now.toTimeString().slice(0, 5);
    $('date').value = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
                    + `-${String(now.getDate()).padStart(2, '0')}`;
    paintWhen();
}

// Powrót do aplikacji z tła: pole ma pokazywać dzisiejszą chwilę, zanim
// pasażer kliknie Szukaj. Ręcznie ustawiona godzina obowiązuje, dopóki nie
// minie; potem wraca „teraz" (odjazd - „przyjazd teraz" nie ma sensu).
document.addEventListener('visibilitychange', () => {
    if (document.hidden) return;
    const passed = whenPassed();
    if (passed) setArriveBy(false);
    if (!timeChosen || passed) setTimeNow();
});

// ------------------------------------- wybór godziny i daty (#501) ---
// Kółka są tylko twarzą pól #time i #date (leżą w #when-fields i jeżdżą też do
// Rozkładów): wyszukiwanie, link i zapamiętana trasa czytają je tak samo jak zawsze.
const whenBox = $('when');
const WEEKDAYS = ['nd', 'pn', 'wt', 'śr', 'cz', 'pt', 'sb'];
const WHEEL_DAYS = 14;
const WHEEL_H = 40;
// Przełączenie z „teraz" na „Przyjazd": od której chwili liczy się domyślną godzinę.
// Pierwsza pełna godzina co najmniej pół godziny od teraz: tuż przed pełną
// godziną (3:58) najbliższa (4:00) nie dawała szans dojechać, więc 5:00.
const arriveFromNow = now => {
    const soonest = new Date(now.getTime() + 30 * 60000);
    const hour = new Date(soonest.getFullYear(), soonest.getMonth(), soonest.getDate(), soonest.getHours());
    return hour < soonest ? new Date(hour.getTime() + 3600000) : hour;
};
let whenSearchTimer = null;
let wheelsChanged = false;
let wheelTimer = null;
// Koła w układzie telefonu, okienko z polem godziny w układzie komputera - to
// samo przełączenie, co dla całej reszty aplikacji (phoneLayout).
let whenWheels = false;
// Czy od otwarcia wyboru pasażer sam zmienił godzinę albo dzień (patrz setWhenMode).
let whenTouched = false;
let whenSnapshot = null;

const midnight = d => new Date(d.getFullYear(), d.getMonth(), d.getDate());
const isoDay = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
    + `-${String(d.getDate()).padStart(2, '0')}`;
const clockOf = minutes => `${String(Math.floor(minutes / 60)).padStart(2, '0')}:`
    + String(minutes % 60).padStart(2, '0');
const minutesOf = d => d.getHours() * 60 + d.getMinutes();

// Pierwszy i ostatni dzień z rozkładem jadą z serwera: tylko na nie da się
// cokolwiek wyszukać. Bez bazy ich nie ma - wtedy od dziś.
const firstDayIso = () => $('when-fields').dataset.first || isoDay(new Date());
const lastDayIso = () => $('when-fields').dataset.last;
const dayOfIso = iso => {
    const [y, m, d] = iso.split('-').map(Number);
    return new Date(y, m - 1, d);
};
function clampIso(iso) {
    const last = lastDayIso();
    if (iso < firstDayIso()) return firstDayIso();
    return last && iso > last ? last : iso;
}
function clampMoment(moment) {
    const iso = isoDay(moment);
    const clamped = clampIso(iso);
    if (clamped === iso) return moment;
    const [y, m, d] = clamped.split('-').map(Number);
    return clamped < iso ? new Date(y, m - 1, d, 23, 55) : new Date(y, m - 1, d, 0, 0);
}

// Nieustawiona ręcznie godzina to „teraz", a pola formularza odświeżają się
// dopiero przy wyszukaniu albo powrocie z tła.
function whenMoment() {
    if (!timeChosen) return new Date();
    const [y, m, d] = $('date').value.split('-').map(Number);
    const [hh, mm] = (timeInput.value || '00:00').split(':').map(Number);
    return new Date(y, m - 1, d, hh, mm);
}

function whenDayLabel(day) {
    const diff = Math.round((midnight(day) - midnight(new Date())) / 86400000);
    if (diff === 0) return 'Dziś';
    if (diff === 1) return 'Jutro';
    if (diff === -1) return 'Wczoraj';
    return `${WEEKDAYS[day.getDay()]} ${day.getDate()}.${String(day.getMonth() + 1).padStart(2, '0')}`;
}

// Minuta wybrana ręcznie już minęła (ta, która trwa, jeszcze nie).
const momentPassed = moment => moment.getTime() + 60000 <= Date.now();
const whenPassed = () => timeChosen && momentPassed(whenMoment());

function writeWhen(moment) {
    timeInput.value = clockOf(minutesOf(moment));
    $('date').value = isoDay(moment);
    timeChosen = true;
}

// Kilka stuknięć pod rząd (+15, +15) to jedno wyszukiwanie.
function searchWhenSoon() {
    clearTimeout(whenSearchTimer);
    whenSearchTimer = setTimeout(() => {
        if (startInput.value && endInput.value) search();
    }, 600);
}

// Rozkłady pokazują odjazdy, a z pokładu jedzie się teraz: tam przyjazdu nie ma.
const inTimetable = () => document.body.classList.contains('mode-timetable');
const whenArrive = () => arriveBy && !onboardOn && !inTimetable();

function whenModeName() {
    return whenArrive() ? 'Przyjazd' : 'Odjazd';
}

// Przyjazd z „teraz" dostaje najbliższą pełną godzinę; powrót do odjazdu bez
// ruszania godziny i dnia oddaje „teraz", które było przy otwarciu wyboru.
function setWhenMode(arrive) {
    setArriveBy(arrive);
    if (arrive && !timeChosen) writeWhen(clampMoment(arriveFromNow(new Date())));
    if (!arrive && whenSnapshot && !whenSnapshot.chosen && !whenTouched) {
        setTimeNow();
        return;
    }
    paintWhen();
}

function nowChip() {
    return `<button type="button" class="when-chip${timeChosen ? '' : ' on'}" data-now>Teraz</button>`;
}

function paintPill() {
    const at = whenMoment();
    const pill = whenBox.querySelector('.when-pill');
    pill.disabled = onboardOn;
    pill.title = onboardOn ? 'W pojeździe liczy się od teraz' : '';
    // Tekst w jednym kawałku: odstęp pastylki rozsuwał „13:00 , dziś".
    pill.innerHTML = `<span>${whenModeName()} `
        + (timeChosen ? `<b>${clockOf(minutesOf(at))}</b>, ${whenDayLabel(at).toLowerCase()}` : '<b>teraz</b>')
        + `</span>${onboardOn ? '' : ' <span class="when-caret">▾</span>'}`;
}

function paintWhen() {
    if (!whenBox.firstChild) {
        whenBox.innerHTML = '<button type="button" class="when-pill" aria-haspopup="dialog"></button>';
        whenBox.firstChild.addEventListener('click', openWhen);
    }
    paintPill();
}

// Z pokładu jedzie się teraz: wybrana godzina wskazywałaby inny kurs niż ten,
// którym się jedzie. Pastylka stoi wtedy na „teraz" i się nie otwiera, a po
// wyjściu z pojazdu wraca wcześniejszy wybór - o ile jeszcze nie minął.
let whenBeforeOnboard = null;
document.addEventListener('planner:startmode', () => {
    if (onboardOn && !whenBeforeOnboard) {
        whenBeforeOnboard = timeChosen
            ? {arrive: arriveBy, time: timeInput.value, date: $('date').value} : {};
        setArriveBy(false);
        setTimeNow();
    } else if (!onboardOn && whenBeforeOnboard) {
        const before = whenBeforeOnboard;
        whenBeforeOnboard = null;
        if (savedWhenFresh(before)) {
            timeInput.value = before.time;
            $('date').value = before.date;
            timeChosen = true;
            setArriveBy(before.arrive);
        }
    }
    paintWhen();
});

// Kółka: arkusz leży w <body>, bo panel z kartą przycina to, co z niego wystaje.
const whenBackdrop = document.createElement('div');
whenBackdrop.className = 'when-backdrop';
whenBackdrop.hidden = true;
const whenSheet = document.createElement('div');
whenSheet.className = 'when-sheet';
whenSheet.hidden = true;
whenSheet.setAttribute('role', 'dialog');
whenSheet.setAttribute('aria-label', 'Godzina odjazdu');
// Klik w puste miejsce okienka zostawia fokus w nim, więc Enter i Esc działają dalej.
whenSheet.tabIndex = -1;
document.body.append(whenBackdrop, whenSheet);

function wheelHtml(name, label, items) {
    return `<div class="when-wheel" data-wheel="${name}" tabindex="0" aria-label="${label}">`
        + items.map(([value, text]) => `<div class="when-item" data-v="${value}">${text}</div>`).join('')
        + '</div>';
}

function sheetTopHtml() {
    return `${modesHtml()}
        <div class="when-sheet-head">
            ${whenArrive() ? '' : nowChip()}
            <button type="button" class="button primary when-done" data-done>Gotowe</button>
        </div>`;
}

function modesHtml() {
    if (inTimetable()) return '';
    return `<div class="when-modes" role="tablist">
            <button type="button" role="tab" class="when-mode${arriveBy ? '' : ' on'}" data-mode="depart"
                    aria-selected="${!arriveBy}">Odjazd</button>
            <button type="button" role="tab" class="when-mode${arriveBy ? ' on' : ''}" data-mode="arrive"
                    aria-selected="${arriveBy}"${onboardOn ? ' disabled' : ''}>Przyjazd</button>
        </div>`;
}

// Dni z rozkładem, a do tego dzień już wybrany, gdy (z linku) leży poza nimi -
// inaczej samo otwarcie wyboru przestawiłoby go na inny.
function rangeDays() {
    const first = dayOfIso(firstDayIso());
    const lastIso = lastDayIso()
        || isoDay(new Date(first.getFullYear(), first.getMonth(), first.getDate() + WHEEL_DAYS - 1));
    const days = [];
    for (let day = first; isoDay(day) <= lastIso;
         day = new Date(day.getFullYear(), day.getMonth(), day.getDate() + 1)) {
        days.push(day);
    }
    const picked = midnight(whenMoment());
    if (!days.some(day => isoDay(day) === isoDay(picked))) {
        days.push(picked);
        days.sort((a, b) => a - b);
    }
    return days;
}

function openWhen() {
    whenSnapshot = {arrive: arriveBy, chosen: timeChosen, time: timeInput.value, date: $('date').value};
    whenWheels = phoneLayout.active();
    wheelsChanged = false;
    whenTouched = false;
    whenBackdrop.hidden = false;
    whenSheet.hidden = false;
    whenSheet.classList.toggle('when-desk', !whenWheels);
    if (whenWheels) openWheels();
    else openDesk();
}

function openWheels() {
    const days = rangeDays().map(day => [isoDay(day), whenDayLabel(day)]);
    const hours = Array.from({length: 24}, (_, h) => [h, String(h).padStart(2, '0')]);
    const minutes = Array.from({length: 12}, (_, i) => [i * 5, String(i * 5).padStart(2, '0')]);
    whenSheet.innerHTML = `<div class="when-sheet-top">${sheetTopHtml()}</div>
        <div class="when-wheels">
            ${wheelHtml('day', 'Dzień', days)}${wheelHtml('hour', 'Godzina', hours)}${wheelHtml('min', 'Minuta', minutes)}
            <div class="when-band"></div>
        </div>`;
    whenSheet.style.top = whenSheet.style.left = '';
    scrollWheels('instant');
}

// Na komputerze zamiast kół: godzina wpisywana z klawiatury i dni do kliknięcia.
// Kółko myszy i gładzik nie umieją kręcić kolumną o jedną pozycję (gładzik
// rzucał godziną na koniec doby), a przeciąganie myszą nie działa w ogóle.
function openDesk() {
    // Przyciski na dole zostają widoczne zawsze; w niskim oknie przewija się
    // tylko to, co nad nimi (pasek przewijania bywa schowany, więc „Gotowe"
    // pod krawędzią łatwo przeoczyć).
    whenSheet.innerHTML = `<div class="when-desk-body">
            ${modesHtml()}
            <div class="when-desk-time">
                <input type="text" class="when-time" inputmode="numeric" maxlength="5" autocomplete="off"
                       aria-label="Godzina" title="Wpisz godzinę, np. 1745; strzałki ↑↓ zmieniają godzinę albo minuty">
            </div>
            ${calendarHtml()}
        </div>
        <div class="when-desk-foot">
            ${nowChip()}
            <button type="button" class="button primary when-done" data-done>Gotowe</button>
        </div>`;
    paintDesk();
    placeDesk();
    const input = whenSheet.querySelector('.when-time');
    input.focus();
    input.select();
}

// Mały kalendarz dni z rozkładem: tygodnie od poniedziałku, w komórce numer
// dnia, a na początku nowego miesiąca jego skrót. Dni spoza rozkładu są puste.
const MONTHS = ['sty', 'lut', 'mar', 'kwi', 'maj', 'cze', 'lip', 'sie', 'wrz', 'paź', 'lis', 'gru'];

function calendarHtml() {
    const days = rangeDays();
    const inRange = new Set(days.map(isoDay));
    const first = days[0];
    const last = days[days.length - 1];
    const monday = new Date(first.getFullYear(), first.getMonth(), first.getDate() - (first.getDay() + 6) % 7);
    const today = isoDay(new Date());
    const cells = [];
    for (let day = monday; day <= last || cells.length % 7;
         day = new Date(day.getFullYear(), day.getMonth(), day.getDate() + 1)) {
        const iso = isoDay(day);
        if (!inRange.has(iso)) {
            cells.push('<span class="when-cal-off" aria-hidden="true"></span>');
            continue;
        }
        const newMonth = day.getMonth() !== first.getMonth() && day.getDate() === 1;
        cells.push(`<button type="button" role="radio" class="when-day${iso === today ? ' today' : ''}"
                data-day="${iso}" aria-label="${whenDayLabel(day)}">`
            + (newMonth ? `<span class="when-cal-month">${MONTHS[day.getMonth()]}</span>` : '')
            + `${day.getDate()}</button>`);
    }
    return `<div class="when-cal" role="radiogroup" aria-label="Dzień">
            ${['pn', 'wt', 'śr', 'cz', 'pt', 'sb', 'nd'].map(name => `<span class="when-cal-head">${name}</span>`).join('')}
            ${cells.join('')}
        </div>`;
}

function paintDesk() {
    const at = whenMoment();
    const input = whenSheet.querySelector('.when-time');
    input.value = input.dataset.shown = clockOf(minutesOf(at));
    input.classList.remove('bad');
    for (const tab of whenSheet.querySelectorAll('[data-mode]')) {
        const on = (tab.dataset.mode === 'arrive') === arriveBy;
        tab.classList.toggle('on', on);
        tab.setAttribute('aria-selected', on);
    }
    const now = whenSheet.querySelector('[data-now]');
    now.hidden = whenArrive();
    now.classList.toggle('on', !timeChosen);
    paintDays();
}

function paintDays() {
    const iso = isoDay(whenMoment());
    for (const day of whenSheet.querySelectorAll('.when-day')) {
        const on = day.dataset.day === iso;
        day.classList.toggle('on', on);
        day.setAttribute('aria-checked', on);
        day.tabIndex = on ? 0 : -1;
    }
}

// Okienko stoi pod pastylką, a gdy pod nią brakuje miejsca - nad nią; w niskim
// oknie obok panelu, a gdy i tam się nie mieści - pod pastylką z przewijaniem
// w środku. Pastylki nie zasłania nigdy.
function placeDesk() {
    if (whenSheet.hidden || whenWheels) return;
    const GAP = 6, EDGE = 8;
    whenSheet.style.maxHeight = '';
    const pill = whenBox.querySelector('.when-pill').getBoundingClientRect();
    const panel = $('sidebar').getBoundingClientRect();
    const width = whenSheet.offsetWidth;
    const height = whenSheet.offsetHeight;
    const below = window.innerHeight - EDGE - (pill.bottom + GAP);
    const above = pill.top - GAP - EDGE;
    let top, left = Math.max(EDGE, Math.min(pill.left, window.innerWidth - width - EDGE));
    if (height <= below) {
        top = pill.bottom + GAP;
    } else if (height <= above) {
        top = pill.top - GAP - height;
    } else if (panel.right + GAP + width <= window.innerWidth - EDGE) {
        left = panel.right + GAP;
        top = Math.max(EDGE, Math.min(pill.top, window.innerHeight - EDGE - height));
        if (height > window.innerHeight - 2 * EDGE) whenSheet.style.maxHeight = `${window.innerHeight - 2 * EDGE}px`;
    } else if (below >= above) {
        top = pill.bottom + GAP;
        whenSheet.style.maxHeight = `${below}px`;
    } else {
        whenSheet.style.maxHeight = `${above}px`;
        top = pill.top - GAP - above;
    }
    whenSheet.style.top = `${top}px`;
    whenSheet.style.left = `${left}px`;
}

window.addEventListener('resize', () => {
    if (whenSheet.hidden) return;
    if (phoneLayout.active() !== whenWheels) closeWheels();
    else placeDesk();
});

// Godzina z pola: „1745", „17:45", „17.45", „930". W trakcie pisania liczy się
// tylko pełna godzina (cztery cyfry albo minuty po dwukropku) - „250" to może
// być początek „2500", a nie 2:50. Krótsze („9", „930") dopiero przy zatwierdzeniu.
function parseClock(text, whole) {
    const digits = text.trim().replace(/[:.\s]/g, '');
    if (!/^\d{1,4}$/.test(digits)) return null;
    if (!whole && digits.length < 4 && !/[:.]\d{2}$/.test(text.trim())) return null;
    const [h, m] = digits.length <= 2 ? [digits, '0'] : [digits.slice(0, -2), digits.slice(-2)];
    return Number(h) <= 23 && Number(m) <= 59 ? Number(h) * 60 + Number(m) : null;
}

function pickDesk(moment) {
    whenTouched = true;
    writeWhen(clampMoment(moment));
    wheelsChanged = true;
    paintPill();
    whenSheet.querySelector('[data-now]').classList.remove('on');
}

// Nietknięte pole niczego nie zmienia: „teraz" zostaje „teraz".
function typedClock(whole) {
    const input = whenSheet.querySelector('.when-time');
    if (input.value === input.dataset.shown) return true;
    const minutes = parseClock(input.value, whole);
    // W trakcie pisania „174" to jeszcze nie błąd, tylko początek „1745".
    input.classList.toggle('bad', minutes === null && (whole || !/^\d{0,3}$|^\d{1,2}[:.]\d?$/.test(input.value.trim())));
    if (minutes === null) return false;
    const at = whenMoment();
    if (minutes !== minutesOf(at) || !timeChosen) {
        pickDesk(new Date(at.getFullYear(), at.getMonth(), at.getDate(), Math.floor(minutes / 60), minutes % 60));
        paintDays();
    }
    input.dataset.shown = clockOf(minutes);
    return true;
}

// Strzałki: kursor na godzinie zmienia godzinę, na minutach - minuty co 5;
// przejście przez północ przenosi też dzień.
function stepClock(input, up) {
    if (!typedClock(true)) return;
    const onHour = input.selectionStart <= 2 && input.selectionEnd <= 2;
    const at = whenMoment();
    const next = new Date(at);
    if (onHour) next.setHours(at.getHours() + (up ? 1 : -1));
    else next.setMinutes(up ? Math.floor(at.getMinutes() / 5) * 5 + 5 : Math.ceil(at.getMinutes() / 5) * 5 - 5);
    pickDesk(next);
    paintDesk();
    if (onHour) input.setSelectionRange(0, 2);
    else input.setSelectionRange(3, 5);
}

function pickDay(button) {
    const at = whenMoment();
    const [y, m, d] = button.dataset.day.split('-').map(Number);
    if (button.dataset.day !== isoDay(at)) pickDesk(new Date(y, m - 1, d, at.getHours(), at.getMinutes()));
    paintDays();
    whenSheet.querySelector('.when-day.on').focus();
}

whenSheet.addEventListener('input', event => {
    if (event.target.classList.contains('when-time')) typedClock(false);
});

whenSheet.addEventListener('focusout', event => {
    // Po Esc pole gaśnie razem z okienkiem i nie może już niczego wpisać.
    if (whenSheet.hidden) return;
    if (event.target.classList.contains('when-time') && typedClock(true)) {
        event.target.value = clockOf(minutesOf(whenMoment()));
    }
});

// Zmiana w Rozkładach przelicza tablicę od razu, a trasę dopiero po powrocie
// do wyszukiwarki (patrz resumePlanner) - schowanej nie ma po co liczyć.
let whenChangedAway = false;

function closeWheels() {
    if (whenSheet.hidden) return;
    if (wheelTimer) {
        clearTimeout(wheelTimer);
        settleWheels();
    }
    whenSheet.hidden = true;
    whenBackdrop.hidden = true;
    if (!whenWheels) whenBox.querySelector('.when-pill').focus();
    if (!wheelsChanged) return;
    document.dispatchEvent(new Event('planner:when'));
    if (inTimetable()) whenChangedAway = true;
    else searchWhenSoon();
}

// Rozkłady liczą tablicę od godziny z pola, więc „teraz" musi być świeże.
function whenEnterTimetable() {
    if (!timeChosen) setTimeNow();
    paintWhen();
}

// Na komputerze „Gotowe", Enter i klik obok przyjmują też godzinę dopiero
// wpisywaną („9" to 9:00); błędnej nie przyjmują, okienko zostaje otwarte.
function finishWhen() {
    if (!whenWheels && !typedClock(true)) {
        whenSheet.querySelector('.when-time').focus();
        return;
    }
    closeWheels();
}

// Kolumna przewijana przez aplikację (Teraz, stuknięta pozycja) pamięta, dokąd
// jedzie: „Gotowe" w trakcie płynnego przewijania czytało pozycję z połowy
// drogi, a po schowaniu arkusza nawet 00:00.
function wheelIndex(wheel) {
    if (wheel.dataset.target !== undefined) return Number(wheel.dataset.target);
    return Math.max(0, Math.min(wheel.children.length - 1, Math.round(wheel.scrollTop / WHEEL_H)));
}

function scrollWheelTo(wheel, index, behavior) {
    wheel.dataset.target = index;
    wheel.scrollTo({top: index * WHEEL_H, behavior});
}

function scrollWheels(behavior) {
    const at = whenMoment();
    const indexOf = {
        day: [...whenSheet.querySelectorAll('[data-wheel="day"] .when-item')]
            .findIndex(item => item.dataset.v === isoDay(at)),
        hour: at.getHours(),
        min: Math.min(11, Math.round(at.getMinutes() / 5)),
    };
    for (const wheel of whenSheet.querySelectorAll('.when-wheel')) {
        scrollWheelTo(wheel, Math.max(0, indexOf[wheel.dataset.wheel]), behavior);
    }
    markWheels();
}

function markWheels() {
    for (const wheel of whenSheet.querySelectorAll('.when-wheel')) {
        const index = wheelIndex(wheel);
        [...wheel.children].forEach((item, i) => item.classList.toggle('on', i === index));
    }
}

// Po zatrzymaniu kółek chwila z nich trafia do formularza. Ta sama chwila co
// w formularzu (np. „teraz" zaokrąglone do 5 min) niczego nie zmienia.
function settleWheels() {
    wheelTimer = null;
    markWheels();
    const value = name => {
        const wheel = whenSheet.querySelector(`[data-wheel="${name}"]`);
        return wheel.children[wheelIndex(wheel)].dataset.v;
    };
    const [y, m, d] = value('day').split('-').map(Number);
    const picked = new Date(y, m - 1, d, Number(value('hour')), Number(value('min')));
    const at = whenMoment();
    const shown = new Date(at.getFullYear(), at.getMonth(), at.getDate(), at.getHours(),
                           Math.min(55, Math.round(at.getMinutes() / 5) * 5));
    if (picked.getTime() === shown.getTime()) return;
    whenTouched = true;
    writeWhen(picked);
    paintPill();
    // „Teraz" jest tylko przy odjeździe.
    whenSheet.querySelector('[data-now]')?.classList.remove('on');
    wheelsChanged = true;
}

whenSheet.addEventListener('scroll', () => {
    if (whenSheet.hidden) return;
    clearTimeout(wheelTimer);
    wheelTimer = setTimeout(settleWheels, 120);
}, true);

// Palec albo mysz na kolumnie: od teraz liczy się to, gdzie ją zostawi.
for (const type of ['touchstart', 'pointerdown']) {
    whenSheet.addEventListener(type, event => {
        if (!event.target.closest('.when-wheels')) return;
        for (const wheel of whenSheet.querySelectorAll('.when-wheel')) delete wheel.dataset.target;
    }, {passive: true});
}

// Koła w zwężonym oknie komputera: przewijanie przeglądarki rzucało kolumną
// o dwie godziny na ząbek kółka, a przeciągnięcie myszą nic nie robiło. Ząbek
// kółka to jedna pozycja; gładzik zbiera przesunięcie i co pół pozycji z hakiem
// przesuwa o jedną. Dotyk telefonu idzie dalej zwykłym przewijaniem.
const WHEEL_STEP_PX = 60;
const WHEEL_NOTCH_PX = 50;
let wheelPx = 0;
let wheelPxAt = 0;

function stepWheel(wheel, step) {
    const index = Math.max(0, Math.min(wheel.children.length - 1, wheelIndex(wheel) + step));
    scrollWheelTo(wheel, index, 'smooth');
    markWheels();
}

whenSheet.addEventListener('wheel', event => {
    const wheel = event.target.closest('.when-wheel');
    if (!wheel) return;
    event.preventDefault();
    const px = event.deltaY * (event.deltaMode === 1 ? WHEEL_H
        : event.deltaMode === 2 ? wheel.clientHeight : 1);
    if (!px) return;
    if (event.timeStamp - wheelPxAt > 200 || Math.sign(px) !== Math.sign(wheelPx)) wheelPx = 0;
    wheelPxAt = event.timeStamp;
    wheelPx += px;
    if (Math.abs(wheelPx) < WHEEL_STEP_PX && Math.abs(px) < WHEEL_NOTCH_PX) return;
    const step = Math.sign(wheelPx);
    wheelPx = 0;
    stepWheel(wheel, step);
}, {passive: false});

// Przeciąganie myszą: kolumna jedzie za kursorem, a po puszczeniu staje na
// najbliższej pozycji. Samo kliknięcie (bez ruchu) wybiera pozycję jak dotąd.
let wheelDrag = null;
let wheelDragged = false;

whenSheet.addEventListener('pointerdown', event => {
    const wheel = event.target.closest('.when-wheel');
    if (!wheel || event.pointerType !== 'mouse' || event.button !== 0) return;
    wheelDragged = false;
    wheelDrag = {wheel, id: event.pointerId, y: event.clientY, top: wheel.scrollTop, moved: false};
});

whenSheet.addEventListener('pointermove', event => {
    if (!wheelDrag || event.pointerId !== wheelDrag.id) return;
    const dy = event.clientY - wheelDrag.y;
    if (!wheelDrag.moved && Math.abs(dy) > 4) {
        wheelDrag.moved = true;
        wheelDrag.wheel.setPointerCapture(event.pointerId);
        wheelDrag.wheel.style.scrollSnapType = 'none';
    }
    if (wheelDrag.moved) wheelDrag.wheel.scrollTop = wheelDrag.top - dy;
});

function endWheelDrag(event) {
    if (!wheelDrag || event.pointerId !== wheelDrag.id) return;
    const {wheel, moved} = wheelDrag;
    wheelDrag = null;
    if (!moved) return;
    wheelDragged = true;
    wheel.style.scrollSnapType = '';
    const index = Math.max(0, Math.min(wheel.children.length - 1, Math.round(wheel.scrollTop / WHEEL_H)));
    scrollWheelTo(wheel, index, 'smooth');
    markWheels();
}
whenSheet.addEventListener('pointerup', endWheelDrag);
whenSheet.addEventListener('pointercancel', endWheelDrag);

whenSheet.addEventListener('click', event => {
    const item = event.target.closest('.when-item');
    const day = event.target.closest('.when-day');
    const now = event.target.closest('[data-now]');
    const mode = event.target.closest('[data-mode]');
    if (mode) {
        setWhenMode(mode.dataset.mode === 'arrive');
        wheelsChanged = true;
        if (whenWheels) {
            whenSheet.querySelector('.when-sheet-top').innerHTML = sheetTopHtml();
            scrollWheels('smooth');
        } else {
            // Po zmianie trybu wpisuje się godzinę: pole od razu gotowe, Enter zatwierdza.
            paintDesk();
            const input = whenSheet.querySelector('.when-time');
            input.focus();
            input.select();
        }
    } else if (item && wheelDragged) {
        wheelDragged = false;   // puszczenie po przeciągnięciu to nie kliknięcie
    } else if (item) {
        const wheel = item.parentElement;
        scrollWheelTo(wheel, [...wheel.children].indexOf(item), 'smooth');
    } else if (day) {
        pickDay(day);
    } else if (now) {
        // „Teraz" to odjazd: wybrane w Rozkładach nie zostawia trasie przyjazdu.
        setArriveBy(false);
        setTimeNow();
        wheelsChanged = true;
        now.classList.add('on');
        if (whenWheels) scrollWheels('smooth');
        else paintDesk();
    } else if (event.target.closest('[data-done]')) finishWhen();
});

// Esc zamyka bez zmian: wraca to, co było przy otwarciu, łącznie z trybem.
function cancelWheels() {
    clearTimeout(wheelTimer);
    wheelTimer = null;
    setArriveBy(whenSnapshot.arrive);
    timeChosen = whenSnapshot.chosen;
    timeInput.value = whenSnapshot.time;
    $('date').value = whenSnapshot.date;
    wheelsChanged = false;
    paintWhen();
    closeWheels();
}

// Tab krąży po okienku, zamiast uciekać pod nie na stronę.
function trapTab(event) {
    const stops = [...whenSheet.querySelectorAll('button, input')]
        .filter(el => !el.disabled && !el.hidden && el.tabIndex >= 0);
    const at = stops.indexOf(document.activeElement);
    const next = event.shiftKey ? (at <= 0 ? stops.length - 1 : at - 1) : (at + 1) % stops.length;
    stops[next].focus();
    if (stops[next].classList.contains('when-time')) stops[next].select();
}

const DAY_KEYS = {ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7};

// Klawisze łapie dokument: fokus bywa w okienku albo, po kliknięciu obok
// pola, na samej stronie pod nim - a tło i tak zasłania resztę.
document.addEventListener('keydown', event => {
    if (whenSheet.hidden) return;
    if (!whenSheet.contains(event.target) && event.target !== document.body) return;
    const day = event.target.closest('.when-day');
    if (event.key === 'Escape') {
        event.preventDefault();
        cancelWheels();
    } else if (event.key === 'Enter' && !event.target.closest('[data-mode], [data-now]')) {
        event.preventDefault();
        if (whenWheels) {
            clearTimeout(wheelTimer);
            settleWheels();
            wheelsChanged = true;
        }
        if (day) pickDay(day);
        finishWhen();
    } else if (whenWheels) {
        // na telefonie klawiatury nie ma
    } else if (event.key === 'Tab') {
        event.preventDefault();
        trapTab(event);
    } else if (event.target.classList.contains('when-time') && (event.key === 'ArrowUp' || event.key === 'ArrowDown')) {
        event.preventDefault();
        stepClock(event.target, event.key === 'ArrowUp');
    } else if (day && event.key in DAY_KEYS) {
        event.preventDefault();
        // Strzałki po kalendarzu: w bok o dzień, w górę i w dół o tydzień;
        // poza dni z rozkładem się nie wychodzi.
        const at = dayOfIso(day.dataset.day);
        const to = whenSheet.querySelector(`.when-day[data-day="${isoDay(
            new Date(at.getFullYear(), at.getMonth(), at.getDate() + DAY_KEYS[event.key]))}"]`);
        if (to) pickDay(to);
    }
});

whenBackdrop.addEventListener('click', () => finishWhen());

function setArriveBy(on) {
    arriveBy = on;
}

paintWhen();

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
    hideLocateOffer();
    hideRecallOffer();
    resetResults();
    restyle(...previous);
    setView('map');       // nową relację wybiera się na mapie
    forgetLastSearch();
    syncAddress();
});

// ----------------------------------------------------------- moje miejsca ----
//
// Trzy przyciski „jedź tam stąd" (pasek .places-bar, patrz index.html).
// Miejsce to to samo, co cel wyszukiwania: nazwa przystanku (kanoniczna, jak
// w `sel`) albo punkt {lat, lon}. Zapisane tylko w tej przeglądarce, osobnym
// kluczem - „Przywróć domyślne" w ⚙ ich nie kasuje, bo to nie ustawienie.
const MY_PLACES_KEY = 'metal-planner:my-places';
const MY_PLACES_COUNT = 3;
const placesBar = $('places-bar');
const placeDialog = $('place-dialog');
const placeWhere = $('place-where');
const placeMsg = $('place-msg');
const placePickHint = $('place-pick-hint');

let myPlaces = loadMyPlaces();
let placeSlot = null;      // które miejsce jest w oknie
let placeDraft = null;     // punkt z ◎ albo z mapy - pole pokazuje go jako tekst
let placePicking = false;  // okno schowane, czeka na klik w mapę
let placeLongPress = false;

function loadMyPlaces() {
    let saved = [];
    try {
        saved = JSON.parse(localStorage.getItem(MY_PLACES_KEY)) || [];
    } catch {
        // localStorage niedostępny albo zepsuty JSON - przyciski zaczynają puste
    }
    return Array.from({length: MY_PLACES_COUNT}, (_, i) => {
        const p = saved[i];
        return p && p.icon && (typeof p.place === 'string' || isPoint(p.place)) ? p : null;
    });
}

function saveMyPlaces() {
    try {
        localStorage.setItem(MY_PLACES_KEY, JSON.stringify(myPlaces));
    } catch {
        // localStorage niedostępny - miejsce działa do odświeżenia strony
    }
}

function renderPlaces() {
    placesBar.replaceChildren(...myPlaces.map((p, i) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'icon-button place-button' + (p ? ' set' : '');
        button.dataset.slot = i;
        button.textContent = p ? p.icon : '+';
        const label = p ? `Jedź stąd do: ${displayValue(p.place)}`
            : `Moje miejsce ${i + 1} — przypisz miejsce`;
        button.setAttribute('aria-label', label);
        button.title = p ? `${label}\nPrzytrzymaj, żeby zmienić` : label;
        return button;
    }));
}

function showPlaceMsg(text) {
    placeMsg.textContent = text || '';
    placeMsg.hidden = !text;
}

function setPlaceDraft(point) {
    placeDraft = point;
    placeWhere.value = displayValue(point);
    showPlaceMsg('');
}

function openPlaceDialog(slot) {
    if (placeDialog.open) return;
    placeSlot = slot;
    const p = myPlaces[slot];
    $('place-title').textContent = `Moje miejsce ${slot + 1}`;
    for (const radio of placeDialog.querySelectorAll('[name="place-icon"]')) {
        radio.checked = !!p && radio.value === p.icon;
    }
    placeDraft = p && isPoint(p.place) ? p.place : null;
    placeWhere.value = p ? displayValue(p.place) : '';
    $('place-remove').hidden = !p;
    showPlaceMsg('');
    placeDialog.showModal();
}

/** „51.1093, 17.0386", „(51.1093, 17.0386)" albo z przecinkami dziesiętnymi
    „51,1093 17,0386" - wpisane ręcznie albo skopiowane z pola „Skąd". */
function parsePoint(text) {
    const m = text.trim().match(
        /^\(?\s*(-?\d{1,2}(?:[.,]\d+)?)\s*(?:[,;]\s*|\s+)(-?\d{1,3}(?:[.,]\d+)?)\s*\)?$/);
    if (!m) return null;
    const lat = parseFloat(m[1].replace(',', '.'));
    const lon = parseFloat(m[2].replace(',', '.'));
    return Math.abs(lat) <= 90 && Math.abs(lon) <= 180 ? {lat, lon} : null;
}

/** Co jest teraz w polu okna: punkt ze szkicu, gdy pole wciąż go pokazuje,
    inaczej przystanek z listy albo wpisane współrzędne. */
function placeFromField() {
    const text = placeWhere.value.trim();
    if (placeDraft && text === displayValue(placeDraft)) return placeDraft;
    if (STOP_KIND.has(text)) return text;
    return parsePoint(text);
}

function savePlace() {
    const icon = placeDialog.querySelector('[name="place-icon"]:checked');
    const place = placeFromField();
    if (!icon) { showPlaceMsg('Wybierz ikonę.'); return; }
    if (!place) {
        showPlaceMsg(placeWhere.value.trim()
            ? 'Nie znam takiego przystanku — wybierz go z podpowiedzi, wpisz współrzędne albo wskaż miejsce na mapie.'
            : 'Wybierz miejsce.');
        placeWhere.focus();
        return;
    }
    myPlaces[placeSlot] = {icon: icon.value, place};
    saveMyPlaces();
    renderPlaces();
    placeDialog.close();
}

/** Ta sama pozycja, o którą prosi ◎. */
function locateOnce(onPoint, onError) {
    if (!navigator.geolocation) { onError(GEO_MESSAGES[2]); return; }
    askPosition(
        position => onPoint({lat: position.coords.latitude,
                             lon: position.coords.longitude}),
        error => onError(GEO_MESSAGES[error.code] || GEO_MESSAGES[2]),
    );
}

/** Przypisane miejsce: cel to ono. Start leci z obecnej pozycji tylko wtedy,
    gdy „Skąd" jest puste albo samo pochodzi z lokalizacji (#255, #429) -
    start wpisany albo wybrany ręcznie skrót zostawia. */
function goToPlace(slot) {
    const {place} = myPlaces[slot];
    if (onboardOn) setStartMode(false, false);   // start to „tu stoję", nie pojazd
    hideRecallOffer();
    hideLocateOffer();
    showLocateMsg('');
    const previous = [sel.start, sel.end];
    sel.end = place;
    endInput.value = displayValue(place);
    updatePointMarker('end', place);
    restyle(...previous, sel.end);
    if (startInput.value && !hereIsStart()) { search(); return; }
    // Start idzie tą samą drogą co ◎ (łącznie z godziną „teraz" i pytaniem
    // „Stoisz na…?"), żeby oba przyciski nie rozjechały się w szczegółach.
    if (navigator.geolocation) locateStart(() => { if (startInput.value) search(); });
    else showLocateMsg(GEO_MESSAGES[2]);
}

/** Klik w mapę albo w słupek, gdy okno czeka na wskazanie miejsca. */
function placePickTook(value) {
    if (!placePicking) return false;
    stopPlacePick();
    // Słupek to przystanek z nazwą, nie punkt - pole dostaje wtedy nazwę.
    if (isPoint(value)) setPlaceDraft(value);
    else { placeDraft = null; placeWhere.value = displayValue(value); showPlaceMsg(''); }
    placeDialog.showModal();
    return true;
}

function stopPlacePick() {
    placePicking = false;
    placePickHint.hidden = true;
}

placesBar.addEventListener('click', event => {
    const button = event.target.closest('.place-button');
    if (!button) return;
    // Przytrzymanie już otworzyło okno - puszczenie palca to nie klik.
    if (placeLongPress) { placeLongPress = false; return; }
    const slot = Number(button.dataset.slot);
    if (myPlaces[slot]) goToPlace(slot);
    else openPlaceDialog(slot);
});

// Zmiana przypisanego miejsca: prawy przycisk myszy albo przytrzymanie palca
// (iPhone nie zgłasza przytrzymania jako contextmenu, więc liczymy je sami).
placesBar.addEventListener('contextmenu', event => {
    const button = event.target.closest('.place-button');
    if (!button) return;
    event.preventDefault();
    // Android zgłasza przytrzymanie palca także tak - okno ma się otworzyć
    // raz, a puszczenie palca nie może potem „kliknąć".
    clearTimeout(placePressTimer);
    if (placeLongPress) return;           // przytrzymanie było pierwsze
    placeLongPress = true;
    openPlaceDialog(Number(button.dataset.slot));
});

let placePressTimer = null;
placesBar.addEventListener('pointerdown', event => {
    const button = event.target.closest('.place-button');
    placeLongPress = false;
    if (!button || event.button !== 0) return;
    placePressTimer = setTimeout(() => {
        placeLongPress = true;
        openPlaceDialog(Number(button.dataset.slot));
    }, 600);
});
for (const type of ['pointerup', 'pointerleave', 'pointercancel']) {
    placesBar.addEventListener(type, () => clearTimeout(placePressTimer));
}

attachAutocomplete(placeWhere, () => { placeDraft = null; showPlaceMsg(''); },
                   {suggest: suggestRecentFirst('end'), onEnter: savePlace});
placeWhere.addEventListener('input', () => showPlaceMsg(''));

$('place-form').addEventListener('submit', event => {
    event.preventDefault();
    savePlace();
});

$('place-remove').addEventListener('click', () => {
    myPlaces[placeSlot] = null;
    saveMyPlaces();
    renderPlaces();
    placeDialog.close();
});

const placeLocate = $('place-locate');
if (!navigator.geolocation) placeLocate.hidden = true;
placeLocate.addEventListener('click', () => {
    placeLocate.disabled = true;
    locateOnce(point => {
        placeLocate.disabled = false;
        setPlaceDraft(point);
    }, message => {
        placeLocate.disabled = false;
        showPlaceMsg(message);
    });
});

$('place-pick').addEventListener('click', () => {
    placeDialog.close();
    placePicking = true;
    $('place-pick-text').textContent = `Kliknij na mapie: moje miejsce ${placeSlot + 1}`;
    placePickHint.hidden = false;
    setView('map');            // na telefonie z listy tras mapy nie widać
});

$('place-pick-cancel').addEventListener('click', () => {
    stopPlacePick();
    placeDialog.showModal();
});

placeDialog.addEventListener('close', () => { placeLongPress = false; });

renderPlaces();

// Co zmiana ustawienia robi poza zapamiętaniem. Zapamiętuje, przywraca i
// pokazuje wartość wspólny mechanizm (patrz „USTAWIENIA" wyżej); tu zostaje
// tylko skutek dla reszty aplikacji: `paint` rysuje stan i odpala się też na
// starcie, `react` tylko po ruchu użytkownika. Ustawienie, które nic nie robi
// samo (aplikacja czyta je przez setting() w chwili użycia), nie ma tu wpisu.
//
// Suwaki Ustawień Developerskich: etykieta od razu, mapa i lista propozycji
// po krótkim debounce (odpowiedź z ciepłym cache to ~10 ms, więc działa
// "na żywo"). Jedno wspólne zapytanie (loadPlan) niesie obie rzeczy naraz,
// więc każdy suwak siłą rzeczy odświeża i mapę, i listę - nie ma już
// suwaków "tylko dla mapy".
function debounced(run, wait) {
    let timer = null;
    return (...args) => {
        clearTimeout(timer);
        timer = setTimeout(() => run(...args), wait);
    };
}

function replanQuietly() {
    // Start z pokładu nie ma pola „skąd" - o komplecie pytania mówi
    // tam wybór pojazdu (patrz onboardReady).
    if (!endInput.value || (onboardOn ? !onboardReady() : !startInput.value)) return;
    loadPlan('quiet').catch(() => showError('Nie udało się połączyć z serwerem.'));
}

function replanSoon() {
    return debounced(replanQuietly, 200);
}

// „Stoję tutaj / Jestem w pojeździe" da się schować - kto nie jeździ
// z pokładu, ma o jeden rząd formularza mniej. Bez przełącznika nie byłoby
// jak wrócić z trybu pojazdu, więc start jest wtedy zawsze stojącym miejscem.
function showStartMode(on) {
    document.body.classList.toggle('start-mode-off', !on);
    if (!on && onboardOn) setStartMode(false);
}

// Propozycje tras da się wyłączyć w całości (sekcja „Wygląd aplikacji") -
// zostaje sama mapa, a serwer listy w ogóle nie składa (patrz queryParams).
// Znika lista, jej nagłówek ze strzałką i karta „Szukam połączeń…” (czeka
// na listę), ale nie błędy w tym samym miejscu: te mówią o wyszukiwaniu. Na telefonie znika też zakładka „Trasy".
function showRoutes(on) {
    document.body.classList.toggle('routes-off', !on);
    // Ustawienia samej listy bez listy nic nie zmieniają.
    $('transfer-gain').closest('.set-row').hidden = !on;
    $('via-open').closest('.set-row').hidden = !on;
    const onRoutes = document.body.classList.contains('view-list')
        && !document.body.classList.contains('mode-timetable');
    if (!on && onRoutes) document.querySelector('#view-tabs [data-view="map"]').click();
}
// Widok telefonu wraca na ostatnią zakładkę dopiero pod koniec wczytywania -
// wtedy okazuje się, czy nie stoi się na schowanej „Trasy".
document.addEventListener('DOMContentLoaded', () => showRoutes(setting('routes-on')));

// W pamięci leży GOTOWY html tablicy, przycięty do starej liczby wierszy i
// starych szarych godzin - bez czyszczenia ustawienie działałoby dopiero na
// kropkach jeszcze nietkniętych.
function reloadTimetable() {
    timetableCache.clear();
    if (timetableTarget) {
        loadTimetable(timetableTarget, timetableTarget.where, timetableTarget.sec);
    }
}

// Kolor linii siedzi w gotowym html i w warstwach mapy, więc zmiana kolorów
// przerysowuje to, co już stoi, z tego, co już przyszło - bez zapytania.
// Wachlarz w rozkładach jest zdjęty i wróci sam, już w nowych kolorach
// (patrz resumePlanner).
function repaintLineColors() {
    reloadTimetable();
    renderJourneys();
    renderTimeHeadline();
    renderVehicles();
    syncOnboardView();
    if (lastSchemeData) renderScheme(lastSchemeData);
    if (window.timetableMode) window.timetableMode.repaint();
    if (plannerSuspended) return;
    if (lastFlow) drawFlow(lastFlow, false);
    if (selectedJourney !== null) drawJourney(selectedJourney, true);
}

// Zmiana rysuje stan (`paint`, też na starcie) i/albo coś robi po ruchu (`react`).
const SETTING_EFFECTS = {
    'car-count': {react: replanSoon()},
    'bike-count': {react: replanSoon()},
    'transfer-gain': {react: replanSoon()},
    'walk-pace': {react: replanSoon()},
    'bike-kmh': {react: replanSoon()},
    'bike-overhead': {react: replanSoon()},
    'car-kmh': {react: replanSoon()},
    'car-overhead': {react: replanSoon()},
    'onboard-slack': {react: replanSoon()},
    // Dostawczaki zmieniają odpowiedź serwera, więc jak suwak: od razu nowe zapytanie.
    'cars-horizon': {react: () => {
        if (!startInput.value || !endInput.value) return;
        loadPlan('quiet').catch(() => showError('Nie udało się połączyć z serwerem.'));
    }},
    'car-vans': {react: () => {
        if (!flowOnScreen()) refreshCarLayer();
        if (!startInput.value || !endInput.value) return;
        loadPlan('quiet').catch(() => showError('Nie udało się połączyć z serwerem.'));
    }},
    // Schemat przychodzi zawsze z mapą, więc włączenie tylko go rysuje (i mapa
    // kadruje się obok niego przy następnym wyszukaniu).
    'scheme-on': {react: () => { if (lastSchemeData) renderScheme(lastSchemeData); }},
    // Chodzenie pieszo zmienia odpowiedź serwera, jak dostawczaki.
    'walking': {react: () => {
        if (!startInput.value || !endInput.value) return;
        loadPlan('quiet').catch(() => showError('Nie udało się połączyć z serwerem.'));
    }},
    // Zakres mapy na pasku to tylko podgląd: zmiana przerysowuje pasek, bez nowego zapytania.
    'headline-map-range': {react: () => renderTimeHeadline()},
    // Odległość albo czas przy przejściach i rowerze - wszędzie naraz: dymki
    // i kropki na mapie oraz schemat.
    'walk-time': {react: () => { applyDotOpts(); if (lastSchemeData) renderScheme(lastSchemeData); }},
    'line-colors': {react: repaintLineColors},
    'bikes-merged': {paint: () => paintBikeButtons()},
    'start-mode-switch': {paint: showStartMode},
    'routes-on': {paint: showRoutes, react: on => {
        // Ostatnia odpowiedź przyszła bez listy - po włączeniu trzeba ją doliczyć,
        // tak jak propozycje z rowerem (patrz replanInPlace).
        if (on) replanInPlace();
    }},
    // Gest „pociągnij w dół, żeby odświeżyć" - overscroll-behavior na <html>
    // (patrz style.css) gasi go bez ruszania zwykłego przewijania.
    'no-pull-refresh': {paint: on => document.documentElement.classList.toggle('no-pull-refresh', on)},
    'location-refresh': {react: () => syncAutoRefresh()},
    // Zmiana zaczyna od nowa - także tam, gdzie listę przełączono kliknięciem -
    // i od razu przerysowuje otwartą propozycję.
    'via-open': {react: () => {
        viaShown = new WeakMap();
        if (selectedJourney !== null) renderJourneys();
    }},
    // Bez przycisku strefa pokazuje się tylko pod kursorem (patrz refreshZoneLayer).
    'zone-button': {react: () => refreshZoneLayer()},
    'sound-level': {react: level => {
        // Zmiana od razu gra: inaczej trzeba by szukać trasy, żeby usłyszeć,
        // jak brzmi wybrana głośność.
        if (level !== 'off') playPipeDrop();
    }},
    'dot-size': {react: debounced(() => applyDotOpts(), 60)},   // przeciąganie suwaka: jedno przemalowanie na klatkę
    'dot-rows': {react: reloadTimetable},
    'tt-past': {react: reloadTimetable},
    'names-zoom': {react: () => placeStopNames()},
    // Te suwaki nie dotykają serwera - mapa przemalowuje się natychmiast,
    // z ostatniej odpowiedzi (lastFlow), bez ponownego zapytania.
    'look-weight': {react: debounced(() => applyLook(), 60)},
    'look-dim': {react: debounced(() => applyLook(), 60)},
    'look-label-step': {react: debounced(() => applyLook(), 60)},
    'look-label-size': {react: debounced(() => applyLook(), 60)},
    'look-label-op': {react: debounced(() => applyLook(), 60)},
};
// Przełączniki, które tylko przemalowują z lastFlow, bez zapytania do serwera.
for (const id of ['time-hover', 'time-ends', 'time-chips', 'time-show-headline', 'time-ride']) {
    SETTING_EFFECTS[id] = {react: () => applyTimeOpts()};
}
for (const id of ['vehicles-direction', 'vehicles-stats']) {
    SETTING_EFFECTS[id] = {react: () => renderVehicles()};
}
for (const id of ['dot-center', 'tip-cursor', 'tip-panel', 'bike-rides',
                  'car-times', 'debug-why']) {
    SETTING_EFFECTS[id] = {react: () => applyDotOpts()};
}

// Strojenia schowane stałą (LOOK_TUNING) nie mają wiersza w panelu.
for (const row of settingsRows) {
    if (row.dataset.tuning) row.hidden = !TUNING[row.dataset.tuning];
}

for (const info of SETTINGS.values()) {
    paintSettingOutput(info);
    const effect = SETTING_EFFECTS[info.id];
    if (effect && effect.paint) effect.paint(setting(info.id));
}

// Poprzedni wybór, do którego wraca lista po anulowaniu któregoś z okien.
let soundLevelBefore = setting('sound-level');
// Natywna lista ma już wartość „Wyłączony”, zanim padnie pierwsze pytanie, więc
// do czasu odpowiedzi wiersz nie może zmienić grupy.
let soundLevelConfirming = false;

function acceptSoundLevel(info, value) {
    soundLevelBefore = value;
    storeSetting(info.id, value);
    // Zliczamy tu, nie na zdarzeniu `change`: anulowane wyłączenie nie jest zmianą.
    recordSettingChange(info.id);
    syncSettingsSnapshot();
    SETTING_EFFECTS[info.id].react(value);
}

const PIPE_OFF_STEPS = [
    {text: 'Czy jesteś w pełni przekonany, że chcesz wyłączyć ten przepiękny element aplikacji?', ok: 'Tak'},
    {text: 'Czy wiesz, że to Ci zepsuje experience aplikacji?', ok: 'Tak'},
    {text: 'Czy wiesz, że badania amerykańskich naukowców stwierdziły, że słuchanie Metal Pipe\'a pozwala zachować dobry słuch nawet w wieku 100 lat?',
     ok: 'Wyłącz mimo to', joke: true},
];

/** Trzy okna z rzędu; true tylko, gdy użytkownik przeszedł wszystkie. */
async function confirmPipeOff() {
    const dialog = $('pipe-off-dialog');
    let confirmed = true;
    // To samo okno stoi otwarte przez wszystkie kroki i zamykamy je raz, na
    // końcu: `close` odpala się asynchronicznie, więc zamknięcie między krokami
    // uruchomiłoby obsługę następnego kroku jako anulowanie.
    dialog.showModal();
    for (const step of PIPE_OFF_STEPS) {
        $('pipe-off-text').textContent = step.text;
        $('pipe-off-ok').textContent = step.ok;
        $('pipe-off-joke').hidden = !step.joke;
        confirmed = await new Promise(resolve => {
            $('pipe-off-ok').onclick = () => resolve(true);
            $('pipe-off-cancel').onclick = () => resolve(false);
            // Esc zamyka okno samo, klik w tło zamykamy tu - oba to anulowanie.
            dialog.onclose = () => resolve(false);
            dialog.onclick = e => { if (e.target === dialog) resolve(false); };
        });
        if (!confirmed) break;
    }
    $('pipe-off-ok').onclick = $('pipe-off-cancel').onclick = null;
    dialog.onclose = dialog.onclick = null;
    if (dialog.open) dialog.close();
    return confirmed;
}

for (const type of ['input', 'change']) {
    devPanel.addEventListener(type, event => {
        const info = SETTING_BY_CONTROL.get(event.target);
        if (!info) return;
        const value = readControl(info.control);
        if (info.id === 'sound-level') {
            // `input` leci też przy przewijaniu listy klawiaturą, a okna
            // potwierdzeń nie mają się mnożyć.
            if (type === 'input') return;
            if (value !== 'off') {
                acceptSoundLevel(info, value);
                return;
            }
            const previous = soundLevelBefore;
            soundLevelConfirming = true;
            confirmPipeOff().then(confirmed => {
                soundLevelConfirming = false;
                if (confirmed) acceptSoundLevel(info, value);
                else info.control.value = previous;
                syncSettingCopies();
                markChangedSettings();
            });
            return;
        }
        storeSetting(info.id, value);
        paintSettingOutput(info);
        if (type !== (info.kind === 'range' ? 'input' : 'change')) return;
        const effect = SETTING_EFFECTS[info.id];
        if (effect && effect.react) effect.react(value);
    }, true);   // faza przechwytywania: działa też dla zdarzeń, które nie bąblują
}

// --- wygląd mapy: przemalowanie suwakami (schowane, patrz LOOK_TUNING) -----
//
// Wartości są już dobrane (siedzą w index.html jako domyślne), więc cała
// sekcja jest domyślnie schowana - `LOOK_TUNING = true` przywraca ją, gdyby
// trzeba było stroić od nowa.
// Grubość linii to grubość najjaśniejszej: każda linia jest najjaśniejsza
// (patrz look-weight w index.html).

/** Przemalowanie z ostatniej odpowiedzi - bez zapytania do serwera. Wybrana
    trasa rysuje się na nowo NA WIERZCHU przemalowanego wachlarza (kolejność
    warstw w canvasie to kolejność dokładania). */
function applyLook() {
    document.documentElement.style.setProperty('--chip-scale', setting('look-label-size'));
    if (lastFlow) drawFlow(lastFlow, false);
    if (selectedJourney !== null) drawJourney(selectedJourney, true);
    const dump = $('look-dump');
    if (dump) {
        dump.textContent = [
            ...Object.entries(look),
            ...[...SETTINGS.keys()].filter(id => id.startsWith('look-') && SETTINGS.get(id).kind === 'range')
                .map(id => [id, setting(id)]),
        ].map(([k, v]) => `${k}: ${v}`).join(', ');
    }
}
document.documentElement.style.setProperty('--chip-scale', setting('look-label-size'));

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

const PIPE_SOURCES = [
    ['audio/ogg; codecs=opus', '/static/sounds/metal-pipe.ogg'],
    ['audio/mp4; codecs="mp4a.40.2"', '/static/sounds/metal-pipe.m4a'],
];

// Nagranie jest głośne (szczyt ponad 0 dBFS), a to ma być żart w tle,
// nie alarm. 0.175 ("normalnie") to głośność, którą użytkownik już uznał za
// górną granicę. "Po cichutku" (domyślne, #447) to ok. 29% tego: wyraźnie
// ciszej, ale wciąż słychać.
//
// Głośność idzie przez mikser przeglądarki (Web Audio), nie przez
// `volume` elementu audio: iOS tego pola nie słucha i gra zawsze głośnością
// systemu. Wzmocnienie miksera słucha wszędzie, więc każdy efekt może mieć
// swoją głośność. Cena na iPhonie: dźwięk z miksera milknie przy włączonym
// wyciszeniu telefonu - dla żartu w tle to raczej zaleta.
const PIPE_VOLUMES = {normal: 0.175, quiet: 0.05};
const pipeVolume = () => PIPE_VOLUMES[setting('sound-level')];

let pipeAudio = null;
let soundMixer = null;
let pipeBuffer = null;       // zdekodowane nagranie, gdy gra mikser
let pipeLoading = false;
let pipeUrl;                 // undefined, dopóki nie zapytaliśmy przeglądarki
let pipePending = false;     // rura miała zagrać, zanim nagranie doszło
let pipeVoice = null;        // aktualnie grająca rura z miksera

// iPhone domyślnie traktuje dźwięk ze strony jak odtwarzacz: przejmuje
// głośnik i zatrzymuje Spotify czy podcast, którego pasażer słucha (#243).
// "ambient" to kategoria efektów w tle - miesza się z muzyką, a muzyka gra
// dalej. Nie "transient": ta miała ściszać muzykę na chwilę, ale Safari
// mapuje ją na to samo, a na części wersji gubi przy niej dźwięk.
if (navigator.audioSession) navigator.audioSession.type = 'ambient';

/** Mikser przeglądarki - jeden na stronę, null bez Web Audio. */
function mixer() {
    if (soundMixer) return soundMixer;
    const Mixer = window.AudioContext || window.webkitAudioContext;
    if (!Mixer) return null;
    soundMixer = new Mixer();
    return soundMixer;
}

/** Plik w formacie, który przeglądarka umie odtworzyć; null, gdy żaden.
    Pytamy raz: odpowiedź się nie zmienia, a bez zapamiętania przeglądarka
    bez naszych formatów sondowałaby je przy każdym dotknięciu i klawiszu. */
function pipeSourceUrl() {
    if (pipeUrl !== undefined) return pipeUrl;
    const probe = document.createElement('audio');
    const pick = probe.canPlayType
        && PIPE_SOURCES.find(([type]) => probe.canPlayType(type));
    pipeUrl = pick ? pick[1] : null;
    return pipeUrl;
}

// Nagranie gra z bufora miksera, a nie z elementu audio: element to dla
// iOS "odtwarzacz multimediów", a ten zatrzymuje muzykę innych aplikacji
// bez względu na typ sesji dźwięku. Czysty mikser zostaje efektem w tle.
// Pobieramy przy pierwszym dotknięciu, żeby nagranie było gotowe, zanim
// serwer odpowie na wyszukiwanie.
function loadPipe() {
    if (pipeLoading) return;
    const ctx = mixer();
    const url = ctx && pipeSourceUrl();
    if (!url) return;
    pipeLoading = true;
    // Nieudane pobranie zapomina też o czekającej rurze - inaczej
    // kolejna próba przy zwykłym dotknięciu zagrałaby ją bez wyszukiwania.
    const fail = () => { pipeLoading = false; pipePending = false; };
    fetch(url)
        .then(response => response.arrayBuffer())
        // Dekodowanie na callbackach, bo starsze Safari nie zwracają obietnicy.
        .then(data => ctx.decodeAudioData(data, buffer => {
            pipeBuffer = buffer;
            if (pipePending) playPipeBuffer();
        }, fail))
        .catch(fail);
}

function playPipeBuffer() {
    pipePending = false;
    const ctx = mixer();
    // Drugie wyszukiwanie w trakcie pierwszego dźwięku ma zagrać OD NOWA,
    // a nie nałożyć się na poprzednie.
    if (pipeVoice) pipeVoice.stop();
    const gain = ctx.createGain();
    gain.gain.value = pipeVolume();
    gain.connect(ctx.destination);
    const voice = ctx.createBufferSource();
    voice.buffer = pipeBuffer;
    voice.connect(gain);
    voice.onended = () => { if (pipeVoice === voice) pipeVoice = null; };
    voice.start(0);
    pipeVoice = voice;
}

// Safari wpuszcza dźwięk z miksera tylko wtedy, gdy obudzi się go W TRAKCIE
// gestu, a rura gra dopiero po odpowiedzi serwera - już po geście. Budzimy
// go więc przy każdym dotknięciu i klawiszu, nie raz: iOS usypia mikser
// z powrotem, gdy strona zejdzie w tło.
function wakeMixer() {
    if (setting('sound-level') === 'off') return;
    const ctx = mixer();
    if (!ctx) return;
    if (ctx.state !== 'running') ctx.resume().catch(() => {});
    loadPipe();
}
for (const type of ['pointerdown', 'keydown']) {
    document.addEventListener(type, wakeMixer, {capture: true});
}

/** Element audio tylko dla przeglądarek bez Web Audio. Powstaje przy
    pierwszym użyciu i zostaje - jeden na stronę. Zwraca null, gdy
    przeglądarka nie umie żadnego z naszych formatów. */
function pipeElement() {
    if (pipeAudio) return pipeAudio;
    const url = pipeSourceUrl();
    if (!url) return null;
    const element = document.createElement('audio');
    element.src = url;
    element.preload = 'auto';
    pipeAudio = element;
    return pipeAudio;
}

function prefersLessMotion() {
    return !!(window.matchMedia
              && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
}

/** Cicho, gdy dźwięk wyłączony, gdy system prosi o ograniczenie
    animacji albo gdy przeglądarka nie umie żadnego z formatów. */
function playPipeDrop() {
    if (setting('sound-level') === 'off' || prefersLessMotion()) return;
    if (mixer()) {
        if (pipeBuffer) playPipeBuffer();
        else { pipePending = true; loadPipe(); }
        return;
    }
    const audio = pipeElement();
    if (!audio) return;
    // Drugie wyszukiwanie w trakcie pierwszego dźwięku ma zagrać OD NOWA,
    // a nie zostać po cichu pominięte.
    audio.volume = pipeVolume();
    audio.currentTime = 0;
    const started = audio.play();
    // Przeglądarka odmawia, dopóki strona nie dostała gestu. Tu zawsze
    // jesteśmy po kliknięciu, ale odrzucona obietnica nie może wywalić
    // reszty łańcucha.
    if (started && started.catch) started.catch(() => {});
}

// --- przełączniki "czasu na mapie" -----------------------------------------
//
// Nic tu nie rusza serwera: wszystkie liczby są już w ostatniej odpowiedzi
// (lastFlow), więc przełącznik przemalowuje mapę natychmiast, bez zapytania.
function applyTimeOpts() {
    if (lastFlow) drawFlow(lastFlow, false);   // grupki i pasek liczą się od nowa
    else renderTimeHeadline();
    renderFlowPick();                          // dymek pod kursorem, jeśli akurat wisi
}

// --- kropki przystanków i miejsce na rozkład -------------------------------
//
// Też bez zapytania do serwera: obie współrzędne węzła i cała tablica odjazdów
// są już w odpowiedziach, więc wystarczy przemalować z lastFlow i przerysować
// wybraną trasę w miejscu (keepView - kadr ma się nie ruszyć).
function applyDotOpts() {
    restyle(sel.start, sel.end);
    if (lastFlow) drawFlow(lastFlow, false);
    if (selectedJourney !== null) drawJourney(selectedJourney, true);
    if (!setting('tip-panel')) hideSidePanel();
    if (!setting('tip-cursor') && flowTooltip) {
        map.removeLayer(flowTooltip);
        flowTooltip = null;
    }
}

// --- lista ustawień: wyszukiwarka i nagłówki z etykiet -----------------------
//
// Ustawień przybywa szybciej, niż da się je trzymać w sensownych kategoriach,
// więc każdy wiersz niesie tylko etykiety (`data-labels`), a nagłówki składają
// się z nich same, z wierszy widocznych w tej chwili: każda etykieta z co
// najmniej trzema wierszami dostaje nagłówek, a wiersz stoi pod każdą swoją
// etykietą. Kontrolka jest jedna (oryginał, pod pierwszym nagłówkiem) - pod
// pozostałymi stoją kopie, które przekazują zmianę oryginałowi, więc zmiana
// liczy się raz, a obsługa po id zostaje bez zmian. Wiersz bez nagłówka ląduje
// w „Inne". Kolejność układa się z dziennika ostatnich zmian i jest liczona
// tylko przy otwarciu panelu, żeby nic nie przeskakiwało pod palcem.
const settingsSearch = $('settings-search');

for (const row of settingsRows) {
    const tags = [];
    if (row.classList.contains('dev-only')) tags.push('DEV');
    if (row.dataset.obsolete !== undefined) tags.push('obsolete');
    for (const tag of tags) {
        const badge = document.createElement(tag === 'DEV' ? 'span' : 'a');
        badge.className = tag === 'DEV' ? 'dev-badge' : 'dev-badge obsolete-badge';
        badge.textContent = tag;
        if (tag === 'obsolete') {
            badge.href = row.dataset.issueUrl;
            badge.target = '_blank';
            badge.rel = 'noopener';
            badge.dataset.tip = 'Korzystasz z tego? Kliknij, żeby dać znać, że jest ważne (otworzy się zgłoszenie na GitHubie)';
        }
        (row.querySelector('label:not(.button), .set-title') || row).append(badge);
    }
}

const settingsInfo = new Map(settingsRows.map(row => {
    const copy = row.cloneNode(true);
    for (const skip of copy.querySelectorAll('output, .info, .dev-badge')) skip.remove();
    const labels = (row.dataset.labels || '').split(',').map(label => label.trim()).filter(Boolean);
    return [row, {
        labels,
        name: fold(copy.textContent).replace(/\s+/g, ' '),
        folded: labels.map(fold),
    }];
}));

const SETTINGS_SUGGEST_RECENT = 3;
const SETTINGS_SUGGEST_FREQUENT = 5;

// Dziennik z chwili otwarcia panelu: z niego liczy się kolejność aż do
// następnego otwarcia.
let settingsLog = {counts: new Map(), recent: [], known: null};

// Najrzadszy odstęp między przeliczeniami kolejności (ustawienie w ⚙). Wyłączony
// = przeliczanie przy każdym otwarciu panelu.
const SETTINGS_ORDER_KEY = 'metal-planner:settings-order';
const SETTINGS_ORDER_LIMITS = {
    '1h': 3600e3, '1d': 24 * 3600e3, '7d': 7 * 24 * 3600e3, '30d': 30 * 24 * 3600e3,
};

function loadSettingsOrder() {
    try {
        return JSON.parse(localStorage.getItem(SETTINGS_ORDER_KEY));
    } catch {
        return null;       // localStorage niedostępny albo zepsuty JSON
    }
}

function snapshotSettingsLog() {
    const limit = SETTINGS_ORDER_LIMITS[setting('order-limit')];
    const stored = loadSettingsOrder();
    if (limit && stored && Date.now() - stored.t < limit) {
        settingsLog = {counts: new Map(stored.counts), recent: stored.recent, known: new Set(stored.known)};
        return;
    }
    const log = loadSettingsStats().log;
    const counts = new Map();
    for (const event of log) counts.set(event.id, (counts.get(event.id) || 0) + 1);
    const recent = [];
    for (const event of [...log].reverse()) {
        if (!recent.includes(event.id)) recent.push(event.id);
    }
    settingsLog = {counts, recent, known: null};
    // Zapis także przy wyłączonym limicie: włączenie go niczego wtedy nie przestawia.
    try {
        localStorage.setItem(SETTINGS_ORDER_KEY, JSON.stringify({
            t: Date.now(), counts: [...counts], recent,
            known: settingsRows.map(row => row.dataset.setting),
        }));
    } catch {
        // localStorage niedostępny - kolejność po prostu liczy się za każdym razem
    }
}

const isObsolete = row => row.dataset.obsolete !== undefined;
const rowScore = row => settingsLog.counts.get(row.dataset.setting) || 0;
const rowIndex = new Map(settingsRows.map((row, i) => [row, i]));
// Ustawienie dodane po ostatnim przeliczeniu staje na końcu wśród równych.
const rowIsNew = row => (settingsLog.known && !settingsLog.known.has(row.dataset.setting) ? 1 : 0);
const byScore = (a, b) => rowScore(b) - rowScore(a) || rowIsNew(a) - rowIsNew(b)
    || rowIndex.get(a) - rowIndex.get(b);

// Wiersze z suwakiem albo przełącznikiem da się skopiować; reszta (link,
// wgranie pliku, przycisk wersji) ma swoje id i stoi tylko w jednym miejscu.
const isCopyable = row => SETTINGS.has(row.dataset.setting);

let copyCounter = 0;
function makeSettingCopy(row) {
    const copy = row.cloneNode(true);
    copy.classList.add('set-copy');
    copy.classList.remove('changed');
    copy.dataset.copyOf = row.dataset.setting;
    delete copy.dataset.setting;
    const renamed = new Map();
    for (const el of copy.querySelectorAll('[id]')) {
        if (el.tagName === 'OUTPUT') el.dataset.of = el.id;
        else renamed.set(el.id, `${el.id}-copy-${++copyCounter}`);
        el.removeAttribute('id');
    }
    for (const el of copy.querySelectorAll('input, select')) el.id = renamed.get(row.dataset.setting) || '';
    for (const label of copy.querySelectorAll('label[for]')) label.htmlFor = renamed.get(label.htmlFor) || '';
    return copy;
}

function syncSettingCopies() {
    for (const copy of settingsList.querySelectorAll('.set-copy')) {
        const source = $(copy.dataset.copyOf);
        const input = copy.querySelector('input, select');
        if (input.type === 'checkbox') input.checked = source.checked;
        else input.value = source.value;
        for (const out of copy.querySelectorAll('output')) out.textContent = $(out.dataset.of).textContent;
    }
}

// Zmiana w kopii trafia do oryginału jak ruch w nim samym - tak działa jego
// obsługa i liczenie zmian.
for (const type of ['input', 'change']) {
    devPanel.addEventListener(type, event => {
        const copy = event.target.closest('.set-copy');
        if (copy) {
            const source = $(copy.dataset.copyOf);
            if (source.type === 'checkbox') source.checked = event.target.checked;
            else source.value = event.target.value;
            // Odświeżenie zrobi ten sam nasłuch przy zdarzeniu oryginału, raz.
            source.dispatchEvent(new Event(type, {bubbles: true}));
            return;
        }
        if (soundLevelConfirming) return;
        syncSettingCopies();
        markChangedSettings();
    });
}

function settingGroups(visible) {
    const byLabel = new Map();
    for (const row of visible.filter(row => !isObsolete(row))) {
        for (const label of settingsInfo.get(row).labels) {
            if (!byLabel.has(label)) byLabel.set(label, []);
            byLabel.get(label).push(row);
        }
    }
    const order = [...byLabel.keys()];
    return [...byLabel].filter(([, rows]) => rows.length >= 3)
        .map(([label, rows]) => ({
            label, rows: [...rows].sort(byScore),
            score: rows.reduce((sum, row) => sum + rowScore(row), 0),
        }))
        .sort((a, b) => b.score - a.score || b.rows.length - a.rows.length
            || order.indexOf(a.label) - order.indexOf(b.label));
}

function suggestedSettings(visible) {
    const usable = id => visible.find(row => row.dataset.setting === id && isCopyable(row) && !isObsolete(row));
    const recent = settingsLog.recent.filter(usable).slice(0, SETTINGS_SUGGEST_RECENT);
    const frequent = [...settingsLog.counts.keys()]
        .filter(id => usable(id) && !recent.includes(id))
        .sort((a, b) => settingsLog.counts.get(b) - settingsLog.counts.get(a)
            || settingsLog.recent.indexOf(a) - settingsLog.recent.indexOf(b))
        .slice(0, SETTINGS_SUGGEST_FREQUENT);
    return [...recent, ...frequent].map(usable);
}

function renderSettingsList() {
    const needle = fold(settingsSearch.value.trim());
    const shown = visibleSettingRows();
    const matching = shown.filter(row => {
        const info = settingsInfo.get(row);
        return info.name.includes(needle) || info.folded.some(label => label.includes(needle));
    });
    for (const row of settingsRows) row.classList.toggle('search-miss', !matching.includes(row) && !row.hidden);
    const prefs = storedSettings();
    const addGroup = (nodes, label, rows, openByDefault) => {
        const group = document.createElement('details');
        group.className = 'set-group';
        const saved = prefs['fold:' + label];
        group.open = saved === undefined ? openByDefault : !!saved;
        group.append(Object.assign(document.createElement('summary'), {textContent: label}), ...rows);
        group.addEventListener('toggle', () => storeSetting('fold:' + label, group.open));
        nodes.push(group);
    };
    const nodes = [];
    const placed = new Set();
    if (needle) {
        // Wyniki jako płaska lista, każde ustawienie raz.
        const flat = [...matching.filter(row => !isObsolete(row)), ...matching.filter(isObsolete)];
        nodes.push(...flat);
        for (const row of flat) placed.add(row);
    } else {
        const suggested = suggestedSettings(shown);
        if (suggested.length) {
            addGroup(nodes, 'Proponowane', suggested.map(makeSettingCopy), true);
            nodes[nodes.length - 1].dataset.kind = 'suggested';
        }
        const changedRows = changedSettingRows(shown);
        if (changedRows.length) nodes.push(buildChangedGroup(changedRows, prefs));
        for (const group of settingGroups(shown)) {
            const rows = [];
            for (const row of group.rows) {
                if (!placed.has(row)) rows.push(row);
                else if (isCopyable(row)) rows.push(makeSettingCopy(row));
                placed.add(row);
            }
            addGroup(nodes, group.label, rows, false);
        }
        const rest = shown.filter(row => !placed.has(row) && !isObsolete(row));
        if (rest.length) addGroup(nodes, 'Inne', rest, false);
        for (const row of rest) placed.add(row);
        // Przestarzałe na samym dole, zawsze razem, bez względu na swoje tagi.
        const obsolete = shown.filter(isObsolete);
        if (obsolete.length) addGroup(nodes, 'Obsolete', obsolete, false);
        for (const row of obsolete) placed.add(row);
    }
    // Wiersze schowane z innych powodów (albo bez trafienia) zostają w DOM, na końcu.
    nodes.push(...settingsRows.filter(row => !placed.has(row)));
    settingsList.replaceChildren(...nodes);
    syncSettingCopies();
    markChangedSettings();
}

// Kolejność liczy się z dziennika przy otwarciu panelu (i przy starcie), nigdy
// przy otwartym.
function refreshSettingsOrder() {
    snapshotSettingsLog();
    renderSettingsList();
}

settingsSearch.addEventListener('input', renderSettingsList);
// Część wierszy chowają i pokazują inne ustawienia (np. próg przesiadki bez
// propozycji tras) - nagłówki muszą to nadążyć.
new MutationObserver(renderSettingsList).observe(settingsList,
    {attributes: true, attributeFilter: ['hidden'], subtree: true});

// --- zmienione ustawienia i powrót do domyślnych ----------------------------
//
// Wartości domyślne to te z index.html (SETTINGS[...].dflt), więc opcja różna od
// nich dostaje znacznik.

// Stała sekcja pod „Proponowane": ustawienia różne od domyślnych, zawsze z bieżącego
// stanu (nie z migawki kolejności), więc odświeża się na żywo.
const CHANGED_GROUP = 'Inne niż domyślne';

function changedSettingRows(shown) {
    return shown.filter(row => isCopyable(row) && differsFromDefault(SETTINGS.get(row.dataset.setting)));
}

function visibleSettingRows() {
    return settingsRows.filter(row => !row.hidden && row.dataset.expired === undefined && (devMode || !row.classList.contains('dev-only')));
}

function buildChangedGroup(rows, prefs) {
    const saved = prefs['fold:' + CHANGED_GROUP];
    const group = document.createElement('details');
    group.className = 'set-group';
    group.dataset.kind = 'changed';
    group.dataset.ids = rows.map(row => row.dataset.setting).join(',');
    group.open = saved === undefined ? false : !!saved;
    const summary = Object.assign(document.createElement('summary'), {textContent: CHANGED_GROUP});
    summary.dataset.changed = rows.length;
    const obsolete = rows.filter(isObsolete).length;
    if (obsolete) {
        const count = Object.assign(document.createElement('span'), {className: 'obsolete-count', textContent: obsolete});
        count.title = `${obsolete} ` + plural(obsolete, 'ustawienie przestarzałe', 'ustawienia przestarzałe', 'ustawień przestarzałych');
        summary.append(count);
    }
    group.append(summary, ...rows.map(makeSettingCopy));
    group.addEventListener('toggle', () => storeSetting('fold:' + CHANGED_GROUP, group.open));
    return group;
}

// Przy szukaniu lista jest płaska i tej sekcji nie ma.
function refreshChangedGroup() {
    if (settingsSearch.value.trim()) return;
    const rows = changedSettingRows(visibleSettingRows());
    const ids = rows.map(row => row.dataset.setting).join(',');
    const current = settingsList.querySelector('[data-kind=changed]');
    if ((current ? current.dataset.ids : '') === ids) return;
    const next = rows.length ? buildChangedGroup(rows, storedSettings()) : null;
    if (current && next) current.replaceWith(next);
    else if (current) current.remove();
    else if (next) {
        const anchor = [...settingsList.children].find(el => !el.dataset.kind);
        settingsList.insertBefore(next, anchor || null);
    }
    syncSettingCopies();
}

function markChangedSettings() {
    for (const row of settingsList.querySelectorAll('.set-row')) {
        const info = SETTINGS.get(row.dataset.setting || row.dataset.copyOf);
        if (info) row.classList.toggle('changed', differsFromDefault(info));
    }
    refreshChangedGroup();
}
markChangedSettings();

// --- statystyki zmian ustawień (DEV, #404) ------------------------------------
//
// Tylko lokalnie: ile razy i kiedy ostatnio ktoś ruszył każde ustawienie, żeby
// było widać, których nikt nie dotyka. Jeden nasłuch na cały panel zamiast
// podpinania pod każde ustawienie - nowe są liczone same. Suwak liczymy na
// `change` (po puszczeniu), nie na każdy `input`, inaczej jeden ruch dawałby
// dziesiątki zliczeń. Klucz nie jest na liście z #dev-reset: „Przywróć
// domyślne” ma zostawić statystyki, bo to właśnie ono jest jednym z liczonych
// zdarzeń. Tabelę pokazuje osobna strona (/statystyki, statystyki.js).
const SETTINGS_STATS_KEY = 'metal-planner:settings-stats';
const SETTINGS_LOG_LIMIT = 100;

function loadSettingsStats() {
    try {
        const stats = JSON.parse(localStorage.getItem(SETTINGS_STATS_KEY));
        return {
            changes: (stats && stats.changes) || {},
            labels: (stats && stats.labels) || {},
            values: (stats && stats.values) || {},
            log: (stats && stats.log) || [],
            reset: (stats && stats.reset) || {n: 0, t: null},
        };
    } catch {
        return {changes: {}, labels: {}, values: {}, log: [], reset: {n: 0, t: null}};   // localStorage niedostępny albo zepsuty JSON
    }
}

function saveSettingsStats(stats) {
    try {
        localStorage.setItem(SETTINGS_STATS_KEY, JSON.stringify(stats));
    } catch {
        // localStorage niedostępny - statystyki po prostu się nie zapiszą
    }
}

function recordSettingChange(id) {
    const stats = loadSettingsStats();
    const entry = stats.changes[id] || {n: 0, t: null};
    stats.changes[id] = {n: entry.n + 1, t: Date.now()};
    // Z ostatnich zmian układa się kolejność listy ustawień.
    stats.log = [...stats.log, {id, t: Date.now()}].slice(-SETTINGS_LOG_LIMIT);
    saveSettingsStats(stats);
}

function recordSettingsReset() {
    const stats = loadSettingsStats();
    stats.reset = {n: stats.reset.n + 1, t: Date.now()};
    saveSettingsStats(stats);
}

function settingControls() {
    // Kopie (`.set-copy`) tylko przekazują zmianę oryginałowi.
    // Tylko kontrolki wierszy listy: pole wyszukiwania i przyciski akcji nie są
    // ustawieniami (wiersze akcji, jak wybór osoby i kod z Discorda, też nie). Schowane wiersze nic nie zmienią, więc ich nie ma.
    return settingsList.querySelectorAll('.set-row:not([hidden], .set-copy, .set-action) :is(input, select)');
}

// Etykieta tak, jak widać ją w panelu - bez wartości suwaka i dymka „i”.
function settingLabel(control) {
    const label = (control.id && devPanel.querySelector(`label[for="${control.id}"]`))
        || control.closest('label');
    const clone = label.cloneNode(true);
    for (const extra of clone.querySelectorAll('output, .info, .dev-badge, input, select')) extra.remove();
    return clone.textContent.replace(/\s+/g, ' ').trim();
}

function settingValue(control) {
    if (control.type === 'checkbox') return control.checked ? 'tak' : 'nie';
    if (control.tagName === 'SELECT') return control.selectedOptions[0].textContent.trim();
    return control.value;
}

devPanel.addEventListener('change', event => {
    if (![...settingControls()].includes(event.target) || !event.target.id) return;
    if (event.target.id === 'sound-level') return;
    recordSettingChange(event.target.id);
    syncSettingsSnapshot();
});

// Strona /statystyki nie ma panelu, więc nazwy i bieżące wartości bierze stąd.
// Zapisujemy je dla wszystkich, żeby tabela pokazała też ustawienia, których
// nikt nie ruszył.
function syncSettingsSnapshot() {
    const stats = loadSettingsStats();
    let changed = false;
    for (const control of settingControls()) {
        if (!control.id) continue;
        const label = settingLabel(control);
        const value = settingValue(control);
        if (stats.labels[control.id] !== label || stats.values[control.id] !== value) {
            stats.labels[control.id] = label;
            stats.values[control.id] = value;
            changed = true;
        }
    }
    if (changed) saveSettingsStats(stats);
}
syncSettingsSnapshot();
refreshSettingsOrder();
devPanel.addEventListener('settings-open', refreshSettingsOrder);

// --- wgranie rozkładu Siechnic (DEV) -----------------------------------------
//
// Plik idzie na serwer, ten go sprawdza, zapisuje i sam odpala przebudowę
// bazy w tle. Przebudowa trwa około minuty (pobiera też paczkę Wrocławia),
// więc dopytujemy o jej stan, zamiast kazać zgadywać, kiedy odświeżyć.
const SIECHNICE_POLL_MS = 3000;
// Przebudowa startuje w osobnym procesie, więc pierwsze dopytanie może ją
// jeszcze minąć. Gdy po tym czasie nie trwa i bazy nie ma, to się nie udała.
const SIECHNICE_START_GRACE_MS = 20000;

function setSiechniceStatus(text, state) {
    const el = $('siechnice-status');
    el.textContent = text;
    el.className = state ? `field-hint ${state}` : 'field-hint';
}

function describeSiechnice(status) {
    setSiechniceLocked(!status.unlocked);
    if (!status.unlocked) return;
    if (!status.uploaded_at) {
        setSiechniceStatus('Nic nie wgrano — w bazie nie ma Siechnic.');
    } else if (status.rebuilding) {
        setSiechniceStatus(`Wgrany ${status.uploaded_at}. Przebudowa bazy w toku…`, 'warn');
    } else if (status.in_database) {
        setSiechniceStatus(`Wgrany ${status.uploaded_at} — jest w bazie.`, 'ok');
    } else {
        setSiechniceStatus(`Wgrany ${status.uploaded_at}, ale baza jeszcze go nie ma.`, 'warn');
    }
}

// Token z kodu z Discorda; sessionStorage, bo ma żyć do zamknięcia karty.
const SIECHNICE_TOKEN_KEY = 'siechnice-token';

function siechniceToken() {
    try { return sessionStorage.getItem(SIECHNICE_TOKEN_KEY) || ''; } catch (e) { return ''; }
}

// Chroniona kontrolka (data-protected) przy braku odblokowania jest wyłączona,
// ma kłódkę i po kliknięciu skacze do wiersza z kodem - nowa chroniona rzecz
// potrzebuje tylko tego atrybutu.
function setSiechniceLocked(locked) {
    for (const el of document.querySelectorAll('[data-protected]')) {
        el.classList.toggle('locked', locked);
        el.setAttribute('aria-disabled', String(locked));
    }
    $('siechnice-file').disabled = locked;
    $('access-lock').hidden = !locked;
    $('access-unlocked').hidden = locked;
    if (locked) setSiechniceStatus('Wgrywanie zablokowane — kłódka prowadzi do odblokowania.');
}

function setAccessStatus(text, state) {
    const el = $('access-status');
    el.textContent = text;
    el.className = state ? `field-hint ${state}` : 'field-hint';
}

// Wiersz może leżeć w zwiniętej grupie albo być odfiltrowany szukaniem, więc
// najpierw czyścimy wyszukiwarkę, a grupę rozwijamy dopiero po przerysowaniu listy.
function jumpToSetting(id) {
    devToggle.classList.contains('active') || devToggle.click();
    settingsSearch.value = '';
    renderSettingsList();
    const row = settingsList.querySelector(`.set-row[data-setting="${id}"]`);
    const group = row.closest('details');
    if (group) group.open = true;
    row.scrollIntoView({block: 'center', behavior: 'smooth'});
    row.classList.remove('jump-flash');
    void row.offsetWidth;
    row.classList.add('jump-flash');
    row.addEventListener('animationend', () => row.classList.remove('jump-flash'), {once: true});
}

document.addEventListener('click', event => {
    const locked = event.target.closest('[data-protected].locked');
    if (!locked) return;
    event.preventDefault();
    jumpToSetting('dev-access');
});

$('server-logs-download').addEventListener('click', async event => {
    if (event.currentTarget.classList.contains('locked')) return;
    const status = $('server-logs-status');
    status.textContent = '';
    try {
        const response = await fetch('/api/dev/logi', {headers: {'X-Dev-Token': siechniceToken()}});
        if (!response.ok) {
            const body = await response.json().catch(() => ({}));
            throw new Error(body.error || `serwer odpowiedział ${response.status}`);
        }
        const link = document.createElement('a');
        link.href = URL.createObjectURL(await response.blob());
        link.download = 'metal-planner-server.log';
        link.click();
        URL.revokeObjectURL(link.href);
    } catch (e) {
        status.textContent = `Nie udało się pobrać logów: ${e.message}`;
    }
});

async function fetchSiechniceStatus(init = {}) {
    const response = await fetch('/api/dev/siechnice',
        {...init, headers: {'X-Dev-Token': siechniceToken()}});
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || `serwer odpowiedział ${response.status}`);
    return body;
}

async function followSiechniceRebuild() {
    const started = Date.now();
    let sawRebuild = false;
    for (;;) {
        await new Promise(resolve => setTimeout(resolve, SIECHNICE_POLL_MS));
        const status = await fetchSiechniceStatus();
        if (status.in_database && !status.rebuilding) {
            setSiechniceStatus('Gotowe — baza przebudowana z wgranym rozkładem.', 'ok');
            return;
        }
        sawRebuild = sawRebuild || status.rebuilding;
        if (!status.rebuilding && (sawRebuild || Date.now() - started > SIECHNICE_START_GRACE_MS)) {
            setSiechniceStatus('Plik zapisany, ale przebudowa bazy się nie udała — '
                + 'szczegóły w logach serwera. Wejdzie przy nocnej aktualizacji.', 'bad');
            return;
        }
        setSiechniceStatus('Przyjęto. Przebudowa bazy w toku…', 'warn');
    }
}

async function postDevJson(url, payload) {
    const response = await fetch(url, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || `serwer odpowiedział ${response.status}`);
    return body;
}

async function setupSiechniceLock() {
    const person = $('siechnice-person');
    const send = $('siechnice-send');
    const code = $('siechnice-code');
    const unlock = $('siechnice-unlock');
    const info = await (await fetch('/api/dev/kod')).json();
    if (!info.available) {
        for (const el of [person, send, code, unlock]) el.disabled = true;
        setAccessStatus('Nie da się wysłać kodu — na serwerze brak konfiguracji Discorda '
            + '(DISCORD, data/discord.json).', 'bad');
        return;
    }
    for (const login of info.people) person.add(new Option(login, login));
    setAccessStatus('Wybierz osobę i wyślij kod — dostanie go na Discordzie.');
    send.addEventListener('click', async () => {
        send.disabled = true;
        try {
            await postDevJson('/api/dev/kod', {login: person.value});
            setAccessStatus(`Kod poszedł na Discorda do ${person.value}. Ważny 10 min.`, 'ok');
        } catch (e) {
            setAccessStatus(`Nie wysłano kodu: ${e.message}`, 'bad');
        }
        send.disabled = false;
    });
    unlock.addEventListener('click', async () => {
        try {
            const {token} = await postDevJson('/api/dev/odblokuj', {code: code.value.trim()});
            try { sessionStorage.setItem(SIECHNICE_TOKEN_KEY, token); } catch (e) { /* karta bez pamięci: zostanie zablokowana */ }
            code.value = '';
            setAccessStatus('');
            describeSiechnice(await fetchSiechniceStatus());
        } catch (e) {
            setAccessStatus(`Nie odblokowano: ${e.message}`, 'bad');
        }
    });
}

if (devMode) {
    const input = $('siechnice-file');
    input.addEventListener('change', async () => {
        const file = input.files[0];
        if (!file) return;
        const form = new FormData();
        form.append('file', file);
        // Ten sam plik wybrany drugi raz ma znów wywołać change.
        input.value = '';
        setSiechniceStatus(`Wysyłam ${file.name}…`);
        try {
            await fetchSiechniceStatus({method: 'POST', body: form});
            setSiechniceStatus('Przyjęto. Przebudowa bazy w toku…', 'warn');
            await followSiechniceRebuild();
        } catch (e) {
            setSiechniceStatus(`Nie wgrano: ${e.message}`, 'bad');
        }
    });
    // Podpowiedź zamka po opisie stanu, żeby go nie nadpisać, gdy karta jest już odblokowana.
    fetchSiechniceStatus().then(describeSiechnice).catch(() => {}).then(setupSiechniceLock);
}

// ------------------------------------- odliczenie w Dynamic Island (#453) --
//
// Eksperyment tylko dla dewelopera: klik w godzinę odjazdu na tablicy w rogu
// wysyła odliczenie do aplikacji PushWard na iPhonie. Klucz integracji leży
// wyłącznie w localStorage tej przeglądarki; bez niego nic się nie zmienia.
// Wysyła serwer (/api/dev/pushward), nie przeglądarka - patrz pushward.py.
const PUSHWARD_KEY = 'pushward-key';

function pushwardKey() {
    if (!devMode) return '';
    try { return localStorage.getItem(PUSHWARD_KEY) || ''; } catch { return ''; }
}

// Unix-owa chwila odjazdu - to samo liczenie co pushward.end_date: doba
// rozkładu startuje 12 h przed południem czasu warszawskiego, a godziny
// powyżej 24:00 wypadają po północy. W południe nie ma zmiany czasu, więc
// przesunięcie z tej chwili jest pewne.
function warsawOffsetMs(utcMs) {
    const parts = Object.fromEntries(new Intl.DateTimeFormat('en-GB', {
        timeZone: 'Europe/Warsaw', hourCycle: 'h23', year: 'numeric', month: '2-digit',
        day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
    }).formatToParts(new Date(utcMs)).map(p => [p.type, p.value]));
    return Date.UTC(parts.year, parts.month - 1, parts.day, parts.hour, parts.minute, parts.second) - utcMs;
}

function departureEpochSec(day, sec) {
    const [y, m, d] = day.split('-').map(Number);
    const noonUtc = Date.UTC(y, m - 1, d, 12);
    const noonWarsaw = noonUtc - warsawOffsetMs(noonUtc);
    return noonWarsaw / 1000 - 12 * 3600 + sec;
}

// iOS kończy Live Activity po 8 h, więc dalszy odjazd nie doczekałby końca
// odliczenia. Odjazd poza oknem (teraz, teraz + 8 h] nie dostaje atrybutów,
// więc nie jest ani podkreślony, ani klikalny. Okno sprawdza też serwer.
const PUSHWARD_WINDOW_SEC = 8 * 3600;

function departureAttrs(d, stop, sec) {
    const wait = departureEpochSec(mapDate(), sec) - Date.now() / 1000;
    if (wait <= 0 || wait > PUSHWARD_WINDOW_SEC) return '';
    return ` data-dep-sec="${sec}" data-dep-date="${escAttr(mapDate())}"` +
           ` data-dep-num="${escAttr(d.num)}" data-dep-mode="${escAttr(d.mode)}"` +
           ` data-dep-headsign="${escAttr(d.headsign)}" data-dep-stop="${escAttr(stop)}"` +
           ` data-dep-stop-id="${escAttr(d.stop_id)}"` +
           ('siechnice_stop' in d ? ` data-dep-siechnice-stop="${escAttr(d.siechnice_stop || '')}"` : '');
}

const PUSHWARD_ERRORS = {
    401: 'zły albo wygasły klucz integracji',
    403: 'klucz nie ma uprawnień do tworzenia i zmiany aktywności',
    429: 'limit aktualizacji wyczerpany',
    0: 'brak połączenia z PushWard',
};

function describePushwardError(body) {
    const reason = PUSHWARD_ERRORS[body.status] || body.detail || body.error
        || `PushWard odpowiedział ${body.status}`;
    return `${reason}${body.code ? ` (${body.code})` : ''}`;
}

// Karta ma otwierać aplikację na telefonie, a tam localhost i adresy sieci
// domowej nie działają, więc z nich link idzie na wdrożoną kopię (main, README).
const PUBLIC_ORIGIN = 'https://metal-testing.sze.one';
const LOCAL_HOST = /^(localhost|127\.\d+\.\d+\.\d+|\[?::1\]?|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|.+\.local)$/i;

function pushwardLink() {
    const origin = LOCAL_HOST.test(location.hostname) ? PUBLIC_ORIGIN : location.origin;
    const query = readableQuery(linkParams());
    return origin + location.pathname + (query ? '?' + query : '');
}

// Opcjonalnie klik w kartę otwiera przystanek (ten słupek) w zewnętrznym
// rozkładzie. Wybiera kurs, nie przystanek: kurs Siechnic ma własny serwis
// także na słupku wspólnym z MPK we Wrocławiu, reszta jest w Czy na czas.
// Kursu gminnego bez ustalonego słupka nie wysyłamy do Czy na czas, bo tam go nie ma.
function pushwardStopLink(dep) {
    if (dep.depSiechniceStop !== undefined)
        return dep.depSiechniceStop
            ? `https://siechnice.kiedyprzyjedzie.pl/#/stops/${dep.depSiechniceStop}/departures`
            : pushwardLink();
    return `https://czynaczas.pl/wroclaw?przystanek=${encodeURIComponent(dep.depStopId)}`;
}

async function pushwardClick(event) {
    const hour = event.target.closest('[data-dep-sec]');
    const key = pushwardKey();
    if (!hour || !key) return;
    const dep = hour.dataset;
    if (!confirm(`Wysłać odliczenie do Dynamic Island?\n${dep.depNum} → ${dep.depHeadsign}, ${hour.textContent}`)) return;
    try {
        const response = await fetch('/api/dev/pushward', {
            method: 'POST',
            headers: {'Content-Type': 'application/json', 'X-PushWard-Key': key},
            body: JSON.stringify({
                num: dep.depNum, mode: dep.depMode, headsign: dep.depHeadsign,
                stop: dep.depStop, date: dep.depDate, sec: Number(dep.depSec),
                link: setting('pushward-link') ? pushwardStopLink(dep) : pushwardLink(),
            }),
        });
        const body = await response.json().catch(() => ({}));
        if (!response.ok) alert(`Nie wysłano: ${describePushwardError(body)}`);
    } catch {
        alert('Nie wysłano: brak połączenia z serwerem.');
    }
}

if (devMode) {
    const input = $('pushward-key');
    const status = $('pushward-status');
    const show = (text, kind) => {
        status.textContent = text;
        status.className = kind ? `field-hint ${kind}` : 'field-hint';
    };
    const usage = me => me.used === null || me.limit === null ? ''
        : ` — wykorzystano ${me.used} z ${me.limit} aktualizacji w tym miesiącu`;
    // Serwer woła /auth/me; 401/403 to zły klucz, wszystko inne (też blokada
    // po drodze) znaczy tylko, że nie dało się sprawdzić.
    const check = async key => {
        let response;
        try {
            response = await fetch('/api/dev/pushward/check', {headers: {'X-PushWard-Key': key}});
        } catch {
            return {unreachable: true};
        }
        const body = await response.json().catch(() => ({}));
        if (response.ok) return {me: body};
        return body.status === 401 || body.status === 403 ? {invalid: true} : {unreachable: true};
    };
    const describe = async () => {
        const key = pushwardKey();
        document.body.classList.toggle('pushward-on', !!key);
        if (!key) return show('Wyłączone — bez klucza nic się nie zmienia.');
        show('Włączone — klucz zapisany na tym urządzeniu.', 'ok');
        const result = await check(key);
        if (result.me && pushwardKey() === key) show(`Włączone${usage(result.me)}.`, 'ok');
    };
    $('pushward-save').addEventListener('click', async () => {
        const key = input.value.trim();
        if (!key) return;
        show('Sprawdzanie klucza…');
        const result = await check(key);
        if (result.invalid) return show('Nieprawidłowy klucz.', 'bad');
        if (result.unreachable) return show('Nie dało się sprawdzić klucza — klucz nie został zapisany.', 'bad');
        try {
            localStorage.setItem(PUSHWARD_KEY, key);
        } catch {
            return show('Przeglądarka nie pozwala zapisać klucza.', 'bad');
        }
        input.value = '';
        document.body.classList.add('pushward-on');
        show(`Włączone${usage(result.me)}.`, 'ok');
    });
    $('pushward-clear').addEventListener('click', () => {
        try { localStorage.removeItem(PUSHWARD_KEY); } catch { /* i tak nie ma czego usuwać */ }
        input.value = '';
        describe();
    });
    // Licznik odświeżamy przy wejściu na stronę, nie przy otwarciu panelu:
    // ustawienia nie mają zdarzenia otwarcia, więc to najprostsze miejsce.
    describe();
}

// Opis w ⓘ zamiast akapitu (#225) - w Ustawieniach i w rozkładach. Dymek wisi
// na <body>: panele przewijają się w sobie i przycięłyby go na krawędzi. Klik
// też go pokazuje - na telefonie nie ma najechania - i nie przełącza opcji,
// w której etykiecie ikonka siedzi.
const infoTip = document.createElement('div');
infoTip.className = 'info-tip';
infoTip.hidden = true;
document.body.appendChild(infoTip);

function showInfoTip(icon) {
    infoTip.textContent = icon.dataset.tip;
    infoTip.hidden = false;
    const at = icon.getBoundingClientRect();
    const tip = infoTip.getBoundingClientRect();
    const below = at.bottom + 6 + tip.height <= innerHeight;
    infoTip.style.top = `${below ? at.bottom + 6 : at.top - 6 - tip.height}px`;
    infoTip.style.left = `${Math.max(8, Math.min(at.right - tip.width, innerWidth - tip.width - 8))}px`;
}

document.addEventListener('mouseover', event => {
    const icon = event.target.closest('.info, .obsolete-badge');
    if (icon) showInfoTip(icon);
});
document.addEventListener('mouseout', event => {
    if (event.target.closest('.info, .obsolete-badge')) infoTip.hidden = true;
});
document.addEventListener('focusin', event => {
    const icon = event.target.closest('.info, .obsolete-badge');
    if (icon) showInfoTip(icon);
});
document.addEventListener('focusout', event => {
    if (event.target.closest('.info, .obsolete-badge')) infoTip.hidden = true;
});
document.addEventListener('click', event => {
    const icon = event.target.closest('.info');
    if (!icon) return;
    event.preventDefault();
    showInfoTip(icon);
});
document.addEventListener('scroll', () => { infoTip.hidden = true; }, true);

// Jeden przycisk na cały panel: kasuje wszystkie zapamiętane ustawienia
// i przeładowuje stronę, więc każda wartość wraca z index.html tą samą drogą,
// co przy pierwszej wizycie - bez osobnego "przywróć" dla każdego ustawienia,
// które trzeba by pilnować przy każdej nowej opcji. Ostatnie wyszukiwanie ma
// własny klucz, więc mapa wraca ta sama.
$('dev-reset').addEventListener('click', () => {
    recordSettingsReset();
    for (const key of [SETTINGS_KEY, phoneLayout.PREFS_KEY]) {
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
    // Wskazywanie „mojego miejsca" czeka na klik w mapę, a w rozkładach klik
    // znaczy co innego (patrz map.on('click'), timetableTook) - pasek
    // „Kliknij na mapie" wisiałby nad rozkładem, nic nie robiąc. Wejście
    // w rozkłady to porzucenie okna miejsca, więc anuluje się po cichu.
    stopPlacePick();
    if (flowLayer) { map.removeLayer(flowLayer); flowLayer = null; }
    if (flowLabelLayer) { map.removeLayer(flowLabelLayer); flowLabelLayer = null; }
    if (flowDotLayer) { map.removeLayer(flowDotLayer); flowDotLayer = null; }
    if (flowEndDots) { map.removeLayer(flowEndDots); flowEndDots = null; }
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
    if (whenChangedAway) {
        whenChangedAway = false;
        searchWhenSoon();
    }
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
    LINE_COLORS, MODE_LABEL, STOP_NAMES, ROUTE_ICON, lineColor, lineStyle, escClass,
    suspendPlanner, resumePlanner, setTimeNow, paintWhen, whenEnterTimetable,
    trainsOn: () => trainsOn,
};

}

// X w polach tekstowych (#508): czyści pole jak ręczne skasowanie (zdarzenie input) i zostawia w nim kursor.
// Widoczność robi CSS przez :placeholder-shown, więc działa też przy wartościach ustawionych z kodu.
document.addEventListener('DOMContentLoaded', () => {
    for (const id of ['start', 'end', 'place-where', 'ob-line']) {
        const input = document.getElementById(id);
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'field-clear';
        button.title = 'Wyczyść';
        button.setAttribute('aria-label', 'Wyczyść pole');
        button.innerHTML = '&#10005;';
        if (input.classList.contains('with-action')) button.classList.add('beside-action');
        button.addEventListener('mousedown', e => e.preventDefault());
        button.addEventListener('click', () => {
            input.value = '';
            input.dispatchEvent(new Event('input', { bubbles: true }));
            input.focus();
        });
        input.after(button);
    }
});
