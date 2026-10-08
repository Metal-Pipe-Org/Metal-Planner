# The flow-map algorithm, explained with worked examples

This document covers exactly one thing: `plan_flow` in `planner.py`, the
function behind the map you see in the UI (the "show every useful trip at
once" view). Everything else — the data model, the single-route CSA scan,
the frontend — is left out on purpose. *What* the map promises is in
[FLOW_MAP_CONTRACT.md](FLOW_MAP_CONTRACT.md); this file is about *how*.

`plan_flow` decides what to draw with the value map (`_value_map`, issue
#150) - since issue #187 the only map, also when the trip starts on board
a vehicle. The old brightness map is gone.

Two terms you need before any of this makes sense:

1. A **connection** is one scheduled hop between two consecutive stops of
   one bus/tram run — `(departure_time, arrival_time, from_stop, to_stop,
   trip_id)`. A whole day's connections sit in one list, sorted by
   departure time.
2. **CSA** (Connection Scan Algorithm) means: don't build a graph, just
   sweep that sorted list once, keeping track of the best arrival time found
   so far at every stop as you go.

What frames the map:

3. **The fastest arrival** `best_arr`, from the ordinary single-route scan
   (`_scan`). Everything else is measured against it.
4. **A threshold chosen for readability, not set in minutes.** Showing
   everything is the same as showing nothing, so the threshold moves until
   the drawn network has a target density: total length of *distinct*
   corridors divided by the side of the frame the relation is shown in
   (`_corridor_km`, `_map_density`). Lines lying on top of each other count
   once. The target is a slider under ⚙ (default 2.5); "Pokaż więcej"
   ("show more") multiplies it by 2, 3 and 4, and every click is guaranteed
   to add something — if the new target changes nothing, the threshold keeps
   moving to the first thing that is actually new.
5. **Everything after the choice**: cutting into pieces, real street
   geometry, corridor number groups, transfer dots, cars and bikes
   (`_finalize_segments` onwards).

---

# The value map

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

The important difference from ordinary CSA is what gets **dropped**. An
earlier arrival or a later departure must not prune anything along the
way, because the threshold may later forgive the worse label and it has to
come back. A label is dropped only when it has lost for good: it left more
than the search window earlier than another label standing at the same
place no later. The search window starts at 20 minutes past `best_arr` and
grows in 5-minute steps up to 40 only when the map needs it — the cost
grows steeply with the width, because nothing else cuts off "riding around
while waiting".

Labels with the same minutes are **twins**: they travel on as one label and
reach the map together. Two lines arriving at a transfer in the same minute
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

## Step 3 — How many minutes does a trip need to be forgiven? (`_value_entries`)

Each trip gets a tolerance: the **sum** of

8. how much later it arrives than the fastest trip, and
9. how much earlier it leaves than a trip that arrives no later.

In other words: how many minutes longer this journey takes. For the
example:

| Trip | Late | Leaves earlier than needed | Tolerance |
|---|---|---|---|
| A | 0 | 0 | **0** |
| B | 8 | 0 | **8** |
| D | 8 | 8 (B arrives the same and leaves 19:40) | 16 |
| E | 11 | 0 | **11** |

## Step 4 — Same line numbers, same trip (`_same_numbers_best`)

Tolerance alone would eventually let C in: it is exactly as good as B in
minutes. But C is B with one more vehicle added. The rule: a variant
disappears **at every threshold** when another trip rides a subset of its
line numbers (or all of them), is no worse in minutes, and — if the
numbers are identical — is better in something. So:

10. **C** never appears: B rides {5, 17} ⊂ {5, 14, 17} and is no worse.
11. **D** never appears either: same numbers as B, and B leaves later for
    the same arrival.
12. **E** stays, even though it is slower than B: different numbers make it
    a different route.

Among twins with the same numbers, the variant that **walks where the same
vehicle would have carried it** goes first
(`_rides_instead_of_walking` — e.g. ten minutes on foot along the route of
the tram you could have stayed on, or caught three minutes from your bus),
and of the rest the shortest ride wins (so "ride one stop past the
transfer and come back on the same run" is a tail, not a variant).

## Step 5 — Pick the threshold and draw (`_value_map`, `_value_segments`)

The candidate thresholds are the distinct tolerances: 0, 8, 11, … The map
takes the widest one whose drawn network is not denser than the target.
Say the target allows 8: the map shows A and B. One "Pokaż więcej" raises
the target and E (11) comes in. C and D never do.

Drawing: the rides of the chosen trips are grouped by vehicle run; rides of
the same run that touch or overlap become one piece, so a line never lies
on top of itself. There is no brightness — every piece is drawn the same
(`w` = 1). From here the shared machinery takes over (point 5 above).

Each piece also carries `why` for the Debug preview: up to three trips
through it, the tolerance at which each entered and why, and the variants
hidden by the line-number rule.

---

## Step 6 — Reading the sidebar's proposal list off the same map

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
Move the threshold (the density target, "Pokaż więcej") and the list
shrinks or grows in lockstep with the map, automatically, for free.

If the map ever selects nothing despite a connection provably existing,
the map and list both fall back to drawing that one proven-fastest route
directly (`degraded`), so neither one is ever left showing nothing.
