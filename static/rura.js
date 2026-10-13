/* 21:37 - ekran z metalową rurą zamiast planera.

   Pojawia się przy każdym wejściu w minucie 21:37 czasu pasażera. Kto raz
   uczcił, tego w tej samej minucie nie łapie drugi raz - inaczej
   przeładowanie po dźwięku pokazywałoby ekran w kółko.

   Na czas pisania: w trybie deweloperskim `?rura=1` pokazuje ekran
   o każdej porze. */

const RURA_TEXT = 'Uczcijmy Jana Pawła II metalową rurą';

const RURA_DATE = '2 kwietnia 2005, 21:37';

// Brak pliku z dźwiękiem albo odmowa odtworzenia nie może zostawić
// pasażera na tym ekranie - po tylu ms przeładowanie idzie i bez dźwięku.
const RURA_SILENT_RELOAD_MS = 600;

// Nagranie trwa ~2 s; gdyby `ended` nie przyszło (zawieszone pobieranie),
// ekran i tak ma zniknąć.
const RURA_MAX_WAIT_MS = 5000;

{
    const screen = document.getElementById('rura');
    const now = new Date();
    const pad = n => String(n).padStart(2, '0');
    const seenKey = 'metal-planner:rura:'
        + `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
        + `T${pad(now.getHours())}:${pad(now.getMinutes())}`;

    const seen = () => {
        try {
            return localStorage.getItem(seenKey) === '1';
        } catch {
            return false;
        }
    };

    const forced = document.body.classList.contains('dev-mode')
        && new URLSearchParams(location.search).get('rura') === '1';
    const isTime = now.getHours() === 21 && now.getMinutes() === 37;

    if (forced || (isTime && !seen())) {
        document.getElementById('rura-text').textContent = RURA_TEXT;
        document.getElementById('rura-clock').textContent = RURA_DATE;
        screen.hidden = false;

        const button = document.getElementById('rura-button');
        button.addEventListener('click', () => {
            button.disabled = true;
            try {
                localStorage.setItem(seenKey, '1');
            } catch {
                // bez pamięci przeglądarki ekran może wrócić - to tylko żart
            }
            // Bez `?rura=1`, inaczej wymuszony ekran wracałby w kółko.
            const reload = () => location.replace(location.pathname);
            const audio = document.getElementById('rura-sound');
            audio.addEventListener('ended', reload);
            setTimeout(reload, RURA_MAX_WAIT_MS);
            // Odrzucona obietnica to też brak pliku: przeglądarka odmawia
            // dopiero, gdy żadne ze źródeł <audio> się nie wczytało.
            const started = audio.play();
            if (started && started.catch) {
                started.catch(() => setTimeout(reload, RURA_SILENT_RELOAD_MS));
            }
        });
    }
}
