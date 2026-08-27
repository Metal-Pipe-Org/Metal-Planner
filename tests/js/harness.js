/* Emulator frontu mapy przepływów: tyle Leafletu, DOM-u i przeglądarki, ile
   static/app.js potrzebuje, żeby dać się URUCHOMIĆ bez okna.

   Po co: połowa kontraktu mapy (docs/FLOW_MAP_CONTRACT.md) mieszka we
   froncie - punkt 7 (grupki numerów, wskazywanie linii), punkt 8 (progi
   jasności) i punkt 10 (godziny na mapie) nie miały jak być testowane, bo
   testy w tests/ sięgają wyłącznie planner.py. Ten plik to brakująca
   podstawka: prawdziwy app.js na atrapach, karmiony prawdziwą odpowiedzią
   /api/flow (tests/js/flow_fixture.json).

   Świadome ograniczenia atrapy:
   - rzut latlng -> piksele to Web Mercator dokładnie jak w Leaflecie, więc
     kolizje grupek i trafienia kursora liczą się w tych samych jednostkach,
     co na ekranie; nie ma za to animacji, kafelków ani prawdziwego układu
     CSS - rozmiar grupki bierze się z clusterBox w app.js, nie z pomiaru
     tekstu przez przeglądarkę;
   - zdarzenia myszy wywołujemy wprost (pickFromCluster/handleFlowHover),
     nie przez propagację DOM;
   - fetch nigdy nic nie zwraca (thenable, które nie woła callbacków), więc
     nic nie dzieje się asynchronicznie i wynik jest deterministyczny.

   Wstrzykiwanie: cały kod mapy siedzi w app.js wewnątrz bloku
   `if (startInput) { ... }` (patrz tam), więc jego stałe są niewidoczne z
   zewnątrz. Doklejamy więc eksport PRZED ostatnią klamrą pliku - patrz
   INJECTION niżej. Gdyby ten blok kiedyś zniknął, eksport wyląduje w złym
   miejscu i harness padnie z jasnym komunikatem, zamiast po cichu przestać
   cokolwiek sprawdzać.

   Wejście (podstawia je runner z tests/test_flow_map_front.py):
   - APP_SOURCE   - treść static/app.js,
   - FLOW_FIXTURE - odpowiedź /api/flow.
   Wyjście: globalThis.__app z uchwytami do wnętrza app.js. */

const VIEW_SIZE = {x: 1200, y: 800};   // "okno przeglądarki" emulatora

// --- Leaflet: rzuty i miary ------------------------------------------------

const EARTH_R = 6371000;
const scaleAt = zoom => 256 * Math.pow(2, zoom);

function projectXY(lat, lng, scale) {
    const sin = Math.sin(lat * Math.PI / 180);
    return {
        x: (lng + 180) / 360 * scale,
        y: (0.5 - Math.log((1 + sin) / (1 - sin)) / (4 * Math.PI)) * scale,
    };
}

function unprojectXY(x, y, scale) {
    const n = Math.PI * (1 - 2 * y / scale);
    return {
        lat: Math.atan(Math.sinh(n)) * 180 / Math.PI,
        lng: x / scale * 360 - 180,
    };
}

function haversine(a, b) {
    const rad = Math.PI / 180;
    const dLat = (b.lat - a.lat) * rad, dLng = (b.lng - a.lng) * rad;
    const s = Math.sin(dLat / 2) ** 2
        + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLng / 2) ** 2;
    return 2 * EARTH_R * Math.asin(Math.min(1, Math.sqrt(s)));
}

function LatLng(lat, lng) {
    return {
        lat, lng,
        distanceTo(other) { return haversine(this, other); },
        equals(other) { return other && other.lat === lat && other.lng === lng; },
    };
}

function Point(x, y) {
    return {
        x, y,
        distanceTo(other) { return Math.hypot(x - other.x, y - other.y); },
        add(other) { return Point(x + other.x, y + other.y); },
        subtract(other) { return Point(x - other.x, y - other.y); },
    };
}

function toLatLng(a, b) {
    if (a === null || a === undefined) return a;
    if (typeof b === 'number') return LatLng(a, b);
    if (Array.isArray(a)) return LatLng(a[0], a[1]);
    if (typeof a.lat === 'number') return LatLng(a.lat, typeof a.lng === 'number' ? a.lng : a.lon);
    return a;
}

