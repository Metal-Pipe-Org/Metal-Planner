/* Okienka nowości: jednorazowa wiadomość, że doszła nowa funkcja, ze
   strzałką do miejsca, w którym ona jest.

   Dokładka z boku: reszta aplikacji o niej nie wie i od niej nie zależy.
   Wszystko siedzi w tym folderze (skrypt, wygląd, rejestr nowosci.json),
   a w szablonie są tylko dwa znaczniki, które go dołączają (po stronie
   serwera jest jeszcze nowosci.py) - usunięcie ich wyłącza okienka bez śladu.
   Okienko samo korzysta z aplikacji: czyta jej elementy przez selektory,
   klika przyciski paneli przy „Pokaż” i patrzy na wyniki wyszukiwania, ale
   niczego w niej nie woła. Wiedza o panelach aplikacji (PANELS niżej) siedzi
   tu, nie w rejestrze.

   Wpis rejestru (wpisów się nie usuwa, wygasłe po prostu się nie pokazują):
   - id          - stałe, pod nim zapamiętuje się potwierdzenie;
   - title, description - treść okienka; gdy funkcja pojawia się dopiero po
                   jakimś działaniu, opis mówi, co zrobić;
   - place       - selektor miejsca, na które wskazuje strzałka (przy funkcji
                   „po działaniu” - tam, gdzie to działanie się zaczyna);
                   „Pokaż” samo znajduje panel, w którym to miejsce siedzi;
   - expiry      - po ilu dniach wygasa: domyślnie 30, najmniej 14, albo
                   "never".
   Data wdrożenia nie jest polem rejestru: serwer zapamiętuje, kiedy pierwszy
   raz zobaczył wpis, i podaje ją razem z rejestrem (/api/nowosci). Od niej
   liczy się wygasanie. */

