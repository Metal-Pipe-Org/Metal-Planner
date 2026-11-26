# The flow-map algorithm, explained with worked examples

This document covers exactly one thing: how `plan_flow_stages` in `planner.py` decides what the map you see in the UI draws. Everything else — the data model, the single-route CSA scan, the frontend — is left out on purpose. This file is about *how*.

The map draws a few sensible **moves** from the start to the target (`_moves_map` in `planner.py`, the choice itself in `moves.py`), also when the trip starts on board a vehicle — see "The moves map" below. The trips the moves are made of come from one multi-criteria search, described in "The search behind the moves". The old brightness map and the value map (`_value_map`, issue #150, with its density threshold and "Pokaż więcej") are gone.

Two terms you need before any of this makes sense:

1. A **connection** is one scheduled hop between two consecutive stops of
   one bus/tram run — `(departure_time, arrival_time, from_stop, to_stop,
   trip_id)`. A whole day's connections sit in one list, sorted by
   departure time.
2. **CSA** (Connection Scan Algorithm) means: don't build a graph, just
   sweep that sorted list once, keeping track of the best arrival time found
   so far at every stop as you go.

---

# The moves map

A passenger does not choose between lines or departures but between moves: "from here I get on and ride there". Five buses along the same road are one move; two stops next to each other are two moves.

**Search.** The trips come from the multi-criteria scan (`_value_journeys`, Step 2 below), started two minutes before the asked time (a vehicle that has just left may be late), with walking from the start and to the target allowed up to 1.5 km (`MOVES_WALK_M`): whether a walk pays off is decided by its minutes in the trip's cost, not by a hard radius. Each trip is split into its line-number variants (Step 4), and each variant becomes an *instance*: one concrete trip with its rides, departure and arrival. A move is every instance with the same signature — the same sequence of (boarding place, alighting place) pairs.

**Choice** (`moves.choose`, one user heuristic per step in `moves.STEPS`). The cost of a trip is its arrival plus `TRANSFER_GAIN_SEC` per transfer. In order: only trips at most 20 minutes costlier than the best catchable one; the same vehicles with a different transfer place are one trip; one instance per departure of the first vehicle; a move stays only when no other move beats it clearly (by 5 minutes or by a transfer) without being worse in arrival, departure or number of vehicles; a tight transfer gets the variant where the same vehicle rides on next to it; and when every move is tight, the best move with a margin is added. When none of the shown moves has a margin at all, the search runs again with a window twice as wide.

**First view** (`moves.readable`). The fastest trip from the bar above the map is the spine; next to it at most two more moves (`READABLE_MOVES` = 3), and only really different ones: no vehicle passes another shown move's transfer place and no two moves change at the same place. The last slot goes, when there is one, to a move that lets you leave clearly later than all the others. Moves with a walk of 15 minutes or more at either end are not drawn (unless every move has one). Everything else stays one click away: the departure board of a stop.

**Drawing.** Each shown move is drawn as its first instance plus its *branches* — other ways to reach the same last vehicle (`moves.add_branches`). The rides of one vehicle run that touch or overlap become one piece (`_value_segments`), so a line never lies on top of itself, and every piece is drawn the same (`w` = 1). Then the shared machinery takes over: cutting into pieces, real street geometry, corridor number groups, transfer dots (`_finalize_segments` onwards). There is no density threshold and no "Pokaż więcej". City bikes and Traficar are judged the same way, as separate options next to public transport: a bike where it replaces waiting or a transfer (`_bike_moves`), a car only when it belongs to the drawn trip. The scheme next to the map ("Schemat obok mapy") is drawn from the same moves.

**Nothing found.** When the moves find nothing (`_moves_map` returns `None`), the search answers with the ordinary "no connection" error. There is no fallback map any more. "Any station in the city" ("WROCŁAW -") is not supported either: such a name is unknown like any other.

---

# The search behind the moves

The running example is the one that started it all (issue #150), with the
times simplified: **Pl. Zgody → Klecina**, asked at **19:30**.

## Step 1 — Describe every trip by what a passenger cares about

A person choosing between trips doesn't look at "brightness". They look at
**when they get there** and **when they have to leave** — leaving five
minutes later for the same arrival is sitting at home five minutes longer.
Transfers and walking matter too, but only through those two numbers: a
longer walk is a later arrival or an earlier departure.

Suppose these are the trips that exist:

| Trip | Lines | Leave | Arrive |
|---|---|---|---|
| A | 5 → 7 | 19:40 | 20:15 |
| B | 5 → 17 | 19:40 | 20:23 |
| C | 5 → 14 → 17 | 19:40 | 20:23 |
| D | 5 → 17 (an earlier 5) | 19:32 | 20:23 |
| E | 125 | 19:50 | 20:26 |

C is the real case from the issue: at Borek the 14 leaves first, takes you
two stops on, and you wait there for the very 17 you could have boarded at
Borek. Same departure, same arrival, one more transfer.

## Step 2 — Find them all in one pass (`_value_journeys`)

This is CSA with multi-criteria labels, like McRAPTOR but over connections.
A label is "a way of being here": from when you can board (arrival plus
the one-minute transfer buffer), the arrival itself, when you left the
origin, and how many vehicles you've used (at most 5). Walking follows the
same rule as everywhere else: from the origin, once after getting off, and
to the target — never twice in a row.

The important difference from ordinary CSA is what gets **dropped**. An earlier arrival or a later departure must not prune anything along the way, because the moves' reasonable window may still want the worse label. A label is dropped only when it has lost for good: it left more than the search window earlier than another label standing at the same place no later. The window is set by the moves map: twice the transfer penalty plus the reasonable window past `best_arr`, at most 55 minutes (`MOVES_SEARCH_MAX_SEC`). The cost grows steeply with the width, because nothing else cuts off "riding around while waiting".

Labels with the same minutes are **twins**: they travel on as one label and
reach the moves together. Two lines arriving at a transfer in the same minute
are not "one of them is redundant"; they are "take whichever comes first".

At the target, trips group by `(minute of arrival, minute of departure,
number of vehicles)`.

**On board a vehicle** (`seated`, see `onboard.py`) the origin is the next
stop and the clock starts when the vehicle leaves it. Staying in that
vehicle needs no buffer; any other vehicle from that stop is a transfer and
gets the buffer — the same rule as `_board_buffer` in the ordinary scan.
The vehicle you sit in counts as one of the trip's vehicles, so getting
off and switching is two. Every trip leaves at that same second: the
passenger is already travelling, so a later "departure" is worth nothing.

## Step 3 — Line-number variants (`_fill_variants`)

Each trip is split into variants by the line numbers it rides (C above is a variant of its own: 5 → 14 → 17). Among twins with the same numbers, the variant that **walks where the same
vehicle would have carried it** goes first
(`_rides_instead_of_walking` — e.g. ten minutes on foot along the route of
the tram you could have stayed on, or caught three minutes from your bus),
and of the rest the shortest ride wins (so "ride one stop past the
transfer and come back on the same run" is a tail, not a variant).

---

## Step 4 — Reading the sidebar's proposal list off the same map

Everything above describes one thing: the cloud of segments the map draws.
The ranked list of named proposals next to it ("18:45 → 19:22, 1 transfer")
is not a second algorithm — it's a direct reading of the exact same segment
cloud, taken *after* the map has already decided what's actually drawn.

Think of the drawn segments as a small map of "corridors" connected by real
transfer points. Producing a
proposal is then just: start at any corridor that begins at the true origin,
and walk forward through it, at every real transfer point either (a) you've
reached the destination — that's a complete proposal — or (b) you hop into
whichever next corridor you can really catch there. Stop once enough
distinct proposals are found or
the search has spent its (small) budget, then rank what's left by arrival
time, then by fewest transfers, then by least waiting.

**Exploring breadth-first, not one branch at a time.** The search doesn't
pick the single most promising starting point and follow it as deep as it
goes before trying anything else. It works level by level: every live branch — every starting point,
every fork reached along the way — gets a turn at the current depth before
any of them go one level deeper. A dense cluster of near-identical options
right at the start (several lines a stop or two apart, all about equally
good) used to be able to exhaust the entire search budget on trivial
variations of itself before a genuinely different corridor — one the map
was already drawing elsewhere — ever got a look, which is why proposals
could all come back looking like the same route wearing slightly different
middle legs. Working level by level means one rich neighborhood can no
longer starve out the rest; an exact repeat of an already-found proposal is
also thrown out the moment it's found, so it doesn't spend a slot a
different corridor could have used.

The payoff of doing it this way instead of running a separate search: a
proposal can *never* name a transfer that isn't actually drawn on the map,
because it's built from nothing but the map's own already-decided shape.
Whatever moves the map draws, the list shrinks or grows in lockstep with it, automatically, for free.

---

## Speeding the search up further (ideas that change the result, not implemented)

Almost all of the search time now goes into `_value_journeys` — the labels of the journey search within the 40-minute departure window — and into the second, wider pass that the moves map runs when the first one finds only tight transfers (everything else is a few hundredths of a second). The ideas below would make the search faster, but each of them can change what the map shows, so they were measured (25.11.2026, 27 routes, timings after the optimisations) and deliberately left out.

1. Skip the second, wider pass when every move is tight: worst case 13.0 s → 5.4 s, Kozanów → Krzyki at 14:15 11.8 s → 1.4 s, median unchanged. All 27 maps came out the same, but in general a move with some slack, which only the second pass would have found, could go missing.
2. A 30-minute window instead of 40 (55 for the wider pass): median 1.42 s → 0.64 s, worst case 13.0 s → 7.0 s. All 27 maps came out the same, but in general a journey with 2–3 transfers that still fits the reasonable window could drop out. A 45-minute window gains little (median 1.55 s).
3. At most 2 vehicles instead of 3: median 0.60 s, worst case 3.7 s, but 9 of 27 maps change. Not recommended.
4. A wider search from the start instead of two passes: slower (median 5.0 s) and 5 maps change. Not recommended.

27 routes is a small sample. Before adopting idea 1 or 2, compare the maps on a much larger random set of routes and departure times.
