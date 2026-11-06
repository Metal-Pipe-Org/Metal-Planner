"""Wybór podróży jako RUCHÓW (zgłoszenie użytkownika z 2026-11-06).

Pasażer nie wybiera między liniami ani odjazdami, tylko między ruchami: „z tego
miejsca wsiadam i dojeżdżam tam". Pięć autobusów tą samą drogą to jeden ruch,
a dwa przystanki obok siebie to dwa ruchy. Ten moduł zamienia wyszukane
podróże (planner._value_journeys + warianty numerów) w listę ruchów do
pokazania, w kolejnych KROKACH - każdy krok to jedna heurystyka użytkownika,
napisana jako osobna funkcja na liście STEPS. Nowa heurystyka to nowy krok
albo zmiana stałej; reszta zostaje.

Moduł nie zna plannera: dostaje dzień rozkładu i same podróże.

Egzemplarz (instance) to jedna konkretna podróż: kolejne przejazdy (wsiadanie,
wysiadanie), godzina wyjścia i przyjazd. Ruch to wszystkie egzemplarze o tej
samej sygnaturze - ciągu par (miejsce wsiadania, miejsce wysiadania).
"""
from bisect import bisect_left
from statistics import median

import gtfs

# --- strojenie (wszystko w sekundach) ---

# Odjazd sprzed chwili też się pokazuje: pojazd mógł się spóźnić (heurystyka 2).
JUST_LEFT_SEC = 2 * 60

# „W granicach rozsądku" (heurystyki 2 i 3): podróż jest do pokazania, gdy jej
# koszt - przyjazd plus kara za każdą przesiadkę - nie przekracza kosztu
# najlepszej o więcej niż ułamek jej długości, nie mniej niż minimum.
# Kara za przesiadkę to ta sama TRANSFER_GAIN_SEC co na liście propozycji:
# jazda bez przesiadki liczy się sama w sobie.
REASONABLE_MIN_SEC = 20 * 60
REASONABLE_FRACTION = 0.6

# Duża przerwa między odjazdami ruchu (heurystyka 2): dłuższa od minimum
# i od wielokrotności przerw dotychczasowej zbitki. Częste linie (co 5 min)
# mają więc wyższy próg niż rzadkie.
GAP_MIN_SEC = 8 * 60
GAP_FACTOR = 2.5
# Nic absurdalnie dalekiego: pierwszy odjazd, na który da się zdążyć, nie
# może być później niż tyle po zapytaniu, a zbitka nie sięga dalej.
HORIZON_SEC = 90 * 60
MAX_DEPARTURES = 8

# Przesiadka z takim zapasem (ponad sam marsz) jest „na styk" (heurystyka 4).
TIGHT_SEC = 3 * 60

# Różnica, od której jeden ruch wyraźnie bije drugi (koszt albo godzina wyjścia).
EPSILON_SEC = 5 * 60

# Więcej niż tyle pojazdów w jednej podróży to objazd miasta, nie ruch.
MAX_RIDES = 3

# Dojście ze startu albo do celu dłuższe niż tyle to „daleki spacer": taki ruch
# nie wchodzi do pierwszego widoku.
LONG_WALK_SEC = 15 * 60

# Pierwszy widok nowej mapy: tyle różnych ruchów jest jeszcze czytelnych (na
# mapie i w schemacie). Pozostałe nie są rysowane.
READABLE_MOVES = 3

# Aut i rowerów przy mapie w pierwszym widoku (rowery i Traficar biorą zasięg z
# narysowanych ruchów, więc im mniej ruchów, tym mniej ich w ogóle).
FIRST_VIEW_VEHICLES = 1

# Marsz liczy się w minutach przyjazdu, ale dłuższe chodzenie męczy bardziej niż
# siedzenie: tyle razy sekundy marszu doliczają się do kosztu ponad ich wpływ
# na przyjazd (heurystyka 6: bez twardego limitu, ale z ceną).
WALK_EXTRA = 1.0

