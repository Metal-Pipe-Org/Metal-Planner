/* Warstwa PWA: rejestracja service workera, przycisk instalacji i informacja
   o nowej wersji. Celowo osobny plik od app.js - działa także wtedy, gdy nie
   ma bazy rozkładów i panel pokazuje sam komunikat o błędzie. */

if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
        navigator.serviceWorker.register('/sw.js').then(watchForUpdate, () => {});
    });

    // Przejęcie sterowania przez nową wersję = jedno przeładowanie strony.
    let reloading = false;
    navigator.serviceWorker.addEventListener('controllerchange', () => {
        if (reloading) return;
        reloading = true;
        location.reload();
    });
}

/* Nowa wersja czeka w kolejce dopóki żyje stara karta - zamiast czekać na
   zamknięcie wszystkich, proponujemy odświeżenie. */
function watchForUpdate(registration) {
    const offerReload = worker => {
        if (!navigator.serviceWorker.controller) return;   // pierwsza instalacja
        showToast('Jest nowa wersja planera.', 'Odśwież', () => {
            worker.postMessage('SKIP_WAITING');
        });
    };

    if (registration.waiting) offerReload(registration.waiting);

    registration.addEventListener('updatefound', () => {
        const worker = registration.installing;
        if (!worker) return;
        worker.addEventListener('statechange', () => {
            if (worker.state === 'installed') offerReload(worker);
        });
    });
}

/* Przycisk "Zainstaluj" ma sens tylko wtedy, gdy przeglądarka faktycznie
   proponuje instalację - poza tym oknem jest schowany. */
const installButton = document.getElementById('install');
let installPrompt = null;

window.addEventListener('beforeinstallprompt', event => {
    event.preventDefault();
    installPrompt = event;
    if (installButton) installButton.hidden = false;
});

if (installButton) {
    installButton.addEventListener('click', async () => {
        if (!installPrompt) return;
        installButton.hidden = true;
        const prompt = installPrompt;
        installPrompt = null;
        prompt.prompt();
        await prompt.userChoice;
    });
}

window.addEventListener('appinstalled', () => {
    installPrompt = null;
    if (installButton) installButton.hidden = true;
});

/* Panel ⚙ - wymuszenie sprawdzenia aktualizacji.

   Pasek "jest nowa wersja" pojawia się tylko wtedy, gdy przeglądarka sama
   zauważy nowego workera. Tutaj można ją do tego zmusić i - co ważniejsze -
   zobaczyć, że coś jest nie tak: jeśli serwer wydaje inną wersję niż ta,
   na której chodzi aplikacja, a mimo to nie ma aktualizacji, to znaczy, że
   /sw.js jest gdzieś po drodze cache'owany i użytkownicy zostają na starym
   froncie. Bez tej diagnostyki taka awaria jest niewidoczna. */

const swVersionEl = document.getElementById('sw-version');
const swStatusEl = document.getElementById('sw-status');
const swCheckButton = document.getElementById('sw-check');

function setSwStatus(text, state) {
    if (!swStatusEl) return;
    swStatusEl.textContent = text;
    swStatusEl.className = state ? `field-hint ${state}` : 'field-hint';
}

/** Wersja wkompilowana w workera, który obsługuje tę stronę. */
function runningVersion() {
    const worker = navigator.serviceWorker && navigator.serviceWorker.controller;
    if (!worker) return Promise.resolve(null);

    return new Promise(resolve => {
        const channel = new MessageChannel();
        // Worker mógł zostać ubity albo być starą wersją bez tej obsługi -
        // czekanie w nieskończoność zawiesiłoby przycisk.
        const timer = setTimeout(() => resolve(null), 2000);
        channel.port1.onmessage = event => {
            clearTimeout(timer);
            resolve(event.data);
        };
        worker.postMessage('VERSION', [channel.port2]);
    });
}

/** Wersja, którą serwer wydaje w tej chwili - prosto z sieci. */
async function servedVersion() {
    const response = await fetch('/sw.js', {cache: 'no-store'});
    const match = (await response.text()).match(/VERSION = '([^']+)'/);
    return match && match[1];
}

async function checkForUpdate() {
    if (!('serviceWorker' in navigator)) {
        setSwStatus('Ta przeglądarka nie obsługuje service workerów.', 'bad');
        return;
    }

    const registration = await navigator.serviceWorker.getRegistration();
    if (!registration || !navigator.serviceWorker.controller) {
        setSwStatus('Worker jeszcze nie przejął strony — przeładuj i spróbuj ponownie.', 'warn');
        return;
    }

    const [running, served] = await Promise.all([runningVersion(), servedVersion()]);
    if (swVersionEl && running) swVersionEl.textContent = running;

    await registration.update();

    if (registration.installing || registration.waiting) {
        setSwStatus(`Jest nowa wersja (${served}) — instaluje się, zaraz pojawi się pasek z odświeżeniem.`, 'ok');
    } else if (served && running && served !== running) {
        setSwStatus(
            `Coś jest nie tak: serwer wydaje ${served}, a aplikacja chodzi na ${running} `
            + 'i mimo wymuszenia nie widzi aktualizacji. Najczęściej znaczy to, że /sw.js '
            + 'jest cache\'owany po drodze (proxy, CDN, nagłówki) — użytkownicy zostają '
            + 'wtedy na starym froncie.', 'bad');
    } else {
        setSwStatus(`Wszystko aktualne (${running}).`, 'ok');
    }
}

if (swCheckButton) {
    swCheckButton.addEventListener('click', async () => {
        swCheckButton.disabled = true;
        setSwStatus('Sprawdzam…');
        try {
            await checkForUpdate();
        } catch (err) {
            setSwStatus(`Nie udało się sprawdzić: ${err.message}`, 'bad');
        }
        swCheckButton.disabled = false;
    });
}

// Wersję pokazujemy od razu po wejściu - bez klikania widać, co jest w środku.
if ('serviceWorker' in navigator && swVersionEl) {
    navigator.serviceWorker.ready
        .then(runningVersion)
        .then(version => {
            if (version) swVersionEl.textContent = version;
        });
}

/* Jeden wspólny pasek na dole - używa go i aktualizacja, i utrata sieci. */
let toast = null;

function showToast(message, actionLabel, onAction) {
    hideToast();

    toast = document.createElement('div');
    toast.className = 'toast';
    toast.setAttribute('role', 'status');

    const text = document.createElement('span');
    text.textContent = message;
    toast.append(text);

    if (actionLabel) {
        const action = document.createElement('button');
        action.className = 'toast-action';
        action.textContent = actionLabel;
        action.addEventListener('click', () => {
            hideToast();
            onAction();
        });
        toast.append(action);
    }

    const close = document.createElement('button');
    close.className = 'toast-close icon-button';
    close.setAttribute('aria-label', 'Zamknij');
    close.textContent = '✕';
    close.addEventListener('click', hideToast);
    toast.append(close);

    document.body.append(toast);
}

function hideToast() {
    if (toast) toast.remove();
    toast = null;
}

// Po powrocie sieci ostatnie wyszukiwanie i tak trzeba powtórzyć ręcznie,
// ale warto powiedzieć wprost, dlaczego wyniki przestały się pojawiać.
window.addEventListener('offline', () => {
    showToast('Brak połączenia — mapa działa z pamięci, trasy nie.');
});

window.addEventListener('online', hideToast);
