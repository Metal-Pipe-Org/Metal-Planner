/* Sprawdzenia kontraktu mapy, które mieszkają WE FRONCIE (docs/
   FLOW_MAP_CONTRACT.md, punkty 7, 8 i 10). Uruchamiane na prawdziwym
   static/app.js przez emulator z harness.js, na prawdziwej odpowiedzi
   /api/flow z flow_fixture.json.

   Każde sprawdzenie zwraca obiekt z polem `ok` i liczbami, które do niego
   doprowadziły - asercje robi tests/test_flow_map_front.py, żeby komunikat
   błędu pokazywał zmierzoną wartość, a nie samo "false". */

const checks = {};

// Rysujemy jak po wyszukaniu: z kadrowaniem, więc grupki numerów liczą się
// przy tym samym powiększeniu, które zobaczyłby użytkownik.
app.drawFlow(FLOW_FIXTURE, true);

const hits = app.flowHits;
const timed = hits.filter(h => h.seg.stops_t && h.seg.stops_t.length >= 2);

// --- narzędzia -------------------------------------------------------------

/** Odległość punktu od łamanej [m] - płaskie przybliżenie, na dystansach
    jednego kawałka trasy błąd jest poniżej centymetra. */
function metersToPolyline(point, latlngs) {
    const rad = Math.PI / 180;
    const mLat = 111320, mLng = 111320 * Math.cos(point.lat * rad);
    const xy = p => [(p.lng - point.lng) * mLng, (p.lat - point.lat) * mLat];
    let best = Infinity;
    for (let i = 1; i < latlngs.length; i++) {
        const [ax, ay] = xy(latlngs[i - 1]), [bx, by] = xy(latlngs[i]);
        const dx = bx - ax, dy = by - ay;
        const lenSq = dx * dx + dy * dy;
        const t = lenSq > 0 ? Math.max(0, Math.min(1, (-ax * dx - ay * dy) / lenSq)) : 0;
        best = Math.min(best, Math.hypot(ax + t * dx, ay + t * dy));
    }
    return best;
}

function boxOf(marker) {
    const at = app.map.latLngToContainerPoint(marker.getLatLng());
    const [w, h] = app.clusterBox(marker.roster);
    return [at.x - w / 2, at.y - h / 2, at.x + w / 2, at.y + h / 2];
}

function overlaps(a, b) {
    return a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3];
}

function countOverlaps(markers) {
    const boxes = markers.map(boxOf);
    let n = 0;
    const worst = [];
    for (let i = 0; i < boxes.length; i++) {
        for (let j = i + 1; j < boxes.length; j++) {
            if (overlaps(boxes[i], boxes[j])) {
                n++;
                if (worst.length < 5) {
                    worst.push(app.corridorKey(markers[i].roster) + ' / '
                               + app.corridorKey(markers[j].roster));
                }
            }
        }
    }
    return {n, worst};
}

// --- punkt 8: najbledszy kawałek wciąż widoczny ----------------------------

checks.p8_skala_jasnosci = (() => {
    const look = app.look;
    const opac = [0, 0.25, 0.5, 0.75, 1].map(app.lookOpacity);
    const rosnie = opac.every((v, i) => i === 0 || v >= opac[i - 1]);
    return {
        ok: app.lookOpacity(0) === look.minOpacity
            && look.minOpacity >= 0.3 && app.lookWeight(0) >= 2 && rosnie,
        minOpacity: app.lookOpacity(0),
        maxOpacity: app.lookOpacity(1),
        minWeight: app.lookWeight(0),
        rosnaca: rosnie,
    };
})();

checks.p8_nic_narysowane_nie_jest_niewidoczne = (() => {
    // Same linie przepływu (bez białych otoczek - te mają własne, stałe krycie).
    const lines = hits.map(h => h.layer.options);
    const minOp = Math.min(...lines.map(o => o.opacity));
    const minW = Math.min(...lines.map(o => o.weight));
    return {
        ok: minOp >= app.look.minOpacity && minOp >= 0.3 && minW >= 2,
        kawalkow: lines.length,
        najmniejsze_krycie: minOp,
        najmniejsza_grubosc: minW,
        najbledszy_w: Math.min(...hits.map(h => h.seg.w)),
    };
})();

// --- punkt 10: godziny na mapie -------------------------------------------

checks.p10_godzina_na_przystanku_jest_z_rozkladu = (() => {
    let worst = 0, sprawdzonych = 0;
    for (const h of timed) {
        app.ensurePathMetrics(h.seg, h.latlngs);
        const at = h.seg._stopAt, stops = h.seg.stops_t;
        if (!at || at.length !== stops.length) continue;
        for (let i = 0; i < stops.length; i++) {
            const got = app.timeAtPos(h.seg, at[i]);
            if (got === null) continue;
            worst = Math.max(worst, Math.abs(got - stops[i][2]));
            sprawdzonych++;
        }
    }
    return {ok: sprawdzonych > 0 && worst === 0, przystankow: sprawdzonych,
            najwiekszy_blad_s: worst};
})();

checks.p10_kotwice_przystankow_rosna = (() => {
    let zle = 0, kawalkow = 0;
    for (const h of timed) {
        app.ensurePathMetrics(h.seg, h.latlngs);
        const at = h.seg._stopAt || [];
        kawalkow++;
        for (let i = 1; i < at.length; i++) if (at[i] < at[i - 1]) zle++;
    }
    return {ok: kawalkow > 0 && zle === 0, kawalkow, cofniec: zle};
})();

checks.p10_godzina_rosnie_wzdluz_linii = (() => {
    let zle = 0, probek = 0;
    const SAMPLES = 60;
    for (const h of timed) {
        app.ensurePathMetrics(h.seg, h.latlngs);
        const cum = h.seg._cum;
        const total = cum[cum.length - 1];
        if (!(total > 0)) continue;
        let prev = null;
        for (let k = 0; k <= SAMPLES; k++) {
            const t = app.timeAtPos(h.seg, total * k / SAMPLES);
            if (t === null) continue;
            probek++;
            if (prev !== null && t < prev - 1e-6) zle++;
            prev = t;
        }
    }
    return {ok: probek > 0 && zle === 0, probek, cofniec: zle};
})();

checks.p10_kropka_lezy_na_linii = (() => {
    let worst = 0, sprawdzonych = 0, bezczasu = 0;
    for (const h of timed.slice(0, 25)) {
        const mid = h.latlngs[Math.floor(h.latlngs.length / 2)];
        const point = app.map.latLngToContainerPoint(mid);
        const when = app.timeAtHover(h, point);
        if (!when || !when.at) { bezczasu++; continue; }
        worst = Math.max(worst, metersToPolyline(when.at, h.latlngs));
        sprawdzonych++;
    }
    return {ok: sprawdzonych > 0 && worst < 1.0, sprawdzonych, bezczasu,
            najdalej_od_linii_m: worst};
})();

// --- punkt 7: zawsze wiadomo, co tam jedzie -------------------------------

const markers = (app.flowLabelLayer && app.flowLabelLayer.layers) || [];

checks.p7_sa_grupki_numerow = {
    ok: markers.length > 0,
    grupek: markers.length,
    kawalkow_w_kadrze: hits.filter(h => h.latlngs.some(p => app.map.getBounds().contains(p))).length,
};

checks.p7_grupki_nie_nachodza_na_siebie = (() => {
    const res = countOverlaps(markers);
    return {ok: res.n === 0, grupek: markers.length, kolizji: res.n, przyklady: res.worst};
})();

checks.p7_kursor_nad_numerem_wskazuje_dokladnie_te_linie = (() => {
    let sprawdzonych = 0, pomylek = 0, bez_kawalka = 0;
    const przyklady = [];
    for (const marker of markers) {
        for (let i = 0; i < marker.roster.length; i++) {
            app.pickFromCluster(marker, i);
            const pick = app.flowPick;
            if (!pick) { bez_kawalka++; continue; }
            const wskazana = pick.options[pick.index];
            const chciana = marker.roster[i];
            sprawdzonych++;
            if (!wskazana || wskazana.num !== chciana.num || wskazana.kind !== chciana.kind) {
                pomylek++;
                if (przyklady.length < 5) {
                    przyklady.push((chciana.kind + ' ' + chciana.num) + ' -> '
                        + (wskazana ? wskazana.kind + ' ' + wskazana.num : 'nic'));
                }
            }
        }
    }
    return {ok: sprawdzonych > 0 && pomylek === 0, sprawdzonych, pomylek,
            grupek_bez_kawalka_pod_spodem: bez_kawalka, przyklady};
})();

checks.p7_kazda_grupka_opisuje_narysowany_korytarz = (() => {
    let puste = 0, bez_zadnego_kawalka = 0;
    for (const marker of markers) {
        if (!marker.roster.length) { puste++; continue; }
        const at = app.map.latLngToContainerPoint(marker.getLatLng());
        const tu = app.flowHitsAt(at);
        if (!tu.length) bez_zadnego_kawalka++;
    }
    return {ok: puste === 0 && bez_zadnego_kawalka === 0,
            grupek: markers.length, puste, bez_zadnego_kawalka};
})();

// --- regresja: przełącznik naprawdę zmienia rysunek ------------------------

checks.przelacznik_czasu_zmienia_grupki = (() => {
    const roster = markers.length ? markers[0].roster : [{num: '133', kind: 'bus'}];
    const bez = app.clusterBox(roster);
    app.timeOpts.chips = true;
    const zCzasem = app.clusterBox(roster);
    app.drawFlow(FLOW_FIXTURE, false);
    const zNowymi = (app.flowLabelLayer && app.flowLabelLayer.layers) || [];
    const kolizje = countOverlaps(zNowymi);
    app.timeOpts.chips = false;
    app.drawFlow(FLOW_FIXTURE, false);
    return {
        ok: zCzasem[0] > bez[0] && zCzasem[1] > bez[1]
            && zNowymi.length > 0 && kolizje.n === 0,
        bez_czasu: bez, z_czasem: zCzasem,
        grupek_z_czasem: zNowymi.length, kolizji_z_czasem: kolizje.n,
    };
})();

/* Mapa z połączeniami + pusta lista obok = wyjaśnienie, nie awaria. Nade
   wszystko: żadnego "zawęź okno" - to dokładne odwrócenie tego, co użytkownik
   robi przyciskiem "+X min". Pusta mapa to co innego i dalej jest błędem. */
checks.pelna_mapa_bez_listy_nie_jest_bledem = (() => {
    app.renderPlan({...FLOW_FIXTURE, journeys: []}, false);
    const zMapa = app.resultsBox.innerHTML;

    app.renderPlan({...FLOW_FIXTURE, journeys: [], segments: []}, false);
    const bezMapy = app.resultsBox.innerHTML;

    app.renderPlan(FLOW_FIXTURE, false);   // stan z fixture'a wraca na miejsce
    return {
        ok: !zMapa.includes('notice error')       // nie czerwone
            && !zMapa.toLowerCase().includes('zawęź')
            && zMapa.includes('notice')
            && bezMapy.includes('notice error')   // pusta mapa to nadal błąd
            && bezMapy.includes('Nie znaleziono'),
        zMapa: zMapa.slice(0, 200), bezMapy: bezMapy.slice(0, 160),
    };
})();

// --- tryb awaryjny widoczny na ekranie ------------------------------------