# Wariant „ten sam pojazd jedzie dalej" może dowozić później niż ruch na styk,
# ale nie o więcej niż tyle.
FALLBACK_LATER_SEC = 15 * 60


def place(day, stop):
    return day.place_of.get(stop, stop)


def instances_of(day, classes, query_sec):
    """Egzemplarze ze wszystkich klas podróży (po jednym na wariant numerów)."""
    conns = day.conns
    out = []
    for cls in classes:
        for nums, rides in cls["kept"]:
            ordered = tuple(sorted(rides, key=lambda r: conns[r[0]][0]))
            first = conns[ordered[0][0]][0]
            walk = ((first - cls["dep"]) + (cls["arr"] - conns[ordered[-1][1]][1])
                    + sum(gtfs.walk_seconds(day, conns[a][3], conns[b][2])
                          for (_, a), (b, _) in zip(ordered, ordered[1:])
                          if conns[a][3] != conns[b][2]))
            out.append({
                "walk": walk,
                "lead": first - cls["dep"],
                "tail": cls["arr"] - conns[ordered[-1][1]][1],
                "rides": ordered,
                "n": len(ordered),
                "arr": cls["arr"],
                "dep": cls["dep"],
                "nums": nums,
                "first": first,
                "sig": tuple((place(day, conns[b][2]), place(day, conns[a][3]))
                             for b, a in ordered),
                "just_left": cls["dep"] < query_sec,
            })
    return out


def cost(inst, gain_sec):
    return inst["arr"] + gain_sec * (inst["n"] - 1) + WALK_EXTRA * inst["walk"]


def reasonable_sec(best_arr, query_sec, wider=0):
    base = max(REASONABLE_MIN_SEC, REASONABLE_FRACTION * (best_arr - query_sec))
    return base * (1 + 0.5 * wider)


# --- kroki ---

def step_reasonable(ctx, insts):
    """Heurystyki 2 i 3: tylko podróże w granicach rozsądku wobec najlepszej."""
    catchable = [i for i in insts if not i["just_left"]]
    best = min(cost(i, ctx["gain_sec"]) for i in catchable or insts)
    limit = best + ctx["reasonable_sec"]
    return [i for i in insts if cost(i, ctx["gain_sec"]) <= limit]


def step_same_vehicles(ctx, insts):
    """Te same pojazdy z innym miejscem przesiadki to jedna podróż: Karwińska,
    Park Wschodni i pętla przy tym samym tramwaju i tym samym autobusie nie są
    trzema ruchami. Zostaje ta, która dowozi najwcześniej, a z równych - ta
    z późniejszym wyjściem (bliższy przystanek), większym zapasem na przesiadce
    i wcześniejszą wysiadką."""
    day = ctx["day"]
    best = {}
    group = {}
    for inst in insts:
        key = tuple(day.conns[board][4] for board, _ in inst["rides"])
        group.setdefault(key, []).append(inst["rides"])
        old = best.get(key)
        if old is None:
            best[key] = inst
            continue

        def rank(i):
            slack = slack_of(day, i)
            return (i["arr"], -i["dep"], -(slack if slack is not None else 10 ** 6),
                    walked_sec(day, i), sum(alight for _, alight in i["rides"]))
        if rank(inst) < rank(old):
            best[key] = inst
    for key, inst in best.items():
        inst["group"] = group[key]
    return list(best.values())


def step_one_per_departure(ctx, insts):
    """Ruch ma jeden egzemplarz na odjazd pierwszego pojazdu: ten, który
    dowozi najwcześniej (te same minuty - mniej przesiadek)."""
    best = {}
    for inst in insts:
        key = (inst["sig"], inst["first"])
        old = best.get(key)
        if old is None or (inst["arr"], inst["n"]) < (old["arr"], old["n"]):
            best[key] = inst
    return list(best.values())


