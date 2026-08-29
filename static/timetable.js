/* Rozkłady jazdy - drugi tryb panelu, obok wyszukiwarki połączeń.

   Odpowiada na dwa pytania, na które wyszukiwarka nie odpowiada wcale:

   - LINIA ("co robi 17-tka") - kierunki, godziny każdego kursu i cały
     przebieg na mapie;
   - PRZYSTANEK ("co odjeżdża z Katedry") - tablica odjazdów wszystkich linii,
     z zaznaczaniem, KTÓRE z nich mają być w tej tablicy złączone; trasy
     zaznaczonych linii - od tego przystanku dalej - rysują się na mapie.

   Jedno pole na jedno i drugie. Rodzaj rozkładu wynika z tego, co się wpisało,
   a nie z przełącznika ustawianego wcześniej: "17" to linia, "Katedra" to
   przystanek, a na liście podpowiedzi widać, co jest czym.

   Oba tryby jeżdżą po TEJ SAMEJ mapie Leafleta co wyszukiwarka, więc wejście
   w rozkłady chowa wachlarz połączeń (plannerBridge.suspendPlanner), a wyjście
   odtwarza go bez ponownego zapytania. Poza tym mostem (patrz koniec app.js)
   ten plik nie sięga do wnętrza wyszukiwarki. */