checks.tryb_awaryjny_mowi_o_sobie_na_ekranie = (() => {
    // Zwykła odpowiedź: żadnego ostrzeżenia.
    app.renderPlan({...FLOW_FIXTURE, degraded: false}, false);
    const zwykla = app.resultsBox.innerHTML;

    // Ta sama odpowiedź oznaczona jako awaryjna: ostrzeżenie NAD listą,
    // w tym samym stylu co pozostałe komunikaty błędów, a lista zostaje.
    app.renderPlan({...FLOW_FIXTURE, degraded: true}, false);
    const awaryjna = app.resultsBox.innerHTML;

    const ma = awaryjna.indexOf('notice error') >= 0
        && awaryjna.toLowerCase().indexOf('awaryjny') >= 0;
    const na_gorze = awaryjna.indexOf('notice error') === 0
        || awaryjna.indexOf('notice error') < awaryjna.indexOf('journey');
    return {
        ok: ma && na_gorze
            && zwykla.toLowerCase().indexOf('awaryjny') < 0
            && awaryjna.length > 0,
        zwykla_ma_ostrzezenie: zwykla.toLowerCase().indexOf('awaryjny') >= 0,
        awaryjna_ma_ostrzezenie: ma,
        ostrzezenie_nad_lista: na_gorze,
    };
})();

// --- kropki przystanków na trasie i ich tablica odjazdów -------------------

/* Legs zbudowane tu na miejscu, nie z fixture'a: kropka pyta o `arr_sec`,
   a to pole młodsze od zapisanej odpowiedzi - fixture i tak testuje co
   innego (mapę przepływów), więc nie ma po co go pod to przestawiać. */
const LEGS = [
    {kind: 'ride', mode: 'bus', num: '134', from: 'Sosnowiecka', to: 'Bardzka',
     dep_sec: 57180, arr_sec: 57960, path: [[51.08, 17.06], [51.09, 17.05], [51.10, 17.04]]},
    {kind: 'walk', minutes: 3, path: [[51.10, 17.04], [51.10, 17.041]]},
    {kind: 'ride', mode: 'tram', num: '5', from: 'Bardzka', to: 'RYNEK',
     dep_sec: 58080, arr_sec: 58560, path: [[51.10, 17.041], [51.11, 17.03]]},
];

const dotsOf = layers => layers.filter(l => l.kind === 'circleMarker');

checks.stop_dots = (() => {
    const dots = dotsOf(app.legLayers(LEGS, {preview: false}));
    // Po jednej na wsiadanie i wysiadanie każdego z dwóch przejazdów.
    const zTooltipem = dots.filter(d => d._tooltip);
    const doNajechania = dots.filter(d => d.options.interactive !== false);
    return {
        ok: dots.length === 4 && zTooltipem.length === 4 && doNajechania.length === 4,
        dots: dots.length,
        withTooltip: zTooltipem.length,
        hoverable: doNajechania.length,
    };
})();

checks.stop_dots_only_when_drawn = (() => {
    // Podgląd pod kursorem na liście nie ma kropek do najechania - myszka
    // jest wtedy nad kartą, nie nad mapą, a warstwa i tak zaraz znika.
    const dots = dotsOf(app.legLayers(LEGS, {preview: true}));
    return {ok: dots.length === 0, dots: dots.length};
})();

/* Ostatnie miejsca idą w podpowiedziach pierwsze, od najświeższego (#142);
   reszta zostaje w swojej kolejności. */
checks.podpowiedzi_ostatnie_miejsca_pierwsze = (() => {
    const items = ['PL. LEGIONÓW', 'PL. GRUNWALDZKI', 'PL. JANA PAWŁA II', 'PL. BEMA']
        .map(name => ({name, at: 0, len: 1}));
    const order = app.recentFirst(items, ['PL. BEMA', 'KRZYKI', 'PL. GRUNWALDZKI'])
        .map(item => item.name);
    return {
        ok: order.join('|') === 'PL. BEMA|PL. GRUNWALDZKI|PL. LEGIONÓW|PL. JANA PAWŁA II',
        order,
    };
})();

/* Za dużo godzin w wierszu: z szarych zostaje ostatnia, godziny na czas
   mają pierwszeństwo, a nadmiar zwija się do "… do" ostatniego kursu -
   z godziną tylko wtedy, gdy lista sięga końca mapy. */
checks.tablica_zwija_nadmiar_godzin = (() => {
    const dep = sec => ({time: '00:00', sec, num: '310',
                         mode: 'bus', headsign: 'Strachowskiego'});
    const secs = [55980, 57180, 57720, 58320, 58920, 59520, 60120, 60720, 61320];
    const data = {stop: 'Lutosławskiego', from_time: '15:33',
                  departures: secs.map(dep)};
    const bylo = app.dotOpts.ttPast;
    app.dotOpts.ttPast = true;
    const bezHoryzontu = app.timetableHtml(data, 58000);       // jesteś 16:06
    const zHoryzontem = app.timetableHtml({...data, horizon: 61320}, 58000);
    app.dotOpts.ttPast = bylo;
    return {
        ok: (zHoryzontem.match(/tt-past/g) || []).length === 1
            && zHoryzontem.includes('class="tt-past">16:02')
            && !zHoryzontem.includes('15:33') && !zHoryzontem.includes('15:53')
            && zHoryzontem.includes('16:12') && zHoryzontem.includes('16:42')
            && !zHoryzontem.includes('16:52') && zHoryzontem.includes('… do 17:02')
            && bezHoryzontu.includes('tt-until">… </span>')
            && !bezHoryzontu.includes('do 17:02'),
        zHoryzontem,
    };
})();

/* Tablica domyślna: jeden wiersz na linię i kierunek, w nim godziny po kolei;
   bez "za ile", "co N min" i godziny w nagłówku. Godziny sprzed chwili
   z mapy są szare, a linia, na którą się już nie zdąży, idzie na koniec. */
checks.tablica_godziny_w_wierszu_linii = (() => {
    const dep = (sec, num, headsign) => ({time: '00:00', sec, num,
                                          mode: 'bus', headsign});
    const data = {stop: 'Park Wschodni', from_time: '22:27', departures: [
        dep(80820, '134', 'KSIĘŻE WIELKIE'),     // 22:27 - przed chwilą z mapy
        dep(81720, '114', 'Zajezdnia TYSKA'),    // 22:42
        dep(82740, '114', 'Zajezdnia TYSKA'),    // 22:59
        dep(81000, '5', 'KSIĘŻE MAŁE'),          // 22:30 - przed
        dep(84000, '5', 'KSIĘŻE MAŁE'),          // 23:20
    ]};
    const html = app.timetableHtml(data, 81600);   // według mapy jesteś 22:40
    const wiersze = html.match(/<li>.*?<\/li>/g) || [];
    // Bez szarych godzin tablica zaczyna się od chwili z mapy - linia, która
    // miała tylko wcześniejsze, znika.
    const bylo = app.dotOpts.ttPast;
    app.dotOpts.ttPast = false;
    const bezSzarych = app.timetableHtml(data, 81600);
    app.dotOpts.ttPast = bylo;
    return {
        ok: wiersze.length === 3
            && wiersze[0].includes('>114<') && wiersze[0].includes('22:42')
            && wiersze[0].includes('22:59')
            && wiersze[1].includes('>5<')
            && wiersze[1].includes('class="tt-past">22:30')
            && wiersze[1].includes('23:20')
            && wiersze[2].includes('>134<')          // już się nie zdąży - na koniec
            && !html.includes('min') && !html.includes('tt-from')
            && !bezSzarych.includes('tt-past') && !bezSzarych.includes('>134<')
            && bezSzarych.includes('23:20'),
        wiersze,
    };
})();

checks.timetable_html_empty = (() => {
    const html = app.timetableHtml({stop: 'Pętla', from_time: '23:59', departures: []});
    return {ok: html.includes('Pętla') && html.includes('tt-note'), html};
})();

/* Kursor nad kropką ma wyłączać dymek "tu jesteś" z mapy przepływów: kropka
   leży na narysowanej linii, więc bez pierwszeństwa oba dymki wychodzą jeden
   na drugim (widać to było na wąskim ekranie). */
checks.pierwszenstwo_kropki_nad_dymkiem_przeplywow = (() => {
    app.drawFlow(FLOW_FIXTURE, false);

    // Punkt, w którym naprawdę coś narysowano - inaczej sprawdzenie
    // przechodziłoby na pusto.
    let point = null;
    for (const h of app.flowHits) {
        const at = app.map.latLngToContainerPoint(h.latlngs[0]);
        if (app.flowHitsAt(at).length) { point = at; break; }
    }
    if (!point) return {ok: false, powod: 'nie ma w co trafić kursorem'};

    const najedz = () => app.handleFlowHover({
        containerPoint: point,
        latlng: app.map.containerPointToLatLng(point),
        originalEvent: {target: null},
    });

    najedz();
    const bezKropki = !!app.flowPick;

    // ...a teraz to samo miejsce, tyle że kursor wszedł na kropkę
    const dot = app.legLayers(LEGS, {preview: false})
                   .filter(l => l.kind === 'circleMarker')[0];
    dot.fire('mouseover');
    najedz();
    const zKropka = !!app.flowPick;

    dot.fire('mouseout');
    najedz();
    const poZejsciu = !!app.flowPick;

    return {
        ok: bezKropki && !zKropka && poZejsciu,
        dymek_bez_kropki: bezKropki,
        dymek_gdy_kursor_na_kropce: zKropka,
        dymek_po_zejsciu_z_kropki: poZejsciu,
    };
})();

/* Ten sam punkt ma dawać ZAWSZE tę samą godzinę. Dwa kursy tej samej linii
   leżą na mapie jeden na drugim, a hity są posortowane po pikselach - branie
   pierwszego z brzegu sprawiało, że drgnięcie kursora przestawiało "tu jesteś"
   o kwadrans (zgłoszone 2026-08-29). Wygrywa kurs z najwcześniejszym "u celu". */
checks.ten_sam_punkt_ta_sama_godzina = (() => {
    const hit = (num, arrive, dist) => ({dist, seg: {num, kind: 'bus', arrive}});
    // kolejność jak z flowHitsAt: rosnąco po odległości w pikselach
    const hits = [hit('102', 51000, 0.5), hit('102', 49800, 1.4), hit('9', 40000, 0.9)];

    const wybrany = app.hitFor(hits, '102', 'bus');
    // ...a teraz to samo, tylko kursor drgnął i kolejność się odwróciła
    const odwrotnie = app.hitFor(
        [hits[1], hits[0], hits[2]].map(h => ({...h})), '102', 'bus');

    return {
        ok: wybrany.seg.arrive === 49800
            && odwrotnie.seg.arrive === 49800
            && app.hitFor(hits, '9', 'bus').seg.arrive === 40000
            && app.hitFor(hits, '77', 'bus') === null,
        wybrany: wybrany.seg.arrive,
        po_drgnieciu: odwrotnie.seg.arrive,
    };
})();

/* Kawałek bez odczytanej godziny u celu nie ma prawa wygrać z takim, który ją
   ma - inaczej dymek traciłby liczbę, którą wcześniej pokazywał. */
checks.kawalek_bez_godziny_nie_wygrywa = (() => {
    const hit = (arrive, dist) => ({dist, seg: {num: '5', kind: 'tram', arrive}});
    const zPrzodu = app.hitFor([hit(undefined, 0.2), hit(52000, 1.1)], '5', 'tram');
    const zTylu = app.hitFor([hit(52000, 0.2), hit(undefined, 1.1)], '5', 'tram');
    const zadenNieMa = app.hitFor([hit(undefined, 0.2), hit(undefined, 1.1)], '5', 'tram');
    return {
        ok: zPrzodu.seg.arrive === 52000 && zTylu.seg.arrive === 52000
            && zadenNieMa !== null,
        z_przodu: zPrzodu.seg.arrive, z_tylu: zTylu.seg.arrive,
    };
})();

