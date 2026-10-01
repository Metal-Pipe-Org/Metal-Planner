# The flow-map algorithm, explained with worked examples

This document covers exactly one thing: `plan_flow` in `planner.py`, the
function behind the map you see in the UI (the "show every useful trip at
once" view). Everything else — the data model, the single-route CSA scan,
the frontend — is left out on purpose. *What* the map promises is in
[FLOW_MAP_CONTRACT.md](FLOW_MAP_CONTRACT.md); this file is about *how*.

`plan_flow` has two modes that share a beginning and an end and differ in
the middle — in how they decide what to draw:

1. **Part 1 — the value map** (`_value_map`, issue #150). The default since
   2026-09-28 and the only one the current contract describes.
2. **Part 2 — the old brightness map.** Still used when the trip starts on
   board a vehicle (the value map doesn't handle that yet) and behind the
   "Stara mapa (z progiem jasności)" switch in Eksperymenty. Its guarantees
   (old contract points 3, 8 and 9) and their tests are still in the code.

Two terms you need before any of this makes sense:

3. A **connection** is one scheduled hop between two consecutive stops of
   one bus/tram run — `(departure_time, arrival_time, from_stop, to_stop,
   trip_id)`. A whole day's connections sit in one list, sorted by
   departure time.
4. **CSA** (Connection Scan Algorithm) means: don't build a graph, just
   sweep that sorted list once, keeping track of the best arrival time found
   so far at every stop as you go.

What both modes share:

5. **The fastest arrival** `best_arr`, from the ordinary single-route scan
   (`_scan`). Everything else is measured against it.
6. **A threshold chosen for readability, not set in minutes.** Showing
   everything is the same as showing nothing, so the threshold moves until
   the drawn network has a target density: total length of *distinct*
   corridors divided by the side of the frame the relation is shown in
   (`_corridor_km`, `_map_density`). Lines lying on top of each other count
   once. The target is a slider under ⚙ (default 2.5); "Pokaż więcej"
   ("show more") multiplies it by 2, 3 and 4, and every click is guaranteed
   to add something — if the new target changes nothing, the threshold keeps
   moving to the first thing that is actually new.
7. **Everything after the choice**: cutting into pieces, real street
   geometry, corridor number groups, transfer dots, cars and bikes
   (`_finalize_segments` onwards).

---

# Part 1 — The value map

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
transfer and come back on the same run" is a tail, not a variant). The
"Stare bliźniaki" switch turns off the first of these two steps.

## Step 5 — Pick the threshold and draw (`_value_map`, `_value_segments`)

The candidate thresholds are the distinct tolerances: 0, 8, 11, … The map
takes the widest one whose drawn network is not denser than the target.
Say the target allows 8: the map shows A and B. One "Pokaż więcej" raises
the target and E (11) comes in. C and D never do.

Drawing: the rides of the chosen trips are grouped by vehicle run; rides of
the same run that touch or overlap become one piece, so a line never lies
on top of itself. There is no brightness — every piece is drawn the same
(`w` = 1). From here the shared machinery takes over (point 7 above).

Each piece also carries `why` for the Debug preview: up to three trips
through it, the tolerance at which each entered and why, and the variants
hidden by the line-number rule.

---

# Part 2 — The old brightness map

Still live: on board a vehicle, and behind the "Stara mapa" switch. Read
"the time-window slider" in the historical notes below as "the deadline":
since 2026-09-13 it comes from the density target and "Pokaż więcej", not
from a slider, but it still moves, so the reasoning about what must stay
stable while it moves is unchanged.

The running example: a trip from **Rynek** (origin) to **Politechnika**
(destination), starting at **10:00**. The job is to decide, out of every
single trip running that day, which ones are worth drawing, from where to
where, and how brightly.

---

## Step 1 — Establish a deadline

You can't judge "is this trip good enough" without first deciding what
"good enough" means. So the first thing `plan_flow` does is run the
ordinary single-route CSA scan (the same one `plan_route` uses) just to find
the single fastest possible arrival:

```python
best_stop, best_arr, _ = _scan(day, source_stops, target_stops, dep_sec)
```

The deadline is then the latest full minute after `best_arr` at which the
drawn network still fits the density target (`_choose_deadline`, see point
6 above), but never more than 60 minutes after it. Each candidate deadline
means building the whole map below once, so instead of trying every minute
the search jumps ahead (each jump half again as long as the last) until the
map gets too dense, then halves the gap.

With our example: say the fastest way from Rynek to Politechnika takes 20
minutes, arriving **10:20**, and the density target is met at 10 extra
minutes. **Deadline = 10:30.**

Anything that would get you to Politechnika after 10:30 is simply not
interesting enough to draw. Nothing from here on considers those options at
all.

---

## Step 2 — Two scans, answering two different questions

Now the algorithm needs to know, for every stop in the city, two numbers:

1. **`earliest[stop]`** — the soonest you could possibly arrive there,
   starting from Rynek at 10:00. Computed by `_forward`, which is just the
   normal CSA scan with no target to stop early at — it just keeps going
   until `deadline` instead.

2. **`latest[stop]`** — the *latest* moment you could still be standing at
   that stop and still reach Politechnika by 10:30. Computed by `_backward`,
   which runs the *same idea in reverse*: starting from Politechnika at the
   deadline, it walks backward through the connections list (in decreasing
   departure-time order) asking "what's the last connection from here that
   still gets me to the target on time?"

Think of `latest[stop]` as **a countdown clock showing how much slack is
left if you're standing at that stop right now**. Concretely, for our
example:

| Stop | `latest[stop]` | Meaning |
|---|---|---|
| Politechnika (the destination) | 10:30 | You're already there — full slack, by definition |
| pl. Grunwaldzki (5 min ride away) | 10:23 | Must leave by 10:23 (roughly 10:30 minus a 5-min ride, minus a bit for the transfer buffer) |
| Rynek (the origin, ~15 min away) | 10:13 | Must leave by 10:13 |
| Ogród Botaniczny (a detour, far from Politechnika) | 9:50 | Very little slack — it's a long way from the goal |

The bigger picture: **stops closer to the destination have a later
`latest[]`; stops farther away have an earlier one.** It's a stand-in for
"how close is this to the goal," measured in minutes of remaining slack
rather than in meters. That single idea is the key to everything below.

```python
for i in range(bisect_left(day.dep_times, deadline) - 1, -1, -1):
    dep_t, arr_t, dep_s, arr_s, trip = conns[i]
    if dep_t < dep_sec:
        break
    if trip not in trip_ok:
        leave_by = latest.get(arr_s)
        if leave_by is None:
            continue
        buffer = 0 if arr_s in target_set else TRANSFER_SEC
        if arr_t + buffer > leave_by:
            continue
        trip_ok.add(trip)
    if dep_t > latest.get(dep_s, -1):
        latest[dep_s] = dep_t
        ...
```

It works backward for the same reason you'd solve a countdown puzzle
backward: to know "how late I can leave stop X," you first need to know
"how late I can leave wherever that trip goes next" — which is always a
later point in time. Starting from the destination and working backward
guarantees you always know that "next" answer before you need it.

---

## Step 3 — Why a "trip" is the unit, not a single hop

Here's the part that matters: the algorithm doesn't decide "is this one hop
from A to B good?" one hop at a time. It looks at a whole trip (one bus, one
run, start to finish) and decides two things about it:

1. **Where would I get ON this trip** (if at all)?
2. **Where would I get OFF** — and is it even worth drawing?

Why not just check every hop independently? Because `latest[]` doesn't
increase smoothly and predictably along a route — a stop right before a
busy interchange can have *less* slack than the interchange itself (lots of
trips converge there, but the one stop before it might be served by fewer
options). So checking hops one at a time could pass, fail, pass, fail down
the length of a perfectly good trip — the drawn line would flicker with
gaps for no real reason. The fix is to make the decision once per trip,
using the two rules below, and then draw one unbroken line.

---

## Step 4 — Where do you get ON? (the boarding rule)

Take a concrete, slightly weird case: **Bus 145** departs a stop, and its
route first goes *away* from Politechnika, out toward Ogród Botaniczny,
before turning around and heading back past Rynek and on to Politechnika.

Say you could technically reach Ogród Botaniczny by 10:05 (maybe via
another bus, or the same tram platform). Should the algorithm let you
"board" Bus 145 there?

No — and here's the check that stops it:

```python
stop_latest = latest.get(dep_s)
if (origin_latest is not None and stop_latest is not None
        and stop_latest < origin_latest - BACKTRACK_TOL_SEC):
    continue   # reject this boarding point
```

`origin_latest` is `latest[Rynek]` — but computed by a *second*, separate
backward scan run only up to `best_arr` (the fastest possible arrival
time), not up to the current `deadline`. That distinction matters: if it
were computed against `deadline` directly, then widening the time-window
slider could let the scan reach some entirely unrelated, fast trip from a
*different* starting stop somewhere else in the city, inflating
`origin_latest` for no reason connected to Bus 145 at all — and that
inflation could then wrongly reject boarding points that were perfectly
fine a moment ago, just because the slider moved (this was a real bug,
fixed 2026-08-12 — see the log below). Anchoring to `best_arr` instead
keeps this reference point fixed regardless of how wide the window gets,
so widening the slider can only ever make this check *more* forgiving,
never less. `BACKTRACK_TOL_SEC` is 2 minutes. The check says: *reject
boarding here if this stop's slack is more than 2 minutes worse than the
slack you already have at your own starting point.*

Ogród Botaniczny's slack is **9:50** — that's 23 minutes worse than Rynek's
10:13, way past the 2-minute tolerance. Rejected. Boarding there would mean
deliberately moving *away* from the goal before the bus even turns around —
technically reachable, but not a sensible way to think about your journey.

Later, the same Bus 145 also stops at Rynek itself on its way back through.
Boarding *there* is fine: `latest[Rynek]` is exactly `origin_latest`, so the
check passes with room to spare. That becomes the real boarding point used
for drawing this trip.

In short: **the first stop of a trip you can physically catch isn't
automatically the "right" one to draw as the start of the segment — it only
counts if reaching it doesn't require backing away from your destination
first.** This is exactly what kills the "ride out to a far loop terminus and
come back on the same vehicle" pattern — it's technically catchable, but not
useful.

---

## Step 5 — Where do you get OFF? (the exit rule)

Once a valid boarding stop is chosen, the algorithm walks forward along that
same trip's remaining stops, one at a time, asking at each one: *is this
still within the time window if I got off here?*

```python
leave_by = latest.get(arr_s)
if leave_by is None or arr_t > leave_by:
    continue                                     # you'd already be too late
exits.append((len(stops_seq), arr_t + (deadline - leave_by), arr_t, arr_s))
```

That's the only filter left: an absolute, self-contained check — this
stop's own arrival time against this stop's own cutoff. Nothing about it
depends on any *other* stop, so it can never flip from "yes" to "no" purely
because the time-window slider moved: once a given stop is reachable in
time, it stays reachable at every wider window too.

**There used to be a second check here, comparing each stop's slack to the
best slack seen earlier on the same ride, to reject stops that weren't
"real progress."** It was removed 2026-08-12. The intent was reasonable —
a bus drifting the wrong way for a while shouldn't look as promising as one
making steady progress — but the check compared `latest[]` values computed
against the *current* time-window deadline, and two neighboring stops'
`latest[]` grow at different, unrelated rates as that deadline widens
(each reflects whatever alternate escape route happens to exist at that
specific stop). Their relative order could flip purely from widening the
slider, silently deleting a real, physically unchanged stretch of a route
from the map — this was the direct cause of the "routes disappear when I
widen the window" bug reported by the user. The guarantee this check
existed for — that a course only gets *dimmer*, never brighter, after
skipping a real opportunity to transfer — turned out to already be
guaranteed correctly and stably by Step 6's per-exit refinement below, so
removing the check lost nothing and fixed the instability.

If a trip never produces a single valid exit anywhere along its route, it
isn't drawn at all — there's no "maybe, dimly" fallback for a trip that
never goes anywhere useful.

One more detail worth naming: if the trip does eventually reach
Politechnika itself, that stop is always a valid exit (it's the finish
line — no progress check needed), and the walk stops there; there's no
point tracing the bus's route further past your actual destination.

---

## Step 6 — Turning "good enough" into a brightness number

Every exit needs a value: "if I got off here, how close would I end up to
the deadline?" For most exits, the algorithm doesn't actually know what
happens next — the two-way scan in Step 2 only proved you *could* still
make it (`leave_by` is a yes/no line in the sand, not a timetable of what
comes after). Finding out exactly how good the rest of the trip from this
one specific stop would be means tracing the whole onward journey again
from there — the same amount of work as the entire search, repeated once
per exit. With thousands of exits across a city, that's not affordable.

So the algorithm takes a shortcut: build one pool of trips worth
considering at all (Steps 3-5 above), and while scoring how good each one's
exits are, let an exit **borrow a real number from another trip already in
that same pool**, whenever that other trip happens to depart from this
stop shortly after arrival. If nothing in the pool connects from here,
there's nothing real to borrow, and the algorithm needs a placeholder
number instead.

**The placeholder, and the bug it used to hide.** That placeholder used to
be `arr_t + (deadline - leave_by)` — "arrival time here, plus however much
slack `latest[]` still implied was left." The problem: `leave_by` is capped
by how often that specific line runs (a bus that comes once an hour has a
`leave_by` that doesn't move no matter what), while `deadline` grows every
time the time-window slider goes up. So this placeholder's *badness* was
measured as a gap to an ever-moving target — widening the slider could make
an exit stuck with the placeholder look artificially worse, purely because
the slider moved, with nothing about the real schedule changing. That's
what caused segments to flicker in and out as the slider moved: a
placeholder-stuck exit would dim as the window widened, then brighten again
once the wider window let the pool finally include a real trip to borrow
from.

The fix: the placeholder is now `arr_t + min(WAIT_CAP_SEC, deadline -
leave_by)` — capped at the same 20-minute "still a reasonable wait" cutoff
the app already uses elsewhere to judge whether a transfer is realistic at
all (`WAIT_CAP_SEC`). Below that cap it behaves exactly as it did before;
past it, it simply stops growing. Widening the window can now only ever
leave a placeholder unchanged or replace it with something real and
better — never make it worse.

**Reading the real value.** For every exit, the algorithm asks a profile of
the whole network — "I get off here at this time; when is the earliest I can
be at the target?" — and uses that answer. The profile
(`planner._target_profile`) is one backward scan over the same connections,
Pareto-pruned per stop, so the answer is *read from the schedule* rather than
estimated, and it covers every stop in the window, not just the ones already
drawn. When the profile says INF, that is a statement, not a gap: getting off
*here* leads nowhere within the window — almost always because the only way
onward is to stay on this very vehicle and there isn't even a transfer buffer
to spare. Such a position simply takes its value from the suffix, i.e. from
riding on. The placeholder above survives only past a course's last useful
exit, and is still never shown as a clock time.

Until 2026-08-29 this was instead a fixed-point loop (at most 8 passes) over
continuations *visible on the map*: take a neighbouring segment's finished
value and shift it by how much later you board (`suffix[j] + shift`). That
assumed a rigid timetable behaves elastically — that the entire downstream
chain slides by exactly the boarding delay. It doesn't: a later run drops a
connection and the arrival jumps by a quarter of an hour, not by the minute
you were late. Worse, the result was flagged as *read* even though it was
derived from an estimate (possibly of another estimate), so the map printed
it as a definite time. On `LEŚNICA → BARTOSZOWICE, 16:44` that made 116 of
232 exits optimistic by up to 10 minutes — and since a value below the
optimum is clamped to `q = 1.0` anyway, half the map glowed at full
brightness with nothing behind it.

Once every exit has a final value, brightness for *that exit* is:

```python
q_of(bound) = clamp(1 - (bound - best_arr) / span, 0, 1)
```

where `span` is the distance from `best_arr` to the worst bound *actually
shown anywhere on the map* (not the full width of the time window — see
Step 7's note on why). The single optimal route scores `1.0`; the worst
option that still made the cut scores `0.0`. Because a trip can have
several exits, each with its own bound, `q_of` is applied per exit, and a
segment's overall `seg["q"]` is just the best (brightest) of all of them —
which, since exits earlier in a ride can always fall back on anything
reachable later in the same ride, is always the value at its very first
exit. That distinction — a segment's best-ever brightness vs. its
brightness at one specific later point — matters again in Step 7.

---

## Step 7 — One more honesty pass before drawing

Even after all that, a segment could technically start from a stop nobody
can actually reach, or trail off somewhere that isn't the destination and
isn't a real transfer point. Every segment discovered within the deadline
window gets trimmed on both ends before anything is drawn — there's no
separate brightness cutoff at this stage, every segment goes through this
same honesty pass regardless of its score:

- **Start**: either it's the real origin (Rynek), or there's another
  currently-drawn segment you could catchably transfer onto that lands you
  partway along this one.
- **End**: either it reaches the destination, or it's cut back to the last
  point where it can hand off to another segment that genuinely *continues*
  (see below) — never left dangling.

A hand-off at the end of a tail only counts when the segment standing
there really is a continuation. Two conditions, both added 2026-08-15
after the map kept growing stumps in real data:

1. **It must not turn back onto ground we already rode.** Not merely "not
   back to the previous stop" — back to *any* stop this trip already
   passed. A vehicle doing that is the way back, not a way onward:
   classically a terminus loop the map drives onto purely in order to turn
   around. The one-stop version of this test (the first attempt, the same
   day) caught only the tightest loops and missed the common case by a
   wide margin: tram 1 rode all the way up to the Kamieńskiego loop
   "anchored" on tram 15, which promptly comes back down through Bałtycka
   and Kleczkowska — stops tram 1 had just ridden through. The real
   transfer was four stops earlier, at Pl. Staszica, and that is where the
   tail now ends. Nothing is lost by refusing these: if we already stood
   at that stop, the segment departing *from* it is drawn on its own and
   anchors itself.
2. **It must itself be drawn beyond that stop.** Physically continuing in
   the timetable is not enough. Otherwise two tails prop each other up:
   tram 1 and tram 7 both end at Bałtycka, each pointing at the other as
   its "continuation", and the map keeps two stumps meeting at a stop
   nothing leaves. Since ranges only ever shrink, this stays a
   well-founded fixed point.

Direction is read from the trip's stop order in the timetable, never from
what currently fits inside the time window — otherwise widening the slider
would change the answer and erase branches that were visible at a narrower
setting.

This is deliberately *not* the same as passing a better transfer and
riding on (Step 6): there you're still heading toward the destination,
just not optimally, so the stretch stays drawn, only dimmer.

**What was removed to make this hold (2026-08-15).** The end check used to
*also* require the continuation to be comparably bright (within 10
percentage points) so a bright corridor wouldn't trail off into some
barely-relevant side street. That was always housekeeping, never a
requirement of contract point 4 — and it was the last ingredient of the
end check that depends on how wide the window is: brightness is scaled
against the worst option that *currently* fits (contract point 9), so both
sides of that comparison move when the slider moves, and they can move
apart. On its own that only cost an anchor here and there; combined with
the strict continuation test above, each flip cascaded down a whole chain
of anchors. Measured across 6 relations swept 100%→200%: 32 drawn stretches
vanished purely from widening the window. Dropping the brightness
condition brings that to **zero — with more pieces drawn, not fewer**. The
side street it guarded against can no longer form anyway: a continuation
now has to lead onward *and* be drawn onward, so it is part of a real path
to the destination, and it is drawn dim (points 3 and 8) rather than
excluded. The same condition was dropped from the transfer graph behind
the route-proposals list, which mirrors this check by design.

Measured after all of the above, on 14 relations × 14 window widths
(100%–300%): zero dangling tips at every width, zero stretches lost to
widening the window.

An earlier rule tried to enforce the same intent by comparing how late you
could still *depart* from each stop. That number is high at a busy
interchange because service is frequent there, not because it's close to
the destination, so the rule deleted half the map along with the loops
(measured: 42 instead of 83 drawn pieces on one relation). Tuning its
tolerance — the old "Tolerancja regresji" slider, since removed — only
moved the noise threshold, which is why it never worked.

A note on the brightness that *stayed*: wherever the code still compares a
segment's brightness at a point, it uses *that specific point's own*
brightness (Step 6's per-exit `q_of`), not the segment's best-ever score.
Using the best-ever score was a bug, fixed 2026-08-12: if a ride picks up
one excellent, distant opportunity much further along, that excellence
correctly lights up the *whole* ride behind it (Step 6's
fallback-to-later-exits already does that, honestly) — but it was then
also being used as an unreasonably high bar for a completely unrelated,
ordinary transfer near the *start* of the same ride, decoupled from
anything actually true about that earlier point.

An earlier rule tried to enforce the same intent by comparing how late you
could still *depart* from each stop. That number is high at a busy
interchange because service is frequent there, not because it's close to
the destination, so the rule deleted half the map along with the loops
(measured: 42 instead of 83 drawn pieces on one relation). Tuning its
tolerance — the old "Tolerancja regresji" slider, since removed — only
moved the noise threshold, which is why it never worked.

Net result: nothing on the map starts from nowhere, and nothing trails off
into thin air, regardless of how wide the time window is set.

One subtlety this trimming had to get right: the "start" check above only
makes sense for a boarding stop reached *during* the journey — it must never
apply to your own starting stop(s). A point clicked on the map can expand to
several physically distinct nearby stops, all equally valid, zero-cost
places to begin from. An earlier version of this rule compared every
candidate boarding stop's slack against the *single best* slack among all of
those starting stops, which wrongly rejected boarding at a perfectly good
starting stop whenever a different, physically separate starting stop
happened to have better onward prospects — even though being at any of your
own starting points is never "backtracking." The rule now only ever
compares a stop against that same standard when the stop was reached by
riding or walking *during* the journey, never when it's one of the starting
stops themselves.

---

## The short version

For every trip running that day: find the earliest stop you could board
without backtracking away from your goal, then walk forward collecting
every stop where you'd still be on schedule. If you find at least one such
stop, score each one by how much real margin it leaves before the
deadline (borrowing a real number from another trip you could transfer
onto where possible), and only keep the parts of the ride that are
connected enough to belong on the map — anchored at both ends to something
real: at the start, anything catchable you could have arrived on; at the
end, something that actually carries you onward rather than back over
ground you already covered, and that is itself drawn onward.

---

## Step 8 — Reading the sidebar's proposal list off the same map

Everything above describes one thing: the cloud of segments the map draws.
The ranked list of named proposals next to it ("18:45 → 19:22, 1 transfer")
is not a second algorithm — it's a direct reading of the exact same segment
cloud, taken *after* Step 7's trimming has already decided what's actually
drawn.

Think of the kept, trimmed segments as a small map of "corridors" connected
by real transfer points — the same transfer points Step 7 already checked
when it decided a segment's tail was allowed to reach that far. Producing a
proposal is then just: start at any corridor that begins at the true origin,
and walk forward through it, at every real transfer point either (a) you've
reached the destination — that's a complete proposal — or (b) you hop into
whichever next corridor is reachable there, exactly the way Step 7 already
verified was legitimate (same "comparably bright" test, judged at that
same specific point — see Step 7's 2026-08-12 fix — so this list can't
end up more conservative than what the map itself now draws). Explore the
brightest branches first, stop once enough distinct proposals are found or
the search has spent its (small) budget, then rank what's left by arrival
time, then by fewest transfers, then by least waiting.

**Exploring breadth-first, not one branch at a time.** "Explore the
brightest branches first" doesn't mean picking the single most promising
starting point and following it as deep as it goes before trying anything
else. It works level by level: every live branch — every starting point,
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
shrinks or grows in lockstep with the map, automatically, for free — there's
no separate brightness threshold that the two could disagree about.

One invariant is worth stating outright: the single fastest route scores
`1.0` by definition (Step 6) — it's exactly as good as "the best possible,"
zero slack burned. That means widening or narrowing the time window will
never filter the fastest route out of the list; a narrower window only ever
prunes the *slower* alternatives underneath it, on the list exactly as it
does on the map.

Step 7's trimming answers a slightly different question than "is this route
fast" — it's "is this segment anchored to something real on both ends" —
and an earlier version of that anchoring rule could, in a specific
situation, disagree with "is this route fast": a starting-point cluster
with several physically distinct nearby stops could see the genuinely
fastest route's own boarding stop rejected, purely because a *different*
stop in that same cluster had better prospects (see the note at the end of
Step 7 — that's now fixed at the source, not patched around here). As a
last-resort safety net for whatever's left unanticipated, if the anchoring
step ever throws away literally every segment despite a connection
provably existing, the map and list both fall back to drawing that one
proven-fastest route directly, so neither one is ever left showing nothing.