def walked_sec(day, inst):
    """Ile sekund marszu między słupkami przy przesiadkach egzemplarza."""
    if "walked" not in inst:
        conns = day.conns
        inst["walked"] = sum(
            gtfs.walk_seconds(day, conns[a][3], conns[b][2])
            for (_, a), (b, _) in zip(inst["rides"], inst["rides"][1:])
            if conns[a][3] != conns[b][2])
    return inst["walked"]


def slack_of(day, inst):
    """Najmniejszy zapas przesiadki egzemplarza w sekundach (None bez przesiadek)."""
    if "slack" not in inst:
        slacks = transfer_slacks(day, inst)
        inst["slack"] = min(slacks)[0] if slacks else None
    return inst["slack"]


def is_safe(day, inst):
    slack = slack_of(day, inst)
    return slack is None or slack > TIGHT_SEC


def dominates(better, worse):
    """Czy `better` wypiera `worse`: dociera nie później, wychodzi nie wcześniej,
    ma nie więcej przesiadek - i w czymś wyraźnie lepszy: o całe EPSILON_SEC
    albo o przesiadkę. Dwie minuty różnicy w wyjściu to nie powód, żeby ukryć
    ruch, który pasażer zna i woli."""
    if (better["arr"] > worse["arr"] or better["dep"] < worse["dep"]
            or better["n"] > worse["n"]):
        return False
    return (worse["arr"] - better["arr"] >= EPSILON_SEC
            or better["dep"] - worse["dep"] >= EPSILON_SEC
            or better["n"] < worse["n"])


def step_not_dominated(ctx, insts):
    """Heurystyki 3 i 6: ruch zostaje, gdy w czymś nie ustępuje innym -
    bezpośredni dojazd mimo późniejszego przyjazdu, dalszy przystanek, na
    którym coś się łapie tylko tam. Egzemplarze jednego ruchu nie wypierają
    się nawzajem - cała zbitka odjazdów ma być pokazana."""
    by_sig = {}
    for inst in insts:
        by_sig.setdefault(inst["sig"], []).append(inst)
    ordered = sorted(insts, key=lambda i: cost(i, ctx["gain_sec"]))
    alive = set()
    for sig, own in by_sig.items():
        for x in own:
            if not any(y["sig"] != sig and dominates(y, x) for y in ordered):
                alive.add(sig)
                break
    return [i for i in insts if i["sig"] in alive]


def step_insurance(ctx, insts):
    """Heurystyka 4: gdy wszystkie ruchy są na styk, obok nich musi być
    najlepszy ruch z zapasem, żeby było dokąd wrócić, gdy uciekł."""
    day, gain = ctx["day"], ctx["gain_sec"]
    def safe(inst):
        return is_safe(day, inst)
    firsts = {}
    for inst in sorted(insts, key=lambda i: i["dep"]):
        if not inst["just_left"]:
            firsts.setdefault(inst["sig"], inst)
    if not firsts or any(safe(i) for i in firsts.values()):
        return insts
    shown = {i["sig"] for i in insts}
    pool_firsts = {}
    for inst in sorted(ctx["pool"], key=lambda i: i["dep"]):
        if not inst["just_left"]:
            pool_firsts.setdefault(inst["sig"], inst)
    best_safe = min((i for i in pool_firsts.values()
                     if i["sig"] not in shown and safe(i)),
                    key=lambda i: cost(i, gain), default=None)
    if best_safe is None:
        return insts
    return insts + [i for i in ctx["pool"] if i["sig"] == best_safe["sig"]]


