"""Schemat blokowy obok mapy - eksperyment „nowa mapa” (zgłoszenie #367).

Schemat to ta sama mapa zdjęta z geografii: węzły to te same kropki
przesiadek co na mapie (plus start i cel), a strzałka to jedna jazda - od
wsiadania do wysiadania. Przystanek, który linia tylko mija, nie tnie jej
strzałki, nawet gdy stoi na nim kropka; linie między tymi samymi węzłami są
jedną strzałką; strzałka kończy się tam, gdzie się naprawdę wysiada, Polinki schemat nie
rysuje wcale, a przejścia piesze, którymi mapa dochodzi do gałęzi albo do
celu, to osobne kropkowane kreski bez numeru. Linia jadąca przez kilka węzłów
to jedna nitka od węzła do węzła. Start stoi na samej górze,
a cel na samym dole - pilnuje tego sam kształt grafu: start jest jedynym
źródłem, cel jedynym ujściem.

Bez przełącznika nic się nie zmienia: odpowiedź nie ma pola `scheme`,
a strona nie pobiera ELK-a i nie pokazuje panelu.

Rysunek składa front: ELK liczy tylko położenie pudełek, kreski i napisy
rysujemy sami. Linia, z której się wysiada, kończy się strzałką na górze
pudełka; linia, która węzeł tylko mija, idzie przez pudełko jedną ciągłą
pionową kreską bez strzałki; poza tym kreski nie wchodzą w pudełka. Godzin na
schemacie nie ma.
"""

import json
import re
import threading
from datetime import date, datetime, timedelta

import pytest

import gtfs
import planner
from tests.gtfs_builder import make_day

WHEN = datetime(2026, 1, 5, 0, 0, 0)


def _dzien():
    """Dwie drogi S -> E: przesiadka z 5 w 17 na B albo prosto dwójką
    i czwórką - te dwie jadą tymi samymi przystankami."""
    return make_day([
        {"trip_id": "PIATKA", "label": "Tramwaj 5",
         "stops": [("S", 0, 0), ("B", 600, 600)]},
        {"trip_id": "SIEDEMNASTKA", "label": "Tramwaj 17",
         "stops": [("B", 900, 900), ("X", 1100, 1100), ("E", 1500, 1500)]},
        {"trip_id": "DWOJKA", "label": "Autobus 2",
         "stops": [("S", 300, 300), ("E", 1500, 1500)]},
        {"trip_id": "CZWORKA", "label": "Autobus 4",
         "stops": [("S", 360, 360), ("E", 1560, 1560)]},
    ])


def _po_nazwach(scheme):
    name = {n["id"]: n.get("place") if n["role"] == "stop" else n["role"]
            for n in scheme["nodes"]}
    return {(name[e["from"]], name[e["to"]], tuple(line["num"] for line in e["lines"]))
            for e in scheme["edges"]}


def _kawalek(num, *stops, kind="tram"):
    return {"num": num, "kind": kind, "stops_n": list(stops)}


def _wezel(place, *lines, start=False):
    """lines: (numer, flow) - jak w węźle mapy (planner._transfer_nodes)."""
    node = {"place": place, "name": place,
            "lines": [{"num": num, "kind": kind, "flow": flow}
                      for num, flow, kind in ((*l, "tram") if len(l) == 2 else l
                                              for l in lines)]}
    if start:
        node["start"] = True
    return node


def test_bez_flagi_odpowiedz_jest_taka_jak_dotad(install_day):
    install_day(_dzien())
    bez = planner.plan_flow("S", "E", WHEN, more=3)
    z = planner.plan_flow("S", "E", WHEN, more=3, new_map=True)
    assert "scheme" not in bez
    assert "scheme" in z
    z.pop("scheme")
    assert json.dumps(z, sort_keys=True) == json.dumps(bez, sort_keys=True)


def test_start_na_gorze_cel_na_dole(install_day):
    install_day(_dzien())
    scheme = planner.plan_flow("S", "E", WHEN, more=3, new_map=True)["scheme"]
    roles = [n["role"] for n in scheme["nodes"]]
    assert roles.count("start") == 1 and roles.count("end") == 1
    start = next(n["id"] for n in scheme["nodes"] if n["role"] == "start")
    end = next(n["id"] for n in scheme["nodes"] if n["role"] == "end")
    zrodla = {n["id"] for n in scheme["nodes"]} - {e["to"] for e in scheme["edges"]}
    ujscia = {n["id"] for n in scheme["nodes"]} - {e["from"] for e in scheme["edges"]}
    assert zrodla == {start}
    assert ujscia == {end}


def test_ten_sam_wybor_to_jedna_strzalka(install_day):
    install_day(_dzien())
    scheme = planner.plan_flow("S", "E", WHEN, more=3, new_map=True)["scheme"]
    krawedzie = _po_nazwach(scheme)
    # Dwójka i czwórka jadą ze startu do celu: jedna strzałka z dwiema
    # liniami, nie dwie.
    assert ("start", "end", ("2", "4")) in krawedzie
    assert ("start", "B", ("5",)) in krawedzie
    assert ("B", "end", ("17",)) in krawedzie
    # Godzin na strzałkach nie ma - są w tablicy węzła.
    assert all(set(e) - {"fast"} == {"from", "to", "lines"} for e in scheme["edges"])