/* Kropki wachlarza: po jednej na węzeł z backendu, każda do najechania. */
checks.kropki_wachlarza = (() => {
    const dots = app.flowStopDots([
        {name: 'PILCZYCE', lat: 51.13, lon: 16.95, sec: 48720,
         lines: [{num: '3', kind: 'tram', headsign: 'KSIĘŻE MAŁE'}]},
        {name: 'Rondo', lat: 51.11, lon: 17.01, sec: 49000, lines: []},
    ]);
    return {
        ok: dots.length === 2 && dots.every(d => d._tooltip),
        kropek: dots.length,
        z_tooltipem: dots.filter(d => d._tooltip).length,
    };
})();

/* Kropka waży tyle, co to, co przy niej leży: krycie idzie z jasności węzła
   przez tę samą skalę, co krycie linii. Start jest wyjątkiem - to nie jedna
   z opcji, tylko miejsce, w którym stoisz (zgłoszone 2026-09-04). */
checks.kropka_bierze_jasnosc_z_otoczenia = (() => {
    const dots = app.flowStopDots([
        {name: 'Jasny', lat: 51.13, lon: 16.95, sec: 48720, lines: [], w: 1},
        {name: 'Blady', lat: 51.11, lon: 17.01, sec: 49000, lines: [], w: 0},
        {name: 'Start', lat: 51.10, lon: 17.00, sec: 48000, lines: [],
         w: 0, start: true},
    ]);
    const [jasny, blady, start] = dots.map(d => d.options.opacity);
    return {
        ok: jasny === app.lookOpacity(1) && blady === app.lookOpacity(0)
            && blady < jasny && start === app.lookOpacity(1)
            && dots.every(d => d.options.fillOpacity === d.options.opacity),
        jasny, blady, start,
    };
})();

/* Tablica pokazuje tylko to, w co MAPA pozwala tu wsiąść - z kierunkiem,
   bo ta sama linia mija węzeł w obie strony (zgłoszone 2026-08-29:
   dymek na Pilczycach wypisywał tramwaj jadący tam, skąd się przyjechało). */
checks.tablica_tylko_to_co_mapa_oferuje = (() => {
    const dep = (num, mode, headsign) => ({time: '13:32', num, mode, headsign});
    const data = {stop: 'PILCZYCE', from_time: '13:32', departures: [
        dep('3', 'tram', 'KSIĘŻE MAŁE'),
        dep('3', 'tram', 'LEŚNICA'),      // ta sama trójka, druga strona
        dep('152', 'bus', 'BLACHARSKA'),  // mapa jej stąd nie proponuje
        dep('20', 'tram', 'LEŚNICA'),
    ]};
    const lines = [{num: '3', kind: 'tram', headsign: 'KSIĘŻE MAŁE'},
                   {num: '20', kind: 'tram', headsign: 'LEŚNICA'}];
    const zostalo = app.keepOfferedLines(data, lines).departures;
    const bezFiltra = app.keepOfferedLines(data, null).departures;
    return {
        ok: zostalo.length === 2
            && zostalo[0].headsign === 'KSIĘŻE MAŁE'
            && zostalo[1].num === '20'
            && bezFiltra.length === 4,
        zostalo: zostalo.map(d => d.num + '→' + d.headsign),
    };
})();

/* Mocna wersja "tylko to, co jeszcze zdąży": nie "czy odjazd mieści się
   w oknie mapy" (warunek konieczny), tylko "czy TYM kursem w ogóle się
   dojedzie" - serwer podaje przy linii ostatni taki odjazd (depart_by,
   patrz planner._line_deadlines). */
checks.odjazd_ktorym_sie_nie_zdazy_wypada = (() => {
    const dep = (sec, num, headsign) => ({time: '00:00', sec, num, mode: 'tram', headsign});
    const data = {stop: 'PILCZYCE', from_time: '17:00', departures: [
        dep(61200, '3', 'KSIĘŻE MAŁE'),   // 17:00 - zdąży
        dep(61800, '3', 'KSIĘŻE MAŁE'),   // 17:10 - ostatni, który zdąży
        dep(62400, '3', 'KSIĘŻE MAŁE'),   // 17:20 - już nie
        dep(62400, '20', 'OPORÓW'),       // inna linia, inny termin - zdąży
    ]};
    const lines = [
        {num: '3', kind: 'tram', headsign: 'KSIĘŻE MAŁE', depart_by: 61800},
        {num: '20', kind: 'tram', headsign: 'OPORÓW', depart_by: 63000},
    ];
    const zostalo = app.keepOfferedLines(data, lines).departures;
    // Bez depart_by (np. odpowiedź z cache'u sprzed zmiany) nie wycinamy nic -
    // brak liczby nie jest powodem do gubienia wierszy.
    const bezTerminu = app.keepOfferedLines(data, lines.map(
        ({num, kind, headsign}) => ({num, kind, headsign}))).departures;
    return {
        ok: zostalo.length === 3 && !zostalo.some(d => d.num === '3' && d.sec === 62400)
            && bezTerminu.length === 4,
        zostalo: zostalo.map(d => d.num + '@' + d.sec),
    };
})();

/* Liczba wierszy to ustawienie panelu, a nie stała wpisana w kod - i ma swój
   sufit, bo w pamięci przeglądarki może leżeć wartość z czasów innego zakresu
   (albo w ogóle nie liczba). */
checks.liczba_wierszy_to_ustawienie = (() => {
    const bylo = app.dotOpts.rows;
    const odczyt = [];
    for (const ile of [3, 8, 999, 0, 'iks']) {
        app.dotOpts.rows = ile;
        odczyt.push(app.timetableRows());
    }
    app.dotOpts.rows = bylo;
    return {
        ok: odczyt[0] === 3 && odczyt[1] === 8
            && odczyt[2] === app.TIMETABLE_ROWS_MAX && odczyt[3] === 1
            && odczyt[4] === app.DOT_DEFAULTS.rows,
        odczyt,
    };
})();

/* Suwak naprawdę przycina tablicę - nie tylko zmienia liczbę w ustawieniach. */
checks.suwak_przycina_tablice = (() => {
    const dep = min => ({time: '00:0' + min, sec: min * 60, num: String(min),
                         mode: 'bus', headsign: 'PRACZE'});
    const data = {stop: 'Halicka', from_time: '14:21',
                  departures: [1, 2, 3, 4, 5].map(dep)};
    const bylo = app.dotOpts.rows;
    app.dotOpts.rows = 2;
    const krotka = (app.timetableHtml(data).match(/<li>/g) || []).length;
    app.dotOpts.rows = 5;
    const dluga = (app.timetableHtml(data).match(/<li>/g) || []).length;
    app.dotOpts.rows = bylo;
    return {ok: krotka === 2 && dluga === 5, krotka, dluga};
})();

/* Odjazd po zamknięciu okna mapy nie należy do żadnego rysowanego wariantu.
   Warunek konieczny, nie wystarczający - mocniejszy odsiew wymagałby godzin
   przyjazdu kawałków, a te bywają niemożliwe (patrz punkt 11 kontraktu). */
checks.odjazdy_za_horyzontem_wypadaja = (() => {
    const dep = sec => ({time: '00:00', sec, num: '107',
                         mode: 'bus', headsign: 'PRACZE'});
    const data = {stop: 'Halicka', from_time: '14:21',
                  departures: [dep(51660), dep(52860), dep(53460), dep(55260)]};
    const zostalo = app.keepWithinHorizon(data, 52860).departures;
    const bezHoryzontu = app.keepWithinHorizon(data, null).departures;
    return {
        // 52860 to sam horyzont - mieści się, dopiero późniejsze wypadają
        ok: zostalo.length === 2 && bezHoryzontu.length === 4,
        zostalo: zostalo.map(d => d.sec),
    };
})();


// --- dźwięk spadającej rury ------------------------------------------------

function nagrajDzwiek(fn) {
    const przed = {zagrane: audioLog.zagrane, przewiniete: audioLog.przewiniete};
    fn();
    return {
        zagrane: audioLog.zagrane - przed.zagrane,
        przewiniete: audioLog.przewiniete - przed.przewiniete,
        zrodlo: audioLog.ostatni,
    };
}

checks.dzwiek_wybiera_format_ktory_przegladarka_umie = (() => {
    // Emulator udaje przeglądarkę bez Ogg Opus, ale z AAC - czyli Safari.
    // Ma sięgnąć po drugi plik, a nie po pierwszy z listy.
    app.soundOpts.pipe = true;
    const w = nagrajDzwiek(() => app.playPipeDrop());
    return {
        ok: w.zagrane === 1 && /metal-pipe\.m4a$/.test(w.zrodlo || ''),
        zrodlo: w.zrodlo,
        formaty: app.PIPE_SOURCES.map(s => s[0]),
    };
})();

checks.dzwiek_gra_od_poczatku_przy_powtorzeniu = (() => {
    // Drugie wyszukiwanie w trakcie pierwszego dźwięku ma zagrać od nowa,
    // a nie zostać po cichu pominięte.
    app.soundOpts.pipe = true;
    const w = nagrajDzwiek(() => { app.playPipeDrop(); app.playPipeDrop(); });
    return {ok: w.zagrane === 2 && w.przewiniete === 2, ...w};
})();

checks.dzwiek_milczy_przy_ograniczonym_ruchu = (() => {
    app.soundOpts.pipe = true;
    globalThis.__mniejRuchu = true;
    const w = nagrajDzwiek(() => app.playPipeDrop());
    globalThis.__mniejRuchu = false;
    return {ok: w.zagrane === 0, ...w};
})();

checks.dzwiek_milczy_gdy_wylaczony = (() => {
    app.soundOpts.pipe = false;
    const w = nagrajDzwiek(() => app.playPipeDrop());
    app.soundOpts.pipe = true;
    return {ok: w.zagrane === 0, ...w};
})();

checks.dzwiek_nie_zatrzymuje_muzyki = (() => {
    // Domyślna sesja dźwięku na iPhonie zatrzymuje Spotify i podcasty.
    // "ambient" gra na muzyce, a muzyka leci dalej (#243).
    return {ok: navigator.audioSession.type === 'ambient',
            typ: navigator.audioSession.type};
})();

checks.nagranie_nie_gra_na_pelnej_glosnosci = (() => {
    // Nagranie ma szczyt ponad 0 dBFS - w pełnej głośności to alarm, nie żart.
    return {ok: app.PIPE_VOLUME > 0 && app.PIPE_VOLUME < 0.6, glosnosc: app.PIPE_VOLUME};
})();

/* --- kropki węzłów: gdzie stoją i która jest startowa ------------------- */

/* Węzeł to jedno MIEJSCE o kilku słupkach. Przełącznik wybiera między
   peronem (ten, z którego wzięta jest godzina) a środkiem wszystkich
   słupków - obie liczby przychodzą z backendu, front tylko sięga po jedną. */
checks.kropka_peron_albo_srodek = (() => {
    const node = {lat: 51.11422, lon: 17.05046, clat: 51.11368, clon: 17.05069};
    const bylo = app.dotOpts.center;

    app.dotOpts.center = false;
    const peron = app.nodePoint(node);
    app.dotOpts.center = true;
    const srodek = app.nodePoint(node);
    // Odpowiedź sprzed zmiany w plannerze nie ma clat - kropka ma wtedy
    // stanąć na peronie, a nie zniknąć z mapy na undefined.
    const stary = app.nodePoint({lat: 51.11422, lon: 17.05046});

    app.dotOpts.center = bylo;
    return {
        ok: peron[0] === node.lat && peron[1] === node.lon
            && srodek[0] === node.clat && srodek[1] === node.clon
            && stary[0] === node.lat && stary[1] === node.lon,
        peron, srodek, stary,
    };
})();

/* Kropka przystanku, z którego wyruszamy, jest rozpoznawana i zielona jak
   słupki startu, które zastępuje - okienko w rogu musi wiedzieć, od czyjego
   rozkładu zacząć. */
