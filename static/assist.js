/* Asystent podróży - trzeci widok tej samej aplikacji.

   Wyszukiwarka i rozkłady odpowiadają na pytania o CAŁĄ podróż. Asystent
   odpowiada na jedno, znacznie węższe: „co mam teraz zrobić". Cała różnica
   jest w tym, GDZIE stoi odpowiedź.

   Lista w rodzaju „145 · za 3 min · 12:41" jest kompletna i bezużyteczna
   naraz: żeby z niej skorzystać, trzeba te symbole z powrotem przykleić do
   rzeczywistości - która krawędź, w którą stronę, który z dwóch stojących tu
   pojazdów. Mapa nigdy o to nie prosiła, bo odpowiadała od razu w świecie.
   Asystent robi więc to samo, tylko z bliska:

     * mapa jest wycentrowana na TOBIE, w zasięgu kilku minut pieszo;
     * każda opcja to KRESKA wychodząca z prawdziwej krawędzi w prawdziwym
       kierunku - geometria z rozkładu, po ulicach i torach (punkt 6
       kontraktu), nigdy prosta ani „mniej więcej tam";
     * numer linii stoi PRZY SWOJEJ KRAWĘDZI, razem z godziną odjazdu, więc
       nie trzeba szukać, do którego słupka się ta liczba odnosi;
     * dotknięcie numeru dorysowuje dalszy przebieg tej opcji i podaje
       godzinę w celu, a reszta PRZYGASA - nie znika (punkt 8 kontraktu:
       przygaszone wciąż ma być widoczne).

   Linii wspólnej krawędzi się NIE ROZSUWA (punkt 7): dwie linie odjeżdżające
   z tego samego słupka dostają jedną grupkę numerów, a nie dwie kreski
   odsunięte od siebie dla wygody rysunku.

   Co jest czyją robotą. Ten plik nie liczy ani jednej godziny i ani jednego
   metra - wszystko przychodzi gotowe z /api/assist (patrz assist.py), razem
   z geometrią. Tu zostaje wyłącznie: zapytać, narysować, pokazać wybraną.

   Po mapie jeździ ten sam Leaflet, co wyszukiwarka, więc wejście w asystenta
   chowa wachlarz połączeń (plannerBridge.suspendPlanner), a wyjście odtwarza
   go bez ponownego zapytania. Poza tym mostem (patrz koniec app.js) ten plik
   nie sięga do wnętrza wyszukiwarki.

   Duży ekran. Telefon jest świadomie poza zakresem - czeka go osobna
   przeróbka całego zachowania aplikacji i dopasowywanie się do niego teraz
   znaczyłoby zostawić półśrodki, których potem i tak nikt nie użyje. */

