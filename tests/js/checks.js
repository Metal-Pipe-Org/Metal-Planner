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

checks.timetable_html = (() => {
    const html = app.timetableHtml({
        stop: 'Bardzka',
        from_time: '16:06',
        departures: [
            {time: '16:06', sec: 57960, in_min: 0, num: '134', mode: 'bus',
             headsign: 'LEŚNICA'},
            {time: '16:08', sec: 58080, in_min: 2, num: '5', mode: 'tram',
             headsign: 'BISKUPIN'},
            // drugi kurs 134 - ma się ZWINĄĆ w notkę przy pierwszym,
            // a nie stanąć jako trzeci wiersz
            {time: '16:16', sec: 58560, in_min: 10, num: '134', mode: 'bus',
             headsign: 'LEŚNICA'},
        ],
    });
    const wierszy = (html.match(/<li>/g) || []).length;
    return {
        ok: html.includes('Bardzka') && html.includes('16:06')
            && html.includes('badge bus') && html.includes('badge tram')
            && html.includes('LEŚNICA')
            && html.includes('2 min')
            && wierszy === 2                     // 3 odjazdy -> 2 wiersze
            // Rytm w TEJ SAMEJ linii co "za ile" (nie osobnym wierszem),
            // a najbliższy odjazd to "0 min", nie "teraz" - nagłówek mówi
            // "od 16:06" i to nie jest godzina zegarowa.
            && html.includes('0 min<small> · co 10 min</small>')
            && !html.includes('teraz'),
        wierszy,
        html: html.slice(0, 200),
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
    const dep = (num, mode, headsign) => ({time: '13:32', in_min: 0, num, mode, headsign});
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
    const dep = (sec, num, headsign) => ({time: '00:00', sec, in_min: 0,
                                          num, mode: 'tram', headsign});
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

/* Osiem odjazdów jednej linii to nie osiem opcji, tylko jedna opcja i jej
   rytm. Zostaje jeden wiersz: najbliższy odjazd + "co X min". */
checks.powtorzenia_zwijaja_sie_w_notke = (() => {
    const dep = (min, num, headsign) => ({time: '00:00', sec: min * 60, in_min: min,
                                          num, mode: 'tram', headsign});
    const wynik = app.summariseRepeats([
        dep(4, '3', 'LEŚNICA'), dep(12, '20', 'OPORÓW'), dep(19, '3', 'LEŚNICA'),
        dep(34, '3', 'LEŚNICA'), dep(49, '3', 'LEŚNICA'),
    ]);
    const trojka = wynik.find(d => d.num === '3');
    const dwudziestka = wynik.find(d => d.num === '20');
    return {
        ok: wynik.length === 2                      // jeden wiersz na linię
            && trojka.in_min === 4                  // najbliższy, nie któryś dalszy
            && trojka.every_min === 15              // ...i rytm w notce
            && dwudziestka.every_min === undefined  // pojedynczy kurs bez notki
            && wynik[0].num === '3',                // kolejność po najbliższym
        wiersze: wynik.map(d => `${d.num} za ${d.in_min}` +
                                (d.every_min ? ` co ${d.every_min}` : '')),
    };
})();

/* Odstęp to MEDIANA - jeden nocny przeskok nie ma prawa opisać taktu. */
checks.rytm_z_mediany_nie_ze_sredniej = (() => {
    const dep = min => ({time: '00:00', sec: min * 60, in_min: min,
                         num: '5', mode: 'tram', headsign: 'KRZYKI'});
    // przerwy: 10, 10, 10, 120 -> mediana 10, średnia 37,5
    const wynik = app.summariseRepeats([dep(0), dep(10), dep(20), dep(30), dep(150)]);
    return {ok: wynik[0].every_min === 10, every_min: wynik[0].every_min};
})();

/* Takt pisze się także wtedy, gdy kolejny kurs wypada już POZA zakresem mapy:
   "co 20 min" to informacja o linii, nie o oknie. Sprawdzane przez cały dymek,
   bo chodzi też o to, czy pełna tablica w ogóle dochodzi tam, gdzie liczy się
   rytm. */
checks.rytm_zostaje_gdy_kolejny_kurs_jest_poza_zakresem = (() => {
    const dep = (min, num) => ({time: '00:00', sec: min * 60, in_min: min,
                               num, mode: 'bus', headsign: 'KRZYKI'});
    const kursy = [dep(3, '112'), dep(23, '112'), dep(43, '112'), dep(63, '112')];
    const pelna = {stop: 'Sosnowiecka', from_time: '12:00',
                   departures: kursy, all_departures: kursy};
    // Przez PRAWDZIWE sito, nie obok niego: pełna tablica ma przez nie
    // przejść nietknięta. W oknie zostaje tylko pierwszy kurs.
    const poOdsiewie = app.keepWithinHorizon(pelna, 10 * 60);
    const html = app.timetableHtml(poOdsiewie);
    // ...a bez pełnej tablicy nie ma z czego policzyć rytmu i notki nie ma.
    const bezPelnej = app.timetableHtml({
        stop: 'Sosnowiecka', from_time: '12:00', departures: [kursy[0]],
    });
    return {
        ok: poOdsiewie.departures.length === 1
            && html.includes('co 20 min') && !bezPelnej.includes('co '),
        html: html.slice(-160), bezPelnej: bezPelnej.slice(-160),
    };
})();

/* Kierunek to osobna opcja - i osobny wiersz z własnym rytmem. */
checks.notka_rozroznia_kierunki = (() => {
    const dep = (min, headsign) => ({time: '00:00', sec: min * 60, in_min: min,
                                     num: '3', mode: 'tram', headsign});
    const wynik = app.summariseRepeats([dep(0, 'LEŚNICA'), dep(2, 'KSIĘŻE MAŁE'),
                                        dep(20, 'LEŚNICA'), dep(22, 'KSIĘŻE MAŁE')]);
    return {
        ok: wynik.length === 2 && wynik.every(d => d.every_min === 20),
        wiersze: wynik.map(d => d.headsign + ' co ' + d.every_min),
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
    const dep = min => ({time: '00:0' + min, sec: min * 60, in_min: min,
                         num: String(min), mode: 'bus', headsign: 'PRACZE'});
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
    const dep = sec => ({time: '00:00', sec, in_min: 0, num: '107',
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

/* Kropka przystanku, z którego wyruszamy, jest rozpoznawana ZAWSZE - także
   przy wyłączonym wyróżnieniu, bo okienko w rogu musi wiedzieć, od czyjego
   rozkładu zacząć. */
checks.kropka_startowa_rozpoznana = (() => {
    const bylo = app.dotOpts.start;
    app.dotOpts.start = false;            // wyróżnienie wyłączone...
    const dots = app.flowStopDots([
        {name: 'PILCZYCE', lat: 51.13, lon: 16.95, sec: 48720, lines: [], start: true},
        {name: 'Rondo', lat: 51.11, lon: 17.01, sec: 49000, lines: []},
    ]);
    const zielona = dots[0].options.color === '#1b5e20';
    app.dotOpts.start = bylo;
    return {
        // ...ale kropka i tak wie, że jest startowa - tylko nie jest zielona.
        ok: dots[0].isStart === true && dots[1].isStart === false && !zielona,
        start: dots[0].isStart, drugi: dots[1].isStart, zielona,
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
        {time: '16:04', sec: 57840, in_min: 4, num: '3', mode: 'tram',
         headsign: 'LEŚNICA', flow: 'start'},
    ]};
    const lines = [
        {num: '3', kind: 'tram', headsign: 'LEŚNICA', flow: 'start'},
        {num: '107', kind: 'bus', headsign: 'PRACZE', flow: 'end', arrive: 57600},
        // "end" bez godziny przyjazdu nie ma czego pokazać - nie zmyślamy jej
        {num: '9', kind: 'tram', headsign: 'PARK', flow: 'end'},
    ];
    const wiersze = app.withArrivals(data, lines, 57600).departures;
    const przyjazd = wiersze.find(d => d.num === '107');
    const bezLinii = app.withArrivals(data, null, 57600).departures;
    return {
        ok: wiersze.length === 2 && bezLinii.length === 1
            && przyjazd.flow === 'end' && przyjazd.time === '16:00'
            && przyjazd.in_min === 0 && przyjazd.mode === 'bus',
        wiersze: wiersze.map(d => `${d.num}/${d.flow}@${d.time}`),
    };
})();

/* Linia "end" NIE jest ofertą do wsiadania - w tablicy odjazdów nie ma prawa
   zostać, bo wypisana z najbliższym odjazdem udaje opcję, której mapa nie
   proponuje. Jej wiersz dokłada withArrivals, i to z innej godziny. */
checks.przyjazd_nie_udaje_odjazdu = (() => {
    const dep = (sec, num, mode, headsign) => ({time: '00:00', sec, in_min: 0,
                                                num, mode, headsign});
    const data = {stop: 'Bardzka', from_time: '16:00', departures: [
        dep(57840, '3', 'tram', 'LEŚNICA'),
        dep(58000, '107', 'bus', 'PRACZE'),   // ta linia tu tylko PRZYWOZI
    ]};
    const lines = [
        {num: '3', kind: 'tram', headsign: 'LEŚNICA', flow: 'through'},
        {num: '107', kind: 'bus', headsign: 'PRACZE', flow: 'end', arrive: 57600},
    ];
    const po = app.keepOfferedLines(data, lines);
    const pelne = app.withArrivals(po, lines, 57600).departures;
    return {
        ok: po.departures.length === 1
            && po.departures[0].num === '3' && po.departures[0].flow === 'through'
            && pelne.length === 2
            && pelne.find(d => d.num === '107').sec === 57600,
        odjazdy: po.departures.map(d => d.num + '/' + d.flow),
    };
})();

/* Przyjazd i odjazd tej samej linii to dwa różne zdarzenia na tym przystanku -
   zwinięte w jeden wiersz udawałyby rytm kursowania, którego nie ma. */
checks.przyjazd_nie_zwija_sie_z_odjazdem = (() => {
    const wiersz = (sec, flow) => ({time: '00:00', sec, in_min: 0, num: '3',
                                    mode: 'tram', headsign: 'LEŚNICA', flow});
    const wynik = app.summariseRepeats([wiersz(57600, 'end'), wiersz(58200, 'start')]);
    return {
        ok: wynik.length === 2 && wynik[0].flow === 'end'
            && wynik.every(d => d.every_min === undefined),
        wiersze: wynik.map(d => d.flow + '@' + d.sec),
    };
})();

/* Kolumna ze znakiem pojawia się tylko tam, gdzie jest czym ją wypełnić:
   tablica pod kropką WYBRANEJ trasy pyta o cały przystanek i nie wie, co się
   tu z którą linią dzieje - pusta kolumna przesuwałaby jej wiersze bez powodu.
   Przyjazd stoi w kolejności czasowej, nie na końcu listy. */
checks.tablica_miesza_przyjazdy_z_odjazdami = (() => {
    const html = app.timetableHtml({stop: 'Bardzka', from_time: '16:00', departures: [
        {time: '16:04', sec: 57840, in_min: 4, num: '3', mode: 'tram',
         headsign: 'LEŚNICA', flow: 'start'},
        {time: '16:00', sec: 57600, in_min: 0, num: '107', mode: 'bus',
         headsign: 'PRACZE', flow: 'end'},
        {time: '16:09', sec: 58140, in_min: 9, num: '20', mode: 'tram',
         headsign: 'OPORÓW', flow: 'through'},
    ]});
    const bezPrzeplywu = app.timetableHtml({stop: 'Bardzka', from_time: '16:00',
        departures: [{time: '16:04', sec: 57840, in_min: 4, num: '3',
                      mode: 'tram', headsign: 'LEŚNICA'}]});
    return {
        ok: html.includes('tt-rows has-flow')
            && html.includes('tt-flow-end') && html.includes('tt-flow-start')
            && html.includes('tt-flow-through')
            // przyjazd o 16:00 przed odjazdem o 16:04
            && html.indexOf('tt-flow-end') < html.indexOf('tt-flow-start')
            && !bezPrzeplywu.includes('has-flow')
            && !bezPrzeplywu.includes('<svg'),
        html: html.slice(0, 160),
    };
})();

/* Czekanie jest widoczne, nie schowane (punkt 13 kontraktu). */
checks.czekanie_jest_widoczne = (() => {
    const jutro = app.waitNoticeHtml(
        {day_offset: 1, starts: '00:03', waits_sec: 240});
    const dzis = app.waitNoticeHtml(
        {day_offset: 0, starts: '12:30', waits_sec: 88 * 60});
    const zaraz = app.waitNoticeHtml(
        {day_offset: 0, starts: '11:10', waits_sec: 8 * 60});
    return {
        ok: jutro.includes('jutro') && jutro.includes('00:03')
            && dzis.includes('12:30') && dzis.includes('88 min')
            && !dzis.includes('jutro')
            && zaraz === '',
        jutro, dzis, zaraz,
    };
})();

/* Przycisk "+X min" przy pasku nad mapą: X to połowa tego, co mapa pokazuje
   TERAZ (klik rozciąga zakres razy 1,5), klik przekazuje nowy zakres do
   serwera, a przy suficie 2 h przycisku nie ma wcale - nie ma już czego
   dokładać. */
checks.przycisk_przedluza_zakres_mapy = (() => {
    const pasek = () => document.getElementById('time-headline').innerHTML;
    const etykieta = html => (html.match(/headline-more[^>]*>\+([^<]*)</) || [])[1] || '';

    const krok = app.horizonStep(FLOW_FIXTURE.limit_sec);
    const naStarcie = etykieta(pasek());

    app.extendHorizon(FLOW_FIXTURE.limit_sec, krok);
    const zapytanie = app.queryParams().toString();

    // Sufit: zakres już na 2 h - nie ma czego dokładać, przycisk znika.
    app.drawFlow({...FLOW_FIXTURE, limit_sec: app.MAX_HORIZON_SEC}, false);
    const przySuficie = pasek();
    // ...a tuż pod sufitem przycisk obiecuje tylko to, co zostało do sufitu,
    // nie pełną połowę okna.
    const podSufitem = app.horizonStep(app.MAX_HORIZON_SEC - 600);
    app.drawFlow(FLOW_FIXTURE, false);   // mapa wraca do stanu z fixture'a

    return {
        ok: krok === FLOW_FIXTURE.limit_sec / 2          // połowa okna
            && naStarcie === '35 min'                    // połowa z 1 h 10 min
            && app.mapHorizonSec === 1.5 * FLOW_FIXTURE.limit_sec
            && zapytanie.includes('horizon_sec=6300')    // i to leci do serwera
            && !przySuficie.includes('headline-more')    // przy 2 h nie ma przycisku
            && podSufitem === 600                        // przy 1h50 dokłada 10 min
            && app.horizonStep(app.MAX_HORIZON_SEC) === 0,
        krok, naStarcie, podSufitem,
        horizon: app.mapHorizonSec,
        zapytanie: zapytanie.slice(0, 200),
        sufit: przySuficie.slice(0, 200),
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

JSON.stringify(checks);