def step_same_vehicle_fallback(ctx, insts):
    """Heurystyka 5: przesiadka na styk ma obok siebie wariant z TYM SAMYM
    pojazdem jadącym dalej - tramwaj dowiezie na pętlę, gdzie wsiada się w
    cokolwiek, jeśli autobus z przystanku wcześniej uciekł. Ruch, który
    wygrał w minutach, a jest na styk, nie może zabrać pasażerowi tego wyjścia.
    Dołożony wariant sam musi mieć zapas, inaczej nie jest wyjściem."""
    day, gain = ctx["day"], ctx["gain_sec"]
    shown_sigs = {i["sig"] for i in insts}
    best_of = {}
    for inst in insts:
        if not inst["just_left"] and (inst["sig"] not in best_of
                                      or cost(inst, gain) < cost(best_of[inst["sig"]], gain)):
            best_of[inst["sig"]] = inst
    extra = []
    for inst in best_of.values():
        slacks = transfer_slacks(day, inst)
        if not slacks:
            continue
        sec, k = min(slacks)
        if sec > TIGHT_SEC:
            continue
        prefix = _trips(day, inst, k)
        fallbacks = [y for y in ctx["pool"]
                     if not y["just_left"]
                     and k < y["n"] <= inst["n"]
                     and y["arr"] <= inst["arr"] + FALLBACK_LATER_SEC
                     and _trips(day, y, k) == prefix
                     and y["sig"][k - 1][1] != inst["sig"][k - 1][1]
                     and is_safe(day, y)]
        if fallbacks:
            chosen = min(fallbacks, key=lambda y: cost(y, gain))
            if chosen["sig"] in shown_sigs:
                continue
            shown_sigs.add(chosen["sig"])
            extra += [y for y in ctx["pool"] if y["sig"] == chosen["sig"]]
    return insts + extra


def _trips(day, inst, count):
    return tuple(day.conns[board][4] for board, _ in inst["rides"][:count])


def departures_of_move(ctx, own, everything):
    """Heurystyka 2: odjazdy od tego sprzed chwili, przez całą zbitkę bliskich,
    do pierwszej dużej przerwy - z rozsądkiem - plus pierwszy po niej."""
    own = sorted(own, key=lambda i: i["dep"])
    query = ctx["query_sec"]
    horizon = query + HORIZON_SEC
    catchable = [i for i in own if not i["just_left"]]
    if not catchable or catchable[0]["dep"] > horizon:
        return []
    shown, gaps = [], []
    for k, inst in enumerate(own):
        shown.append(inst)
        if len(shown) >= MAX_DEPARTURES or inst["dep"] > horizon:
            return shown
        if k + 1 == len(own):
            break
        gap = own[k + 1]["dep"] - inst["dep"]
        threshold = max(GAP_MIN_SEC, GAP_FACTOR * median(gaps)) if gaps else GAP_MIN_SEC
        if gap > threshold:
            shown.append(own[k + 1])
            return shown
        gaps.append(gap)
    # Zbitka skończyła się granicą rozsądku, nie przerwą: kolejny odjazd (choć
    # już nie wart wyboru) mówi, na ile można się spóźnić.
    later = [i for i in everything if i["dep"] > shown[-1]["dep"]
             and i["dep"] <= horizon]
    if later:
        shown.append(min(later, key=lambda i: i["dep"]))
    return shown


def choose(day, insts, query_sec, best_arr, gain_sec, wider=0, prefer=None):
    """(ruchy do pokazania, ile ukryto): [{sig, shown: [egzemplarze], first,
    cost, far_walk}] od najlepszego. Pusta lista, gdy nic nie ma."""
    if not insts:
        return [], 0, True
    ctx = {"day": day, "query_sec": query_sec, "gain_sec": gain_sec,
           "reasonable_sec": reasonable_sec(best_arr, query_sec, wider)}
    ctx["pool"] = sorted(step_one_per_departure(ctx, step_same_vehicles(
        ctx, step_reasonable(ctx, insts))),
                         key=lambda i: cost(i, gain_sec))
    by_sig_all = {}
    for inst in insts:
        by_sig_all.setdefault(inst["sig"], []).append(inst)
    chosen = insts
    for step in STEPS:
        chosen = step(ctx, chosen)
    by_sig = {}
    for inst in chosen:
        by_sig.setdefault(inst["sig"], []).append(inst)
    moves = []
    for sig, own in by_sig.items():
        shown = departures_of_move(ctx, own, by_sig_all[sig])
        if not shown:
            continue
        first = next(i for i in shown if not i["just_left"])
        moves.append({"sig": sig, "shown": shown, "first": first,
                      "cost": cost(first, gain_sec)})
    anchor = fastest(day, moves, prefer)
    moves = distinct_last_vehicle(day, moves, anchor)
    for move in moves:
        move["far_walk"] = max(move["first"]["lead"], move["first"]["tail"]) >= LONG_WALK_SEC
    if all(move["far_walk"] for move in moves):
        for move in moves:
            move["far_walk"] = False
    moves.sort(key=lambda m: (m["far_walk"], m["cost"], -m["first"]["dep"]))
    shown, hidden = readable(day, moves, anchor)
    return shown, hidden, any(is_safe(day, m["first"]) for m in moves)