(function () {
'use strict';

const B = window.plannerBridge;
if (!B) return;         // brak bazy rozkładów - panel pokazuje sam komunikat

const {map, esc, fitTo, LINE_COLORS, MODE_LABEL} = B;
const $ = id => document.getElementById(id);

const queryInput = $('tt-query');
const dateInput = $('tt-date');
const clearButton = $('tt-clear');
const resultsBox = $('tt-results');
const hintBox = $('tt-hint');
if (!queryInput || !resultsBox) return;

// Numery linii do podpowiedzi - wstawione w stronę razem z nazwami
// przystanków, więc pole działa od pierwszego wpisanego znaku, bez zapytania.
const LINES = JSON.parse($('line-names').textContent);
const LINE_NUMS = LINES.map(line => line.num);
const MODE_OF_NUM = new Map(LINES.map(line => [line.num, line.mode]));

// Ile linii naraz ma sens na mapie. Powyżej tego progu z węzła przesiadkowego
// robi się kłębek, w którym nie widać już żadnej pojedynczej trasy - wtedy
// tablica zostaje, a mapa czeka na zawężenie wyboru.
const MAX_ROUTES_ON_MAP = 8;
// Ile kierunków jednej linii rysujemy. Linia ma dwa podstawowe, a poza nimi
// garść kursów skróconych - te leżą na trasie podstawowej i nic nie wnoszą.
const MAX_DIRS_PER_LINE = 2;
// Ile odjazdów pokazujemy od razu. Doba na dużym węźle to ~2000 wierszy.
const BOARD_PAGE = 40;
// Przybliżenie tablicy bez tras: sam przystanek to punkt, a punkt kadruje się
// na maksymalne zbliżenie - z którego nie widać nawet, w którym jest mieście.
const BOARD_ZOOM = 15;
// Wariant "poboczny" linii: tyle razy mniej kursów niż jej wariant główny.
// Kursy zjazdowe do zajezdni mają ich pojedyncze sztuki i nie mają prawa
// stać w jednym rzędzie z kierunkiem, którym linia jeździ cały dzień.
const SIDE_VARIANT_RATIO = 0.25;

// ------------------------------------------------------------- stan ----

let kind = null;          // 'line' albo 'stop' - wynika z tego, co znaleziono
let data = null;          // ostatnia odpowiedź serwera
let token = 0;            // odsiewa odpowiedzi na nieaktualne zapytania

let variantIndex = 0;     // wybrany kierunek linii
let courseIndex = null;   // wybrany kurs tej linii
let sideOpen = false;     // rozwinięte warianty skrócone
let recenter = false;     // przewinąć pasek godzin do wybranego kursu

let picked = new Set();   // zaznaczone linie tablicy ("bus|8")
let fromSec = null;       // od której godziny pokazujemy odjazdy (null = doba)
let boardLimit = BOARD_PAGE;
let openDep = null;       // rozwinięty odjazd (indeks) - jego kurs jest na mapie
let tripCache = new Map();

// ------------------------------------------------------ warstwy mapy ----

let routeLayer = null;    // przebiegi linii
let markerLayer = null;   // przystanki tego rozkładu
let focusLayer = null;    // wskazany przystanek albo wybrany kurs

function dropLayer(layer) {
    if (layer) map.removeLayer(layer);
    return null;
}

function clearMap() {
    routeLayer = dropLayer(routeLayer);
    markerLayer = dropLayer(markerLayer);
    focusLayer = dropLayer(focusLayer);
}

const colorOf = mode => LINE_COLORS[mode] || LINE_COLORS.other;

/* Trzy siły rysowania trasy - kolorowa nitka w białej otoczce, tak samo jak
   wybrana trasa w wyszukiwarce, tylko w trzech natężeniach:
   - 'full'  - to, o co pytamy (przebieg linii, kurs od wsiadania dalej);
   - 'soft'  - kilka tras naraz, każda równie ważna (zaznaczone linie tablicy);
   - 'ghost' - kontekst, nie odpowiedź (część kursu przejechana przed nami). */
const ROUTE_LOOK = {
    full: {casing: [9, 0.95], line: [5, 1]},
    soft: {casing: [7, 0.9], line: [4, 0.9]},
    ghost: {casing: [6, 0.5], line: [3, 0.4]},
};

function routeLines(path, mode, level = 'full') {
    const look = ROUTE_LOOK[level];
    const latlngs = path.map(p => L.latLng(p));
    const stroke = (color, [weight, opacity]) => L.polyline(latlngs, {
        color, weight, opacity,
        lineCap: 'round', lineJoin: 'round', interactive: false,
    });
    return [stroke('#fff', look.casing), stroke(colorOf(mode), look.line)];
}

function stopDots(stops, mode, times) {
    return stops.map((stop, i) => {
        const marker = L.circleMarker([stop.lat, stop.lon], {
            radius: 5, weight: 2, color: colorOf(mode),
            fillColor: '#fff', fillOpacity: 1,
        });
        const time = times && times[i];
        marker.bindTooltip(time ? `${time} · ${stop.name}` : stop.name);
        return marker;
    });
}

function terminusDots(stops, mode) {
    return [stops[0], stops[stops.length - 1]].filter(Boolean).map(stop =>
        L.circleMarker([stop.lat, stop.lon], {
            radius: 8, weight: 3, color: '#263238',
            fillColor: colorOf(mode), fillOpacity: 1, interactive: false,
        }));
}

function showRoutes(layers, markers, refit, points) {
    routeLayer = dropLayer(routeLayer);
    markerLayer = dropLayer(markerLayer);
    if (layers.length) routeLayer = L.layerGroup(layers).addTo(map);
    if (markers.length) markerLayer = L.layerGroup(markers).addTo(map);
    if (refit && points && points.length) fitTo(points);
}

/** Wszystkie słupki miasta naraz to tło, na którym nie widać narysowanej
    trasy - dokładnie ten sam problem, co przy mapie przepływów, więc i to samo
    rozwiązanie. Przygaszamy je dopiero, gdy jest co przygaszać. */
const dimBase = dim => B.setBaseDim(dim);

/** Trasa dociągnięta asynchronicznie - dokładamy ją do warstwy, która już
    stoi na mapie, zamiast przerysowywać całość: odpowiedzi wracają jedna po
    drugiej i każde przerysowanie mrugałoby resztą. */
function addRoute(path, mode) {
    if (!routeLayer) routeLayer = L.layerGroup().addTo(map);
    for (const layer of routeLines(path, mode, 'soft')) routeLayer.addLayer(layer);
}

// ------------------------------------------------------ przełącznik ----

const modeButtons = [$('mode-toggle'), $('mode-toggle-m')].filter(Boolean);
const tabLabel = $('tab-list-label');

const active = () => document.body.classList.contains('mode-timetable');

/** Wejście w rozkłady chowa wachlarz połączeń i odsłania panel (przy schowanym
    panelu przełącznik nie miałby czego pokazać); wyjście odtwarza dokładnie to,
    co wyszukiwarka miała na ekranie - z pamięci, bez zapytania. */
function setMode(on) {
    if (on === active()) return;
    document.body.classList.toggle('mode-timetable', on);
    for (const button of modeButtons) {
        button.classList.toggle('active', on);
        button.setAttribute('aria-pressed', String(on));
    }
    if (tabLabel) tabLabel.textContent = on ? 'Rozkład' : 'Trasy';

    if (on) {
        document.body.classList.remove('panel-hidden');
        B.suspendPlanner();
        draw(true);
        queryInput.focus();
    } else {
        clearMap();
        dimBase(false);
        B.resumePlanner();
    }
}

for (const button of modeButtons) {
    button.addEventListener('click', () => setMode(!active()));
}

// Klik w słupek na mapie: w tym trybie znaczy "pokaż rozkład tego przystanku".
window.timetableMode = {
    pickStop(name) {
        if (!active() || !name) return false;
        queryInput.value = name;
        load('stop');
        return true;
    },
};

// ------------------------------------------- jedno pole, dwa rodzaje ----

/** Podpowiedzi: najpierw pasujące linie, potem przystanki. Numer wpisuje się
    krótko i trafia w kilkanaście linii naraz, więc ich pula jest ograniczona -
    inaczej "1" wypełniłoby całą listę numerami i nie zostałoby miejsca na
    przystanek, którego nazwa zaczyna się od cyfry ("8 Maja"). */
const LINE_LIMIT = 5, STOP_LIMIT = 6;

function suggest(query) {
    const lines = B.suggestionsFor(query, LINE_NUMS, null, LINE_LIMIT)
        .map(item => ({...item, kind: 'line', mode: MODE_OF_NUM.get(item.name)}));
    const stops = B.suggestionsFor(query, B.STOP_NAMES, null, STOP_LIMIT)
        .map(item => ({...item, kind: 'stop'}));
    return [...lines, ...stops];
}

function suggestionRow(item) {
    // Numer linii bez podświetlania trafienia: plakietka JEST tym numerem,
    // więc pasuje cała, a kolorowy fragment w kolorowym prostokącie tylko
    // zabiera kontrast.
    if (item.kind === 'line') {
        return `<span class="badge ${esc(item.mode)}">${esc(item.name)}</span>`
             + `<span class="ac-kind">${esc(MODE_LABEL[item.mode] || 'Linia')}</span>`;
    }
    return `<span class="ac-pin" aria-hidden="true"></span>`
         + `<span class="ac-name">${B.suggestionHtml(item)}</span>`
         + `<span class="ac-kind">przystanek</span>`;
}

B.attachAutocomplete(queryInput, item => load(item.kind), {
    suggest,
    render: suggestionRow,
    onEnter: () => load(),
});

/** Czego szuka wpisany tekst, gdy nikt nie wybrał podpowiedzi. Numer linii
    jest rozstrzygający - żaden przystanek we Wrocławiu nie nazywa się samą
    liczbą - a wszystko inne jedzie do przystanków. */
function kindOf(query) {
    return MODE_OF_NUM.has(query.trim()) ? 'line' : 'stop';
}

const currentDate = () => (dateInput && dateInput.value) || '';

// ------------------------------------------------------ zapytania ----

function setBusy(on) {
    document.body.classList.toggle('tt-searching', on);
    resultsBox.setAttribute('aria-busy', String(on));
}

function reset() {
    ++token;
    setBusy(false);
    kind = null;
    data = null;
    variantIndex = 0;
    courseIndex = null;
    sideOpen = false;
    picked = new Set();
    fromSec = null;
    boardLimit = BOARD_PAGE;
    openDep = null;
    tripCache = new Map();
    resultsBox.innerHTML = '';
    clearMap();
    dimBase(false);
    syncField();
}

function syncField() {
    const filled = !!queryInput.value.trim();
    clearButton.hidden = !filled;
    hintBox.hidden = !!data;
}

function fail(message, suggestions) {
    let html = `<div class="notice error"><p>${esc(message)}</p>`;
    if (suggestions && suggestions.length) {
        html += '<p>Czy chodziło o:</p><ul>' + suggestions.map(name =>
            `<li><a href="#" data-suggest="${esc(name)}">${esc(name)}</a></li>`
        ).join('') + '</ul>';
    }
    resultsBox.innerHTML = html + '</div>';
}

function load(forced) {
    const query = queryInput.value.trim();
    if (!query) { reset(); return; }
    const wanted = forced || kindOf(query);
    const mine = ++token;
    clearMap();
    setBusy(true);
    resultsBox.innerHTML =
        '<div class="notice loading"><span class="spinner" aria-hidden="true"></span>'
        + 'Czytam rozkład…</div>';

    const params = new URLSearchParams({date: currentDate()});
    let url;
    if (wanted === 'line') {
        params.set('num', query);
        const mode = MODE_OF_NUM.get(query);
        if (mode) params.set('mode', mode);
        url = '/api/line?' + params;
    } else {
        params.set('stop', query);
        url = '/api/stop_board?' + params;
    }

    fetch(url).then(r => r.json()).then(payload => {
        if (mine !== token) return;
        if (payload.error) {
            kind = null;
            data = null;
            clearMap();
            dimBase(false);
            fail(payload.error, payload.suggestions);
            return;
        }
        kind = wanted;
        data = payload;
        variantIndex = 0;
        sideOpen = false;
        openDep = null;
        boardLimit = BOARD_PAGE;
        if (kind === 'line') {
            // Rozkład otwiera się na najbliższym kursie, a nie na pustej
            // liście przystanków: pytanie brzmi "o której to jedzie",
            // więc jakaś godzina musi być na ekranie od razu.
            const first = mainVariants()[0] || payload.variants[0];
            variantIndex = payload.variants.indexOf(first);
            courseIndex = nextCourseIndex(first);
            recenter = true;
        } else {
            picked = new Set(payload.lines.map(lineKey));
            fromSec = defaultFromSec(payload);
        }
        // Widok PRZED treścią: na telefonie zakładka "Mapa" trzyma listę
        // wyników w display:none, a wtedy pasek godzin ma zerową wysokość
        // i nie da się go przewinąć do wybranego kursu.
        B.setView('list');
        render();
        draw(true);
    }).catch(() => {
        if (mine === token) fail('Nie udało się połączyć z serwerem.');
    }).finally(() => {
        if (mine === token) { setBusy(false); syncField(); }
    });
}

queryInput.addEventListener('input', syncField);
if (dateInput) dateInput.addEventListener('change', () => { if (data) load(kind); });
$('tt-today').addEventListener('click', () => {
    dateInput.value = isoToday();
    if (data) load(kind);
});
clearButton.addEventListener('click', () => {
    queryInput.value = '';
    reset();
    queryInput.focus();
});

// --------------------------------------------------------- rysowanie ----

function draw(refit) {
    if (!active()) return;
    if (!data) { clearMap(); return; }
    if (kind === 'line') drawLine(refit);
    else drawBoard(refit);
}

function render() {
    syncField();
    if (!data) return;
    if (kind === 'line') renderLine();
    else renderBoard();
}

function isoToday() {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
         + `-${String(now.getDate()).padStart(2, '0')}`;
}

const nowSec = () => {
    const now = new Date();
    return now.getHours() * 3600 + now.getMinutes() * 60;
};

/** Godzina odniesienia dla rozkładu - "teraz", ale tylko dla dnia
    dzisiejszego. Rozkład na inny dzień czyta się od początku doby, bo
    bieżąca godzina nic o nim nie mówi. */
const referenceSec = () => (data.date === isoToday() ? nowSec() : null);

const DAY_NAMES = ['niedziela', 'poniedziałek', 'wtorek', 'środa',
                   'czwartek', 'piątek', 'sobota'];

function dayLabel() {
    const [y, m, d] = data.date.split('-').map(Number);
    const date = new Date(y, m - 1, d);
    const name = DAY_NAMES[date.getDay()];
    return data.date === isoToday()
        ? `dziś, ${name}` : `${name} ${d}.${String(m).padStart(2, '0')}`;
}

function plural(n, one, few, many) {
    if (n === 1) return one;
    const rest = n % 10, hundreds = n % 100;
    return rest >= 2 && rest <= 4 && (hundreds < 12 || hundreds > 14) ? few : many;
}

// ------------------------------------------------- rozkład jednej linii ----

const variantOf = () => (data && data.variants ? data.variants[variantIndex] : null);

/** Kierunki podstawowe kontra kursy skrócone. Linia ma dwa kierunki, a poza
    nimi zjazdy do zajezdni i wjazdy z pętli w połowie trasy - pokazane
    równorzędnie zamieniają wybór kierunku w listę kilkunastu pozycji, w
    której trzeba szukać tej właściwej. */
function mainVariants() {
    if (!data.variants.length) return [];
    const top = data.variants[0].trips.length;
    return data.variants.filter(v => v.trips.length >= top * SIDE_VARIANT_RATIO);
}

const sideVariants = () => data.variants.filter(v => !mainVariants().includes(v));

/** Pierwszy kurs po godzinie odniesienia (albo ostatni w dobie, gdy już po
    wszystkim). Bez tego rozkład otwiera się na liście przystanków bez godzin. */
function nextCourseIndex(variant) {
    if (!variant || !variant.trips.length) return null;
    const from = referenceSec();
    if (from === null) return 0;
    const found = variant.trips.findIndex(trip => trip.sec >= from);
    return found === -1 ? variant.trips.length - 1 : found;
}

function courseTimes() {
    const variant = variantOf();
    if (!variant || courseIndex === null) return null;
    const course = variant.trips[courseIndex];
    return course ? course.times : null;
}

function drawLine(refit) {
    const variant = variantOf();
    focusLayer = dropLayer(focusLayer);
    if (!variant) { clearMap(); return; }
    dimBase(true);
    showRoutes(
        routeLines(variant.path, data.mode),
        [...stopDots(variant.stops, data.mode, courseTimes()),
         ...terminusDots(variant.stops, data.mode)],
        refit,
        variant.path,
    );
}

function renderLine() {
    if (!data.variants.length) {
        fail(data.note || 'Tego dnia ta linia nie kursuje.');
        return;
    }
    const variant = variantOf();
    const times = courseTimes();
    const course = courseIndex === null ? null : variant.trips[courseIndex];

    const directionButton = v => {
        const i = data.variants.indexOf(v);
        return `<button type="button" class="tt-dir${i === variantIndex ? ' active' : ''}"
                        data-variant="${i}" aria-pressed="${i === variantIndex}">
                    <span class="tt-dir-to">${esc(v.headsign)}</span>
                    <span class="tt-dir-sub">z ${esc(v.from)}</span>
                </button>`;
    };
    const side = sideVariants();

    const courses = variant.trips.map((trip, i) => `
        <button type="button" class="tt-course${i === courseIndex ? ' active' : ''}"
                data-course="${i}" aria-pressed="${i === courseIndex}">${esc(trip.dep)}</button>`
    ).join('');

    const stops = variant.stops.map((stop, i) => `
        <li class="tt-stop" data-stop="${i}">
            <span class="tt-t">${times ? esc(times[i]) : ''}</span>
            <span class="tt-dot"></span>
            <span class="tt-name">${esc(stop.name)}</span>
        </li>`).join('');

    resultsBox.innerHTML = `
        <div class="tt-title">
            <span class="badge ${esc(data.mode)}">${esc(data.num)}</span>
            <span class="tt-title-main">${esc(MODE_LABEL[data.mode] || 'Linia')}
                ${esc(data.num)}</span>
            <span class="tt-title-sub">${esc(dayLabel())}</span>
        </div>

        <div class="card tt-card">
            <div class="tt-dirs">${mainVariants().map(directionButton).join('')}</div>
            ${side.length ? `
                <details class="tt-side"${sideOpen ? ' open' : ''}>
                    <summary>${side.length}
                        ${plural(side.length, 'kurs skrócony', 'kursy skrócone', 'kursów skróconych')}
                        — do zajezdni i z pętli po drodze</summary>
                    <div class="tt-dirs">${side.map(directionButton).join('')}</div>
                </details>` : ''}
        </div>

        <div class="card tt-card">
            <div class="tt-card-head">
                <h3 class="tt-h3">Odjazdy z „${esc(variant.from)}"</h3>
                <span class="tt-count">${variant.trips.length}</span>
            </div>
            <div class="tt-chips tt-courses">${courses}</div>
        </div>

        <div class="card tt-card">
            <div class="tt-card-head">
                <h3 class="tt-h3">${course
                    ? `Kurs ${esc(course.dep)} → ${esc(variant.headsign)}`
                    : 'Przystanki'}</h3>
                <span class="tt-count">${variant.stops.length}</span>
            </div>
            <ol class="tt-stops ${esc(data.mode)}">${stops}</ol>
        </div>`;

    scrollCoursesToActive();
}

/** Pasek godzin jest długi (doba to i sto kursów), a interesująca jest ta
    jedna wybrana - więc pasek ustawia się na niej, zamiast zaczynać od 4 rano.
    Tylko po zmianie rozkładu albo kierunku: przy klikaniu godzin pasek ma
    stać w miejscu, w którym użytkownik go zostawił. */
function scrollCoursesToActive() {
    if (!recenter) return;
    recenter = false;
    const strip = resultsBox.querySelector('.tt-courses');
    const chip = strip && strip.querySelector('.tt-course.active');
    if (!chip) return;
    strip.scrollTop = Math.max(
        0, chip.offsetTop - strip.clientHeight / 2 + chip.offsetHeight / 2);
}

// ------------------------------------------------ tablica przystanku ----

const lineKey = line => `${line.mode}|${line.num}`;

const defaultFromSec = payload =>
    (payload.date === isoToday() ? nowSec() : null);

/** Linie tablicy pogrupowane po numerze - kierunki tej samej linii są jednym
    przełącznikiem. Zaznaczanie ma odpowiadać na "chcę widzieć 8, 9 i 17",
    a nie kazać odhaczać osobno każdego headsigna. */
function lineGroups() {
    const groups = new Map();
    data.lines.forEach((line, i) => {
        const key = lineKey(line);
        if (!groups.has(key)) {
            groups.set(key, {key, num: line.num, mode: line.mode, count: 0, indexes: []});
        }
        const group = groups.get(key);
        group.count += line.count;
        group.indexes.push(i);
    });
    return [...groups.values()];
}

const isPicked = line => picked.has(lineKey(line));

/** Indeksy odjazdów do pokazania - indeksy, nie obiekty, bo rozwinięty kurs
    jest zapamiętany pozycją w PEŁNEJ liście (ta się nie zmienia przy filtrach). */
function visibleDepartures() {
    const out = [];
    data.departures.forEach((dep, i) => {
        if (isPicked(data.lines[dep.line]) && (fromSec === null || dep.sec >= fromSec)) {
            out.push(i);
        }
    });
    return out;
}

// Trasy tablicy dociągają się jedna po drugiej, a zaznaczenie linii można
// zmienić w trakcie - numer rysowania odsiewa odpowiedzi na poprzedni wybór.
let boardDraw = 0;

function drawBoard(refit) {
    const mine = ++boardDraw;
    focusLayer = dropLayer(focusLayer);
    const stopDot = L.circleMarker(data.center, {
        radius: 9, weight: 3, color: '#263238',
        fillColor: '#ffd54f', fillOpacity: 1,
    }).bindTooltip(data.stop);

    // Trasy zaznaczonych linii - po jednej na kierunek, liczone z kursu
    // najbliższego wybranej godzinie: to on odpowiada na pytanie "dokąd
    // pojedzie to, co za chwilę tu podjedzie".
    const groups = lineGroups().filter(group => picked.has(group.key));
    const chosen = [];
    for (const group of groups) {
        const main = [...group.indexes]
            .sort((a, b) => data.lines[b].count - data.lines[a].count)
            .slice(0, MAX_DIRS_PER_LINE);
        for (const i of main) chosen.push({line: data.lines[i], i});
    }

    showRoutes([], [stopDot], false, null);

    if (!chosen.length || groups.length > MAX_ROUTES_ON_MAP) {
        // Bez tras na mapie zostaje sam przystanek - a wtedy reszta słupków
        // jest tym, po czym się do niego trafia, więc ma być widoczna.
        dimBase(openDep !== null);
        if (refit) map.setView(data.center, BOARD_ZOOM);
        if (openDep !== null) drawOpenDeparture();
        return;
    }
    dimBase(true);

    // Każda trasa dokłada się do mapy sama, gdy tylko dojdzie - kadrujemy
    // dopiero po wszystkich, żeby widok nie skakał przy każdej odpowiedzi.
    const points = [data.center];
    Promise.all(chosen.map(({line, i}) => {
        const dep = representative(i);
        if (!dep) return null;
        return fetchTrip(dep).then(trip => {
            if (!trip || mine !== boardDraw || !active() || kind !== 'stop') return;
            addRoute(trip.tail, line.mode);
            points.push(...trip.tail);
        });
    })).then(() => {
        if (mine === boardDraw && refit && openDep === null) fitTo(points);
    });
    if (openDep !== null) drawOpenDeparture();
}

function representative(lineIndex) {
    const list = data.departures.filter(dep => dep.line === lineIndex);
    if (!list.length) return null;
    if (fromSec === null) return list[0];
    return list.find(dep => dep.sec >= fromSec) || list[list.length - 1];
}

const tripKey = dep => `${currentDate()}|${dep.trip}|${dep.stop}|${dep.sec}`;

function fetchTrip(dep) {
    const key = tripKey(dep);
    if (tripCache.has(key)) return Promise.resolve(tripCache.get(key));
    const params = new URLSearchParams({
        trip: dep.trip, stop: dep.stop, dep: dep.sec, date: currentDate(),
    });
    return fetch('/api/trip?' + params).then(r => r.json()).then(trip => {
        if (trip.error) return null;
        tripCache.set(key, trip);
        return trip;
    }).catch(() => null);
}

function drawOpenDeparture() {
    const dep = data.departures[openDep];
    if (!dep) return;
    dimBase(true);          // rysujemy konkretny kurs - reszta słupków to tło
    fetchTrip(dep).then(trip => {
        if (!trip || !active() || kind !== 'stop' || openDep === null) return;
        focusLayer = dropLayer(focusLayer);
        const onward = trip.stops.slice(trip.board_index);
        focusLayer = L.layerGroup([
            ...routeLines(trip.path, trip.mode, 'ghost'),
            ...routeLines(trip.tail, trip.mode, 'full'),
            ...stopDots(onward, trip.mode, onward.map(s => s.t)),
        ]).addTo(map);
        fitTo(trip.tail);
    });
}

function renderBoard() {
    const groups = lineGroups();
    const shown = visibleDepartures();
    const page = shown.slice(0, boardLimit);
    const all = picked.size === groups.length;

    const chips = groups.map(group => `
        <button type="button" class="tt-line badge ${esc(group.mode)}${picked.has(group.key) ? '' : ' off'}"
                data-line="${esc(group.key)}" aria-pressed="${picked.has(group.key)}"
                title="${esc(MODE_LABEL[group.mode] || 'Linia')} ${esc(group.num)} · ${group.count} ${plural(group.count, 'kurs', 'kursy', 'kursów')}"
                >${esc(group.num)}</button>`
    ).join('');

    const rows = page.map(index => {
        const dep = data.departures[index];
        const line = data.lines[dep.line];
        const open = index === openDep;
        return `
        <li class="tt-dep${open ? ' open' : ''}" data-dep="${index}" tabindex="0"
            role="button" aria-expanded="${open}">
            <span class="tt-t">${esc(dep.t)}</span>
            <span class="badge ${esc(line.mode)}">${esc(line.num)}</span>
            <span class="tt-head">${esc(line.headsign)}</span>
            ${dep.platform ? `<span class="tt-platform">${esc(dep.platform)}</span>` : ''}
        </li>${open ? tripHtml(dep) : ''}`;
    }).join('');

    const mapNote = picked.size === 0
        ? 'Nic nie zaznaczone — tablica jest pusta.'
        : groups.length > MAX_ROUTES_ON_MAP && picked.size > MAX_ROUTES_ON_MAP
            ? `Zostaw najwyżej ${MAX_ROUTES_ON_MAP} linii, a ich trasy pokażą się na mapie.`
            : 'Trasy zaznaczonych linii — stąd dalej — widać na mapie.';

    resultsBox.innerHTML = `
        <div class="tt-title">
            <span class="ac-pin big" aria-hidden="true"></span>
            <span class="tt-title-main">${esc(data.stop)}</span>
            <span class="tt-title-sub">${esc(dayLabel())}</span>
        </div>

        <div class="card tt-card">
            <div class="tt-card-head">
                <h3 class="tt-h3">Linie</h3>
                <button type="button" class="tt-mini" data-pick="${all ? 'none' : 'all'}">
                    ${all ? 'odznacz wszystkie' : 'zaznacz wszystkie'}
                </button>
            </div>
            <div class="tt-chips">${chips}</div>
            <p class="field-hint">${esc(mapNote)}</p>
        </div>

        <div class="card tt-card">
            <div class="tt-card-head">
                <h3 class="tt-h3">Odjazdy</h3>
                <span class="tt-count">${shown.length}</span>
                <div class="tt-switch">
                    <button type="button" class="tt-mini${fromSec === null ? '' : ' active'}"
                            data-from="now">od teraz</button>
                    <button type="button" class="tt-mini${fromSec === null ? ' active' : ''}"
                            data-from="day">cała doba</button>
                </div>
            </div>
            <ol class="tt-board">${rows}</ol>
            ${shown.length > page.length ? `
                <button type="button" class="button tt-more" data-more="1">
                    Pokaż kolejne ${Math.min(BOARD_PAGE, shown.length - page.length)}
                    z ${shown.length - page.length}
                </button>` : ''}
            ${shown.length ? '' : '<p class="field-hint">Nic tu nie odjeżdża w tym oknie.</p>'}
        </div>`;
}

function tripHtml(dep) {
    const trip = tripCache.get(tripKey(dep));
    if (!trip) return '<li class="tt-trip"><span class="field-hint">Czytam kurs…</span></li>';
    const stops = trip.stops.slice(trip.board_index).map((stop, i) => `
        <li class="tt-stop${i === 0 ? ' first' : ''}">
            <span class="tt-t">${esc(stop.t)}</span>
            <span class="tt-dot"></span>
            <span class="tt-name">${esc(stop.name)}</span>
        </li>`).join('');
    return `<li class="tt-trip">
        <ol class="tt-stops ${esc(trip.mode)}">${stops}</ol>
        <p class="field-hint">Kliknij godzinę ponownie, żeby zwinąć ten kurs.</p>
    </li>`;
}

// --------------------------------------------------------- zdarzenia ----

resultsBox.addEventListener('click', event => {
    const suggestion = event.target.closest('[data-suggest]');
    if (suggestion) {
        event.preventDefault();
        queryInput.value = suggestion.dataset.suggest;
        load();
        return;
    }

    const variant = event.target.closest('[data-variant]');
    if (variant) {
        const index = Number(variant.dataset.variant);
        sideOpen = !mainVariants().includes(data.variants[index]);
        variantIndex = index;
        // Kierunek zmienia zestaw kursów - trzymanie numeru poprzedniego
        // pokazałoby przypadkową godzinę, więc wracamy do najbliższej.
        courseIndex = nextCourseIndex(variantOf());
        recenter = true;
        render();
        draw(true);
        return;
    }

    const course = event.target.closest('[data-course]');
    if (course) {
        const index = Number(course.dataset.course);
        courseIndex = courseIndex === index ? null : index;
        render();
        draw(false);
        return;
    }

    const line = event.target.closest('[data-line]');
    if (line) {
        const key = line.dataset.line;
        if (picked.has(key)) picked.delete(key);
        else picked.add(key);
        boardLimit = BOARD_PAGE;
        render();
        draw(true);
        return;
    }

    const pick = event.target.closest('[data-pick]');
    if (pick) {
        picked = pick.dataset.pick === 'all'
            ? new Set(data.lines.map(lineKey)) : new Set();
        boardLimit = BOARD_PAGE;
        render();
        draw(true);
        return;
    }

    const from = event.target.closest('[data-from]');
    if (from) {
        fromSec = from.dataset.from === 'day' ? null : (referenceSec() ?? 0);
        boardLimit = BOARD_PAGE;
        render();
        draw(false);
        return;
    }

    if (event.target.closest('[data-more]')) {
        boardLimit += BOARD_PAGE;
        render();
        return;
    }

    const dep = event.target.closest('[data-dep]');
    if (dep) {
        const index = Number(dep.dataset.dep);
        openDep = openDep === index ? null : index;
        render();
        if (openDep === null) {
            focusLayer = dropLayer(focusLayer);
            draw(false);
        } else {
            // Kurs bywa jeszcze w drodze - rysujemy i przerysowujemy listę,
            // gdy odpowiedź dojdzie (tripHtml czyta z tego samego cache).
            drawOpenDeparture();
            fetchTrip(data.departures[index]).then(() => {
                if (openDep === index) render();
            });
        }
        return;
    }

    const stop = event.target.closest('[data-stop]');
    if (stop) {
        const variantNow = variantOf();
        const point = variantNow && variantNow.stops[Number(stop.dataset.stop)];
        if (point) map.setView([point.lat, point.lon], Math.max(map.getZoom(), 15));
    }
});

resultsBox.addEventListener('keydown', event => {
    const row = event.target.closest('.tt-dep');
    if (row && (event.key === 'Enter' || event.key === ' ')) {
        event.preventDefault();
        row.click();
    }
});

})();