# Katedra -> Wojszyce w małym: 7 i 10 do Galerii (G), tam 113 do celu; 2 jedzie
# dalej przez Galerię aż na Dworzec MDK (M), gdzie też jest 113. Na mapie na
# G i M stoją kropki, a 113 tylko przez M przejeżdża.
KATEDRA = dict(
    pieces=[
        _kawalek("7", "K", "U", "G"),
        _kawalek("10", "K", "U", "G"),
        _kawalek("2", "K", "U", "G", "B", "M"),
        _kawalek("113", "G", "B", "M", "D", "W", kind="bus"),
    ],
    nodes=[
        _wezel("K", ("7", "start"), ("10", "start"), ("2", "start"), start=True),
        _wezel("G", ("7", "end"), ("10", "end"), ("2", "through"),
               ("113", "start", "bus")),
        _wezel("M", ("2", "end"), ("113", "through", "bus")),
    ],
    target_places={"W"},
)


def test_linia_nie_jest_cieta_na_mijanym_przystanku():
    krawedzie = _po_nazwach(planner._scheme(**KATEDRA))
    # Dwójka i 113 jadą przez kilka węzłów: to nitki (start - G - M dla 2,
    # G - M - cel dla 113), a nie osobne strzałki z każdego węzła do końca.
    assert krawedzie == {
        ("start", "G", ("2", "7", "10")),
        ("G", "M", ("113", "2")),
        ("M", "end", ("113",)),
    }


def test_linia_przez_kilka_wezlow_to_lancuch_a_nie_strzalki_do_konca():
    scheme = planner._scheme(**KATEDRA)
    # 113 nie ma strzałki G -> cel ani start -> cel: tylko G -> M -> cel.
    assert not any(a == "G" and b == "end" for a, b, _ in _po_nazwach(scheme))
    nazwa = {n["id"]: n["role"] if n["role"] != "stop" else n["place"] for n in scheme["nodes"]}
    nitka = {(nazwa[e["from"]], nazwa[e["to"]]) for e in scheme["edges"]
             if any(l["num"] == "113" and l.get("chain") for l in e["lines"])}
    assert nitka == {("G", "M"), ("M", "end")}
    # 7 i 10 kończą jazdę na G (nic tam nie jedzie dalej): bez `chain` po tej stronie.
    start_g = next(e for e in scheme["edges"] if nazwa[e["from"]] == "start" and nazwa[e["to"]] == "G")
    assert {l["num"]: bool(l.get("chain")) for l in start_g["lines"]} == {"7": False, "10": False, "2": True}


def test_jazda_jest_ciete_w_kazdym_mijanym_wezle_nawet_bez_przesiadki():
    # 1 jedzie S -> A -> E i nikt na A się z niej nie przesiada - ale A jest
    # kropką mapy (wysiada się tu z 1 i wsiada w 9), więc jazda do celu to
    # nitka S -> A -> E, a nie druga strzałka S -> E obok.
    scheme = planner._scheme(
        pieces=[_kawalek("1", "S", "A", "E"), _kawalek("9", "A", "E")],
        nodes=[_wezel("S", ("1", "start"), start=True),
               _wezel("A", ("1", "through"), ("9", "start"))],
        target_places={"E"},
    )
    assert _po_nazwach(scheme) == {("start", "A", ("1",)), ("A", "end", ("1", "9"))}


def test_przystanek_bez_kropki_nie_jest_wezlem():
    nodes = planner._scheme(**KATEDRA)["nodes"]
    assert {n.get("place") for n in nodes if n["role"] == "stop"} == {"G", "M"}


def test_linia_urwana_tam_gdzie_nie_ma_kropki_konczy_sie_pudelkiem_tego_miejsca():
    scheme = planner._scheme(
        pieces=[_kawalek("4", "W", "Q", "X"), _kawalek("810", "Y", "Z", "R", kind="bus"),
                # Polinki schemat ukrywa, i jej końców też.
                _kawalek("Polinka", "P", "N", kind="cable")],
        nodes=[_wezel("P", ("Polinka", "start", "cable"), start=True), _wezel("W", ("4", "start")),
               _wezel("Y", ("810", "start", "bus"))],
        target_places={"R"},
    )
    # 4 jedzie z kropki W i mapa urywa ją na X (kropki tam nie ma): pudełko X,
    # oznaczone jako bez kropki. 810 kończy w celu.
    assert _po_nazwach(scheme) == {("W", "X", ("4",)), ("Y", "end", ("810",))}
    assert {n["place"] for n in scheme["nodes"] if n.get("bare")} == {"X"}
    assert not any(n.get("place") in ("N",) for n in scheme["nodes"])
    # Pudełka bez dojścia stoją pod startem, bez wyjścia - nad celem.
    nazwa = {n["id"]: n["role"] if n["role"] != "stop" else n["place"] for n in scheme["nodes"]}
    assert {(nazwa[a], nazwa[b]) for a, b in scheme["order"]} == {
        ("start", "W"), ("start", "Y"), ("X", "end")}