def distinct_last_vehicle(day, moves, anchor):
    """Ruchy kończące się TYM SAMYM pojazdem (ten sam kurs ostatniego
    przejazdu) to jedna podróż dowożona z różnych miejsc - nie są „naprawdę
    różne" i na mapie leżałyby jedna na drugiej (ten sam autobus raz jako
    kontynuacja, raz jako osobna przesiadka). Zostaje lepszy: tańszy, z równych
    - z większym zapasem na przesiadce."""
    best = {}
    for move in moves:
        first = move["first"]
        key = day.conns[first["rides"][-1][0]][4]
        slack = slack_of(day, first)
        rank = (move is not anchor, move["cost"],
                -(slack if slack is not None else 10 ** 6), -first["dep"])
        if key not in best or rank < best[key][0]:
            best[key] = (rank, move)
    return [move for _, move in best.values()]


def fastest(day, moves, prefer=None):
    """Ruch z najwcześniejszym przyjazdem: najszybsza podróż jest na mapie
    zawsze, bo pasek nad mapą i schemat o niej mówią. Z równych tym samym
    przyjazdem wygrywa ta, którą pasek nazywa (`prefer` = numery jej linii po
    kolei), potem tańsza."""
    def nums(move):
        return tuple(day.trip_info[day.conns[b][4]][0].rsplit(" ", 1)[-1]
                     for b, _ in move["first"]["rides"])
    return min(moves, key=lambda m: (m["first"]["arr"], nums(m) != prefer, m["cost"]))


def passes_and_transfers(day, rides):
    """(miejsca, których dotyka ruch, miejsca PRZEJŚĆ pieszo między różnymi
    miejscami). Ruch dotyka miejsca, gdy jego pojazd je mija, wsiada w nim albo
    wysiada. Przejście między miejscami (MDK -> Dworzec Główny) to klamra na
    schemacie, a cudza linia, która tam wjeżdża albo tamtędy przejeżdża,
    nie daje się już narysować prosto przez pudełko; zmiana słupka w obrębie
    jednego miejsca (tramwaj i autobus na Parku Wschodnim) nią nie jest."""
    conns = day.conns
    touched, walks = set(), set()
    for k, (board, alight) in enumerate(rides):
        idxs = gtfs.trip_conns(day, conns[board][4])
        lo, hi = idxs.index(board), idxs.index(alight)
        touched |= {place(day, conns[i][3]) for i in idxs[lo:hi + 1]}
        touched.add(place(day, conns[board][2]))
        if k:
            there = place(day, conns[board][2])
            here = place(day, conns[rides[k - 1][1]][3])
            if there != here:
                walks |= {there, here}
    return touched, walks


def clashes(one, other):
    """Ile razy ruch wchodzi w cudzą klamrę przejścia pieszo."""
    return len(one[0] & other[1]) + len(other[0] & one[1])


def variants_of(day, inst):
    """Te same pojazdy, to samo wsiadanie i ten sam przyjazd, tylko inne miejsce
    przesiadki (Karwińska, Park Wschodni, pętla): pierwszy to wybrany."""
    conns = day.conns
    first, last = inst["rides"][0][0], inst["rides"][-1][1]
    out = [inst["rides"]]
    for rides in inst.get("group", ()):
        if (rides not in out and rides[0][0] == first
                and conns[rides[-1][1]][1] == conns[last][1]
                and conns[rides[-1][1]][3] == conns[last][3]):
            out.append(rides)
    return out


