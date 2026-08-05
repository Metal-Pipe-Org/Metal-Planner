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

const sidebar = $('sidebar');
$('sidebar-toggle').addEventListener('click', () => {
    document.body.classList.toggle('panel-hidden');
});

// Panel deweloperski jest schowany za przyciskiem - normalny użytkownik
// nie ma po co go widzieć, a strojenie algorytmu musi zostać pod ręką.
const devPanel = $('dev-panel');
const devToggle = $('dev-toggle');
if (devPanel) {
    const setDev = open => {
        devPanel.classList.toggle('hidden', !open);
        devToggle.classList.toggle('active', open);
        devToggle.setAttribute('aria-expanded', String(open));
    };
    devToggle.addEventListener('click', () => setDev(devPanel.classList.contains('hidden')));
    $('dev-close').addEventListener('click', () => setDev(false));
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

map.on('click', e => pickEndpoint({lat: e.latlng.lat, lon: e.latlng.lng}));

// ---------------------------------------------------- kadrowanie widoku ----

function fitTo(points) {
    if (!points.length) return;
    const wide = window.matchMedia('(min-width: 761px)').matches;
    const panelVisible = !document.body.classList.contains('panel-hidden');
    const gutter = 40;
    const left = wide && panelVisible ? sidebar.offsetWidth + gutter : gutter;
    const bottom = !wide && panelVisible
        ? Math.min(sidebar.offsetHeight + gutter, window.innerHeight * 0.6)
        : gutter;
    map.fitBounds(L.latLngBounds(points), {
        paddingTopLeft: [left, gutter],
        paddingBottomRight: [gutter, bottom],
        maxZoom: 16,     // krótka trasa nie ma wjeżdżać w widok pojedynczej ulicy
    });
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
// wyjścia. Czytelność przy nachodzeniu: jaskrawe segmenty mają białą otoczkę
// (styl mapy tramwajowej), a najechanie na linię podświetla ją i pokazuje
// numer - widać, "która co gdzie", nawet w wiązce.
const BRIGHT_W = 0.45;

let flowLayer = null;
let flowParts = [];       // {layer, opacity, weight} - do przygaszania pod wybraną trasą

function clearFlow() {
    if (flowLayer) { map.removeLayer(flowLayer); flowLayer = null; }
    flowParts = [];
    setBaseDim(false);
}

function relBrightness(w) {
    const qMinFrac = Number($('qmin').value) / 100;
    return qMinFrac < 1
        ? Math.max(0, Math.min(1, (w - qMinFrac) / (1 - qMinFrac)))
        : 1;
}

function drawFlow(flow, refit) {
    if (flowLayer) map.removeLayer(flowLayer);   // bez clearFlow: przemalowanie
    flowParts = [];                              // wszystkich słupków tam i z powrotem
    setBaseDim(true);                            // to przy każdym ruchu suwaka za dużo
    const faint = [], casings = [], bright = [], badges = [];
    const byLine = new Map();  // "num|kind" -> [segmenty] do plakietek

    for (const s of flow.segments) {        // posortowane po w rosnąco
        const key = s.num + '|' + s.kind;
        const rel = relBrightness(s.w);
        const color = LINE_COLORS[s.kind] || LINE_COLORS.other;
        const weight = 1 + 3.5 * rel;
        const opacity = 0.10 + 0.85 * rel;
        const line = L.polyline(s.path, {color, opacity, weight});
        // Podpowiedź liczona przy każdym najechaniu, bo lista propozycji
        // przychodzi osobnym zapytaniem - często dopiero po narysowaniu mapy.
        line.bindTooltip(() => {
            const label = `${MODE_LABEL[s.kind] || MODE_LABEL.other} ${s.num}`;
            return journeyForLine(s.num, s.kind) === null
                ? label : `${label} · kliknij, aby otworzyć trasę`;
        }, {sticky: true});
        line.on('mouseover', () => {
            line.setStyle({opacity: 1, weight: weight + 2.5});
            line.bringToFront();       // spod wiązki na wierzch
        });
        line.on('mouseout', () => line.setStyle(currentFlowStyle(line)));
        line.on('click', event => {
            const index = journeyForLine(s.num, s.kind, event.latlng);
            if (index === null) return;   // linia bez propozycji: klik ustawia punkt
            L.DomEvent.stop(event);       // ...a z propozycją - otwiera ją
            openJourney(index, true);
        });
        flowParts.push({layer: line, opacity, weight});
        if (rel >= BRIGHT_W) {
            const casing = L.polyline(s.path, {
                color: '#fff', opacity: 0.9, weight: weight + 2.5, interactive: false,
            });
            flowParts.push({layer: casing, opacity: 0.9, weight: weight + 2.5});
            casings.push(casing);
            bright.push(line);
        } else {
            faint.push(line);
        }
        if (!byLine.has(key)) byLine.set(key, []);
        byLine.get(key).push({...s, rel});
    }

    // Plakietki z numerem linii - tylko dla linii, które mają sensowny udział
    // w przepływie; na najjaśniejszym segmencie linii, dłuższe segmenty
    // dostają dodatkowe plakietki w 1/4 i 3/4 trasy.
    for (const [key, segs] of byLine) {
        const best = segs[segs.length - 1];
        if (best.rel < 0.4) continue;
        const [num, kind] = key.split('|');
        const positions = new Set([Math.floor(best.path.length / 2)]);
        if (best.path.length >= 20) {
            positions.add(Math.floor(best.path.length / 4));
            positions.add(Math.floor(3 * best.path.length / 4));
        }
        for (const p of positions) {
            badges.push(L.marker(best.path[p], {
                icon: L.divIcon({
                    className: `line-badge ${kind}`, html: esc(num), iconSize: null,
                }),
                interactive: false,
                opacity: 0.45 + 0.55 * best.rel,
            }));
        }
    }

    // Kolejność: blade tło -> białe otoczki -> jaskrawe korytarze -> plakietki.
    flowLayer = L.layerGroup([...faint, ...casings, ...bright, ...badges]).addTo(map);
    if (selectedJourney !== null) dimFlow(true);
    if (!refit) return;

    // Kadr: najciaśniejszy sensowny próg jasności, żeby nie skakać do widoku
    // całego województwa przez jedną bladą nitkę...
    let points = [];
    for (const threshold of [0.7, BRIGHT_W, 0]) {
        points = flow.segments.filter(s => relBrightness(s.w) >= threshold)
                              .flatMap(s => s.path);
        if (points.length >= 4) break;
    }
    fitTo([...points, ...endpointPoints()]);   // start i cel zawsze w kadrze
}

let flowDimmed = false;
const DIM_FACTOR = 0.22;

function currentFlowStyle(layer) {
    const part = flowParts.find(p => p.layer === layer);
    if (!part) return {};
    return {
        opacity: flowDimmed ? part.opacity * DIM_FACTOR : part.opacity,
        weight: part.weight,
    };
}

/** Wybrana trasa musi być czytelna, więc reszta wachlarza schodzi w tło. */
function dimFlow(dim) {
    flowDimmed = dim;
    for (const part of flowParts) {
        part.layer.setStyle({opacity: dim ? part.opacity * DIM_FACTOR : part.opacity});
    }
    if (flowLayer) {
        flowLayer.eachLayer(l => {
            if (l.setOpacity) l.setOpacity(dim ? 0.2 : 1);   // plakietki linii
        });
    }
}

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
        </div>
        <ol class="journeys">${cards}</ol>
        <p class="results-foot">
            Na mapie widać wszystkie sensowne dojazdy — im jaśniejsza linia,
            tym lepsza opcja. Kliknij propozycję albo linię na mapie, żeby
            zobaczyć całą trasę.
        </p>`;

    scrollToSelected();
}

function scrollToSelected() {
    const card = resultsBox.querySelector('.journey.selected');
    if (card) card.scrollIntoView({block: 'start', behavior: 'smooth'});
}

resultsBox.addEventListener('click', event => {
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
    if (!isPoint(sel.start)) { sel.start = data.start; startInput.value = data.start; }
    if (!isPoint(sel.end)) { sel.end = data.end; endInput.value = data.end; }
}

function loadFlow(token, refit) {
    const params = queryParams();
    params.set('qmin', (Number($('qmin').value) / 100).toFixed(2));
    params.set('tol', $('tol').value);
    return Promise.all([fetch('/api/flow?' + params).then(r => r.json()), stopsReady])
        .then(([flow]) => {
            if (token !== requestToken) return;
            if (flow.error) { clearFlow(); showError(flow.error, flow.suggestions); return; }
            adoptNames(flow);
            restyle(sel.start, sel.end);
            drawFlow(flow, refit);
        });
}

function loadJourneys(token) {
    return fetch('/api/journeys?' + queryParams())
        .then(r => r.json())
        .then(data => {
            if (token !== requestToken) return;
            if (data.error) { showError(data.error, data.suggestions); return; }
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

function search() {
    if (!startInput.value || !endInput.value) return;
    const token = ++requestToken;
    clearJourney();
    clearPreview();
    resultsBox.innerHTML = '<div class="notice">Szukam połączeń…</div>';
    Promise.all([loadFlow(token, true), loadJourneys(token)])
        .catch(() => showError('Nie udało się połączyć z serwerem.'));
}

$('search').addEventListener('click', search);

for (const input of [startInput, endInput]) {
    input.addEventListener('keydown', e => { if (e.key === 'Enter') search(); });
}

// Ręczne wpisanie w pole tekstowe wychodzi z trybu "wybrany punkt" - dalej
// liczy się to, co user wpisał, jak przy zwykłym wyszukiwaniu.
startInput.addEventListener('input', () => {
    if (isPoint(sel.start)) { sel.start = null; updatePointMarker('start', null); }
});
endInput.addEventListener('input', () => {
    if (isPoint(sel.end)) { sel.end = null; updatePointMarker('end', null); }
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
});

// Suwaki panelu deweloperskiego: etykieta od razu, mapa po krótkim debounce
// (odpowiedź z ciepłym cache to ~10 ms, więc działa "na żywo"). Lista
// propozycji od tych parametrów nie zależy - przeładowujemy tylko przepływ.
function liveSlider(inputId, valueId, affectsList) {
    const input = $(inputId);
    const valueEl = $(valueId);
    let timer = null;
    input.addEventListener('input', () => {
        valueEl.textContent = input.value;
        clearTimeout(timer);
        timer = setTimeout(() => {
            if (!startInput.value || !endInput.value) return;
            loadFlow(requestToken, false);
            if (affectsList) loadJourneys(requestToken);
        }, 200);
    });
}
liveSlider('qmin', 'qmin-value');
liveSlider('tol', 'tol-value');
liveSlider('range', 'range-value', true);   // zasięg zmienia też same trasy

}