checks.kropka_startowa_rozpoznana = (() => {
    const nodes = [
        {name: 'PILCZYCE', lat: 51.13, lon: 16.95, sec: 48720, lines: [], start: true},
        {name: 'Rondo', lat: 51.11, lon: 17.01, sec: 49000, lines: []},
    ];
    const dots = app.flowStopDots(nodes);
    const zielona = dot => dot.options.color === '#1b5e20';
    return {
        ok: dots[0].isStart === true && dots[1].isStart === false
            && zielona(dots[0]) && !zielona(dots[1]),
        start: dots[0].isStart, drugi: dots[1].isStart,
    };
})();

/* Na wybranej trasie startu nie trzeba rozpoznawać w ogóle: to wsiadanie do
   pierwszego przejazdu. Kropka wysiadania z niego - już nie. */
checks.kropka_startowa_na_trasie = (() => {
    const dots = dotsOf(app.legLayers(LEGS, {preview: false}));
    return {
        ok: dots.filter(d => d.isStart).length === 1 && dots[0].isStart === true,
        startowych: dots.filter(d => d.isStart).length,
        pierwsza: dots[0].isStart,
    };
})();

/* Okienko w rogu otwiera się samo, z tablicą przystanku startowego - tak,
   jakby ktoś od razu najechał na jego kropkę. (fetch w emulatorze nigdy nie
   odpowiada, więc do okienka trafia stan "Ładowanie..." - to wystarcza, żeby
   sprawdzić, że w ogóle zostało zaadresowane.) */
checks.okienko_startuje_od_przystanku_startowego = (() => {
    const bylPanel = app.dotOpts.tipPanel;
    const nodes = [
        {name: 'PILCZYCE', lat: 51.13, lon: 16.95, sec: 48720, lines: [], start: true},
        {name: 'Rondo', lat: 51.11, lon: 17.01, sec: 49000, lines: []},
    ];
    const bezFlagi = nodes.map(n => ({...n, start: undefined}));

    app.dotOpts.tipPanel = false;
    app.flowPanel.hidden = true;
    app.drawFlow({...FLOW_FIXTURE, nodes}, false);
    const przyWylaczonym = app.flowPanel.hidden;

    app.dotOpts.tipPanel = true;
    app.drawFlow({...FLOW_FIXTURE, nodes}, false);
    const przyWlaczonym = app.flowPanel.hidden;
    const tresc = String(app.flowPanelBody.innerHTML || '');

    // Odpowiedź bez oznaczonego startu nie ma czego pokazać - okienko milczy.
    app.flowPanel.hidden = true;
    app.drawFlow({...FLOW_FIXTURE, nodes: bezFlagi}, false);
    const bezStartu = app.flowPanel.hidden;

    app.dotOpts.tipPanel = bylPanel;
    return {
        ok: przyWylaczonym === true && przyWlaczonym === false
            && tresc.length > 0 && bezStartu === true,
        przy_wylaczonym_schowane: przyWylaczonym,
        przy_wlaczonym_schowane: przyWlaczonym,
        bez_startu_schowane: bezStartu,
        tresc: tresc.slice(0, 60),
    };
})();


// --- trzy rzeczy, które mogą się tu dziać z linią (punkt 11) ---------------

/* Wiersz dostaje znak mówiący, CO SIĘ TU Z TĄ LINIĄ DZIEJE - i są trzy różne
   znaki, nie jeden na wszystko. Lewy koniec: kreska "stąd rusza", grot "już
   jedzie". Prawy: grot "jedzie dalej", kreska "tu koniec jazdy". */
checks.trzy_znaki_przeplywu = (() => {
    const znaki = ['start', 'through', 'end'].map(app.flowIcon);
    const nieznany = app.flowIcon(undefined);
    return {
        ok: znaki.every(h => h.includes('<svg') && h.includes('<title>'))
            && new Set(znaki).size === 3          // trzy RÓŻNE, nie trzy takie same
            && znaki[0].includes('tt-flow-start')
            && znaki[1].includes('tt-flow-through')
            && znaki[2].includes('tt-flow-end')
            // Nieznany przepływ nie zgaduje ikonki, ale zostawia kolumnę -
            // inaczej godziny w wierszach przestałyby stać w jednej osi.
            && !nieznany.includes('<svg') && nieznany.includes('tt-flow'),
        znaki: znaki.map(h => h.slice(0, 60)),
    };
})();

/* Linia, którą się tu tylko PRZYJEŻDŻA, dostaje własny wiersz - z godziny
   przyjazdu z węzła, bo w tablicy odjazdów przystanku jej nie ma. */
checks.przyjazdy_dokladaja_wiersze = (() => {
    const data = {stop: 'Bardzka', from_time: '16:00', departures: [
        {time: '16:04', sec: 57840, num: '3', mode: 'tram',
         headsign: 'LEŚNICA', flow: 'start'},
    ]};
    const lines = [
        {num: '3', kind: 'tram', headsign: 'LEŚNICA', flow: 'start'},
        {num: '107', kind: 'bus', headsign: 'PRACZE', flow: 'end', arrive: 57600},
        // "end" bez godziny przyjazdu nie ma czego pokazać - nie zmyślamy jej
        {num: '9', kind: 'tram', headsign: 'PARK', flow: 'end'},
    ];
    const wiersze = app.withArrivals(data, lines).departures;
    const przyjazd = wiersze.find(d => d.num === '107');
    const bezLinii = app.withArrivals(data, null).departures;
    return {
        ok: wiersze.length === 2 && bezLinii.length === 1
            && przyjazd.flow === 'end' && przyjazd.time === '16:00'
            && przyjazd.mode === 'bus',
        wiersze: wiersze.map(d => `${d.num}/${d.flow}@${d.time}`),
    };
})();

/* Linia "end" NIE jest ofertą do wsiadania - w tablicy odjazdów nie ma prawa
   zostać, bo wypisana z najbliższym odjazdem udaje opcję, której mapa nie
   proponuje. Jej wiersz dokłada withArrivals, i to z innej godziny. */
checks.przyjazd_nie_udaje_odjazdu = (() => {
    const dep = (sec, num, mode, headsign) => ({time: '00:00', sec, num, mode, headsign});
    const data = {stop: 'Bardzka', from_time: '16:00', departures: [
        dep(57840, '3', 'tram', 'LEŚNICA'),
        dep(58000, '107', 'bus', 'PRACZE'),   // ta linia tu tylko PRZYWOZI
    ]};
    const lines = [
        {num: '3', kind: 'tram', headsign: 'LEŚNICA', flow: 'through'},
        {num: '107', kind: 'bus', headsign: 'PRACZE', flow: 'end', arrive: 57600},
    ];
    const po = app.keepOfferedLines(data, lines);
    const pelne = app.withArrivals(po, lines).departures;
    return {
        ok: po.departures.length === 1
            && po.departures[0].num === '3' && po.departures[0].flow === 'through'
            && pelne.length === 2
            && pelne.find(d => d.num === '107').sec === 57600,
        odjazdy: po.departures.map(d => d.num + '/' + d.flow),
    };
})();

/* Kolumna ze znakiem pojawia się tylko tam, gdzie jest czym ją wypełnić:
   tablica pod kropką WYBRANEJ trasy pyta o cały przystanek i nie wie, co się
   tu z którą linią dzieje - pusta kolumna przesuwałaby jej wiersze bez powodu.
   Przyjazd stoi w kolejności czasowej, nie na końcu listy. */
checks.tablica_miesza_przyjazdy_z_odjazdami = (() => {
    const html = app.timetableHtml({stop: 'Bardzka', from_time: '16:00', departures: [
        {time: '16:04', sec: 57840, num: '3', mode: 'tram',
         headsign: 'LEŚNICA', flow: 'start'},
        {time: '16:00', sec: 57600, num: '107', mode: 'bus',
         headsign: 'PRACZE', flow: 'end'},
        {time: '16:09', sec: 58140, num: '20', mode: 'tram',
         headsign: 'OPORÓW', flow: 'through'},
    ]});
    const bezPrzeplywu = app.timetableHtml({stop: 'Bardzka', from_time: '16:00',
        departures: [{time: '16:04', sec: 57840, num: '3',
                      mode: 'tram', headsign: 'LEŚNICA'}]});
    return {
        ok: html.includes('has-flow')
            && html.includes('tt-flow-end') && html.includes('tt-flow-start')
            && html.includes('tt-flow-through')
            // przyjazd o 16:00 przed odjazdem o 16:04
            && html.indexOf('tt-flow-end') < html.indexOf('tt-flow-start')
            && !bezPrzeplywu.includes('has-flow')
            && !bezPrzeplywu.includes('<svg'),
        html: html.slice(0, 160),
    };
})();

/* Czekanie jest widoczne, nie schowane (punkt 13 kontraktu). Komunikat
   staje przy zmianie doby i przy czekaniu dłuższym niż 20 minut tego samego
   dnia; krótsze mówi sam pasek nad mapą (patrz pasek_mowi_kiedy_wyjechac). */
checks.czekanie_jest_widoczne = (() => {
    const jutro = app.waitNoticeHtml(
        {day_offset: 1, starts: '00:03', waits_sec: 240});
    const zaDwa = app.waitNoticeHtml(
        {day_offset: 2, starts: '05:10', waits_sec: 30 * 3600});
    const dzis = app.waitNoticeHtml(
        {day_offset: 0, starts: '12:30', waits_sec: 88 * 60});
    const zaraz = app.waitNoticeHtml(
        {day_offset: 0, starts: '11:10', waits_sec: 8 * 60});
    return {
        ok: jutro.includes('jutro') && jutro.includes('00:03')
            && zaDwa.includes('za 2 dni') && zaDwa.includes('05:10')
            && dzis.includes('Najszybszy dojazd wyjeżdża o 12:30')
            && dzis.includes('88 min')
            && !dzis.includes('nic już') && !dzis.includes('jutro')
            && zaraz === '',
        jutro, zaDwa, dzis, zaraz,
    };
})();

/* Pasek nad mapą: o której wyjechać i dojechać najszybszą trasą, "za ile"
   zawsze, sam czas jazdy tylko z ustawieniem, i zakres godzin mapy. */
checks.pasek_mowi_kiedy_wyjechac = (() => {
    const pasek = () => document.getElementById('time-headline').innerHTML
        .replace(/<[^>]+>/g, '');
    const flow = {...FLOW_FIXTURE, starts: '12:25', best_arrival: '12:40',
                  best_sec: 40 * 60, ride_sec: 15 * 60,
                  map_from: '12:00', deadline: '12:55', limit_sec: 55 * 60};
    const bylo = app.timeOpts.ride;

    app.timeOpts.ride = false;
    app.drawFlow(flow, false);
    const bez = pasek();
    app.timeOpts.ride = true;
    app.drawFlow(flow, false);
    const z = pasek();

    app.timeOpts.ride = bylo;
    app.drawFlow(FLOW_FIXTURE, false);   // mapa wraca do stanu z fixture'a
    return {
        ok: bez.includes('wyjeżdżasz o 12:25') && bez.includes('dojeżdżasz o 12:40')
            && bez.includes('za 40 min') && !bez.includes('jazda')
            && bez.includes('mapa od 12:00 do 12:55')
            && z.includes('za 40 min, jazda 15 min'),
        bez, z,
    };
})();

/* Grupa stacji miasta: na ekranie czytelna etykieta, na serwer nazwa
   kanoniczna. Para prettyStopName/rawStopName musi się znosić - inaczej
   ładna nazwa poleci do /api/flow i wróci jako "nie znaleziono przystanku"
   (błąd, przez który etykieta wróciła kiedyś do myślnika - patrz
   gtfs._match_city_group). */