def test_nic_nie_jest_odcinane_jazda_donikad_tez_jest_na_schemacie():
    scheme = planner._scheme(
        pieces=[_kawalek("1", "S", "A", "E"), _kawalek("9", "A", "Z")],
        nodes=[_wezel("S", ("1", "start"), start=True),
               _wezel("A", ("1", "through"), ("9", "start"))],
        target_places={"E"},
    )
    # 9 nie dojeżdża do celu, ale mapa ją rysuje - schemat nie ocenia, czy
    # prowadzi dokądś.
    assert _po_nazwach(scheme) == {("start", "A", ("1",)), ("A", "end", ("1",)), ("A", "Z", ("9",))}


def test_kawalki_tej_samej_linii_na_szwie_to_jedna_jazda():
    # 3 narysowana w dwóch kawałkach (szew na M, gdzie kropki nie ma) jedzie
    # S -> A -> M -> E jedną nitką; kropka A jest na jej drodze.
    scheme = planner._scheme(
        pieces=[_kawalek("3", "S", "A", "M"), _kawalek("3", "M", "E")],
        nodes=[_wezel("S", ("3", "start"), start=True), _wezel("A", ("3", "through"))],
        target_places={"E"},
    )
    assert _po_nazwach(scheme) == {("start", "A", ("3",)), ("A", "end", ("3",))}
    assert not [n for n in scheme["nodes"] if n.get("bare")]


def _podroz(*rides, arrival):
    """Podróż jak z planisty: rides to (numer, [(przystanek, godzina_s), ...])."""
    legs = []
    for num, stops in rides:
        legs.append({"kind": "ride", "num": num, "from": stops[0][0], "dep_sec": stops[0][1],
                     "to": stops[-1][0], "arr_sec": stops[-1][1],
                     "via": [{"name": n, "t": f"{t // 3600}:{t % 3600 // 60:02d}"}
                             for n, t in stops[1:-1]]})
    return {"legs": legs, "arrival_sec": arrival}


def test_godziny_z_podrozy_i_najszybsza_sciezka():
    scheme = planner._scheme(
        pieces=[_kawalek("1", "S", "A", "B"), _kawalek("2", "S", "E"),
                _kawalek("5", "B", "E"), _kawalek("9", "A", "E")],
        nodes=[_wezel("S", ("1", "start"), ("2", "start"), start=True),
               _wezel("A", ("1", "end"), ("9", "start")),
               _wezel("B", ("1", "end"), ("5", "start"))],
        target_places={"E"},
    )
    planner._scheme_times(scheme, [
        _podroz(("2", [("S", 3600), ("E", 4800)]), arrival=4800),
        _podroz(("1", [("S", 3600), ("A", 3900), ("B", 4200)]),
                ("5", [("B", 4260), ("E", 4500)]), arrival=4500),
    ])
    name = {n["id"]: n["role"] if n["role"] != "stop" else n["place"] for n in scheme["nodes"]}
    # Najwcześniejsza obecność w węźle, także na przystanku, który linia mija;
    # cel - najwcześniejszy przyjazd.
    assert {name[n["id"]]: n["arrive_sec"] for n in scheme["nodes"] if "arrive_sec" in n} == {
        "A": 3900, "B": 4200, "end": 4500}
    # Przyjazd przy numerze każdej strzałki do celu: dwójką 4800, piątką 4500.
    do_celu = {(name[e["from"]], l["num"]): l.get("arrive_sec")
               for e in scheme["edges"] if name[e["to"]] == "end" for l in e["lines"] if "arrive_sec" in l}
    assert do_celu == {("start", "2"): 4800, ("B", "5"): 4500}
    # Najszybsza podróż (piątka z przesiadką na B) jest wyróżniona, dwójka nie.
    assert {(name[e["from"]], name[e["to"]]) for e in scheme["edges"] if e.get("fast")} == {
        ("start", "A"), ("A", "B"), ("B", "end")}


# ----------------------------------------------------- bez chodzenia ---

def _dzien_z_przejsciem():
    """START i OBOK o różnych nazwach, 100 m od siebie (da się dojść pieszo);
    PERON_1 i PERON_2 jedna nazwa "Peron" - słupki jednego przystanku."""
    day = make_day(
        [{"trip_id": "T1", "label": "Tramwaj 1",
          "stops": [("START", 0, 0), ("PERON_1", 600, 600)]},
         {"trip_id": "T2", "label": "Tramwaj 2",
          "stops": [("PERON_2", 900, 900), ("OBOK", 1200, 1200)]}],
        names={"PERON_1": "Peron", "PERON_2": "Peron"},
        siblings={"START": {"OBOK": 300}, "OBOK": {"START": 300},
                  "PERON_1": {"PERON_2": 500}, "PERON_2": {"PERON_1": 500}})
    day.stop_coords["OBOK"] = (day.stop_coords["START"][0] + 0.001, day.stop_coords["START"][1])
    return day