(function () {
'use strict';

const SEEN_KEY = 'metal-planner:news-seen';
const LAST_KEY = 'metal-planner:news-last';
// Zapisuje szablon (templates/index.html), zanim cokolwiek innego sięgnie
// po localStorage - patrz tam.
const FIRST_VISIT_KEY = 'metal-planner:first-visit';

const DAY_MS = 24 * 60 * 60 * 1000;
const NEW_USER_DAYS = 7;
const DEFAULT_EXPIRY_DAYS = 30;
const GAP = 12;
const MARGIN = 8;
const ARROW_INSET = 18;

// Panele aplikacji, w których miejsce może siedzieć schowane: `inside` to
// panel, `opener` - przycisk, którym otwiera go użytkownik.
const PANELS = [
    {inside: '#dev-panel', opener: '#dev-toggle'},
    {inside: '#timetable-card', opener: '#view-tabs [data-view="timetable"]'},
];

let seen, last, firstVisit;
try {
    firstVisit = localStorage.getItem(FIRST_VISIT_KEY);
    seen = JSON.parse(localStorage.getItem(SEEN_KEY)) || [];
    last = JSON.parse(localStorage.getItem(LAST_KEY));
} catch {
    // Bez pamięci potwierdzenie nie przetrwa odświeżenia, a okienko
    // wracałoby przy każdym wejściu.
    return;
}
if (firstVisit === null || Date.now() - Number(firstVisit) < NEW_USER_DAYS * DAY_MS) return;

function save(key, value) {
    try {
        localStorage.setItem(key, JSON.stringify(value));
    } catch {
        // localStorage niedostępny - okienko wróci przy następnej wizycie
    }
}

function expired(entry, now) {
    const days = entry.expiry ?? DEFAULT_EXPIRY_DAYS;
    return days !== 'never' && now >= entry.deployed + days * DAY_MS;
}

/** Najnowsze najpierw; przy tej samej dacie wygrywa wpis niżej w rejestrze,
    czyli dopisany później. */
function newestFirst(entries) {
    return entries.map((entry, index) => ({entry, index}))
        .sort((a, b) => b.entry.deployed - a.entry.deployed || b.index - a.index)
        .map(item => item.entry);
}

/** Okienko, które pokazało się, ale nikt go nie potwierdził, wraca przy
    następnej wizycie. Nowe wolno pokazać dopiero po dobie od poprzedniego. */
function pick(entries) {
    const now = Date.now();
    const live = entries.filter(entry => !expired(entry, now) && !seen.includes(entry.id));
    const pending = last && live.find(entry => entry.id === last.id);
    if (pending) return pending;
    if (last && now - last.at < DAY_MS) return null;
    const next = newestFirst(live)[0];
    if (next) save(LAST_KEY, {id: next.id, at: now});
    return next || null;
}

let box = null;

/** Miejsce widać, gdy stoi na ekranie i nic go nie zasłania - samo okienko
    się nie liczy, bo to ono ma na nie wskazywać. */
function visible(el) {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) return false;
    const x = r.left + r.width / 2;
    const y = r.top + r.height / 2;
    if (x < 0 || y < 0 || x > window.innerWidth || y > window.innerHeight) return false;
    const hit = document.elementsFromPoint(x, y).find(node => !box || !box.contains(node));
    return !!hit && (el.contains(hit) || hit.contains(el));
}

function show(entry) {
    const target = () => document.querySelector(entry.place);
    const fromHome = visible(target());
    let pointing = fromHome;
    let acknowledged = false;

    box = document.createElement('div');
    box.className = 'news-popup card';
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-label', 'Nowość: ' + entry.title);
    box.innerHTML = `
        <button type="button" class="icon-button news-close" title="Zamknij" aria-label="Zamknij">✕</button>
        <p class="news-title"></p>
        <p class="news-text"></p>`;
    box.querySelector('.news-title').textContent = entry.title;
    box.querySelector('.news-text').textContent = entry.description;

    const showButton = document.createElement('button');
    showButton.type = 'button';
    showButton.className = 'button primary news-show';
    showButton.textContent = 'Pokaż';
    if (!fromHome) box.append(showButton);
    document.body.append(box);

    function acknowledge() {
        if (acknowledged) return;
        acknowledged = true;
        seen.push(entry.id);
        save(SEEN_KEY, seen);
    }

    let frame = 0;
    function close() {
        cancelAnimationFrame(frame);
        observer.disconnect();
        document.removeEventListener('click', onClick, true);
        window.removeEventListener('pointerdown', touch, true);
        window.removeEventListener('keydown', touch, true);
        box.remove();
    }

    box.querySelector('.news-close').addEventListener('click', () => {
        acknowledge();
        close();
    });

    showButton.addEventListener('click', () => {
        acknowledge();
        const el = target();
        if (!visible(el)) {
            const panel = PANELS.find(candidate => el?.closest(candidate.inside));
            if (panel) document.querySelector(panel.opener)?.click();
        }
        target()?.scrollIntoView({block: 'nearest'});
        pointing = true;
        showButton.remove();
    });

    // Użycie samej funkcji to też potwierdzenie, a okienko nie ma już po co
    // zasłaniać tego, co ktoś właśnie włączył.
    function onClick(event) {
        if (!pointing || box.contains(event.target)) return;
        const el = target();
        if (el && el.contains(event.target)) {
            acknowledge();
            close();
        }
    }
    document.addEventListener('click', onClick, true);

    // Zignorowanie: ktoś wyszukał trasę albo wszedł w rozkłady. Okienko
    // zostaje do odświeżenia strony, tylko więcej nie wróci. Liczy się
    // dopiero po pierwszym dotknięciu strony: link z trasą wyszukuje sam przy
    // wejściu, a to jeszcze nie jest korzystanie z aplikacji.
    let touched = false;
    const touch = event => { if (!box.contains(event.target)) touched = true; };
    window.addEventListener('pointerdown', touch, true);
    window.addEventListener('keydown', touch, true);
    let timetableOn = document.body.classList.contains('mode-timetable');
    const observer = new MutationObserver(records => {
        if (!touched) {
            timetableOn = document.body.classList.contains('mode-timetable');
            return;
        }
        for (const record of records) {
            if (record.target === document.body) {
                const on = document.body.classList.contains('mode-timetable');
                if (on && !timetableOn) acknowledge();
                timetableOn = on;
            } else if (record.addedNodes.length) {
                acknowledge();
            }
        }
    });
    observer.observe(document.body, {attributes: true, attributeFilter: ['class']});
    const results = document.getElementById('results');
    if (results) observer.observe(results, {childList: true});

    function layout() {
        frame = requestAnimationFrame(layout);
        const el = target();
        if (!pointing) {
            box.hidden = false;
            box.dataset.side = 'none';
            box.style.left = `${(window.innerWidth - box.offsetWidth) / 2}px`;
            box.style.top = `${MARGIN + 8}px`;
            return;
        }
        box.hidden = !visible(el);
        if (box.hidden) return;
        place(box, el.getBoundingClientRect());
    }
    layout();
}

/** Strona z największą ilością miejsca, w kolejności: pod, nad, z prawej,
    z lewej; pierwsza, na której okienko mieści się w całości, wygrywa. */
function place(el, r) {
    const w = el.offsetWidth;
    const h = el.offsetHeight;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const room = {
        below: vh - r.bottom - GAP - MARGIN - h,
        above: r.top - GAP - MARGIN - h,
        right: vw - r.right - GAP - MARGIN - w,
        left: r.left - GAP - MARGIN - w,
    };
    const side = Object.keys(room).find(key => room[key] >= 0)
        || Object.keys(room).reduce((a, b) => room[b] > room[a] ? b : a);
    const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
    let left, top, arrow;
    if (side === 'below' || side === 'above') {
        left = clamp(r.left + r.width / 2 - w / 2, MARGIN, vw - MARGIN - w);
        top = side === 'below' ? r.bottom + GAP : r.top - GAP - h;
        arrow = clamp(r.left + r.width / 2 - left, ARROW_INSET, w - ARROW_INSET);
    } else {
        top = clamp(r.top + r.height / 2 - h / 2, MARGIN, vh - MARGIN - h);
        left = side === 'right' ? r.right + GAP : r.left - GAP - w;
        arrow = clamp(r.top + r.height / 2 - top, ARROW_INSET, h - ARROW_INSET);
    }
    el.dataset.side = side;
    el.style.left = `${left}px`;
    el.style.top = `${top}px`;
    el.style.setProperty('--news-arrow', `${arrow}px`);
}

window.addEventListener('load', () => {
    fetch('/api/nowosci')
        .then(response => response.json())
        .then(registry => {
            const entry = pick(registry.popups);
            if (entry) show(entry);
        })
        .catch(() => {});
});

})();
