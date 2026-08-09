# The flow-map algorithm, explained with a worked example

This document covers exactly one thing: `plan_flow` in `planner.py`, the
function behind the map you actually see in the UI (the "show every useful
trip at once, brightest = best" view). Everything else — the data model,
the single-route CSA scan, the frontend — is left out on purpose.

Two terms you need before any of this makes sense:

- A **connection** is one scheduled hop between two consecutive stops of one
  bus/tram run — `(departure_time, arrival_time, from_stop, to_stop, trip_id)`.
  A whole day's connections sit in one list, sorted by departure time.
- **CSA** (Connection Scan Algorithm) means: don't build a graph, just sweep
  that sorted list once, keeping track of the best arrival time found so far
  at every stop as you go.

`plan_flow` doesn't compute one route — it decides, out of every single trip
running that day, which ones are worth drawing on the map, from where to
where, and how brightly. That's the whole job. Everything below builds up to
exactly that decision, using one running example: a trip from **Rynek**
(origin) to **Politechnika** (destination), starting at **10:00**.

---

## Step 1 — Establish a deadline

You can't judge "is this trip good enough" without first deciding what
"good enough" means. So the very first thing `plan_flow` does is run the
ordinary single-route CSA scan (the same one `plan_route` uses) just to find
the single fastest possible arrival:

```python
best_stop, best_arr, _ = _scan(day, source_stops, target_stops, dep_sec)
duration = best_arr - dep_sec
extra = min(max(int(duration * (SLOWDOWN - 1)), MIN_EXTRA_SEC), MAX_EXTRA_SEC)
deadline = best_arr + extra
```

With our example: say the fastest way from Rynek to Politechnika takes 20
minutes, arriving **10:20**. The rules are: allow up to 1.5× that time, but
never less than 5 extra minutes and never more than 30:

```python
SLOWDOWN = 1.5          # allow routes taking up to ~1.5x the fastest time
MIN_EXTRA_SEC = 300     # ...but always at least 5 minutes of slack...
MAX_EXTRA_SEC = 1800    # ...and never more than 30 minutes
```

20 minutes × 0.5 = 10 minutes of extra slack (between the 5-minute floor and
30-minute ceiling, so it applies as-is). **Deadline = 10:30.**

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

`origin_latest` is just `latest[Rynek]` — **10:13** in our table above.
`BACKTRACK_TOL_SEC` is 2 minutes. The check says: *reject boarding here if
this stop's slack is more than 2 minutes worse than the slack you already
have at your own starting point.*

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

## Step 5 — Where do you get OFF? (the exit / progress rule)

Once a valid boarding stop is chosen, the algorithm walks forward along that
same trip's remaining stops, one at a time, asking at each one: *is this
worth offering as a place to get off?*

```python
leave_by = latest.get(arr_s)
if leave_by is None or arr_t > leave_by:
    continue                                     # (a) you'd already be too late
if (board_latest is not None
        and leave_by <= board_latest - PROGRESS_TOL_SEC):
    continue                                     # (b) this stop isn't real progress
exits.append((len(stops_seq), arr_t + (deadline - leave_by), arr_t, arr_s))
```

Two separate checks, and they answer two separate questions:

**(a) Are you still on schedule?** `arr_t > leave_by` just means: you
arrived at this stop later than the last moment `latest[]` says is still
workable. If so, this stop is already too late to be useful — skip it as an
exit (but keep walking the rest of the trip; one bad stop doesn't kill the
whole segment).

**(b) Did this ride actually get you closer, or did it just eat time?**
This is the one that needs a concrete illustration, because two very
different situations produce *the same arrival time*:

- Sitting at a stop doing nothing for 10 minutes.
- Riding a bus for 10 minutes in the *wrong direction* and then it happens
  to be exactly as far (in slack-time) from the goal as when you started.

Both "cost" you 10 minutes. Only one of them was worth boarding. The
progress check tells them apart by comparing `latest[]` at this stop to
`latest[]` back at boarding (`board_latest`): if the ride was genuinely
useful, you should be **closer** to the goal now, meaning your slack should
have gone *up* (later `latest[]`), not stayed flat or dropped. Continuing
our table: boarding at Rynek gave `board_latest = 10:13`. A few stops later
at pl. Grunwaldzki, `latest[] = 10:23` — that's 10 minutes *better*, comfortably
past the `PROGRESS_TOL_SEC` (3-minute noise allowance) — real progress,
counts as a valid exit.

If instead the bus had looped back near its own starting point and
`latest[]` there had dropped back down to, say, 10:11 (worse than the 10:13
you started with), the check `leave_by <= board_latest - PROGRESS_TOL_SEC`
would trigger and this stop would simply not be offered as an exit — the
ride happened, time passed, but nothing about your position relative to the
goal actually improved.

**Why this matters for the map:** without rule (b), a bus heading the wrong
way for a while would show up on the map looking exactly as promising as one
heading the right way, because raw arrival-time arithmetic can't tell "spent
10 minutes going nowhere useful" apart from "spent 10 minutes waiting."
Rule (b) is what keeps only forward progress lit up.

If a trip never produces a single valid exit anywhere along its route, it
isn't drawn at all — there's no "maybe, dimly" fallback for a trip that
never goes anywhere useful.

One more detail worth naming: if the trip does eventually reach
Politechnika itself, that stop is always a valid exit (it's the finish
line — no progress check needed), and the walk stops there; there's no
point tracing the bus's route further past your actual destination.

---

## Step 6 — Turning "good enough" into a brightness number

Each exit gets a rough value first — `arr_t + (deadline - leave_by)`,
read as "arrival time here, plus however much slack `latest[]` still implied
was left." That's a decent estimate but can be too generous for
infrequently-running lines (their `latest[]` may reflect "wait for the very
last bus of the evening," which overstates how good getting off there
really is). So the algorithm refines it: for every exit, it looks at what
you could *actually, concretely* transfer onto right there, and uses that
real number instead when one exists. This refinement is a short loop (at
most 8 passes) because one segment's refined value can depend on another
segment's refined value, and it settles down once nothing changes anymore.

Once every segment has a final value, brightness is just:

```python
seg["q"] = (deadline - seg["bound"]) / (deadline - best_arr)
```

The single optimal route scores `1.0`; something that only just squeaks in
under the deadline scores close to `0.0`. That's the number the map turns
directly into line opacity.

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
  point where it can hand off to another segment that's comparably bright
  (within 10 percentage points of its own score) — not left dangling into
  some barely-relevant side street.

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
stops where you'd still be on schedule *and* genuinely closer to your
destination than when you boarded. If you find at least one such stop,
draw one line from boarding to the best of them, score it by how much real
margin it leaves before the deadline, and only keep the ones bright enough
and connected enough to belong on the map.

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
verified was legitimate (same "comparably bright" test — a corridor never
hands off to something distinctly dimmer than itself). Explore the
brightest branches first, stop once enough distinct proposals are found or
the search has spent its (small) budget, then rank what's left by arrival
time, then by fewest transfers, then by least waiting.

The payoff of doing it this way instead of running a separate search: a
proposal can *never* name a transfer that isn't actually drawn on the map,
because it's built from nothing but the map's own already-decided shape.
Move the one time-window slider ("show routes up to N minutes slower than
the fastest") and the list shrinks or grows in lockstep with the map,
automatically, for free — there's no separate brightness threshold anymore
that the two could disagree about.

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
