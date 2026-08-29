/* Rozkłady jazdy - drugi tryb panelu, obok wyszukiwarki połączeń.

   Odpowiada na dwa pytania, na które wyszukiwarka nie odpowiada wcale:

   - LINIA ("co robi 17-tka") - warianty trasy (kierunki i kursy skrócone),
     lista przystanków, godziny wybranego kursu i cały przebieg na mapie;
   - PRZYSTANEK ("co odjeżdża z Katedry") - tablica odjazdów wszystkich linii,
     z zaznaczaniem, KTÓRE z nich mają być w tej tablicy złączone; trasy
     zaznaczonych linii - od tego przystanku dalej - rysują się na mapie.

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
const resultsBox = $('tt-results');
const hintBox = $('tt-hint');
if (!queryInput || !resultsBox) return;

// Numery linii do podpowiedzi - wstawione w stronę razem z nazwami
// przystanków, więc pole działa od pierwszego wpisanego znaku, bez zapytania.
const LINES = JSON.parse($('line-names').textContent);
const LINE_NUMS = LINES.map(line => line.num);
const MODE_OF_NUM = new Map(LINES.map(line => [line.num, line.mode]));

// Ile tras naraz ma sens na mapie. Powyżej tego progu z węzła przesiadkowego
// robi się kłębek, w którym nie widać już żadnej pojedynczej linii - wtedy
// tablica zostaje, a mapa czeka na zawężenie wyboru.
const MAX_ROUTES_ON_MAP = 8;
// Ile kierunków jednej linii rysujemy. Linia ma dwa podstawowe, a poza nimi
// garść kursów skróconych - te ostatnie dokładają nitki, które i tak leżą na
// trasie podstawowej, więc na mapie nic nie wnoszą.
const MAX_DIRS_PER_LINE = 2;
// Ile odjazdów pokazujemy od razu. Doba na dużym węźle to ~2000 wierszy.
const BOARD_PAGE = 60;
// Przybliżenie tablicy bez tras: sam przystanek to punkt, a punkt kadruje się
// na maksymalne zbliżenie - z którego nie widać nawet, w którym jest mieście.
const BOARD_ZOOM = 15;

// ------------------------------------------------------------- stan ----

let kind = 'line';        // 'line' albo 'stop'
let data = null;          // ostatnia odpowiedź serwera
let token = 0;            // odsiewa odpowiedzi na nieaktualne zapytania

let variantIndex = 0;     // wybrany wariant trasy linii
let courseIndex = null;   // wybrany kurs tej linii (null = sam przebieg)

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

/** Wszystkie słupki miasta na raz to tło, na którym nie widać narysowanej
    trasy - dokładnie ten sam problem, co przy mapie przepływów, więc i to samo
    rozwiązanie. Przygaszamy je dopiero, gdy jest co przygaszać. */
function dimBase(dim) {
    B.setBaseDim(dim);
}

// ------------------------------------------------------ przełącznik ----

const modeButtons = [$('mode-toggle'), $('mode-toggle-m')].filter(Boolean);
const tabLabel = $('tab-list-label');

function active() {
    return document.body.classList.contains('mode-timetable');
}

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
        setKind('stop');
        queryInput.value = name;
        load();
        return true;
    },
};

// ------------------------------------------------------ pole i tryby ----

function setKind(next) {
    if (kind === next) return;
    kind = next;
    for (const tab of document.querySelectorAll('.tt-tab')) {
        const on = tab.dataset.tt === kind;
        tab.classList.toggle('active', on);
        tab.setAttribute('aria-pressed', String(on));
    }
    queryInput.placeholder = kind === 'line'
        ? 'Numer linii, np. 17' : 'Przystanek, np. Katedra';
    hintBox.textContent = kind === 'line'
        ? 'Wpisz numer linii — zobaczysz jej kursy i przebieg na mapie.'
        : 'Wpisz przystanek albo kliknij słupek na mapie — dostaniesz tablicę '
          + 'odjazdów wszystkich linii, które z niego jadą.';
    reset();
}

for (const tab of document.querySelectorAll('.tt-tab')) {
    tab.addEventListener('click', () => {
        setKind(tab.dataset.tt);
        queryInput.value = '';
        queryInput.focus();
    });
}

