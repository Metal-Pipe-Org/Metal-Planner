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
        // „Nie” w pytaniu o kurs (#231) - poprawia się go w rozwiniętym formularzu.
        document.addEventListener('planner:edit', () => collapse(false));
        // ✕ zaczyna nową relację, a tę wpisuje się w rozwiniętym formularzu.
        document.getElementById('clear').addEventListener('click', () => collapse(false));
    }

    // Klucz dawnych ustawień tylko dla telefonu (zakładka „Trasy" - dziś
    // wspólna opcja „Wyłącz propozycje tras", patrz app.js). Zostaje, żeby
    // reset ustawień sprzątał to, co mogło zostać w przeglądarce.
    const PREFS_KEY = 'metal-planner:phone-prefs';

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
