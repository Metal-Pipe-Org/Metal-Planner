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

JSON.stringify(checks);