checks.grupa_stacji_wraca_kanoniczna = (() => {
    const etykieta = app.prettyStopName('WROCŁAW -');
    const zwykly = app.prettyStopName('PL. GRUNWALDZKI');

    // Najważniejsze: to, co widzi użytkownik w polu, wychodzi na serwer
    // w postaci, którą rozumie wyszukiwarka.
    app.startInput.value = etykieta;
    app.endInput.value = 'Sosnowiecka';
    const params = app.queryParams();

    // Nazwa kanoniczna z odpowiedzi /api/flow wraca do pola jako etykieta.
    app.adoptNames({start: 'WROCŁAW -', end: 'Sosnowiecka'});
    const wPolu = app.startInput.value;

    return {
        ok: etykieta === 'WROCŁAW (dowolna stacja)'
            && zwykly === 'PL. GRUNWALDZKI'
            && app.rawStopName(etykieta) === 'WROCŁAW -'
            && app.rawStopName(zwykly) === 'PL. GRUNWALDZKI'
            && params.get('start') === 'WROCŁAW -'
            && params.get('end') === 'Sosnowiecka'
            && wPolu === 'WROCŁAW (dowolna stacja)',
        etykieta, zwykly, start: params.get('start'), end: params.get('end'), wPolu,
    };
})();

/* Przycisk "Pokaż więcej" przy pasku nad mapą (punkt 2): kliknięcie dokłada
   jedną wyjściową gęstość i leci do serwera jako `more` razem z gęstością
   z suwaka. Po trzecim kliknięciu przycisku nie ma - to zwykły licznik, bez
   pytania serwera, czy jest jeszcze co dołożyć. */
checks.pokaz_wiecej_doklada_gestosc = (() => {
    const pasek = () => document.getElementById('time-headline').innerHTML;

    app.drawFlow({...FLOW_FIXTURE, more: 0, at_ceiling: false}, false);
    const naStarcie = pasek();

    app.showMore();
    const zapytanie = app.queryParams().toString();

    app.drawFlow({...FLOW_FIXTURE, more: app.MAX_MAP_MORE, at_ceiling: false}, false);
    const poTrzecim = pasek();
    app.drawFlow({...FLOW_FIXTURE, more: 1, at_ceiling: true}, false);
    const przySuficie = pasek();
    app.drawFlow(FLOW_FIXTURE, false);   // mapa wraca do stanu z fixture'a

    return {
        ok: naStarcie.includes('headline-more')
            && app.mapMore === 1
            && zapytanie.includes('more=1')              // i to leci do serwera
            && zapytanie.includes('density=')            // razem z gęstością z suwaka
            && zapytanie.includes('cars=')               // i liczbą aut, którą też mnoży
            && !zapytanie.includes('horizon_sec')
            && !poTrzecim.includes('headline-more')      // po trzecim nie ma przycisku
            && przySuficie.includes('headline-more'),    // sufit serwera go nie chowa
        more: app.mapMore,
        zapytanie: zapytanie.slice(0, 200),
        naStarcie: naStarcie.slice(-200),
        poTrzecim: poTrzecim.slice(-200),
    };
})();

/* Warstwa żywych pojazdów (przycisk ◉) przy narysowanej mapie przepływów:
   pokazuje TYLKO linie, które są na tej mapie. Pojazd linii, której mapa nie
   rysuje, odpowiada na inne pytanie i ma jej nie zasłaniać; bez mapy nie ma
   czego zawężać i widać wszystko. */
checks.pojazdy_zawezone_do_linii_z_mapy = (function () {
    app.drawFlow(FLOW_FIXTURE, false);
    app.stopsLayer.addTo(app.map);      // w emulatorze /api/stops nie odpowiada
    const zMapy = app.flowHits[0].seg;                  // linia, którą mapa rysuje
    app.lastVehicles = [
        {line: zMapy.num, kind: zMapy.kind, lat: 51.10, lon: 17.00},
        {line: '999', kind: 'bus', lat: 51.11, lon: 17.02},   // spoza mapy
    ];

    // renderVehicles wprost: w emulatorze fetch nigdy nie odpowiada, więc samo
    // włączenie warstwy nie doczekałoby się rysowania.
    app.setVehiclesOn(true);
    app.renderVehicles();
    const przyMapie = app.vehiclesLayer.layers.map(m => m.options.icon.html);
    const slupki = app.map.hasLayer(app.stopsLayer);   // włącznik ich nie chowa

    // Zgaszona mapa = brak powodu do zawężania.
    app.clearFlow();
    app.renderVehicles();
    const bezMapy = app.vehiclesLayer.layers.length;
    const filtrBezMapy = app.vehiclesFilter();

    app.setVehiclesOn(false);
    app.drawFlow(FLOW_FIXTURE, false);   // mapa wraca do stanu z fixture'a

    return {
        ok: przyMapie.length === 1 && przyMapie[0] === zMapy.num
            && bezMapy === 2 && filtrBezMapy === null && slupki === true,
        linia: zMapy.num, przyMapie, bezMapy, filtrBezMapy, slupki,
    };
})();

/* Ostatni etap Traficarem (patrz planner._car_drive_leg). Front ma go
   narysować INACZEJ niż kurs: odcinek auto -> cel jest prostą, nie przebiegiem
   ulicami, więc kreskowana linia zamiast ciągłej i żadnej białej otoczki,
   którą dostają prawdziwe kursy. */

const DRIVE_LEGS = [
    {kind: 'ride', mode: 'tram', num: '5', from: 'Katedra', to: 'Krakowska',
     dep_sec: 50520, arr_sec: 51240, path: [[51.11, 17.04], [51.09, 17.05]]},
    {kind: 'walk', to_car: true, minutes: 2, metres: 145, from: 'Krakowska',
     to: 'ul. Testowa', path: [[51.09, 17.05], [51.089, 17.051]]},
    {kind: 'drive', mode: 'car', num: 'Traficar', line: 'Traficar KK08703',
     from: 'ul. Testowa', to: 'Iwiny', from_time: '14:21', to_time: '14:33',
     dep_sec: 51660, arr_sec: 52380, minutes: 12, km: 5, start_min: 5,
     plate: 'KK08703', model: 'Dacia Sandero', fuel: 26, range: 104,
     estimated: true, path: [[51.089, 17.051], [51.03, 17.07]]},
];

checks.traficar_jedzie_kreskowana_a_nie_jak_kurs = (() => {
    const layers = app.legLayers(DRIVE_LEGS, {preview: false});
    const linie = layers.filter(l => l.kind === 'polyline');
    const auto = linie.filter(l => l.options.dashArray === '10,8');
    const otoczki = linie.filter(l => l.options.color === '#fff');
    const plakietki = layers.filter(
        l => l.kind === 'marker' && (l.options.icon.className || '').includes('car'));
    return {
        // Jedna kreskowana linia auta, jedna plakietka przy niej - i tylko
        // JEDNA biała otoczka, ta od prawdziwego przejazdu tramwajem.
        ok: auto.length === 1 && otoczki.length === 1 && plakietki.length === 1
            && auto[0].options.color !== '#fff'
            && auto[0].options.dashArray !== undefined,
        kreskowanych: auto.length, otoczek: otoczki.length,
        plakietek: plakietki.length,
    };
})();

checks.traficar_widac_na_karcie_przed_rozwinieciem = (() => {
    const zAutem = {
        departure: '14:02', arrival: '14:33', duration_min: 31, wait_min: 2,
        transfers: 1, traficar: true, legs: DRIVE_LEGS,
    };
    app.renderPlan({...FLOW_FIXTURE, journeys: [zAutem]}, false);
    const html = app.resultsBox.innerHTML;
    return {
        ok: html.includes('badge car') && html.includes('Traficar')
            && html.includes('ostatni odcinek autem')
            // Łącznik kropkowany: między tramwajem a autem trzeba dojść.
            && html.includes('hop walk'),
        html: html.slice(html.indexOf('j-lines'), html.indexOf('j-lines') + 260),
    };
})();

/* Czas podróży kończącej się autem jest SZACUNKIEM i ma to być widać na
   pierwszej liczbie, którą się czyta - nie dopiero w rozwiniętej karcie. */
checks.traficar_czas_oznaczony_jako_szacunek = (() => {
    const zAutem = {
        departure: '14:02', arrival: '14:33', duration_min: 31, wait_min: 2,
        transfers: 1, traficar: true, legs: DRIVE_LEGS,
    };
    const zwykla = {...zAutem, traficar: false, legs: DRIVE_LEGS.slice(0, 1)};
    app.renderPlan({...FLOW_FIXTURE, journeys: [zAutem]}, false);
    const auto = app.resultsBox.innerHTML;
    app.renderPlan({...FLOW_FIXTURE, journeys: [zwykla]}, false);
    const bezAuta = app.resultsBox.innerHTML;

    // Rozwinięta karta mówi wprost, skąd te liczby - i podaje dystans też
    // z "ok.", bo jest obarczony tym samym szacunkiem co czas.
    const szczegoly = app.detailHtml(zAutem);
    return {
        ok: auto.includes('j-duration est') && auto.includes('ok. 31 min')
            // Zwykła trasa ma godziny z rozkładu i żadnego "ok." przy czasie.
            && !bezAuta.includes('j-duration est') && bezAuta.includes('>31 min<')
            && szczegoly.includes('auto nie ma rozkładu')
            && szczegoly.includes('ok. 12 min') && szczegoly.includes('ok. 5 km')
            && szczegoly.includes('ok. 5 min'),      // odbiór i start auta
        auto: auto.slice(auto.indexOf('j-duration'), auto.indexOf('j-duration') + 90),
        szczegoly: szczegoly.slice(szczegoly.indexOf('tl-info'),
                                   szczegoly.indexOf('tl-info') + 220),
    };
})();

/* Auto car-sharingu jest MIEJSCEM na mapie, nie kursem (kontrakt p.15):
   dostaje własny znacznik z godziną, o której się przy nim jest, i z samą
   odległością celu w linii prostej - a wachlarz wygląda dokładnie tak samo,
   jak bez aut. */
checks.auto_to_miejsce_a_nie_kurs = (() => {
    const auto = {
        lat: 51.09, lon: 17.02, plate: 'WE1AA11', model: 'Renault Clio',
        where: 'ul. Testowa', fuel: 80, range: 300, ogarniam: [],
        at: 56100, walk_sec: 420, walk_m: 300, from: 'Kamienna', to_dest_m: 2744,
    };
    app.drawFlow({...FLOW_FIXTURE, cars: [auto]}, false);
    const znaczniki = app.flowCarLayer.getLayers();
    const kawalkow_z_autem = app.flowParts.length;
    const tip = znaczniki.length ? znaczniki[0]._tooltip.content : '';

    app.drawFlow(FLOW_FIXTURE, false);
    return {
        ok: znaczniki.length === 1
            && kawalkow_z_autem === app.flowParts.length
            && tip.includes('15:35')            // o której jest się przy aucie
            && tip.includes('7 min')            // dojście - ta sama reguła, co każde
            && tip.includes('Kamienna')
            && tip.includes('2,7 km')           // do celu, w linii prostej
            && !/jazd|ok\./.test(tip)           // o samej jeździe mapa milczy
            && !tip.includes('Testowa')         // ulicy postoju nie pokazujemy
            // Odpowiedź bez aut nie zostawia po nich znacznika.
            && app.flowCarLayer.getLayers().length === 0,
        znacznikow: znaczniki.length,
        kawalkow_z_autem,
        kawalkow_bez_auta: app.flowParts.length,
        tip,
    };
})();

