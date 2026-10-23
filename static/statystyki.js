// Strona /statystyki: tabela zliczeń zmian ustawień zapisanych przez app.js
// w localStorage tej przeglądarki (#404). Nazwy ustawień przychodzą razem ze
// zliczeniami, bo etykiety żyją na stronie głównej, a tu panelu nie ma.
const SETTINGS_STATS_KEY = 'metal-planner:settings-stats';
const $ = id => document.getElementById(id);

function loadStats() {
    try {
        const stats = JSON.parse(localStorage.getItem(SETTINGS_STATS_KEY));
        return {
            changes: (stats && stats.changes) || {},
            labels: (stats && stats.labels) || {},
            values: (stats && stats.values) || {},
            reset: (stats && stats.reset) || {n: 0, t: null},
        };
    } catch {
        return {changes: {}, labels: {}, values: {}, reset: {n: 0, t: null}};   // localStorage niedostępny albo zepsuty JSON
    }
}

function statsAgo(t) {
    if (!t) return '—';
    const minutes = Math.floor((Date.now() - t) / 60000);
    if (minutes < 1) return 'przed chwilą';
    if (minutes < 60) return `${minutes} min temu`;
    if (minutes < 60 * 24) return `${Math.floor(minutes / 60)} godz. temu`;
    if (minutes < 60 * 24 * 30) return `${Math.floor(minutes / 60 / 24)} dni temu`;
    return new Date(t).toLocaleDateString('pl-PL');
}

function statsRows() {
    const stats = loadStats();
    // Etykiety znają też ustawienia, których nikt nie ruszył - to one są wynikiem tej tabeli.
    const rows = Object.entries(stats.labels).map(([id, label]) => {
        const entry = stats.changes[id] || {n: 0, t: null};
        return {id, label, changes: entry.n, last: entry.t, value: stats.values[id] ?? '—'};
    });
    return rows.sort((a, b) => b.changes - a.changes);
}

function renderStats() {
    const stats = loadStats();
    const table = document.createElement('table');
    table.className = 'stats-table';
    const head = table.createTHead().insertRow();
    for (const title of ['Ustawienie', 'Zmian', 'Ostatnio', 'Teraz']) {
        head.appendChild(document.createElement('th')).textContent = title;
    }
    const body = table.createTBody();
    const addRow = (label, changes, last, value) => {
        const row = body.insertRow();
        row.insertCell().textContent = label;
        row.insertCell().textContent = changes;
        row.insertCell().textContent = statsAgo(last);
        row.insertCell().textContent = value;
        if (!changes) row.className = 'untouched';
        return row;
    };
    addRow('Przywróć domyślne', stats.reset.n, stats.reset.t, '—').classList.add('stats-reset');
    for (const row of statsRows()) addRow(row.label, row.changes, row.last, row.value);
    $('stats-body').replaceChildren(table);
}

$('stats-copy').addEventListener('click', async () => {
    const stats = loadStats();
    const iso = t => (t ? new Date(t).toISOString() : null);
    const json = JSON.stringify({
        settings: statsRows().map(r => ({
            id: r.id, label: r.label, changes: r.changes, last: iso(r.last), value: r.value,
        })),
        reset: {count: stats.reset.n, last: iso(stats.reset.t)},
    }, null, 2);
    const button = $('stats-copy');
    await navigator.clipboard.writeText(json);
    button.textContent = 'Skopiowano';
    setTimeout(() => { button.textContent = 'Kopiuj statystyki'; }, 1500);
});

$('stats-clear').addEventListener('click', () => {
    try {
        localStorage.removeItem(SETTINGS_STATS_KEY);
    } catch {
        // localStorage niedostępny - i tak nie ma czego kasować
    }
    renderStats();
});

renderStats();