function Bounds(latlngs) {
    let s = Infinity, n = -Infinity, w = Infinity, e = -Infinity;
    const extend = ll => {
        const p = toLatLng(ll);
        s = Math.min(s, p.lat); n = Math.max(n, p.lat);
        w = Math.min(w, p.lng); e = Math.max(e, p.lng);
    };
    (latlngs || []).forEach(extend);
    return {
        extend(ll) { extend(ll); return this; },
        getSouth: () => s, getNorth: () => n, getWest: () => w, getEast: () => e,
        getCenter: () => LatLng((s + n) / 2, (w + e) / 2),
        isValid: () => s <= n && w <= e,
        contains(ll) {
            const p = toLatLng(ll);
            return p.lat >= s && p.lat <= n && p.lng >= w && p.lng <= e;
        },
    };
}

// --- Leaflet: warstwy ------------------------------------------------------

const layerBase = extra => Object.assign({
    _added: false,
    addTo(m) { this._added = true; if (m && m._layers) m._layers.add(this); return this; },
    remove() { this._added = false; return this; },
    on() { return this; },
    off() { return this; },
    bindTooltip() { return this; },
    setStyle(style) { Object.assign(this.options, style); return this; },
    setLatLng(ll) { this._latlng = toLatLng(ll); return this; },
    getLatLng() { return this._latlng; },
    setContent(html) { this._content = html; return this; },
    setOpacity(o) { this.options.opacity = o; return this; },
    getElement() { return null; },   // brak prawdziwego DOM-u: bindCluster odpuszcza
    redraw() { return this; },
}, extra);

const L = {
    latLng: toLatLng,
    point: (x, y) => (typeof x === 'object' ? Point(x.x, x.y) : Point(x, y)),
    latLngBounds: list => Bounds(list),
    polyline: (latlngs, options) => layerBase({
        kind: 'polyline', latlngs: (latlngs || []).map(p => toLatLng(p)),
        options: {...(options || {})},
        getBounds() { return Bounds(this.latlngs); },
    }),
    circleMarker: (latlng, options) => layerBase({
        kind: 'circleMarker', _latlng: toLatLng(latlng), options: {...(options || {})},
    }),
    marker: (latlng, options) => layerBase({
        kind: 'marker', _latlng: toLatLng(latlng), options: {...(options || {})},
    }),
    divIcon: options => ({...options}),
    tooltip: options => layerBase({kind: 'tooltip', options: {...(options || {})}}),
    layerGroup: layers => layerBase({kind: 'group', layers: (layers || []).slice()}),
    tileLayer: () => layerBase({kind: 'tiles', options: {}}),
    control: {zoom: () => layerBase({kind: 'control', options: {}})},
    DomEvent: {on: () => L.DomEvent, off: () => L.DomEvent, stop: () => {},
               preventDefault: () => {}, disableClickPropagation: () => {}},
    Browser: {mobile: false},
};

// --- Leaflet: mapa ---------------------------------------------------------

const mapState = {zoom: 13, center: LatLng(51.107, 17.038), size: VIEW_SIZE};