/* Program „Ogarniam": przy aucie, za które Traficar płaci, ma być widać ZA CO
   i ZA ILE - bez najeżdżania po kolei na wszystkie, stąd złota obwódka. Auto
   bez nagrody mówi to wprost, zamiast milczeć. */
checks.ogarniam_widac_na_aucie = (() => {
    const wspolne = {
        model: 'Renault Clio', fuel: 80, range: 300,
        at: 56100, walk_sec: 420, walk_m: 300, from: 'Kamienna', to_dest_m: 2744,
    };
    const zNagroda = {...wspolne, lat: 51.09, lon: 17.02, plate: 'WE1AA11',
                      ogarniam: [{co: 'Sprzątanie', ile: 30},
                                 {co: 'Tankowanie', ile: 15}]};
    const bezNagrody = {...wspolne, lat: 51.10, lon: 17.03, plate: 'WE2BB22',
                        ogarniam: []};
    app.drawFlow({...FLOW_FIXTURE, cars: [zNagroda, bezNagrody]}, false);
    const [zlote, zwykle] = app.flowCarLayer.getLayers();
    const tipZ = zlote._tooltip.content, tipBez = zwykle._tooltip.content;

    app.drawFlow(FLOW_FIXTURE, false);
    return {
        ok: tipZ.includes('Ogarniam: sprzątanie 30 zł · tankowanie 15 zł')
            && tipBez.includes('Ogarniam: nic do wzięcia')
            // Obwódka niesie tę samą wiadomość, co pierwszy wiersz dymka.
            && zlote.options.color === '#f9a825'
            && zwykle.options.color === app.CAR_STYLE.color,
        tipZ, tipBez,
        obwodki: [zlote.options.color, zwykle.options.color],
    };
})();

/* Rower miejski na mapie: kropkę dostaje wyłącznie miejsce, w którym da się
   WSIĄŚĆ na rower. Drugi koniec przejazdu pojawia się razem ze strzałką, pod
   kursorem - kresek jest kilkaset i narysowane naraz zasłaniają mapę. Godzin
   samego przejazdu nie pokazujemy: zostaje odległość, która mówi to samo, a
   nie udaje odczytanej z rozkładu. */
checks.rower_pokazuje_przejazdy_dopiero_pod_kursorem = (() => {
    app.setBikesOn(true);      // rower jest odtąd wyborem pasażera, nie domyślną warstwą
    const stacja = {
        id: 'A', name: 'Kozanowska', lat: 51.09, lon: 17.02, bikes: 7,
        electric: 2, docks: 9, loose: false, at: 56100, walk_sec: 420,
        walk_m: 300, from: 'Kamienna',
        rides: [{
            id: 'B', name: 'Legnicka (Park Magnolia)', lat: 51.10, lon: 17.03,
            bikes: 3, docks: 12, m: 1826, sec: 840, at: 56940,
            options: [{arrival: 58200, vehicles: 2}],
        }],
    };
    app.drawFlow({...FLOW_FIXTURE, bike_places: [stacja],
                  bike_places_live: true}, false);
    const kropki = app.flowBikeLayer.getLayers();
    const kawalkow_z_rowerem = app.flowParts.length;
    const przed = app.flowBikeRideLayer;
    kropki[0].fire('mouseover');
    const po = app.flowBikeRideLayer ? app.flowBikeRideLayer.getLayers() : [];
    const kreski = po.filter(l => l.kind === 'polyline');
    const tipStacji = kropki[0]._tooltip.content;
    const tipCelu = (po.find(l => l._tooltip && !l._tooltip.options.permanent)
                     || {_tooltip: {content: ''}})._tooltip.content;
    const etykieta = (po.find(l => l._tooltip && l._tooltip.options.permanent)
                      || {_tooltip: {content: ''}})._tooltip.content;
    kropki[0].fire('mouseout');
    const poZejsciu = app.flowBikeRideLayer;

    app.drawFlow(FLOW_FIXTURE, false);
    return {
        ok: kropki.length === 1                 // tylko to, na czym można siąść
            && przed === null                   // bez kursora żadnych kresek
            && kreski.length === 1              // pod kursorem - jedna, do celu
            && poZejsciu === null               // i znika razem z kursorem
            && kawalkow_z_rowerem === app.flowParts.length
            && tipStacji.includes('15:35')      // o której jest się PRZY rowerze
            && tipStacji.includes('7 min')      // dojście - ta sama reguła co zawsze
            && tipStacji.includes('Kamienna')
            && tipStacji.includes('7 rowerów (w tym 2 elektryczne)')
            // Zdania "ile przejazdów stąd" nie ma - kreski i tak to pokazują.
            && !/przejazd/.test(tipStacji)
            // Etykietka to SAMA odległość: godzina przejazdu jest policzona,
            // nie odczytana, więc jej nie pokazujemy.
            && etykieta.trim() === '1,8 km'
            // Drugi koniec ma własny dymek, też bez zgadywanej godziny.
            && tipCelu.includes('Legnicka (Park Magnolia)')
            && tipCelu.includes('1,8 km')
            && !/15:4|15:5/.test(tipCelu)
            && app.flowBikeLayer.getLayers().length === 0,
        kropek: kropki.length, kresek: kreski.length,
        tipStacji, tipCelu, etykieta,
    };
})();

/* Pytanie o inny dzień: stacja stoi tam zawsze, więc kropka zostaje - ale
   liczba rowerów jest z TEJ chwili, więc mapa mówi wprost, że jej nie zna,
   zamiast podać dzisiejszą jako jutrzejszą. */
checks.rower_na_inny_dzien_nie_udaje_ze_wie = (() => {
    app.setBikesOn(true);      // jw. - warstwa musi być zapalona
    const stacja = {
        id: 'A', name: 'Kozanowska', lat: 51.09, lon: 17.02, bikes: 7,
        electric: 2, docks: 9, loose: false, at: 56100, walk_sec: 420,
        walk_m: 300, from: 'Kamienna',
        rides: [{
            id: 'B', name: 'Legnicka', lat: 51.10, lon: 17.03, bikes: 3,
            docks: 12, m: 1826, sec: 960, at: 57060,
            options: [{arrival: 58200, vehicles: 2}],
        }],
    };
    app.drawFlow({...FLOW_FIXTURE, bike_places: [stacja], bike_places_live: false},
                 false);
    const kropka = app.flowBikeLayer.getLayers()[0];
    const tip = kropka._tooltip.content;

    app.drawFlow(FLOW_FIXTURE, false);
    return {
        ok: tip.includes('Nie wiadomo, czy będą tu rowery')
            && !tip.includes('7 rowerów')
            // Godzina "jesteś przy nim" pochodzi z ROZKŁADU tamtego dnia,
            // więc zostaje - nieznany jest tylko stan stojaka.
            && tip.includes('15:35')
            && kropka.options.color === app.BIKE_UNKNOWN_STYLE.color
            && kropka.options.color !== app.BIKE_STYLE.color,
        tip, obwodka: kropka.options.color,
    };
})();

/* Przełącznik pod zębatką: kreski wybranych przejazdów i ich stacje końcowe
   stoją na mapie cały czas, a nie tylko pod kursorem - i zostają po zejściu
   kursora z kropki. */
checks.rower_przejazdy_na_stale_po_zapaleniu = (() => {
    app.setBikesOn(true);
    const stacja = {
        id: 'A', name: 'Kozanowska', lat: 51.09, lon: 17.02, bikes: 7,
        electric: 2, docks: 9, loose: false, at: 56100, walk_sec: 420,
        walk_m: 300, from: 'Kamienna',
        rides: [{
            id: 'B', name: 'Legnicka', lat: 51.10, lon: 17.03, bikes: 3,
            docks: 12, m: 1826, sec: 840, at: 56940,
            options: [{arrival: 58200, vehicles: 2}],
        }],
    };
    const bylo = app.dotOpts.bikeRides;
    app.dotOpts.bikeRides = true;
    app.drawFlow({...FLOW_FIXTURE, bike_places: [stacja],
                  bike_places_live: true}, false);
    const warstwy = app.flowBikeRideLayer ? app.flowBikeRideLayer.getLayers() : [];
    const kreski = warstwy.filter(l => l.kind === 'polyline').length;
    const etykieta = (warstwy.find(l => l._tooltip && l._tooltip.options.permanent)
                      || {_tooltip: {content: ''}})._tooltip.content;
    const kropka = app.flowBikeLayer.getLayers()[0];
    kropka.fire('mouseover');
    kropka.fire('mouseout');
    const poZejsciu = app.flowBikeRideLayer ? app.flowBikeRideLayer.getLayers() : [];

    app.dotOpts.bikeRides = bylo;
    app.drawFlow(FLOW_FIXTURE, false);
    return {
        ok: kreski === 1
            && etykieta.trim() === '1,8 km'
            && poZejsciu.filter(l => l.kind === 'polyline').length === 1,
        kreski, etykieta, poZejsciu: poZejsciu.length,
    };
})();


/* W rozkładach o warstwie pojazdów decyduje to, co stoi na ekranie: otwarty
   rozkład linii albo zaznaczone linie tablicy przystanku. Mapa przepływów
   przestaje mieć tu cokolwiek do powiedzenia. */
checks.pojazdy_w_rozkladach_ida_za_ekranem = (function () {
    app.drawFlow(FLOW_FIXTURE, false);
    const seg = app.flowHits[0].seg;
    app.lastVehicles = [
        {line: seg.num, kind: seg.kind, lat: 51.10, lon: 17.00},
        {line: '999', kind: 'bus', lat: 51.11, lon: 17.02},
    ];
    app.setVehiclesOn(true);

    window.timetableMode = {vehicleLines: () => new Set(['bus 999'])};
    app.renderVehicles();
    const wRozkladach = app.vehiclesLayer.layers.map(m => m.options.icon.html);

    window.timetableMode = {vehicleLines: () => null};
    app.renderVehicles();
    const poWyjsciu = app.vehiclesLayer.layers.map(m => m.options.icon.html);

    delete window.timetableMode;
    app.setVehiclesOn(false);
    return {
        ok: wRozkladach.length === 1 && wRozkladach[0] === '999'
            && poWyjsciu.length === 1 && poWyjsciu[0] === seg.num,
        wRozkladach, poWyjsciu,
    };
})();

/* Wejście w rozkłady zdejmuje z mapy CAŁE wyszukiwanie, nie tylko linie:
   kropki węzłów przesiadkowych i wyróżnienie startu z celem opisują pytanie,
   którego na ekranie już nie ma. Wyjście przywraca jedno i drugie. */
checks.rozklady_sprzataja_slady_wyszukiwania = (function () {
    app.sel = {start: 'Sosnowiecka', end: 'Wojszyce'};
    app.updatePointMarker('start', {lat: 51.10, lon: 17.00});
    // Fixture jest sprzed węzłów przesiadkowych, a to właśnie ich kropki
    // zostawały na mapie - dokładamy dwa (patrz kropki_wachlarza).
    app.drawFlow({...FLOW_FIXTURE, nodes: [
        {name: 'PILCZYCE', lat: 51.13, lon: 16.95, sec: 48720, lines: []},
        {name: 'Rondo', lat: 51.11, lon: 17.01, sec: 49000, lines: []},
    ]}, false);
    const kropkiPrzed = app.flowDotLayer.getLayers().length;
    const startPrzed = app.styleFor('Sosnowiecka').fillColor;

    app.suspendPlanner();
    const kropkiPo = app.flowDotLayer;
    const punktPo = app.pointMarkers.start;
    const startPo = app.styleFor('Sosnowiecka').fillColor;

    app.resumePlanner();
    const startZ = app.styleFor('Sosnowiecka').fillColor;

    app.sel = {start: null, end: null};
    app.updatePointMarker('start', null);
    app.drawFlow(FLOW_FIXTURE, false);
    return {
        ok: kropkiPrzed > 0 && kropkiPo === null && punktPo === null
            && startPrzed !== startPo && startZ === startPrzed,
        kropkiPrzed, kropkiPo, punktPo, startPrzed, startPo, startZ,
    };
})();

