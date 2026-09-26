/* Telefon - to, co na wąskim ekranie DZIAŁA inaczej. Wygląd siedzi obok,
   w phone.css; granica "co jest telefonem" w <link id="phone-css">
   (templates/index.html), czytana stąd, żeby arkusz i skrypt nie mogły się
   rozjechać.

   Ładowany przed app.js: app.js pyta tutaj, czy to telefon i ile mapy
   zasłaniają nakładki - nie liczy tego sam. */

// Strona się nie przybliża, tylko mapa (patrz viewport w templates/index.html).
// Safari na iPhonie ignoruje user-scalable=no przy geście dwóch palców, więc
// ten gest jest gaszony tutaj - poza mapą, bo mapa przybliża się sama.
// Niezależnie od szerokości: iPhone obrócony poziomo to już szeroki układ,
// a przybliżona strona rozjeżdża się z mapą tak samo.
{
    const outsideMap = target => !(target instanceof Element) || !target.closest('#map');
    document.addEventListener('touchmove', event => {
        if (event.touches.length > 1 && outsideMap(event.target)) event.preventDefault();
    }, {passive: false});
    document.addEventListener('gesturestart', event => {
        if (outsideMap(event.target)) event.preventDefault();
    });
}

const phoneLayout = (() => {
    const query = window.matchMedia(document.getElementById('phone-css').media);
    const sidebar = document.getElementById('sidebar');
    const tabs = document.getElementById('view-tabs');
    const dock = document.getElementById('map-dock');
    const card = document.querySelector('.search-card');

    // Kolumna warstw stoi tuż pod panelem, a panel zmienia wysokość sam
    // z siebie (zwinięty, rozwinięty, z pokładu, rozkłady) - arkusz dostaje
    // więc jego dolną krawędź jako zmienną, zamiast zgadywać ją liczbą.
    // Pod kolumną jest dok (albo same zakładki), a między nimi bywa ciasno:
    // rozwinięty formularz na małym telefonie albo karta rozkładu zostawiają
    // za mało miejsca na cztery przyciski w pionie. Wtedy kolumna kładzie się
    // w rząd, a gdy i na rząd brakuje miejsca - znika, póki panel nie zmaleje.
    const layerBar = document.querySelector('.layer-bar');
    function placeLayers() {
        const panelBottom = sidebar.getBoundingClientRect().bottom;
        document.documentElement.style.setProperty('--panel-bottom', panelBottom + 'px');
        const dockBox = dock ? dock.getBoundingClientRect() : null;
        const floor = dockBox && dockBox.height ? dockBox.top
            : tabs ? tabs.getBoundingClientRect().top : window.innerHeight;
        const free = floor - panelBottom - 16;
        document.body.classList.remove('layers-row', 'layers-off');
        const column = layerBar.getBoundingClientRect();
        if (free >= column.height) return;
        document.body.classList.add('layers-row');
        if (free < layerBar.getBoundingClientRect().height) {
            document.body.classList.add('layers-off');
        }
    }
    const watch = new ResizeObserver(placeLayers);
    watch.observe(sidebar);
    if (dock) watch.observe(dock);
    window.addEventListener('resize', placeLayers);

    // Po wyszukaniu formularz niesie jedno zdanie ("skąd → dokąd, o której"),
    // a zajmuje ćwierć ekranu nad mapą. Zwija się więc do tego zdania,
    // a stuknięcie w nie rozwija go z powrotem. Na szerokim ekranie klasa nic
    // nie robi, a linijka ma `hidden` - pokazuje ją dopiero phone.css.
    if (card) {   // brak karty = brak bazy rozkładów (panel pokazuje błąd)
        const summary = document.createElement('button');
        summary.type = 'button';
        summary.className = 'search-summary';
        summary.hidden = true;
        summary.setAttribute('aria-label', 'Zmień wyszukiwanie');
        card.prepend(summary);

        const collapse = on => document.body.classList.toggle('search-collapsed', on);

        const part = (cls, text) => {
            const span = document.createElement('span');
            span.className = cls;
            span.textContent = text;
            return span;
        };

        document.addEventListener('planner:search', () => {
            const onboard = document.body.classList.contains('start-onboard');
            const from = onboard
                ? 'linia ' + document.getElementById('ob-line').value.trim()
                : document.getElementById('start').value;
            summary.replaceChildren(
                part('search-summary-from', from),
                part('search-summary-arrow', '→'),
                part('search-summary-to', document.getElementById('end').value),
                part('search-summary-time', document.getElementById('time').value),
            );
            // Klawiatura telefonu zostałaby nad mapą, przypięta do pola,
            // którego już nie widać.
            if (card.contains(document.activeElement)) document.activeElement.blur();
            collapse(true);
        });
        summary.addEventListener('click', () => collapse(false));
        // ✕ zaczyna nową relację, a tę wpisuje się w rozwiniętym formularzu.
        document.getElementById('clear').addEventListener('click', () => collapse(false));
    }

    // Ustawienia tylko dla telefonu w panelu ⚙ (sekcja Layout).
    // Zapamiętane pod własnym kluczem - to ustawienia układu, nie mapy.
    const PREFS_KEY = 'metal-planner:phone-prefs';
    let prefs = {};
    try {
        prefs = JSON.parse(localStorage.getItem(PREFS_KEY)) || {};
    } catch {
        // localStorage niedostępny - zostają ustawienia domyślne
    }

    const PHONE_SWITCHES = {
        'phone-routes-tab': {
            key: 'routesTab',
            show(on) {
                document.body.classList.toggle('routes-tab-off', !on);
                // Schowana zakładka nie może zostać tą, na którą się patrzy.
                const onRoutes = document.body.classList.contains('view-list')
                    && !document.body.classList.contains('mode-timetable');
                if (!on && onRoutes) document.querySelector('#view-tabs [data-view="map"]').click();
            },
        },
    };

    for (const [id, {key, show}] of Object.entries(PHONE_SWITCHES)) {
        const input = document.getElementById(id);
        if (!input) continue;
        input.checked = prefs[key] ?? input.defaultChecked;
        // Po app.js, nie od razu: tryb „w pojeździe" i widok przywraca dopiero
        // on, a wcześniej nie byłoby czego poprawiać.
        document.addEventListener('DOMContentLoaded', () => show(input.checked));
        input.addEventListener('change', () => {
            prefs[key] = input.checked;
            try {
                localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
            } catch {
                // localStorage niedostępny - działa do odświeżenia strony
            }
            show(input.checked);
        });
    }

    /** Ile mapy od góry i od dołu zasłaniają nakładki - kadr trasy ma się
        zmieścić między nimi. Kadrujemy zawsze pod widok mapy, także wtedy,
        gdy akurat patrzymy na listę, bo to ten kadr zobaczymy po przełączeniu
        zakładki.

        Góra: karta wyszukiwania, ale z sufitem - rozwinięta bywa wysoka, a przy
        dosłownym odsunięciu się od niej na kadr zostawał pasek u dołu ekranu.
        Dół: zakładki i dok. Tablica przystanku startowego wchodzi do doku
        dopiero PO kadrowaniu (przychodzi osobnym zapytaniem), więc miejsce na
        nią jest zarezerwowane z góry, a nie zmierzone. */
    function mapInsets() {
        const top = card
            ? Math.min(card.getBoundingClientRect().bottom + 12, window.innerHeight * 0.35)
            : 40;
        const docked = Math.max(dock ? dock.getBoundingClientRect().height : 0,
                                window.innerHeight * 0.22);
        return {top, bottom: (tabs ? tabs.offsetHeight : 0) + 12 + docked};
    }

    return {active: () => query.matches, mapInsets, PREFS_KEY};
})();