// Podpowiedzi: numery linii albo nazwy przystanków, zależnie od trybu. Dwa
// osobne wpięcia w to samo pole - każde ze swoją listą - bo źródło zmienia
// się razem z trybem, a nie w trakcie pisania.
B.attachAutocomplete(queryInput, load, {
    names: () => (kind === 'line' ? LINE_NUMS : B.STOP_NAMES),
    onEnter: load,
});

function currentDate() {
    return dateInput && dateInput.value ? dateInput.value : '';
}

// ------------------------------------------------------ zapytania ----

function setBusy(on) {
    document.body.classList.toggle('tt-searching', on);
    $('tt-search').disabled = on;
    resultsBox.setAttribute('aria-busy', String(on));
}

function reset() {
    ++token;
    setBusy(false);
    data = null;
    variantIndex = 0;
    courseIndex = null;
    picked = new Set();
    fromSec = null;
    boardLimit = BOARD_PAGE;
    openDep = null;
    tripCache = new Map();
    resultsBox.innerHTML = '';
    clearMap();
    dimBase(false);
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

function load() {
    const query = queryInput.value.trim();
    if (!query) return;
    const mine = ++token;
    clearMap();
    setBusy(true);
    resultsBox.innerHTML =
        '<div class="notice loading"><span class="spinner" aria-hidden="true"></span>'
        + 'Czytam rozkład…</div>';

    const params = new URLSearchParams({date: currentDate()});
    let url;
    if (kind === 'line') {
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
        if (payload.error) { data = null; clearMap(); fail(payload.error, payload.suggestions); return; }
        data = payload;
        variantIndex = 0;
        courseIndex = null;
        openDep = null;
        boardLimit = BOARD_PAGE;
        if (kind === 'stop') {
            picked = new Set(payload.lines.map(lineKey));
            fromSec = defaultFromSec(payload);
        }
        render();
        draw(true);
        B.setView('list');
    }).catch(() => {
        if (mine === token) fail('Nie udało się połączyć z serwerem.');
    }).finally(() => {
        if (mine === token) setBusy(false);
    });
}

$('tt-search').addEventListener('click', load);
if (dateInput) dateInput.addEventListener('change', () => { if (data) load(); });
$('tt-clear').addEventListener('click', () => {
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
    if (!data) return;
    if (kind === 'line') renderLine();
    else renderBoard();
}

// ------------------------------------------------- rozkład jednej linii ----

const variantOf = () => (data && data.variants ? data.variants[variantIndex] : null);

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
    const times = courseTimes();
    dimBase(true);
    showRoutes(
        routeLines(variant.path, data.mode),
        [...stopDots(variant.stops, data.mode, times),
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

    const variants = data.variants.map((v, i) => `
        <button type="button" class="tt-variant${i === variantIndex ? ' active' : ''}"
                data-variant="${i}" aria-pressed="${i === variantIndex}">
            <span class="tt-dir">→ ${esc(v.headsign)}</span>
            <span class="tt-sub">z ${esc(v.from)} · ${v.trips.length}
                ${plural(v.trips.length, 'kurs', 'kursy', 'kursów')}</span>
        </button>`).join('');

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
        <div class="results-head">
            <h2>${esc(data.label)}</h2>
            <span class="results-count">${data.variants.length}</span>
        </div>
        <div class="card tt-card">
            <div class="tt-variants">${variants}</div>
        </div>
        <div class="card tt-card">
            <h3 class="tt-h3">Odjazdy z „${esc(variant.from)}"</h3>
            <div class="tt-chips">${courses}</div>
            <p class="field-hint">
                ${courseIndex === null
                    ? 'Wybierz godzinę odjazdu, żeby zobaczyć czas na każdym przystanku.'
                    : 'Kliknij tę samą godzinę ponownie, żeby wrócić do samej trasy.'}
            </p>
        </div>
        <div class="card tt-card">
            <h3 class="tt-h3">Przystanki <span class="tt-sub">${variant.stops.length}</span></h3>
            <ol class="tt-stops ${esc(data.mode)}">${stops}</ol>
        </div>`;
}

// ------------------------------------------------ tablica przystanku ----

const lineKey = line => `${line.mode}|${line.num}`;

/** Domyślnie tablica zaczyna się "teraz" - ale tylko dla dnia dzisiejszego.
    Rozkład na inny dzień pokazujemy od początku doby, bo bieżąca godzina nic
    o nim nie mówi. */
function defaultFromSec(payload) {
    const today = new Date();
    const iso = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}`
              + `-${String(today.getDate()).padStart(2, '0')}`;
    if (payload.date !== iso) return null;
    return today.getHours() * 3600 + today.getMinutes() * 60;
}

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

/** Trasa dociągnięta asynchronicznie - dokładamy ją do warstwy, która już
    stoi na mapie, zamiast przerysowywać całość: odpowiedzi wracają jedna po
    drugiej i każde przerysowanie mrugałoby resztą. */
function addRoute(path, mode) {
    if (!routeLayer) routeLayer = L.layerGroup().addTo(map);
    for (const layer of routeLines(path, mode, 'soft')) routeLayer.addLayer(layer);
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
        const layers = [
            ...routeLines(trip.path, trip.mode, 'ghost'),
            ...routeLines(trip.tail, trip.mode, 'full'),
            ...stopDots(trip.stops.slice(trip.board_index), trip.mode,
                        trip.stops.slice(trip.board_index).map(s => s.t)),
        ];
        focusLayer = L.layerGroup(layers).addTo(map);
        fitTo(trip.tail);
    });
}