/* Pasek z czasem trasy stoi na środku OKNA, a nie na środku tego, co zostało
   z mapy obok panelu. Dopiero gdy wyśrodkowany wszedłby na panel albo na
   przyciski, odsuwa się w prawo - i ani piksela dalej. */
checks.pasek_czasu_stoi_na_srodku_okna = (function () {
    app.drawFlow(FLOW_FIXTURE, false);
    const el = document.getElementById('time-headline');
    el.hidden = false;
    // Okienko w rogu otwiera się samo przy kropce startowej, a w emulatorze
    // stoi na tej samej ramce co pasek - tu chodzi o przeszkody z lewej.
    app.flowPanel.hidden = true;

    // Emulator daje każdemu elementowi tę samą ramkę: 320 px szerokości,
    // prawa krawędź na 320 - czyli przeszkody kończą się na 320, a pasek ma
    // 320 px szerokości.
    const przeszkoda = 320 + 16;
    window.innerWidth = 1200;
    app.placeTimeHeadline();
    const szeroko = parseFloat(el.style.left);

    window.innerWidth = 700;
    app.placeTimeHeadline();
    const wasko = parseFloat(el.style.left);

    window.innerWidth = 1200;
    return {
        ok: szeroko === (1200 - 320) / 2 && szeroko + 320 / 2 === 1200 / 2
            && wasko === przeszkoda && wasko > (700 - 320) / 2,
        szeroko, wasko, przeszkoda,
    };
})();

checks.auta_dostawczaki_leca_do_serwera = (() => {
    const przelacznik = document.getElementById('car-vans');
    const bylo = przelacznik.checked;
    przelacznik.checked = false;
    const zgaszone = app.queryParams().toString();
    przelacznik.checked = true;
    const zapalone = app.queryParams().toString();
    przelacznik.checked = bylo;
    return {
        ok: zgaszone.includes('car_vans=0') && zapalone.includes('car_vans=1'),
        zgaszone, zapalone,
    };
})();


/* Rodzaj roweru to dwa PRZYCISKI W PASKU warstw (zgłoszenie #147) - ich stan
   idzie w zapytaniu, bo odsiew miejsc robi serwer. Zgaszenie obu gasi rower
   w całości, więc zapytanie przestaje o niego prosić. */
checks.rower_rodzaj_leci_do_serwera = (() => {
    app.setBikesOn(true);
    const oba = app.queryParams().toString();
    app.setBikeKind('regular', false);
    const same_elektryki = app.queryParams().toString();
    app.setBikeKind('electric', false);
    const zadne = app.queryParams().toString();
    app.setBikesOn(true);
    return {
        ok: oba.includes('bike_electric=1') && oba.includes('bike_regular=1')
            && oba.includes('bikes=1')
            && same_elektryki.includes('bike_electric=1')
            && same_elektryki.includes('bike_regular=0')
            && same_elektryki.includes('bikes=1')
            && !zadne.includes('bikes=1'),
        oba, same_elektryki, zadne,
    };
})();


/* Debug pod zębatką: dymek roweru i auta mówi, dlaczego przeszły wybór -
   ale tylko z zapalonym przełącznikiem. Bez niego dymek jest taki jak był. */
checks.debug_mowi_dlaczego = (() => {
    const why = {records: ['najwcześniej przy aucie'], beaten: 0,
                 beaten_by: [], of: 4};
    const car = {model: 'Clio', plate: 'WE1', at: 600, walk_sec: 180, from: 'Rynek',
                 to_dest_m: 900, fuel: 80, range: 400, ogarniam: [], why};
    const place = {name: 'Stacja A', at: 600, walk_sec: 180, from: 'Rynek',
                   bikes: 3, electric: 0,
                   why: {records: [], beaten: 2, beaten_by: ['Stacja X', 'Stacja Z'],
                         of: 9},
                   rides: [{name: 'Stacja B', options: [
                       {bike_at: 780, arrival: 1500, vehicles: 1}]}]};
    const bylo = app.dotOpts.why;
    app.dotOpts.why = false;
    const autoBez = app.carTooltipHtml(car);
    const rowerBez = app.bikeTooltipHtml(place, true);
    app.dotOpts.why = true;
    const auto = app.carTooltipHtml(car);
    const rower = app.bikeTooltipHtml(place, true);
    app.dotOpts.why = bylo;
    return {
        ok: !autoBez.includes('nic go nie bije') && !rowerBez.includes('bije go')
            && auto.includes('nic go nie bije')
            && rower.includes('bije go 2 z 9') && rower.includes('Stacja X')
            && rower.includes('Stacja B'),
        auto, rower,
    };
})();


// Klik w mapę uzupełnia BRAKUJĄCY koniec relacji (#154): cel wpisany ręcznie
// albo z podpowiedzi siedzi tylko w polu (sel.end == null) - i klik, który
// wybiera start, nie ma prawa go skasować.
checks.klik_w_mape_nie_kasuje_wpisanego_celu = (() => {
    const byl = {sel: app.sel, start: app.startInput.value, end: app.endInput.value};
    app.sel = {start: null, end: null};
    app.startInput.value = '';
    app.endInput.value = 'Wojszyce';
    const punkt = {lat: 51.1, lon: 17.03};
    app.pickEndpoint(punkt);
    const poStarcie = {start: app.startInput.value, end: app.endInput.value,
                       selStart: app.sel.start};

    // Odwrotnie: wpisany start, klik wskazuje cel - startu też nie rusza.
    app.sel = {start: null, end: null};
    app.startInput.value = 'Sosnowiecka';
    app.endInput.value = '';
    app.pickEndpoint(punkt);
    const poCelu = {start: app.startInput.value, end: app.endInput.value,
                    selEnd: app.sel.end};

    app.sel = byl.sel;
    app.startInput.value = byl.start;
    app.endInput.value = byl.end;
    return {
        ok: poStarcie.end === 'Wojszyce' && poStarcie.start !== ''
            && poStarcie.selStart === punkt
            && poCelu.start === 'Sosnowiecka' && poCelu.end !== ''
            && poCelu.selEnd === punkt,
        poStarcie, poCelu,
    };
})();

/* Strefa oddawania Traficara (zgłoszenie #157): pod kursorem przy każdym
   aucie, a przy aucie z relokacją w „Ogarniam" także strefa, do której trzeba
   je przestawić. Przycisk 🅿, trzymający strefę na stałe, jest tylko
   z opcją w ustawieniach - domyślnie go nie ma. */
checks.strefa_traficara_pod_kursorem = (() => {
    const kwadrat = [[[[17.0, 51.0], [17.1, 51.0], [17.1, 51.2], [17.0, 51.2],
                       [17.0, 51.0]]]];
    app.zoneData = {end: kwadrat, no_end: kwadrat, relocation: kwadrat};
    const wspolne = {
        model: 'Renault Clio', fuel: 80, range: 300,
        at: 56100, walk_sec: 420, walk_m: 300, from: 'Kamienna', to_dest_m: 2744,
    };
    const zwykle = {...wspolne, lat: 51.09, lon: 17.02, plate: 'WE1AA11',
                    ogarniam: []};
    const doPrzestawienia = {...wspolne, lat: 51.10, lon: 17.03, plate: 'WE2BB22',
                             ogarniam: [{co: 'Relokacja', ile: 20}]};
    app.drawFlow({...FLOW_FIXTURE, cars: [zwykle, doPrzestawienia]}, false);
    const [a, b] = app.flowCarLayer.getLayers();
    const warstw = () => (app.carZoneLayer ? app.carZoneLayer.getLayers().length : 0);

    a.fire('mouseover');
    const przyZwyklym = warstw();
    a.fire('mouseout');
    const poZjechaniu = warstw();
    b.fire('mouseover');
    const przyRelokacji = warstw();
    b.fire('mouseout');

    const opcja = document.getElementById('zone-button');
    const przyciskDomyslnie = !app.zoneToggle.hidden;
    opcja.checked = true;
    app.refreshZoneLayer();
    const przyciskZOpcja = !app.zoneToggle.hidden;
    opcja.checked = false;
    app.refreshZoneLayer();

    app.drawFlow(FLOW_FIXTURE, false);
    return {
        ok: przyZwyklym === 2 && poZjechaniu === 0 && przyRelokacji === 3
            && !przyciskDomyslnie && przyciskZOpcja,
        przyZwyklym, poZjechaniu, przyRelokacji, przyciskDomyslnie, przyciskZOpcja,
    };
})();


/* Przystanki po drodze (zgłoszenie #169). Pierwszy przejazd mija dwa
   przystanki, w tym jeden bez współrzędnych (stacja PKP bywa bez nich) -
   na liście ma swoje miejsce, na mapie nie ma gdzie stanąć. Drugi przejazd
   jedzie o jeden przystanek i nie ma czego rozwijać. */
const VIA_LEGS = [
    {...LEGS[0], line: 'Autobus 134', headsign: 'BARDZKA', from_time: '15:53',
     to_time: '16:06', minutes: 13, stops_count: 3,
     via: [{name: 'Pierwszy', t: '15:57', lat: 51.085, lon: 17.055},
           {name: 'Bez współrzędnych', t: '16:01'}]},
    LEGS[1],
    {...LEGS[2], line: 'Tramwaj 5', headsign: 'KRZYKI', from_time: '16:08',
     to_time: '16:16', minutes: 8, stops_count: 1, via: []},
];
const VIA_JOURNEY = {departure: '15:53', arrival: '16:16', legs: VIA_LEGS};

checks.przystanki_po_drodze_w_osi = (() => {
    const opcja = document.getElementById('via-open');
    const bylo = opcja.checked;
    opcja.checked = false;
    const zwiniete = app.detailHtml(VIA_JOURNEY);
    opcja.checked = true;
    const rozwiniete = app.detailHtml(VIA_JOURNEY);
    opcja.checked = bylo;

    const wiersze = html => html.match(/<li class="tl-via [^"]*"[^>]*>/g) || [];
    const ukryte = html => wiersze(html).filter(w => / hidden>$/.test(w)).length;
    const przelaczniki = html => html.match(/class="tl-info tl-via-toggle"[^>]*>/g) || [];
    const kolejnosc = ['15:53', 'Pierwszy', 'Bez współrzędnych', '16:06']
        .map(t => zwiniete.indexOf(t));
    return {
        // Domyślnie zwinięte: wiersze są w osi, ale schowane, a liczba
        // przystanków jest przyciskiem, który je rozwija.
        ok: wiersze(zwiniete).length === 2 && ukryte(zwiniete) === 2
            && przelaczniki(zwiniete).length === 1
            && przelaczniki(zwiniete)[0].includes('aria-expanded="false"')
            && przelaczniki(zwiniete)[0].includes('data-leg="0"')
            // Opcja z ⚙ rozwija je od razu.
            && ukryte(rozwiniete) === 0
            && przelaczniki(rozwiniete)[0].includes('aria-expanded="true"')
            // Między wsiadaniem a wysiadaniem, po kolei, z godzinami.
            && kolejnosc.every((pos, i) => pos >= 0 && (i === 0 || pos > kolejnosc[i - 1]))
            && zwiniete.includes('>15:57<') && zwiniete.includes('data-via-of="0"')
            // Przejazd o jeden przystanek zostaje zwykłym napisem.
            && zwiniete.includes('<span class="tl-info">1 przystanek · 8 min</span>'),
        wiersze: wiersze(zwiniete), przelaczniki: przelaczniki(zwiniete), kolejnosc,
    };
})();

