/* Service worker planera - serwowany z "/", więc obejmuje całą aplikację.

   Strategie, bo każdy rodzaj zasobu chce czego innego:
   - nawigacje ("/")        -> sieć, a bez sieci ostatnia zapamiętana powłoka;
   - /api/*                 -> tylko sieć (rozkład bez sieci nie ma sensu),
                               offline oddajemy JSON z błędem, który UI umie
                               pokazać jak każdy inny komunikat;
   - statyki i Leaflet      -> cache od razu, w tle odświeżenie;
   - kafelki mapy           -> cache od razu, z ograniczeniem rozmiaru.

   VERSION to odcisk zawartości frontu, podstawiany przez Flaska przy
   serwowaniu /sw.js (patrz `_frontend_digest` w routes.py). Zmiana
   dowolnego pliku = nowa treść tego skryptu = nowe nazwy cache'ów,
   a stare lecą przy aktywacji. Niczego nie trzeba podbijać ręcznie. */

const VERSION = '__VERSION__';
const SHELL = `planer-shell-${VERSION}`;
const ASSETS = `planer-assets-${VERSION}`;
const TILES_LIMIT = 400;

// Kafelki celowo bez wersji frontu: nie zmieniają się razem z aplikacją,
// a szkoda ściągać megabajty mapy po każdej poprawce w CSS. Numer podbija
// się tylko wtedy, gdy zmieni się sposób ich pobierania (np. crossOrigin) -
// odpowiedzi zapisane po staremu nie pasowałyby do nowych żądań.
const TILES = 'planer-tiles-1';

// Powłoka musi się zapisać w całości, inaczej instalacja nie ma sensu.
const SHELL_URLS = [
    '/',
    '/static/offline.html',
    '/static/style.css',
    '/static/app.js',
    '/static/pwa.js',
    '/static/manifest.webmanifest',
    '/static/icons/icon-192.png',
    '/static/icons/icon-512.png',
];

// CDN bywa kapryśny, a brak Leafleta to tylko brak mapy - nie blokujemy
// instalacji, jeśli się nie uda.
const CDN_URLS = [
    'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css',
    'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js',
];

const isTile = url => url.hostname.endsWith('tile.openstreetmap.org');

/* Kafelki i Leaflet lecą z obcego serwera bez CORS, więc dostajemy odpowiedź
   "opaque": status 0 i żadnego wglądu w treść. Taką też trzeba zapisać,
   inaczej z cache nie skorzysta nic spoza naszej domeny. */
const isCacheable = response =>
    response && (response.ok || response.type === 'opaque');

self.addEventListener('install', event => {
    event.waitUntil((async () => {
        const shell = await caches.open(SHELL);
        await shell.addAll(SHELL_URLS);

        const assets = await caches.open(ASSETS);
        await Promise.all(CDN_URLS.map(url => assets.add(url).catch(() => {})));
    })());
});

self.addEventListener('activate', event => {
    event.waitUntil((async () => {
        const keep = [SHELL, ASSETS, TILES];
        const names = await caches.keys();
        await Promise.all(
            names.filter(name => name.startsWith('planer-') && !keep.includes(name))
                 .map(name => caches.delete(name))
        );
        await self.clients.claim();
    })());
});

// Nowa wersja czeka na zamknięcie wszystkich kart; pasek "Odśwież" w pwa.js
// pozwala przejąć sterowanie od razu. Na pytanie o wersję odpowiadamy tym,
// co faktycznie jest wkompilowane w tego workera - panel ⚙ porównuje to
// z wersją serwowaną w tej chwili przez serwer.
self.addEventListener('message', event => {
    if (event.data === 'SKIP_WAITING') self.skipWaiting();
    if (event.data === 'VERSION' && event.ports[0]) {
        event.ports[0].postMessage(VERSION);
    }
});

self.addEventListener('fetch', event => {
    const request = event.request;
    if (request.method !== 'GET') return;

    const url = new URL(request.url);
    if (!url.protocol.startsWith('http')) return;

    // Samego workera nigdy nie podajemy z cache'u - panel ⚙ pyta o /sw.js,
    // żeby poznać wersję serwowaną w tej chwili, i musi dostać prawdę.
    if (url.origin === self.location.origin && url.pathname === '/sw.js') return;

    if (request.mode === 'navigate') {
        event.respondWith(navigation(request));
    } else if (url.origin === self.location.origin && url.pathname.startsWith('/api/')) {
        event.respondWith(apiOnline(request));
    } else if (isTile(url)) {
        event.respondWith(cacheFirst(request, TILES, TILES_LIMIT));
    } else if (url.origin === self.location.origin || CDN_URLS.includes(url.href)) {
        event.respondWith(staleWhileRevalidate(request));
    }
});

/** Strona: świeża z sieci, a po jej stracie ostatnia znana wersja. */
async function navigation(request) {
    const shell = await caches.open(SHELL);
    try {
        const response = await fetch(request);
        if (response.ok) shell.put('/', response.clone());
        return response;
    } catch (err) {
        return (await shell.match('/'))
            || (await shell.match('/static/offline.html'))
            || Response.error();
    }
}

/** API tylko z sieci - offline zwracamy błąd w formacie, który zna frontend. */
async function apiOnline(request) {
    try {
        return await fetch(request);
    } catch (err) {
        return Response.json(
            {error: 'Jesteś offline - planowanie trasy wymaga połączenia z siecią.'},
            {status: 503, headers: {'Cache-Control': 'no-store'}}
        );
    }
}

/** Kafelki i inne niezmienne zasoby: z cache natychmiast, pobranie tylko raz. */
async function cacheFirst(request, cacheName, limit) {
    const cache = await caches.open(cacheName);
    const hit = await cache.match(request);
    if (hit) return hit;

    const response = await fetch(request);
    if (isCacheable(response)) {
        // Zapis nie może przewrócić odpowiedzi - pełny dysk oznacza kafelek
        // bez cache, a nie dziurę na mapie.
        try {
            await cache.put(request, response.clone());
            trim(cacheName, limit);
        } catch (err) {
            /* nic - zostaje sama sieć */
        }
    }
    return response;
}

/** Statyki: cache natychmiast, świeża kopia odkłada się na następny raz. */
async function staleWhileRevalidate(request) {
    const cache = await caches.open(ASSETS);
    const hit = await cache.match(request);

    const network = fetch(request)
        .then(response => {
            if (isCacheable(response)) cache.put(request, response.clone());
            return response;
        })
        .catch(() => hit);

    return hit || network;
}

/** Kafelków przybywa w nieskończoność - trzymamy tylko ostatnie `limit`. */
async function trim(cacheName, limit) {
    if (!limit) return;
    const cache = await caches.open(cacheName);
    const keys = await cache.keys();
    for (const key of keys.slice(0, keys.length - limit)) {
        await cache.delete(key);
    }
}