def test_bez_chodzenia_zostaje_tylko_przejscie_miedzy_slupkami_o_tej_samej_nazwie():
    day = _dzien_z_przejsciem()
    assert set(planner._origin_walk(day, ["START"])) == {"OBOK"}
    free = gtfs.without_walking(day)
    # Dojścia ze startu i do celu znikają, przejście między różnymi nazwami też,
    # a "Peron" -> "Peron" zostaje jako stały skok za WALK_MIN_SEC.
    assert planner._origin_walk(free, ["START"]) == {}
    assert free.siblings == {"PERON_1": {"PERON_2": gtfs.WALK_MIN_SEC},
                             "PERON_2": {"PERON_1": gtfs.WALK_MIN_SEC}}
    # Dzień bazowy nie jest ruszany.
    assert set(planner._origin_walk(day, ["START"])) == {"OBOK"}
    assert gtfs.without_walking(day) is free


# ------------------------------------------------- przejścia piesze ---

def _dzien_z_przesiadka_pieszo(*, bezposredni=False):
    """Z S tramwajem 1 do A; z A idzie się do B (inna nazwa, dwa kroki
    obok siebie), skąd autobus 2 jedzie do E. Z `bezposredni` jest też
    autobus 3 prosto z S do E, żeby mapa miała co pokazać bez chodzenia."""
    trips = [{"trip_id": "T1", "label": "Tramwaj 1", "stops": [("S", 0, 0), ("A", 300, 300)]},
             {"trip_id": "T2", "label": "Autobus 2", "stops": [("B", 900, 900), ("E", 1500, 1500)]}]
    if bezposredni:
        trips.append({"trip_id": "T3", "label": "Autobus 3", "stops": [("S", 100, 100), ("E", 1700, 1700)]})
    return make_day(trips, siblings={"A": {"B": 120}, "B": {"A": 120}})


def test_przejscie_pieszo_z_mapy_jest_na_schemacie(install_day):
    install_day(_dzien_z_przesiadka_pieszo())
    scheme = planner.plan_flow("S", "E", WHEN, more=3, new_map=True)["scheme"]
    name = {n["id"]: n.get("place") or n["role"] for n in scheme["nodes"]}
    assert [(name[w["from"]], name[w["to"]]) for w in scheme["walks"]] == [("A", "B")]
    # To nie jest jazda: nie ma na nim linii i nie jest strzałką.
    assert ("A", "B", ("1",)) not in _po_nazwach(scheme)
    assert [name[e["from"]] + ">" + name[e["to"]] for e in scheme["edges"]] == ["S>A", "B>end"]


def test_bez_chodzenia_schemat_nie_ma_przejsc_pieszych(install_day):
    install_day(_dzien_z_przesiadka_pieszo(bezposredni=True))
    bez = planner.plan_flow("S", "E", WHEN, more=3, new_map=True, walking=False)["scheme"]
    assert bez["walks"] == []


def test_dojscie_do_celu_pieszo_jest_na_schemacie():
    pieces = [_kawalek("1", "S", "A")]
    nodes = [_wezel("S", ("1", "start"), start=True), _wezel("A", ("1", "end"))]
    scheme = planner._scheme(pieces, nodes, target_places={"T"},
                             walkable=lambda a, b: (a, b) == ("A", planner.SCHEME_END))
    name = {n["id"]: n.get("place") or n["role"] for n in scheme["nodes"]}
    assert [(name[w["from"]], name[w["to"]]) for w in scheme["walks"]] == [("A", "end")]
    assert scheme["order"] == [[w["from"], w["to"]] for w in scheme["walks"]]


def test_gałąź_bez_przejścia_pieszo_dostaje_tylko_niewidoczną_podpowiedź_układu():
    pieces = [_kawalek("1", "S", "A"), _kawalek("2", "B", "C")]
    nodes = [_wezel("S", ("1", "start"), start=True), _wezel("A", ("1", "end")),
             _wezel("B", ("2", "start"))]
    scheme = planner._scheme(pieces, nodes, target_places={"C"})
    assert scheme["walks"] == []
    assert scheme["order"]


# ------------------------------------------------------------- strona ---

# ELK z sieci podstawiony: test nie zależy od CDN i ma przewidywalny układ -
# pudełka w jednej kolumnie, co drugie przesunięte w bok, więc długie krawędzie
# muszą omijać pudełka pośrednie. Podstawka zapamiętuje adres, z którego strona
# go pobrała.
ELK_ATRAPA = """
window.ELK = class {
    async layout(graph) {
        return {children: graph.children.map((c, i) => ({
            id: c.id, x: (i % 2) * 90, y: i * 170, width: c.width, height: c.height})),
            edges: []};
    }
};
"""