def readable(day, moves, anchor):
    """Pierwszy widok to najszybszy ruch i do READABLE_MOVES - 1 kolejnych,
    NAPRAWDĘ różnych: żaden pojazd nie mija miejsca przesiadki innego pokazanego
    ruchu i dwa ruchy nie przesiadają się w tym samym miejscu - inaczej linie
    przechodzą przez cudze przesiadki, a mapa i schemat zamieniają się w splot.
    Miejsce przesiadki tych samych pojazdów wybiera się tak, żeby ruchy sobie
    nie przeszkadzały (variants_of). Dalekie spacery i wszystko, co się nie
    mieści, nie jest rysowane - dostęp zostaje przez kliknięcie w przystanek
    (tablica odjazdów). Zwraca (pokazane, ile ukryto, ...)."""
    others = [passes_and_transfers(day, m["first"]["rides"])
              for m in moves if m is not anchor and not m["far_walk"]]

    def settle(move, against):
        inst = move["first"]
        options = variants_of(day, inst)
        scored = [(sum(clashes(passes_and_transfers(day, rides), o) for o in against), k, rides)
                  for k, rides in enumerate(options)]
        clash, _, rides = min(scored)
        if rides != inst["rides"]:
            move["first"] = dict(inst, rides=rides, n=len(rides))
            move["first"].pop("slack", None)
            move["first"].pop("walked", None)
        return clash, passes_and_transfers(day, move["first"]["rides"])

    _, anchor_info = settle(anchor, others)
    shown, infos = [anchor], [anchor_info]
    for move in moves:
        if len(shown) >= READABLE_MOVES:
            break
        if move is anchor or move["far_walk"]:
            continue
        clash, info = settle(move, infos)
        if clash:
            continue
        shown.append(move)
        infos.append(info)
    shown.sort(key=lambda m: (m["far_walk"], m["cost"]))
    return shown, len(moves) - len(shown)


STEPS = [step_reasonable, step_same_vehicles, step_one_per_departure,
         step_not_dominated,
         step_same_vehicle_fallback, step_insurance]


# --- zapas przesiadki i następny odjazd ---

def transfer_slacks(day, inst):
    """[(zapas w sekundach, indeks przejazdu, na który się przesiada)]: ile
    zostaje po dojściu między słupkami, zanim odjedzie następny pojazd."""
    conns = day.conns
    out = []
    for k in range(1, len(inst["rides"])):
        prev_alight = inst["rides"][k - 1][1]
        board = inst["rides"][k][0]
        from_stop, to_stop = conns[prev_alight][3], conns[board][2]
        walk = 0 if from_stop == to_stop else gtfs.walk_seconds(day, from_stop, to_stop)
        out.append((conns[board][0] - (conns[prev_alight][1] + walk), k))
    return out


def next_departure(day, board_place, alight_place, after_sec):
    """Najbliższy odjazd (sekunda, linia) po `after_sec` z miejsca wsiadania
    kursem, który dojeżdża do miejsca wysiadania - dowolną linią: ruch to
    skąd-dokąd, nie którym numerem."""
    index = gtfs.deps_by_stop(day)
    found = []
    for stop in day.stops_by_place.get(board_place, (board_place,)):
        deps = index.get(stop, ())
        start = bisect_left(deps, (after_sec + 1,))
        for dep, trip in deps[start:start + 40]:
            idxs = gtfs.trip_conns(day, trip)
            seen = False
            for i in idxs:
                conn = day.conns[i]
                if conn[2] == stop and conn[0] == dep:
                    seen = True
                if seen and place(day, conn[3]) == alight_place:
                    found.append((dep, day.trip_info[trip][0]))
                    break
    return min(found) if found else None