function renderBoard() {
    const groups = lineGroups();
    const shown = visibleDepartures();
    const page = shown.slice(0, boardLimit);

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
            <span class="tt-head">→ ${esc(line.headsign)}</span>
            ${dep.platform ? `<span class="tt-platform">${esc(dep.platform)}</span>` : ''}
        </li>${open ? tripHtml(dep) : ''}`;
    }).join('');

    const picks = picked.size;
    const mapNote = picks === 0
        ? 'Nie zaznaczono żadnej linii — tablica jest pusta.'
        : picks > MAX_ROUTES_ON_MAP
            ? `Zaznaczonych kierunków jest ${picks} — na mapie rysujemy trasy `
              + `dopiero od ${MAX_ROUTES_ON_MAP} w dół, inaczej nie widać z nich żadnej.`
            : 'Trasy zaznaczonych linii — od tego przystanku dalej — widać na mapie.';

    resultsBox.innerHTML = `
        <div class="results-head">
            <h2>${esc(data.stop)}</h2>
            <span class="results-count">${shown.length}</span>
        </div>
        <div class="card tt-card">
            <div class="tt-filter-head">
                <h3 class="tt-h3">Linie w tej tablicy</h3>
                <button type="button" class="tt-mini" data-pick="all">wszystkie</button>
                <button type="button" class="tt-mini" data-pick="none">żadna</button>
            </div>
            <div class="tt-chips">${chips}</div>
            <p class="field-hint">
                Odhaczone linie znikają z tablicy — zostaje rozkład złożony
                dokładnie z tych, które zaznaczysz. ${esc(mapNote)}
            </p>
        </div>
        <div class="card tt-card">
            <div class="tt-filter-head">
                <h3 class="tt-h3">Odjazdy</h3>
                <button type="button" class="tt-mini${fromSec === null ? ' active' : ''}"
                        data-from="day">cała doba</button>
                <button type="button" class="tt-mini${fromSec === null ? '' : ' active'}"
                        data-from="now">od teraz</button>
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
        <p class="field-hint">Kliknij ponownie, żeby zwinąć ten kurs.</p>
    </li>`;
}

// --------------------------------------------------------- zdarzenia ----

function plural(n, one, few, many) {
    if (n === 1) return one;
    const rest = n % 10, hundreds = n % 100;
    return rest >= 2 && rest <= 4 && (hundreds < 12 || hundreds > 14) ? few : many;
}

resultsBox.addEventListener('click', event => {
    const suggest = event.target.closest('[data-suggest]');
    if (suggest) {
        event.preventDefault();
        queryInput.value = suggest.dataset.suggest;
        load();
        return;
    }

    const variant = event.target.closest('[data-variant]');
    if (variant) {
        variantIndex = Number(variant.dataset.variant);
        courseIndex = null;
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
        fromSec = from.dataset.from === 'day' ? null : defaultFromSec(data) || 0;
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
        if (openDep === null) {
            focusLayer = dropLayer(focusLayer);
            render();
            draw(false);
        } else {
            // Kurs bywa jeszcze w drodze - rysujemy i przerysowujemy listę,
            // gdy odpowiedź dojdzie (tripHtml czyta z tego samego cache).
            render();
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