# Pomiar tego, co strona narysowała: pudełka i ścieżki linii jako punkty.
POMIAR = """
() => {
    const svg = document.querySelector('#scheme-canvas svg');
    const boxes = [...svg.querySelectorAll('rect.scheme-box')].map(r => ({
        id: r.parentNode.dataset.node, x: +r.getAttribute('x'), y: +r.getAttribute('y'),
        w: +r.getAttribute('width'), h: +r.getAttribute('height')}));
    const paths = [...svg.querySelectorAll('g.scheme-stroke')].map(g => {
        const parts = [...g.querySelectorAll('path.scheme-line')];
        return {
            lines: g.dataset.lines, colors: parts.map(p => p.getAttribute('stroke')),
            through: g.classList.contains('scheme-through'), merge: g.classList.contains('scheme-merge'), branch: g.classList.contains('scheme-branch'),
            marker: parts.map(p => p.getAttribute('marker-end')).find(Boolean) || null,
            // Punkty kotwiczne ścieżki: koniec każdego odcinka prostego albo krzywej.
            points: parts[0].getAttribute('d').match(/[MLC][^MLC]*/g).map(c => {
                const n = c.slice(1).trim().split(/[ ,]+/).map(Number);
                return n.slice(-2);
            }),
            // Próbki wzdłuż całej kreski (krzywe też), co 2 px.
            samples: (() => {
                const out = [], path = parts[0], total = path.getTotalLength();
                for (let t = 0; t <= total; t += 2) {
                    const q = path.getPointAtLength(t);
                    out.push([q.x, q.y]);
                }
                return out;
            })()};
    });
    const badges = [...svg.querySelectorAll('g.scheme-badge')].map(g => {
        const r = g.querySelector('rect');
        return {lines: g.dataset.lines, cx: +g.dataset.cx, cy: +g.dataset.cy,
                x: +r.getAttribute('x'), y: +r.getAttribute('y'),
                w: +r.getAttribute('width'), h: +r.getAttribute('height'),
                colors: [...g.querySelectorAll('text')].map(t => t.getAttribute('fill'))};
    });
    return {boxes, paths, badges, text: svg.textContent};
}
"""


# dagre z sieci podstawiony tak samo: ten sam układ co ELK-owa podstawka,
# a punkty pośrednie krawędzi, które przeskakują warstwy, leżą daleko z prawej
# - krzywa ma przez nie obejść pudełka pośrednie.
DAGRE_ATRAPA = """
window.dagre = {
    graphlib: {Graph: class {
        constructor() { this.nodes = new Map(); this.edges = new Map(); }
        setGraph() {}
        setDefaultEdgeLabel() {}
        setNode(id, label) { this.nodes.set(id, label); }
        setEdge(a, b) { this.edges.set(a + '>' + b, {a, b}); }
        node(id) { return this.nodes.get(id); }
        edge(a, b) { return this.edges.get(a + '>' + b); }
    }},
    layout(g) {
        // Rząd z numeru węzła, nie z kolejności wstawiania: renderScheme
        // wstawia węzły w różnej kolejności i podstawka nie ma się tym przejmować.
        const ids = [...g.nodes.keys()];
        const rank = new Map(ids.map(id => [id, +id.slice(1)]));
        ids.forEach(id => Object.assign(g.nodes.get(id), {
            x: g.nodes.get(id).width / 2 + (rank.get(id) % 2) * 90, y: rank.get(id) * 170 + 20}));
        for (const e of g.edges.values()) {
            const a = g.nodes.get(e.a), b = g.nodes.get(e.b);
            e.points = [{x: a.x, y: a.y + 19}];
            for (let r = rank.get(e.a) + 1; r < rank.get(e.b); r++) e.points.push({x: 900, y: r * 170 + 20});
            e.points.push({x: b.x, y: b.y - 19});
        }
    },
};
"""


def _przejazdy(pomiar):
    """{(numery, id pudełka)} - grupy linii, które idą przez pudełko od krawędzi
    do krawędzi jedną pionową kreską. Każde inne wejście kreski we wnętrze
    pudełka to błąd rysunku."""
    out = set()
    for sciezka in pomiar["paths"]:
        if sciezka["merge"]:
            continue         # zlanie dwóch grup w pudełku nie jest proste
        for box in pomiar["boxes"]:
            w_srodku = [(x, y) for x, y in sciezka["samples"]
                        if box["x"] + 1 < x < box["x"] + box["w"] - 1
                        and box["y"] + 1 < y < box["y"] + box["h"] - 1]
            if not w_srodku:
                continue
            xs = [x for x, _ in w_srodku]
            ys = [y for _, y in w_srodku]
            assert max(xs) - min(xs) < 0.5 and min(ys) < box["y"] + 4 \
                and max(ys) > box["y"] + box["h"] - 4, (
                f"grupa {sciezka['lines']} wchodzi w pudełko {box['id']} nie na wylot")
            out.add((sciezka["lines"], box["id"]))
    return out


def _plakietki_na_kreskach(pomiar):
    """Każda plakietka z numerami stoi NA kresce swojej grupy (środek leży na
    jej ścieżce) i nie leży na żadnym pudełku ani na innej plakietce."""
    for badge in pomiar["badges"]:
        assert any(abs(x - badge["cx"]) < 2.5 and abs(y - badge["cy"]) < 2.5
                   for p in pomiar["paths"] if set(badge["lines"].split()) <= set(p["lines"].split())
                   for x, y in p["samples"]), f"plakietka {badge['lines']} nie leży na swojej kresce"
        for box in pomiar["boxes"]:
            assert not (badge["x"] < box["x"] + box["w"] and badge["x"] + badge["w"] > box["x"]
                        and badge["y"] < box["y"] + box["h"] and badge["y"] + badge["h"] > box["y"]), (
                f"plakietka {badge['lines']} leży na pudełku {box['id']}")
    for i, a in enumerate(pomiar["badges"]):
        for b in pomiar["badges"][i + 1:]:
            assert not (a["x"] < b["x"] + b["w"] and a["x"] + a["w"] > b["x"]
                        and a["y"] < b["y"] + b["h"] and a["y"] + a["h"] > b["y"]), (
                f"plakietki {a['lines']} i {b['lines']} nachodzą na siebie")