(function () {
'use strict';

const B = window.plannerBridge;
if (!B) return;         // brak bazy rozkładów - panel pokazuje sam komunikat

const {map, esc, LINE_COLORS, MODE_LABEL, attachAutocomplete, suggestionsFor,
       prettyStopName, rawStopName, saveDevPref} = B;
const $ = id => document.getElementById(id);

const card = $('assist-card');
const hereInput = $('as-here');
const destInput = $('as-dest');
const sideInput = $('as-side');
const lineInput = $('as-line');
const dirSelect = $('as-dir');
const stopSelect = $('as-stop');
const fallbackBox = $('as-fallback');
const recognisedBox = $('as-recognised');
const resultsBox = $('as-results');
const hintBox = $('as-hint');
const placeFields = $('as-place-fields');
const rideFields = $('as-ride-fields');
const modePlaceButton = $('as-mode-place');
const modeRideButton = $('as-mode-ride');
if (!card || !destInput) return;

const LINES = JSON.parse($('line-names').textContent);
const LINE_NUMS = LINES.map(line => line.num);
const MODE_OF_NUM = new Map(LINES.map(line => [line.num, line.mode]));

// Co ile odpowiedź przelicza się sama. Minuta, bo tyle mniej więcej trwa
// „za chwilę": rzadziej i godzina odjazdu na ekranie zdąży się zestarzeć
// o tyle, że przestanie być odpowiedzią, częściej - i każde spojrzenie na
// mapę trafiałoby w przerysowywanie.
const REFRESH_MS = 60000;

// Ile miejsca zostawiamy wokół tego, co ma być widać. Kadr obejmuje mnie
// i wszystkie krawędzie, z których coś odjeżdża - a te leżą najdalej na
// granicy dojścia pieszo (gtfs.WALK_M), więc kadr sam z siebie wychodzi
// „kilka minut pieszo" i nie trzeba mu tego narzucać osobną liczbą.
//
// Z lewej odstęp jest o szerokość panelu większy: mapa idzie pod nim przez
// całą szerokość okna, więc kadr liczony symetrycznie kładzie połowę
// odpowiedzi pod kartą - i to akurat tę połowę, w której stoi „ja".
const FIT_PAD = 70;
const SIDEBAR_PAD = 400;
// Bliżej niż tyle nie przybliżamy nawet przy jednej opcji pod nosem: z
// maksymalnego zbliżenia nie widać już, w którą stronę wychodzi kreska.
const MAX_FIT_ZOOM = 17;

// Wygląd kresek. Gruba i pełna, bo to jest odpowiedź, a nie tło - w
// przeciwieństwie do mapy przepływów tutaj żadna opcja nie jest „bledsza",
// wszystkie przeszły ten sam próg i różnią się dopiero po wskazaniu.
const HEAD_WEIGHT = 6;
const CASING_WEIGHT = 10;
const TAIL_WEIGHT = 4;        // dalszy przebieg wskazanej opcji
const DIM_OPACITY = 0.28;     // przygaszone, ale wciąż widoczne (punkt 8)

const WALK_COLOR = '#455a64';

// ------------------------------------------------------------- stan ----

let where = 'place';      // 'place' albo 'ride'
let herePoint = null;     // {lat, lon} - moja lokalizacja albo klik w mapę
let recognised = null;    // rozpoznany pojazd (patrz /api/side)
let directions = [];      // kierunki linii z /api/onboard (zejście do trzech pól)
let loadedLine = null;    // dla której linii są te kierunki (patrz loadDirections)
let data = null;          // ostatnia odpowiedź /api/assist
let picked = null;        // wskazana opcja (indeks) albo null
let token = 0;            // odsiewa odpowiedzi na nieaktualne pytania
let sideToken = 0;
let dirToken = 0;
let timer = null;

let hovered = null;       // opcja pod kursorem (podgląd dotknięcia)
let clusters = [];        // grupki numerów - do rozsuwania (patrz declutter)
let parts = [];           // warstwy każdej opcji - do przygaszania (patrz applyFocus)
let layer = null;         // kreski, dojścia i moja kropka
let chipLayer = null;     // grupki numerów przy krawędziach
let tailLayer = null;     // dalszy przebieg wskazanej opcji

const active = () => document.body.classList.contains('mode-assist');

// ------------------------------------------------------- przełącznik ----

/** Wejście w asystenta chowa wachlarz połączeń i odsłania panel; wyjście
    odtwarza dokładnie to, co wyszukiwarka miała na ekranie - z pamięci, bez
    ponownego zapytania (patrz suspendPlanner/resumePlanner w app.js). */
function setMode(on) {
    if (on === active()) return;
    // Trzy tryby panelu wykluczają się nawzajem, więc wejście w asystenta
    // zamyka rozkłady. Bez tego obie karty stanęłyby na tej samej półce.
    if (on && window.timetableMode) window.timetableMode.setMode(false);
    document.body.classList.toggle('mode-assist', on);
    const button = $('assist-toggle');
    button.classList.toggle('active', on);
    button.setAttribute('aria-pressed', String(on));
    B.syncTabs();

    if (on) {
        document.body.classList.remove('panel-hidden');
        B.suspendPlanner();
        B.setBaseDim(true);      // słupki schodzą w tło - treścią są opcje
        draw(true);
        start();
        (where === 'ride' ? sideInput : hereInput).focus();
    } else {
        stop();
        clearMap();
        B.setBaseDim(false);
        B.resumePlanner();
    }
}

// Jedno wejście i jedno wyjście: ten sam przycisk, podświetlony, gdy
// asystent stoi na ekranie. Drugiego, „zamknij asystenta" w karcie, tu nie
// ma - w rozkładach jest po to, żeby dało się z nich wyjść na telefonie,
// gdzie pływające przyciski są schowane, a telefon jest poza zakresem
// tego widoku.
$('assist-toggle').addEventListener('click', () => setMode(!active()));

/** Linie, o których mówi to, co stoi teraz na ekranie - do nich zawęża się
    warstwa żywych pojazdów (ta sama umowa, co w rozkładach: patrz
    vehiclesFilter w app.js). Pokazane opcje, a nie wszystko, co jedzie
    w okolicy: warstwa ma mówić o TEJ odpowiedzi. */
window.assistMode = {
    setMode,
    vehicleLines() {
        if (!active() || !data) return null;
        return new Set((data.options || []).map(o => `${o.kind} ${o.num}`));
    },

    /** Klik w mapę: „TU jestem". Słupek przychodzi nazwą, pusty punkt parą
        współrzędnych - jedno i drugie jest miejscem, w którym się stoi, więc
        oba działają tak samo. W trybie „już czymś jadę" klik nie znaczy nic:
        miejscem jest wtedy pojazd, a nie kawałek chodnika pod palcem. */
    pickHere(value) {
        if (!active() || where !== 'place') return false;
        if (typeof value === 'string') {
            herePoint = null;
            hereInput.value = prettyStopName(value);
        } else {
            herePoint = value;
            hereInput.value = 'wskazany punkt';
        }
        ask();
        return true;
    },
};

// ----------------------------------------------------- pytanie ----

/** Co wiemy o tym, gdzie jestem - albo null, gdy jeszcze nic.

    Trzy postacie, dokładnie te, które rozumie /api/assist: punkt (moja
    lokalizacja albo klik w mapę), nazwa przystanku, rozpoznany pojazd.
    Gdy numer boczny nic nie powiedział, a trzy pola są wypełnione, jedzie
    jedno i drugie - numer po to, żeby serwer mógł się z cudzego wyboru
    nauczyć, co ten wóz dziś jedzie (patrz sidenum.remember). */
function whereParams() {
    if (where === 'place') {
        if (herePoint) return {lat: herePoint.lat, lon: herePoint.lon};
        const name = hereInput.value.trim();
        return name ? {start: rawStopName(name)} : null;
    }
    const side = sideInput.value.trim();
    if (recognised && recognised.side === side) return {side};
    const num = lineInput.value.trim();
    const dir = dirSelect.value === '' ? null : directions[Number(dirSelect.value)];
    const stop = stopSelect.value;
    if (!num || !dir || !stop) return null;
    return {
        ...(side ? {side} : {}),
        onboard_num: num,
        onboard_mode: MODE_OF_NUM.get(num) || '',
        onboard_headsign: dir.headsign,
        onboard_stop: stop,
    };
}

function ask() {
    const place = whereParams();
    const dest = destInput.value.trim();
    if (!place || !dest) { clearAll(); return; }

    const mine = ++token;
    card.classList.add('busy');
    fetch('/api/assist?' + new URLSearchParams({
        ...place, end: rawStopName(dest), window: windowMin(),
    }))
        .then(r => r.json())
        .then(answer => {
            if (mine !== token) return;
            card.classList.remove('busy');
            if (answer.error) { showError(answer); return; }
            // Przerysowanie samo z siebie NIE przestawia kadru: patrzący na
            // mapę nie prosił, żeby mu ją co minutę przesuwać pod palcami.
            // Kadr rusza się tylko przy zmianie pytania (patrz `refit`).
            const first = !data || data.start !== answer.start
                || data.end !== answer.end;
            // Wskazana opcja przeżywa samo przeliczenie: widok odświeża się
            // co minutę, a wybór zgaszony bez niczyjego udziału wyglądałby
            // jak awaria. Nie przeżywa jej tylko wtedy, gdy ta opcja
            // faktycznie zniknęła - bo odjechała albo wypadła za próg.
            const held = picked === null ? null : optionKey(data.options[picked]);
            data = answer;
            hovered = null;
            picked = held === null ? null
                : data.options.findIndex(o => optionKey(o) === held);
            if (picked < 0) picked = null;
            draw(first);
            render();
            B.renderVehicles();
        })
        .catch(() => {
            if (mine !== token) return;
            card.classList.remove('busy');
        });
}

function showError(answer) {
    data = null;
    picked = null;
    clearMap();
    // Nierozpoznany pojazd to nie jest brak połączenia - to pytanie, na które
    // trzeba dopytać, i dopytujemy od razu, bez kazania komukolwiek szukać
    // innego przycisku.
    if (answer.fallback) { openFallback(answer.error); return; }
    resultsBox.innerHTML = `<div class="card as-note error">${esc(answer.error)}</div>`;
}

// ------------------------------------------------------- rysowanie ----

function dropLayer(existing) {
    if (existing) map.removeLayer(existing);
    return null;
}

function clearMap() {
    parts = [];
    clusters = [];
    layer = dropLayer(layer);
    chipLayer = dropLayer(chipLayer);
    tailLayer = dropLayer(tailLayer);
}

function clearAll() {
    data = null;
    picked = null;
    clearMap();
    resultsBox.innerHTML = '';
}

const colorOf = option => LINE_COLORS[option.kind] || LINE_COLORS.other;

/** Grupki numerów: JEDNA na krawędź, ze wszystkimi liniami, które z niej
    odjeżdżają (punkt 7 - numerów wspólnego miejsca się nie rozsuwa).

    Krawędzie rozróżniamy po współrzędnej zaokrąglonej do ~11 m, a nie po
    nazwie przystanku: „Katedra" to sześć słupków, z których jedzie się
    w sześć różnych stron, a dwie linie z tego samego słupka mają początek
    geometrii w tym samym miejscu co do metra. */
function byEdge(options) {
    const groups = new Map();
    options.forEach((option, index) => {
        const key = option.at.map(v => v.toFixed(4)).join(',');
        if (!groups.has(key)) groups.set(key, {at: option.at, items: []});
        groups.get(key).items.push({...option, index});
    });
    return [...groups.values()];
}

/** Rozsuwa nachodzące na siebie grupki numerów - W PIONIE, i tylko tyle,
    ile trzeba.

    Grupka MUSI stać przy swojej krawędzi, bo po to w ogóle jest (punkt 7
    kontraktu: zawsze wiadomo, co tu jedzie). Ale dwie krawędzie potrafią
    leżeć kilkanaście metrów od siebie - dwa słupki na rogu - i wtedy ich
    grupki nachodzą na siebie na ekranie tak, że jednej z nich nie da się
    nawet kliknąć: przykrywa ją numer sąsiadki.

    Podnosimy więc tę niżej stojącą dokładnie o tyle, żeby przestała się
    nakładać, i dorysowujemy KRESKĘ PROWADZĄCĄ do jej krawędzi. Numer nadal
    mówi o tym samym słupku, tylko widać, o którym - bez tej kreski
    podniesiona grupka zaczęłaby kłamać o tym, skąd się odjeżdża.

    Liczone w pikselach ekranu i przeliczane po każdym ruchu mapy, bo to
    problem rysunku, nie rozkładu. */
const CLUSTER_GAP_PX = 6;

function declutter() {
    const placed = [];
    const boxes = clusters.map(cluster => {
        const inner = cluster.marker.getElement().firstElementChild;
        const at = map.latLngToContainerPoint(cluster.at);
        return {inner, at, w: inner.offsetWidth, h: inner.offsetHeight};
    });
    // Od góry w dół: grupka wyżej zostaje na miejscu, a ustępuje jej ta,
    // która i tak wisiałaby niżej - inaczej podnoszenie szłoby w nieskończoność
    // przy trzech krawędziach jedna pod drugą.
    for (const box of boxes.slice().sort((a, b) => a.at.y - b.at.y)) {
        let lift = 0;
        for (let guard = 0; guard < clusters.length; guard++) {
            const top = box.at.y - BASE_LIFT_PX - lift - box.h;
            const rect = {x1: box.at.x - box.w / 2, x2: box.at.x + box.w / 2,
                          y1: top, y2: top + box.h};
            const hit = placed.find(other =>
                rect.x1 < other.x2 + CLUSTER_GAP_PX
                && rect.x2 + CLUSTER_GAP_PX > other.x1
                && rect.y1 < other.y2 + CLUSTER_GAP_PX
                && rect.y2 + CLUSTER_GAP_PX > other.y1);
            if (!hit) { placed.push(rect); break; }
            lift += rect.y2 - hit.y1 + CLUSTER_GAP_PX;
        }
        box.inner.style.setProperty('--lift', `${lift}px`);
    }
}

// Ile grupka stoi nad swoją krawędzią, zanim cokolwiek ją podniesie.
const BASE_LIFT_PX = 9;

function chipHtml(option) {
    // Ile idzie się na tę krawędź - przy numerze, a nie tylko w panelu.
    // „16:18" bez tego czyta się jak „masz jedenaście minut", a jeśli osiem
    // z nich trzeba przejść, to jest zupełnie inna odpowiedź na pytanie
    // „co mam teraz zrobić".
    const walk = option.walk_min
        ? `<span class="as-chip-walk">⇢ ${option.walk_min}′</span>` : '';
    return `<button type="button" class="as-chip" data-opt="${option.index}"`
        + ` style="--line: ${colorOf(option)}"`
        + ` title="${esc(option.num)} w stronę ${esc(option.headsign)} —`
        + ` w celu ${esc(option.arrive)}">`
        + `<span class="as-chip-num">${esc(option.num)}</span>`
        + `<span class="as-chip-time">${esc(option.depart)}</span>`
        + walk
        + `</button>`;
}

function drawEdge(group) {
    const marker = L.marker(group.at, {
        icon: L.divIcon({
            className: 'as-cluster',
            html: `<div class="as-cluster-inner">`
                + group.items.map(chipHtml).join('')
                + `<span class="as-leader" aria-hidden="true"></span></div>`,
        }),
        // Nad kreskami i nad kropką - w grupkę się celuje palcem.
        zIndexOffset: 1000,
        keyboard: false,
    }).addTo(chipLayer);
    clusters.push({marker, at: group.at});

    const element = marker.getElement();
    for (const chip of element.querySelectorAll('[data-opt]')) {
        parts[Number(chip.dataset.opt)].chips.push(chip);
    }
    L.DomEvent.disableClickPropagation(element);
    element.addEventListener('click', event => {
        const chip = event.target.closest('[data-opt]');
        if (chip) pick(Number(chip.dataset.opt));
    });
    element.addEventListener('mouseover', event => {
        const chip = event.target.closest('[data-opt]');
        if (chip) preview(Number(chip.dataset.opt));
    });
    element.addEventListener('mouseout', () => preview(null));
}

function draw(refit) {
    clearMap();
    if (!active() || !data) return;

    layer = L.layerGroup().addTo(map);
    chipLayer = L.layerGroup().addTo(map);

    const options = data.options || [];
    parts = [];
    for (const [index, option] of options.entries()) {
        const drawn = {chips: []};
        if (option.walk_path) {
            drawn.walk = L.polyline(option.walk_path, {
                color: WALK_COLOR, weight: 2.5, opacity: 0.9,
                dashArray: '3 6', interactive: false,
            }).addTo(layer);
        }
        // Biała otoczka pod kreską: bez niej dwie linie tego samego koloru
        // wychodzące obok siebie zlewają się z tłem mapy i ze sobą.
        drawn.casing = L.polyline(option.head, {
            color: '#fff', weight: CASING_WEIGHT, opacity: 0.75,
            interactive: false, lineCap: 'round',
        }).addTo(layer);
        drawn.line = L.polyline(option.head, {
            color: colorOf(option), weight: HEAD_WEIGHT, opacity: 1,
            lineCap: 'round',
        }).addTo(layer);
        drawn.line.on('click', () => pick(index));
        drawn.line.on('mouseover', () => preview(index));
        drawn.line.on('mouseout', () => preview(null));
        drawn.arrow = drawArrow(option);
        parts.push(drawn);
    }

    clusters = [];
    for (const group of byEdge(options)) drawEdge(group);
    declutter();
    applyFocus();

    if (data.me) {
        // Dwie kropki, nie jedna: jasna obwódka odcina „ja" od dowolnego
        // tła mapy, a ciemny środek jest tym, co widać kątem oka.
        L.circleMarker(data.me, {
            radius: 13, weight: 0, fillColor: '#fff', fillOpacity: 0.85,
            interactive: false,
        }).addTo(layer);
        L.circleMarker(data.me, {
            radius: 7, weight: 3, color: '#fff', fillColor: '#16202b',
            fillOpacity: 1, interactive: false,
        }).addTo(layer);
    }

    drawTail();
    if (refit) fit();
}

/** Grot na końcu kreski. Kierunek wynika wprawdzie z samego przebiegu, ale
    dopiero grot czyni go czytelnym w sekundę - a to jest jedyne pytanie, na
    które ten widok odpowiada. Obracamy go o azymut OSTATNIEGO odcinka, więc
    pokazuje tam, dokąd geometria faktycznie prowadzi, a nie na prostą od
    przystanku. */
function drawArrow(option) {
    const path = option.head;
    const [a, b] = [path[path.length - 2], path[path.length - 1]];
    if (!a) return null;
    // Azymut na płaszczyźnie ekranu: różnicę długości geograficznej trzeba
    // ścisnąć cosinusem szerokości, inaczej grot na naszej szerokości
    // rozjeżdża się z linią o kilkanaście stopni.
    const dx = (b[1] - a[1]) * Math.cos(b[0] * Math.PI / 180);
    const angle = Math.atan2(dx, b[0] - a[0]) * 180 / Math.PI;
    return L.marker(b, {
        icon: L.divIcon({
            className: 'as-arrow',
            html: `<span style="--line: ${colorOf(option)};`
                + ` --turn: ${angle.toFixed(1)}deg"></span>`,
        }),
        interactive: false,
        keyboard: false,
    }).addTo(layer);
}

/** Dalszy przebieg wskazanej opcji - to, czego nie widać z samej kreski:
    dokąd ta linia jedzie i którędy, aż do celu. */
function drawTail() {
    tailLayer = dropLayer(tailLayer);
    const option = shown();
    if (!option) return;
    tailLayer = L.layerGroup().addTo(map);
    L.polyline(option.path, {
        color: '#fff', weight: TAIL_WEIGHT + 4, opacity: 0.8,
        interactive: false,
    }).addTo(tailLayer);
    L.polyline(option.path, {
        color: colorOf(option), weight: TAIL_WEIGHT, opacity: 0.95,
        interactive: false,
    }).addTo(tailLayer);
}

/** Kadr: ja i wszystkie krawędzie, z których coś odjeżdża. Nie cała trasa -
    asystent odpowiada na „co zrobić teraz", a to dzieje się w zasięgu kilku
    minut pieszo; dokąd te linie jadą dalej, widać po wskazaniu numeru. */
function fit() {
    if (!data) return;
    // Kadr obejmuje też KOŃCE kresek, nie same krawędzie: grot wychodzący
    // poza ekran nie mówi nic, a jest jedynym znakiem kierunku.
    const points = [];
    for (const option of data.options || []) {
        points.push(option.at, option.head[option.head.length - 1]);
        if (option.walk_path) points.push(...option.walk_path);
    }
    if (data.me) points.push(data.me);
    if (!points.length) return;
    map.fitBounds(L.latLngBounds(points), {
        paddingTopLeft: [SIDEBAR_PAD, FIT_PAD],
        paddingBottomRight: [FIT_PAD, FIT_PAD],
        maxZoom: MAX_FIT_ZOOM,
    });
}

// ------------------------------------------------- wskazana opcja ----

const shown = () => {
    const index = hovered !== null ? hovered : picked;
    return index === null || !data ? null : data.options[index];
};

/** Przygasza wszystko poza tą opcją, na którą się patrzy - wybraną klikiem
    albo tylko wskazaną kursorem. Najechanie jest podglądem dotknięcia, więc
    pokazuje to samo, zanim cokolwiek się wybierze.

    PRZEMALOWUJE gotowe warstwy, a nie rysuje ich od nowa. Przerysowanie
    kasowało element, nad którym właśnie stoi kursor - przeglądarka wysyłała
    wtedy `mouseout` na jego gruzach i podgląd gasł w tej samej klatce,
    w której się zapalił.

    Przygaszone zostaje WIDOCZNE (punkt 8 kontraktu) - to wciąż jest opcja,
    tylko nie ta oglądana. */
function applyFocus() {
    const focus = picked !== null ? picked : hovered;
    parts.forEach((drawn, index) => {
        const dim = focus !== null && focus !== index;
        const opacity = dim ? DIM_OPACITY : 1;
        if (drawn.walk) drawn.walk.setStyle({opacity: opacity * 0.9});
        drawn.casing.setStyle({opacity: opacity * 0.75});
        drawn.line.setStyle({opacity});
        if (drawn.arrow) drawn.arrow.getElement().style.opacity = opacity;
        for (const chip of drawn.chips) {
            chip.classList.toggle('dim', dim);
            chip.classList.toggle('on', focus === index);
        }
    });
}

function preview(index) {
    if (picked !== null) return;    // wybrane bije najechane
    if (hovered === index) return;
    hovered = index;
    applyFocus();
    drawTail();
    render();
}

function pick(index) {
    picked = picked === index ? null : index;
    hovered = null;
    applyFocus();
    drawTail();
    render();
}

/** Czym jest ta opcja, niezależnie od jej miejsca na liście. Po samym
    przeliczeniu indeksy się przestawiają (coś odjechało, coś doszło), więc
    wybór zapamiętany numerem pozycji wskazywałby po minucie inną linię. */
const optionKey = option =>
    `${option.kind} ${option.num} ${option.headsign} ${option.stop}`;

// ---------------------------------------------------------- panel ----

const plural = (n, one, few, many) =>
    n === 1 ? one : (n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20)) ? few : many;

function summaryHtml() {
    const options = data.options || [];
    if (!options.length) {
        return `<div class="card as-note">Stąd nie ma teraz nic, czym byłoby
            się w celu nie później niż ${data.window_min} min za najszybszą
            opcją. Pokrętło pod ⚙ przesuwa ten próg.</div>`;
    }
    return `<div class="card as-summary">`
        + `<p class="as-summary-line"><b>${options.length}</b> `
        + `${plural(options.length, 'sposób', 'sposoby', 'sposobów')} stąd do `
        + `<b>${esc(prettyStopName(data.end))}</b>. Najszybciej jesteś tam `
        + `o <b>${esc(data.best_arrival)}</b>.</p>`
        + `<p class="as-summary-note">Wszystko, czym jest się w celu do `
        + `${data.window_min} min później. Dotknij numeru na mapie.</p>`
        + `</div>`;
}

function legHtml(leg) {
    if (leg.kind === 'walk') {
        return `<li class="as-leg walk"><span class="as-leg-icon">⇢</span>`
            + `<span>${esc(leg.text)}</span></li>`;
    }
    return `<li class="as-leg"><span class="badge ${esc(leg.mode)}">${esc(leg.num)}</span>`
        + `<span><b>${esc(leg.from_time)}</b> ${esc(prettyStopName(leg.from))}`
        + ` → <b>${esc(leg.to_time)}</b> ${esc(prettyStopName(leg.to))}`
        + `<span class="as-leg-dir"> w stronę ${esc(leg.headsign)}</span></span></li>`;
}

function detailHtml(option) {
    const walk = option.walk_min
        ? `${option.walk_min} min pieszo do przystanku, ` : '';
    const later = option.later_min
        ? `<span class="as-later">+${option.later_min} min</span>`
        : `<span class="as-later best">najszybciej</span>`;
    return `<div class="card as-detail" style="--line: ${colorOf(option)}">`
        + `<div class="as-detail-head">`
        + `<span class="badge ${esc(option.kind)}">${esc(option.num)}</span>`
        + `<span class="as-detail-dir">w stronę ${esc(option.headsign)}</span>`
        + later + `</div>`
        + `<p class="as-detail-line">${esc(walk)}odjazd <b>${esc(option.depart)}</b>`
        + ` z <b>${esc(prettyStopName(option.stop))}</b>.</p>`
        + `<p class="as-detail-line">W celu o <b>${esc(option.arrive)}</b>`
        + (option.transfers
            ? `, ${option.transfers} ${plural(option.transfers, 'przesiadka', 'przesiadki', 'przesiadek')}.`
            : ', bez przesiadki.')
        + `</p>`
        + `<ol class="as-legs">${option.legs.map(legHtml).join('')}</ol>`
        + `</div>`;
}

function recognisedHtml() {
    if (!data || !data.onboard) { recognisedBox.hidden = true; return; }
    const ride = data.onboard;
    recognisedBox.hidden = false;
    recognisedBox.innerHTML =
        `<span class="badge ${esc(ride.mode)}">${esc(ride.num)}</span> `
        + `w stronę <b>${esc(ride.headsign)}</b>, najbliższy przystanek `
        + `<b>${esc(prettyStopName(ride.stop_name))}</b> o <b>${esc(ride.at)}</b>.`
        + (ride.learned
            ? ` <span class="as-learned">(z tego, co ktoś dziś wpisał ręcznie)</span>`
            : '');
}

function render() {
    if (!data) { resultsBox.innerHTML = ''; recognisedBox.hidden = true; return; }
    const option = shown();
    resultsBox.innerHTML = summaryHtml() + (option ? detailHtml(option) : '');
    recognisedHtml();
}

// -------------------------------------------- gdzie jestem: dwa tryby ----

function setWhere(mode) {
    where = mode;
    placeFields.hidden = mode !== 'place';
    rideFields.hidden = mode !== 'ride';
    for (const [button, on] of [[modePlaceButton, mode === 'place'],
                                [modeRideButton, mode === 'ride']]) {
        button.classList.toggle('active', on);
        button.setAttribute('aria-pressed', String(on));
    }
    // Zmiana pytania zabiera poprzednią odpowiedź na nie: nie da się naraz
    // stać na przystanku i jechać autobusem.
    herePoint = null;
    recognised = null;
    recognisedBox.hidden = true;
    clearAll();
    (mode === 'ride' ? sideInput : hereInput).focus();
}

modePlaceButton.addEventListener('click', () => setWhere('place'));
modeRideButton.addEventListener('click', () => setWhere('ride'));

$('as-locate').addEventListener('click', () => {
    navigator.geolocation.getCurrentPosition(
        position => {
            herePoint = {lat: position.coords.latitude,
                         lon: position.coords.longitude};
            hereInput.value = 'moja lokalizacja';
            ask();
        },
        () => { hintBox.textContent = 'Nie udało się ustalić lokalizacji.'; },
        {enableHighAccuracy: true, timeout: 10000},
    );
});

// ------------------------------------------------- numer boczny ----

/** Rozpoznanie pojazdu po numerze bocznym - osobno od szukania trasy, żeby
    zdanie potwierdzające („145 w stronę Bartoszowic…") stanęło na ekranie
    zaraz po wpisaniu numeru, a nie dopiero razem z wynikiem. To jedyne
    miejsce, w którym widać pomyłkę rozpoznania. */
function recogniseSide() {
    const side = sideInput.value.trim();
    recognised = null;
    if (!side) { recognisedBox.hidden = true; fallbackBox.hidden = true; return; }

    const mine = ++sideToken;
    recognisedBox.hidden = false;
    recognisedBox.innerHTML = 'Szukam tego wozu…';
    fetch('/api/side?' + new URLSearchParams({side}))
        .then(r => r.json())
        .then(answer => {
            if (mine !== sideToken) return;
            if (answer.error) { openFallback(answer.error); return; }
            recognised = {...answer, side};
            fallbackBox.hidden = true;
            recognisedBox.innerHTML =
                `<span class="badge ${esc(answer.mode)}">${esc(answer.num)}</span> `
                + `w stronę <b>${esc(answer.headsign)}</b>, najbliższy przystanek `
                + `<b>${esc(prettyStopName(answer.stop_name))}</b>.`;
            ask();
        })
        .catch(() => {
            if (mine === sideToken) openFallback('Nie udało się sprawdzić numeru.');
        });
}

/** Zejście do trzech pól - natychmiastowe i bez poczucia porażki. Powód
    piszemy wprost, bo jest rzeczowy (kanał odświeża się co 20 minut, co piąty
    wóz nie podaje dniówki), a nie jest niczyją pomyłką. */
function openFallback(reason) {
    loadedLine = null;
    recognisedBox.hidden = false;
    recognisedBox.innerHTML = `${esc(reason)} <b>Powiedz to inaczej:</b>`;
    fallbackBox.hidden = false;
    lineInput.focus();
}

sideInput.addEventListener('change', recogniseSide);
sideInput.addEventListener('input', () => {
    // Czterocyfrowy numer rozpoznajemy, gdy tylko się domknie - czekanie na
    // opuszczenie pola kazałoby kliknąć gdzieś obok, żeby cokolwiek się stało.
    if (sideInput.value.trim().length >= 4) recogniseSide();
});

// --------------------------------------- trzy pola, gdy numer milczy ----

function loadDirections() {
    const num = lineInput.value.trim();
    // Ta sama linia = nie ma czego wczytywać. Bez tego warunku pole linii
    // wczytuje kierunki po raz drugi w chwili, gdy traci kursor (przeglądarka
    // wysyła wtedy własne `change`, obok tego z podpowiedzi) - a wczytywanie
    // zaczyna się od wyczyszczenia obu list, więc kasowało wybrany przed
    // sekundą kierunek i przystanek. Objawiało się to tym, że po zejściu do
    // trzech pól asystent nie szukał już wcale: pytanie stawało się niepełne
    // dokładnie w chwili, w której dopisywało się do niego cel.
    if (num === loadedLine) return;
    loadedLine = num;
    directions = [];
    fillDirections();
    if (!num) return;
    const mine = ++dirToken;
    fetch('/api/onboard?' + new URLSearchParams({
        num, mode: MODE_OF_NUM.get(num) || '',
    }))
        .then(r => r.json())
        .then(answer => {
            if (mine !== dirToken || answer.error) return;
            directions = answer.directions || [];
            fillDirections();
        })
        .catch(() => { if (mine === dirToken) loadedLine = null; });
}

function fillDirections() {
    dirSelect.innerHTML = '<option value="">Kierunek</option>'
        + directions.map((dir, i) =>
            `<option value="${i}">${esc(dir.headsign)}</option>`).join('');
    dirSelect.disabled = !directions.length;
    if (directions.length === 1) dirSelect.value = '0';
    fillStops();
}

function fillStops() {
    const dir = dirSelect.value === '' ? null : directions[Number(dirSelect.value)];
    const stops = dir ? dir.stops : [];
    stopSelect.innerHTML = '<option value="">Przystanek</option>'
        + stops.map(stop =>
            `<option value="${esc(stop.id)}">${esc(prettyStopName(stop.name))}</option>`).join('');
    stopSelect.disabled = !stops.length;
}

attachAutocomplete(lineInput, () => loadDirections(), {
    suggest: query => suggestionsFor(query, LINE_NUMS, null, 8)
        .map(item => ({...item, mode: MODE_OF_NUM.get(item.name)})),
    render: item => `<span class="badge ${esc(item.mode)}">${esc(item.name)}</span>`
        + `<span class="ac-kind">${esc(MODE_LABEL[item.mode] || 'Linia')}</span>`,
    onEnter: () => loadDirections(),
});
lineInput.addEventListener('change', loadDirections);
dirSelect.addEventListener('change', fillStops);
stopSelect.addEventListener('change', ask);

// ------------------------------------------------- pola przystanków ----

for (const [input, onPick] of [[hereInput, () => { herePoint = null; ask(); }],
                               [destInput, ask]]) {
    attachAutocomplete(input, onPick, {onEnter: onPick});
    input.addEventListener('change', onPick);
}

// ------------------------------------------------- próg z pokrętła ----

const windowMin = () => {
    const knob = $('assist-window');
    return knob ? knob.value : '';
};

const knob = $('assist-window');
if (knob) {
    knob.addEventListener('input', () => {
        const out = $('assist-window-value');
        if (out) out.textContent = knob.value;
        saveDevPref('assist-window', knob.value);
    });
    // Przeliczamy dopiero po puszczeniu suwaka: każdy krok to pełne
    // wyszukiwanie, a przeciągnięcie od 1 do 60 to sześćdziesiąt z nich.
    knob.addEventListener('change', () => { if (active()) ask(); });
}

// ---------------------------------------- samo przeliczanie w czasie ----

function start() {
    stop();
    timer = setInterval(() => { if (active()) ask(); }, REFRESH_MS);
}

function stop() {
    if (timer) clearInterval(timer);
    timer = null;
}

// Rozsuwanie liczy się w pikselach, więc po każdej zmianie przybliżenia
// trzeba je przeliczyć: przy oddaleniu krawędzie zbiegają się do siebie
// i grupki, które przed chwilą mieściły się obok, zaczynają na siebie łazić.
map.on('zoomend', () => { if (active() && clusters.length) declutter(); });

})();