const mapStub = {
    _layers: new Set(),
    _handlers: {},
    setView(center, zoom) {
        mapState.center = toLatLng(center);
        if (typeof zoom === 'number') mapState.zoom = zoom;
        this.fire('moveend', {});
        return this;
    },
    getZoom: () => mapState.zoom,
    getCenter: () => mapState.center,
    getSize: () => Point(mapState.size.x, mapState.size.y),
    on(name, fn) { (this._handlers[name] = this._handlers[name] || []).push(fn); return this; },
    off() { return this; },
    addLayer(layer) { this._layers.add(layer); return this; },
    removeLayer(layer) { this._layers.delete(layer); return this; },
    hasLayer(layer) { return this._layers.has(layer); },
    latLngToContainerPoint(ll) {
        const scale = scaleAt(mapState.zoom);
        const p = projectXY(toLatLng(ll).lat, toLatLng(ll).lng, scale);
        const c = projectXY(mapState.center.lat, mapState.center.lng, scale);
        return Point(p.x - c.x + mapState.size.x / 2, p.y - c.y + mapState.size.y / 2);
    },
    containerPointToLatLng(point) {
        const scale = scaleAt(mapState.zoom);
        const c = projectXY(mapState.center.lat, mapState.center.lng, scale);
        const ll = unprojectXY(c.x + point.x - mapState.size.x / 2,
                               c.y + point.y - mapState.size.y / 2, scale);
        return LatLng(ll.lat, ll.lng);
    },
    getBounds() {
        return Bounds([
            this.containerPointToLatLng(Point(0, 0)),
            this.containerPointToLatLng(Point(mapState.size.x, mapState.size.y)),
        ]);
    },
    /* Jak Leaflet: największe powiększenie, przy którym całość mieści się w
       kadrze POMNIEJSZONYM o marginesy, plus przesunięcie środka o różnicę
       marginesów (panel wyszukiwania zasłania lewą stronę mapy, więc kadr
       przesuwa się w prawo - dokładnie tak, jak widzi to użytkownik). */
    fitBounds(bounds, options) {
        if (!bounds || !bounds.isValid()) return this;
        const opt = options || {};
        const tl = opt.paddingTopLeft || opt.padding || [0, 0];
        const br = opt.paddingBottomRight || opt.padding || [0, 0];
        const maxZoom = opt.maxZoom !== undefined ? opt.maxZoom : 18;
        const usable = {x: mapState.size.x - tl[0] - br[0],
                        y: mapState.size.y - tl[1] - br[1]};
        let best = 0;
        for (let z = 0; z <= maxZoom; z++) {
            const scale = scaleAt(z);
            const a = projectXY(bounds.getSouth(), bounds.getWest(), scale);
            const b = projectXY(bounds.getNorth(), bounds.getEast(), scale);
            if (Math.abs(b.x - a.x) <= usable.x && Math.abs(b.y - a.y) <= usable.y) best = z;
        }
        mapState.zoom = best;
        const scale = scaleAt(best);
        const c = bounds.getCenter();
        const p = projectXY(c.lat, c.lng, scale);
        const shifted = unprojectXY(p.x + (br[0] - tl[0]) / 2,
                                    p.y + (br[1] - tl[1]) / 2, scale);
        mapState.center = LatLng(shifted.lat, shifted.lng);
        // Leaflet po przesunięciu kadru woła moveend, a na tym wisi
        // placeLineLabels - bez tego grupki numerów zostałyby policzone dla
        // POPRZEDNIEGO powiększenia (drawFlow kadruje PO ich postawieniu).
        this.fire('moveend', {});
        return this;
    },
    fire(name, event) { (this._handlers[name] || []).forEach(fn => fn(event)); return this; },
    invalidateSize() { return this; },
};
L.map = () => mapStub;

// --- DOM ------------------------------------------------------------------

function fakeElement(id) {
    const el = {
        id, tagName: 'DIV', open: false, checked: false, disabled: false,
        hidden: false, value: '', textContent: '', innerHTML: '',
        offsetWidth: 320, offsetHeight: 40, scrollTop: 0, scrollHeight: 0,
        dataset: {},
        style: {
            _props: new Map(),
            setProperty(k, v) { this._props.set(k, v); },
            getPropertyValue(k) { return this._props.get(k) || ''; },
            removeProperty(k) { this._props.delete(k); },
        },
        children: [], parentElement: null,
        classList: {
            _set: new Set(),
            add(...c) { c.forEach(x => this._set.add(x)); },
            remove(...c) { c.forEach(x => this._set.delete(x)); },
            toggle(c, on) { if (on === undefined) on = !this._set.has(c);
                            on ? this._set.add(c) : this._set.delete(c); return on; },
            contains(c) { return this._set.has(c); },
        },
        addEventListener() {}, removeEventListener() {}, dispatchEvent() { return true; },
        appendChild(child) { this.children.push(child); return child; },
        removeChild() {}, remove() {},
        // Atrapa nie parsuje HTML-a, ale musi go SKLEIĆ we właściwej
        // kolejności - na tym stoi test "ostrzeżenie nad listą".
        insertAdjacentHTML(where, html) {
            if (where === 'afterbegin') this.innerHTML = html + this.innerHTML;
            else if (where === 'beforeend') this.innerHTML = this.innerHTML + html;
        },
        setAttribute() {}, getAttribute() { return null; }, removeAttribute() {},
        // Zwracamy atrapę, nie null: app.js podpina zdarzenia do elementów
        // znalezionych po selektorze (np. .headline-best) i null by go wywrócił.
        _found: new Map(),
        querySelector(sel) {
            if (!this._found.has(sel)) this._found.set(sel, fakeElement(sel));
            return this._found.get(sel);
        },
        querySelectorAll() { return []; },
        closest() { return null; }, contains() { return false; },
        focus() {}, blur() {}, click() {}, scrollIntoView() {}, select() {},
        getBoundingClientRect() {
            return {top: 0, left: 0, right: 320, bottom: 120, width: 320, height: 120};
        },
    };
    if (id === 'stop-names') el.textContent = '[]';   // JSON.parse w app.js
    return el;
}