def _koniec_na_pudelku(sciezka, box):
    x, y = sciezka["points"][-1]
    return y == box["y"] and box["x"] <= x <= box["x"] + box["w"]


@pytest.fixture(scope="module")
def adres():
    try:
        gtfs.all_stop_names()
    except FileNotFoundError:
        pytest.skip("brak bazy rozkładów (data/gtfs.sqlite)")
    from werkzeug.serving import make_server
    import app as aplikacja
    serwer = make_server("127.0.0.1", 0, aplikacja.app, threaded=True)
    threading.Thread(target=serwer.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{serwer.server_port}/"
    serwer.shutdown()


@pytest.fixture(scope="module")
def przegladarka():
    sync_api = pytest.importorskip("playwright.sync_api")
    from tests.test_uklad_telefonu import _chromium
    with sync_api.sync_playwright() as p:
        browser = _chromium(p)
        yield browser
        browser.close()


def _wtorek():
    """Najbliższy wtorek - zwykły dzień rozkładu, żeby relacja miała
    przesiadkę niezależnie od dnia, w którym idą testy."""
    dzis = date.today()
    return (dzis + timedelta(days=(1 - dzis.weekday()) % 7 or 7)).isoformat()


def _strona(przegladarka, adres, elk, uklad="elk"):
    from tests.test_uklad_telefonu import PUSTE_ZRODLA, _odpowiedz
    context = przegladarka.new_context(viewport={"width": 1280, "height": 800},
                                       locale="pl-PL", service_workers="block")
    page = context.new_page()
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    for wzor, odpowiedz in PUSTE_ZRODLA.items():
        page.route(wzor, _odpowiedz(odpowiedz))
    page.route("**/tile.openstreetmap.org/**", lambda route: route.abort())
    page.route("**/npm/elkjs@*/**", lambda route: elk.append(route.request.url)
               or route.fulfill(status=200, content_type="application/javascript",
                                body=ELK_ATRAPA))
    page.route("**/npm/@dagrejs/dagre@*/**", lambda route: elk.append(route.request.url)
               or route.fulfill(status=200, content_type="application/javascript",
                                body=DAGRE_ATRAPA))
    page.goto(adres)
    page.wait_for_selector("#start")
    page.evaluate("u => document.getElementById('scheme-layout').value = u", uklad)
    return context, page


@pytest.mark.parametrize("uklad", ["dagre", "elk"])
@pytest.mark.parametrize("flaga", [False, True])
def test_panel_schematu_tylko_z_flaga(adres, przegladarka, flaga, uklad):
    from tests.test_uklad_telefonu import _wybierz
    elk = []
    context, page = _strona(przegladarka, adres, elk, uklad)
    if flaga:
        page.evaluate("document.getElementById('new-map').checked = true")
    page.fill("#time", "15:00")
    page.evaluate(f"document.getElementById('date').value = '{_wtorek()}'")
    _wybierz(page, "#start", "Katedra")
    _wybierz(page, "#end", "Wojszyce")
    if not page.evaluate("document.body.classList.contains('searching')"):
        page.click("#search")
    page.wait_for_selector("#time-headline .headline-limit", timeout=30000)
    if flaga:
        page.wait_for_selector("#scheme-panel:not([hidden]) svg", timeout=10000)
        # Przypięta wersja biblioteki, nie „latest”.
        biblioteka = "/npm/@dagrejs/dagre@1.1.4/" if uklad == "dagre" else "/npm/elkjs@0.9.3/"
        assert len(elk) == 1 and biblioteka in elk[0]
        pomiar = page.evaluate(POMIAR)
        _plakietki_na_kreskach(pomiar)
        napisy = page.evaluate("[...document.querySelectorAll('.scheme-name')].map(t => t.textContent)")
        assert napisy[0] == "Start: Katedra" and napisy[-1] == "Cel: Wojszyce"
        assert pomiar["paths"]
        _przejazdy(pomiar)
        # Godzin na schemacie nie ma, ani w ⚙ wyboru ich wariantu.
        assert not re.search(r"\d{1,2}:\d{2}", pomiar["text"])
        assert page.locator("#scheme-times").count() == 0
        # Tablica węzła dopiero po najechaniu - ta sama co pod kropką mapy.
        assert not page.is_visible("#scheme-tip")
        # n0 to start, ostatni - cel; między nimi węzły przesiadek.
        page.locator("#scheme-canvas g.node").nth(1).hover()
        page.wait_for_selector("#scheme-tip:not([hidden]) .tip-head", timeout=15000)
        # Panel zwija się do nagłówka, jak lista propozycji.
        page.click("#scheme-toggle")
        assert not page.is_visible("#scheme-viewport")
        assert page.is_visible("#scheme-toggle")
    else:
        page.wait_for_timeout(300)
        assert not page.is_visible("#scheme-panel")
        assert elk == []
    assert page.errors == []
    context.close()


# Graf ręcznie: 5 (tramwaj) i 6 (autobus) jadą od startu przez A do B,
# na B 6 wysiada, a 5 jedzie dalej do celu. Grupa 145+146+149 (same autobusy)
# dzieli się na C: 145 146 jadą do celu, 149 do D; 7 (tramwaj) jedzie ze startu
# prosto do celu.
RECZNY = {
    "nodes": [{"id": "n0", "role": "start", "place": "S"},
              {"id": "n1", "role": "stop", "place": "A"},
              {"id": "n2", "role": "stop", "place": "B"},
              {"id": "n3", "role": "stop", "place": "C"},
              {"id": "n4", "role": "stop", "place": "D", "bare": True},
              {"id": "n5", "role": "end"}],
    "edges": [
        {"from": "n0", "to": "n1", "lines": [{"num": "5", "kind": "tram", "chain": True},
                                              {"num": "6", "kind": "bus", "chain": True}]},
        {"from": "n1", "to": "n2", "lines": [{"num": "5", "kind": "tram", "chain": True},
                                              {"num": "6", "kind": "bus", "chain": True}]},
        {"from": "n2", "to": "n5", "lines": [{"num": "5", "kind": "tram", "chain": True}]},
        {"from": "n0", "to": "n3", "lines": [{"num": "145", "kind": "bus", "chain": True},
                                              {"num": "146", "kind": "bus", "chain": True},
                                              {"num": "149", "kind": "bus", "chain": True}]},
        {"from": "n3", "to": "n5", "lines": [{"num": "145", "kind": "bus", "chain": True},
                                              {"num": "146", "kind": "bus", "chain": True}]},
        {"from": "n3", "to": "n4", "lines": [{"num": "149", "kind": "bus", "chain": True}]},
        {"from": "n4", "to": "n5", "lines": [{"num": "149", "kind": "bus"}]},
        {"from": "n0", "to": "n5", "lines": [{"num": "7", "kind": "tram"},
                                              {"num": "9", "kind": "bus"}]},
    ],
    "walks": [],
    "pairs": [],
    "order": [],
}


@pytest.mark.parametrize("uklad", ["dagre", "elk"])
def test_rysunek_grupy_przejazd_podzial_i_strzalki(adres, przegladarka, uklad):
    elk = []
    context, page = _strona(przegladarka, adres, elk, uklad)
    page.evaluate("document.getElementById('new-map').checked = true")
    page.evaluate("data => renderScheme(data)",
                  {"scheme": RECZNY, "start": "S", "end": "E", "nodes": []})
    page.wait_for_selector("#scheme-panel:not([hidden]) svg", timeout=10000)
    pomiar = page.evaluate(POMIAR)
    po_miejscu = {n["id"]: n.get("place") or n["role"] for n in RECZNY["nodes"]}
    box = {po_miejscu[b["id"]]: b for b in pomiar["boxes"]}
    zwykle = [p for p in pomiar["paths"] if not p["through"] and not p["branch"]]
    # Linie jadą jedną kreską tylko z tymi, z którymi mają ten sam los: 5 i 6
    # jadą do B osobno, bo za A idą w różne odcinki (a dwie kreski w jednym
    # pudełku nigdy nie łączą się bokiem); 145 146 149 dzieli się na 145 146
    # i 149 jeszcze przed C. Reszta jedzie parami do celu.
    assert len(zwykle) == 11
    assert not [p for p in pomiar["paths"] if p["branch"] or p["merge"]]

    def koniec(lines, gdzie):
        return [p for p in zwykle if p["lines"] == lines and _koniec_na_pudelku(p, box[gdzie])]

    # Przez pudełka przechodzą TYLKO linie, które jadą dalej, prostą kreską.
    assert {(l, po_miejscu[i]) for l, i in _przejazdy(pomiar)} == {
        ("5", "A"), ("6", "A"), ("5", "B"), ("145 146", "C"), ("149", "C")}
    # Strzałka tylko tam, gdzie się naprawdę wysiada: 6 w B, 5 i 145 146 w celu,
    # 149 w D i w celu, 7 9 w celu. Odcinki, po których wszyscy jadą dalej, jej
    # nie mają.
    for lines, gdzie in [("5", "A"), ("6", "A"), ("5", "B"), ("145 146", "C"), ("149", "C")]:
        assert koniec(lines, gdzie)[0]["marker"] is None
    for lines, gdzie in [("6", "B"), ("5", "end"), ("145 146", "end"), ("149", "D"),
                         ("149", "end"), ("7 9", "end")]:
        assert koniec(lines, gdzie)[0]["marker"]
    # Grupa z tramwaju i autobusu ma oba kolory, czysta - jeden.
    assert sorted(koniec("7 9", "end")[0]["colors"]) == sorted(["#c62828", "#1565c0"])
    assert koniec("145 146", "C")[0]["colors"] == ["#1565c0"]
    # Jeden napis na grupę, ze wszystkimi numerami.
    numery = [b["lines"] for b in pomiar["badges"]]
    assert {"5", "6", "145 146", "149", "7 9"} <= set(numery)
    # Miejsce bez kropki na mapie (D) wygląda inaczej niż kropki mapy i ma podpowiedź.
    assert page.evaluate("document.querySelectorAll('rect.scheme-box').length") == 6
    assert page.evaluate("""[...document.querySelectorAll('g.node.bare')].map(g => [
        g.dataset.node, g.querySelector('rect').classList.contains('bare'),
        g.querySelector('title').textContent])""") == [
        ["n4", True, "Tu linia kończy się na mapie, ale mapa nie stawia tu kropki"]]
    _plakietki_na_kreskach(pomiar)
    # Miarą wyboru układu jest liczba przecięć kresek, które naprawdę rysujemy.
    assert page.evaluate("schemeCost(schemeLast.boxes, schemeLast.strands).total") >= 0
    assert page.errors == []
    context.close()


def test_kazda_para_pudelek_ma_przejscie_pieszo_z_czasem(install_day):
    install_day(_dzien_z_przesiadka_pieszo())
    scheme = planner.plan_flow("S", "E", WHEN, more=3, new_map=True)["scheme"]
    n = len(scheme["nodes"])
    assert len(scheme["pairs"]) == n * (n - 1) // 2
    name = {x["id"]: x.get("place") or x["role"] for x in scheme["nodes"]}
    por_nazwach = {frozenset((name[p["from"]], name[p["to"]])): p["min"] for p in scheme["pairs"]}
    # Przejście z mapy (krawędź 120 s) liczy się tak jak planista: do pełnej minuty.
    assert por_nazwach[frozenset(("A", "B"))] == 2


def _przejscia(page):
    return page.evaluate("document.querySelectorAll('g.scheme-walk').length")


PARY = [{"from": "n0", "to": "n1", "min": 7}, {"from": "n1", "to": "n2", "min": 2},
        {"from": "n2", "to": "n3", "min": 9}, {"from": "n1", "to": "n4", "min": 4},
        {"from": "n0", "to": "n5", "min": 20}, {"from": "n3", "to": "n4", "min": 5}]


def _schemat_z_parami(przegladarka, adres, uklad, tryb, styl, ile=3):
    elk = []
    context, page = _strona(przegladarka, adres, elk, uklad)
    page.evaluate("document.getElementById('new-map').checked = true")
    page.evaluate("""([m, s, c]) => {
        document.getElementById('scheme-walks-mode').value = m;
        document.getElementById('scheme-walks-style').value = s;
        document.getElementById('scheme-walks-count').value = c;
    }""", [tryb, styl, ile])
    page.evaluate("data => renderScheme(data)",
                  {"scheme": {**RECZNY, "pairs": PARY}, "start": "S", "end": "E", "nodes": []})
    page.wait_for_selector("#scheme-panel:not([hidden]) svg", timeout=10000)
    return context, page


@pytest.mark.parametrize("uklad", ["dagre", "elk"])
@pytest.mark.parametrize("styl,klasa", [("brace", "scheme-walk-brace"), ("dash", "scheme-walk-dash")])
def test_przejscia_pieszo_tryb_wszystkie_i_najkrotsze(adres, przegladarka, uklad, styl, klasa):
    context, page = _schemat_z_parami(przegladarka, adres, uklad, "all", styl)
    assert page.evaluate(f"document.querySelectorAll('g.{klasa}').length") == len(PARY)
    assert page.evaluate("document.querySelector('g.scheme-walk path').getAttribute('stroke')") is None
    assert page.errors == []
    context.close()
    context, page = _schemat_z_parami(przegladarka, adres, uklad, "few", styl, 2)
    assert _przejscia(page) == 2
    assert sorted(page.evaluate("""[...document.querySelectorAll('.scheme-walk-text')]
                                  .map(t => t.textContent)""")) == ["pieszo, 2 min", "pieszo, 4 min"]
    assert page.errors == []
    context.close()


@pytest.mark.parametrize("uklad", ["dagre", "elk"])
@pytest.mark.parametrize("styl", ["brace", "dash"])
def test_przejscia_pieszo_po_najechaniu_na_przystanek(adres, przegladarka, uklad, styl):
    context, page = _schemat_z_parami(przegladarka, adres, uklad, "hover", styl)
    assert _przejscia(page) == 0
    page.dispatch_event("g.node[data-node='n1']", "pointerenter", {"pointerType": "mouse"})
    assert _przejscia(page) == 3
    page.dispatch_event("g.node[data-node='n1']", "pointerleave", {"pointerType": "mouse"})
    assert _przejscia(page) == 0
    assert page.errors == []
    context.close()


@pytest.mark.parametrize("uklad", ["dagre", "elk"])
def test_klamry_nie_nakladaja_sie_na_siebie(adres, przegladarka, uklad):
    context, page = _schemat_z_parami(przegladarka, adres, uklad, "all", "brace")
    pionowe = page.evaluate("""[...document.querySelectorAll('g.scheme-walk-brace path')].map(p => {
        const box = p.getBBox();
        return {x: box.x + box.width, top: box.y, bottom: box.y + box.height};
    })""")
    for i, a in enumerate(pionowe):
        for b in pionowe[i + 1:]:
            assert not (abs(a["x"] - b["x"]) < 10 and a["top"] < b["bottom"] and b["top"] < a["bottom"])
    context.close()