checks.przystanki_po_drodze_na_mapie = (() => {
    const kropki = dotsOf(app.legLayers(VIA_LEGS, {preview: false}));
    const podglad = dotsOf(app.legLayers(VIA_LEGS, {preview: true}));
    const poDrodze = kropki.filter(d => d._tooltip
        && String(d._tooltip.content).includes('Pierwszy'));
    const dot = poDrodze[0];
    return {
        // Cztery kropki wsiadania i wysiadania plus jedna obwódka - przystanek
        // bez współrzędnych nie staje nigdzie, a podgląd spod kursora kropek
        // nie ma wcale.
        ok: kropki.length === 5 && poDrodze.length === 1 && podglad.length === 0
            && dot._tooltip.content === '15:57 · Pierwszy'
            && dot._latlng.lat === 51.085 && dot._latlng.lng === 17.055
            // Pod kropkami przesiadek, nie na nich.
            && kropki.indexOf(dot) === 0,
        kropki: kropki.length, poDrodze: poDrodze.length, podglad: podglad.length,
        dymek: dot && dot._tooltip.content,
    };
})();

// --- przystanki narysowanych linii i ich nazwy (zgłoszenia #252, #253) ------
//
// Fixture nie niesie nazw (powstał przed nimi), więc dokładamy je na kopii:
// nazwa miejsca to współrzędne słupka, a kropki przesiadek dostają nazwę
// swojego słupka - tak samo, jak robi to serwer (planner, pole `place`).

const nazwaSlupka = (lat, lon) => 'P ' + lat + ' ' + lon;
const zNazwami = JSON.parse(JSON.stringify(FLOW_FIXTURE));
for (const seg of zNazwami.segments) {
    if (seg.stops_t) seg.stops_n = seg.stops_t.map(([lat, lon]) => nazwaSlupka(lat, lon));
}
for (const node of zNazwami.nodes || []) node.place = nazwaSlupka(node.lat, node.lon);

function boxOfName(marker) {
    const at = app.map.latLngToContainerPoint(marker.getLatLng());
    const [w, h] = marker.options.icon.iconSize;
    return [at.x, at.y, at.x + w, at.y + h];
}

const warstwa = layer => (layer ? layer.getLayers() : []);
const wskazanyKawalek = () => app.flowHits.find(h => h.seg.stops_n && h.seg.stops_n.length >= 3);

checks.p252_bez_kursora_i_z_daleka_nie_ma_przystankow_linii = (() => {
    app.dotOpts.namesZoom = 17;
    app.drawFlow(zNazwami, true);
    const zoom = app.map.getZoom();
    return {ok: zoom < 17 && !app.flowLineStopLayer && warstwa(app.flowStopsLayer).length === 0
                && warstwa(app.stopNameLayer).length === 0,
            zoom, kropek: warstwa(app.flowStopsLayer).length, nazw: warstwa(app.stopNameLayer).length};
})();

checks.p252_wskazana_linia_pokazuje_swoje_przystanki = (() => {
    const hit = wskazanyKawalek();
    app.showLineHighlight(hit.seg.num, hit.seg.kind);
    const kropki = warstwa(app.flowLineStopLayer);
    const swoje = new Set(app.flowHits
        .filter(h => h.seg.num === hit.seg.num && h.seg.kind === hit.seg.kind && h.seg.stops_t)
        .flatMap(h => h.seg.stops_t.map(([lat, lon]) => lat + ',' + lon)));
    const klucz = d => d.getLatLng().lat + ',' + d.getLatLng().lng;
    const obce = kropki.filter(d => !swoje.has(klucz(d)));
    const przesiadki = new Set((zNazwami.nodes || []).map(n => n.lat + ',' + n.lon));
    const naPrzesiadce = kropki.filter(d => przesiadki.has(klucz(d)));
    // Lżejsze od kropki przesiadki (punkt 11 kontraktu): mniejsze i cieńsze.
    // Start ma swój własny, zielony wygląd - porównujemy ze zwykłą przesiadką.
    const przesiadka = warstwa(app.flowDotLayer).find(d => !d.isStart);
    const lzejsze = kropki.every(d => d.options.radius < przesiadka.options.radius
                                      && d.options.weight < przesiadka.options.weight);
    const dymek = kropki.length ? kropki[0].getTooltip().content : '';
    app.hideLineHighlight();
    return {ok: kropki.length > 0 && obce.length === 0 && naPrzesiadce.length === 0 && lzejsze
                && /\d{1,2}:\d{2} · <b>P /.test(dymek) && !app.flowLineStopLayer,
            kropek: kropki.length, obcych: obce.length, naPrzesiadce: naPrzesiadce.length,
            lzejsze, dymek};
})();

checks.p252_dymek_linii_podaje_najblizszy_przystanek = (() => {
    const hit = wskazanyKawalek();
    const [lat, lon] = hit.seg.stops_t[1];
    const when = app.timeAtHover(hit, app.map.latLngToContainerPoint([lat, lon]));
    return {ok: !!when && when.stop === nazwaSlupka(lat, lon),
            oczekiwany: nazwaSlupka(lat, lon), podany: when && when.stop};
})();

checks.p253_z_bliska_nazwy_tylko_przystankow_mapy = (() => {
    const hit = wskazanyKawalek();
    const [lat, lon] = hit.seg.stops_t[1];
    // Słupek miasta, przez który nic z mapy nie jedzie - ma zostać bez nazwy.
    app.cityStops.length = 0;
    app.cityStops.push({name: 'OBCY', lat: lat + 0.0006, lon: lon + 0.0006});
    app.map.setView([lat, lon], 17);
    const nazwy = warstwa(app.stopNameLayer);
    const teksty = nazwy.map(m => m.options.icon.html);
    const zMapy = new Set(zNazwami.segments.flatMap(s => s.stops_n || []));
    const boxes = nazwy.map(boxOfName);
    let nachodzi = 0;
    for (let i = 0; i < boxes.length; i++) {
        for (let j = i + 1; j < boxes.length; j++) if (overlaps(boxes[i], boxes[j])) nachodzi++;
        for (const c of app.clusterBoxes) if (overlaps(boxes[i], c)) nachodzi++;
    }
    return {ok: nazwy.length > 0 && teksty.every(t => zMapy.has(t)) && !teksty.includes('OBCY')
                && new Set(teksty).size === teksty.length && nachodzi === 0
                && warstwa(app.flowStopsLayer).length > 0,
            nazw: nazwy.length, nachodzi, grupek: app.clusterBoxes.length,
            kropek: warstwa(app.flowStopsLayer).length};
})();

checks.p253_bez_mapy_nazwy_calego_miasta_raz_na_miejsce = (() => {
    const hit = wskazanyKawalek();
    const [lat, lon] = hit.seg.stops_t[1];
    app.cityStops.length = 0;
    // Dwa perony jednego miejsca i jedno inne miejsce obok.
    app.cityStops.push({name: 'PLAC', lat, lon},
                       {name: 'PLAC', lat: lat + 0.0002, lon},
                       {name: 'ULICA', lat: lat - 0.0008, lon: lon - 0.0012});
    app.clearFlow();
    const blisko = warstwa(app.stopNameLayer).map(m => m.options.icon.html).sort();
    app.map.setView([lat, lon], 15);
    const daleko = warstwa(app.stopNameLayer).length;
    app.cityStops.length = 0;
    return {ok: JSON.stringify(blisko) === JSON.stringify(['PLAC', 'ULICA']) && daleko === 0,
            blisko, daleko};
})();

checks.p253_wezel_z_peronami_po_obu_stronach_dostaje_nazwe = (() => {
    // pl. Grunwaldzki (2026-10-09): dwa słupki miejsca kilka pikseli od
    // siebie, po skosie - każde z czterech miejsc obok ŚRODKA zahacza
    // o któryś z nich. Nazwa ma i tak stanąć, przy którymś ze słupków.
    app.cityStops.length = 0;
    const lat = 51.1115, lon = 17.061, dLat = 0.00004, dLon = 0.000064;
    app.cityStops.push({name: 'WĘZEŁ', lat: lat + dLat, lon: lon - dLon},
                       {name: 'WĘZEŁ', lat: lat - dLat, lon: lon + dLon});
    app.map.setView([lat, lon], 17);
    const nazwy = warstwa(app.stopNameLayer).map(m => m.options.icon.html);
    app.cityStops.length = 0;
    return {ok: JSON.stringify(nazwy) === JSON.stringify(['WĘZEŁ']), nazwy};
})();

// --- rura z miksera przeglądarki --------------------------------------------
// Drugie uruchomienie app.js, tym razem z Web Audio - jak na każdym
// współczesnym telefonie. Musi być na końcu: pierwsza kopia też zobaczy
// odtąd mikser, a wszystkie jej sprawdzenia dźwięku są już za nami.

window.AudioContext = FakeMixer;
const mikser = runApp(APP_SOURCE);

function nagrajMikser(fn) {
    const przed = {zagrane: mixerLog.zagrane, zatrzymane: mixerLog.zatrzymane,
                   elementy: audioLog.zagrane};
    fn();
    return {
        zagrane: mixerLog.zagrane - przed.zagrane,
        zatrzymane: mixerLog.zatrzymane - przed.zatrzymane,
        zElementu: audioLog.zagrane - przed.elementy,
    };
}

checks.mikser_nieudane_pobranie_nie_gra_pozniej = (() => {
    // Wyszukiwanie przy zerwanej sieci: nagranie nie doszło, rura milczy.
    // Kolejne dotknięcie ponawia pobranie - i to już NIE może zagrać rury,
    // bo nikt w tej chwili niczego nie szukał.
    const w = nagrajMikser(() => {
        mikser.playPipeDrop();
        recordingFetches[recordingFetches.length - 1].nieudane();
        mikser.wakeMixer();
        recordingFetches[recordingFetches.length - 1].pobrane();
    });
    return {ok: recordingFetches.length === 2 && w.zagrane === 0 && w.zElementu === 0,
            pobran: recordingFetches.length, ...w};
})();

checks.mikser_gra_z_bufora_nie_z_elementu = (() => {
    // Element audio to dla iOS odtwarzacz, który zatrzymuje muzykę (#243).
    const w = nagrajMikser(() => mikser.playPipeDrop());
    return {ok: w.zagrane === 1 && w.zElementu === 0
                && /metal-pipe\.m4a$/.test((mixerLog.ostatni || {}).zdekodowane || ''),
            bufor: mixerLog.ostatni, ...w};
})();

checks.mikser_drugie_wyszukiwanie_gra_od_nowa = (() => {
    // Poprzednia rura milknie, nowa gra od początku - bez nakładania się.
    mikser.playPipeDrop();
    const w = nagrajMikser(() => mikser.playPipeDrop());
    return {ok: w.zagrane === 1 && w.zatrzymane === 1, ...w};
})();

checks.mikser_wyszukiwanie_przed_nagraniem_gra_po_pobraniu = (() => {
    // Świeża kopia app.js: nagranie jeszcze się nie pobrało, a wynik
    // wyszukiwania już jest. Rura ma zagrać, gdy tylko nagranie dojdzie.
    const swieza = runApp(APP_SOURCE);
    const start = {zagrane: mixerLog.zagrane, elementy: audioLog.zagrane};
    swieza.playPipeDrop();
    const przedPobraniem = mixerLog.zagrane - start.zagrane;
    recordingFetches[recordingFetches.length - 1].pobrane();
    const poPobraniu = mixerLog.zagrane - start.zagrane;
    return {ok: przedPobraniem === 0 && poPobraniu === 1
                && audioLog.zagrane === start.elementy,
            przedPobraniem, poPobraniu};
})();

JSON.stringify(checks);