const elements = new Map();
const document = {
    body: fakeElement('body'),
    documentElement: fakeElement('html'),
    getElementById(id) {
        if (!elements.has(id)) elements.set(id, fakeElement(id));
        return elements.get(id);
    },
    createElement: tag => fakeElement(tag),
    querySelector: sel => document.body.querySelector(sel),
    querySelectorAll: () => [],
    addEventListener() {}, removeEventListener() {},
};

const localStorage = {
    _data: new Map(),
    getItem(k) { return this._data.has(k) ? this._data.get(k) : null; },
    setItem(k, v) { this._data.set(k, String(v)); },
    removeItem(k) { this._data.delete(k); },
};

const navigator = {geolocation: {getCurrentPosition() {}, watchPosition() {}},
                   serviceWorker: {register() { return {then: thenable}; }}};

/* fetch, który NIGDY nie woła swoich callbacków: emulator ma być
   deterministyczny i synchroniczny, a app.js i tak nie potrzebuje listy
   przystanków do rysowania przepływu. */
function thenable() { return {then: thenable, catch: thenable, finally: thenable}; }
const fetch = () => thenable();

const window = {
    matchMedia: () => ({matches: true, addEventListener() {}, addListener() {}}),
    innerHeight: VIEW_SIZE.y, innerWidth: VIEW_SIZE.x,
    addEventListener() {}, removeEventListener() {},
    location: {search: '', href: 'http://localhost/'},
    localStorage, document, navigator, fetch,
    requestAnimationFrame(fn) { return 0; },
    setTimeout() { return 0; }, clearTimeout() {},
};

// --- uruchomienie app.js ---------------------------------------------------

/* Eksport wnętrza bloku `if (startInput) { ... }`. Gettery, nie wartości:
   flowHits/flowLabelLayer to `let`, podmieniane przy każdym rysowaniu. */
const INJECTION = `
;globalThis.__app = {
    map, look, timeOpts, LOOK_DEFAULTS,
    lookOpacity, lookWeight, fmtClock,
    drawFlow, renderPlan, placeLineLabels, clusterBox, clusterRows, corridorOf, corridorKey,
    get resultsBox() { return resultsBox; },
    flowHitsAt, corridorOptions, pickFromCluster, handleFlowHover, clearFlowHover,
    ensurePathMetrics, projectOnPath, timeAtPos, timeAtHover,
    get flowHits() { return flowHits; },
    get flowLabelLayer() { return flowLabelLayer; },
    get flowPick() { return flowPick; },
    get flowParts() { return flowParts; },
};
`;

function runApp(source) {
    const cut = source.lastIndexOf('}');
    if (cut < 0) throw new Error('app.js nie kończy się klamrą - sprawdź INJECTION w harness.js');
    const patched = source.slice(0, cut) + INJECTION + source.slice(cut);
    // eval, nie osobny plik: kod mapy jest w bloku, więc eksport musi powstać
    // w TYM SAMYM zasięgu, a nie obok niego.
    (function () { eval(patched); })();
    if (!globalThis.__app || !globalThis.__app.drawFlow) {
        throw new Error('eksport z app.js nie powstał - blok `if (startInput)` '
                        + 'zmienił kształt, popraw INJECTION w harness.js');
    }
    return globalThis.__app;
}

const app = runApp(APP_SOURCE);
