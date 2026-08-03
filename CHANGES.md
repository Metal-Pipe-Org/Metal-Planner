# What changed, and how the algorithm works now

This documents a concrete round of fixes applied to the flow-planning
algorithm (`planner.py`, `gtfs.py`, `routes.py`, `templates/index.html`),
following the architectural review in this project's history: the first
four of its five proposed principles were implemented (canonical places, a
generic transfer/"bridge" model, a staged pipeline, and a running-best
progress check). The fifth principle (a cross-trip non-reversal guard) was
**deliberately left out** of this pass — it's a real, separate gap, but out
of scope here.

Every claim below was verified by actually running the planner against the
live GTFS database, not just by reading the code — the specific numbers are
real output from real queries.

---

## 1. Destinations are now resolved as places, not name strings

**The problem.** Wrocław's schedule data represents big interchanges as
several differently-named directional platforms — e.g. `PL. GRUNWALDZKI`,
`PL. GRUNWALDZKI W/t`, `PL. GRUNWALDZKI Z/a`, and six more, all sitting
within about 150 meters of each other. Matching a rider's typed destination
against stop names by exact string equality meant that a bus or tram
stopping at one of the eight *variant* platforms was invisible to "did we
arrive" — the algorithm would keep walking that trip forward past the
rider's real destination, because as far as it knew, `W/t` and plain
`PL. GRUNWALDZKI` were unrelated places.

**The fix (`gtfs.py`).** A new step resolves stops into canonical
**places** before anything else happens:

- `_platform_base_name` recognizes the directional-platform suffix pattern
  used at these interchanges (a space, then `Z`/`W`/`Pd`/`Pn`, a slash, a
  short code — e.g. `" W/t"`) and strips it to get the base name.
- `_build_places` starts from the existing "identical name" groups (trusted
  as before, no distance check — that's how it already worked) and, for any
  stop whose name matches the suffix pattern, tries to attach it to the
  base-name group — but **only if it's genuinely close** (within 400m,
  `PLACE_MAX_SPAN_M`). This is a safety net against accidentally merging two
  unrelated places elsewhere in the city that happen to end in a similar
  string. Checked against the live database: this pattern currently matches
  exactly 8 stop names, all at `PL. GRUNWALDZKI`, all within the 400m cap —
  so this isn't a broad fuzzy-match, it's a narrow, verified fix for a real,
  specific naming convention, general enough to also catch any other
  interchange that uses the same convention in a future data refresh.
- `match_stop` now expands whatever it initially matches to the *whole*
  canonical place, so searching `PL. GRUNWALDZKI` correctly resolves to all
  16 stop IDs (8 plain-named + 8 directional platforms), not just 8.
- `day.siblings` (the walking-transfer relation used everywhere in the
  scan) is now built from these same place clusters, so a transfer between
  `PL. GRUNWALDZKI` and `PL. GRUNWALDZKI W/t` is modeled exactly like any
  other same-place platform change.

**Verified impact.** Before this fix, `plan_route('KSIĘŻE WIELKIE', 'PL.
GRUNWALDZKI')` could only ever end at one of the 8 plain-named platforms.
After the fix, run live against the real schedule, it correctly rides
straight to `PL. GRUNWALDZKI Pn/t` — a platform it couldn't previously
recognize as "arrived" at all. In the flow map for the same query, a
previously-invisible route (`Kościuszki → PL. GRUNWALDZKI W/t`, spanning
118 geometry points) now shows up as the single brightest tram-2 option —
it existed in the schedule the whole time, the algorithm just couldn't see
that it reached the destination.

**A second bug found after shipping the first fix, and fixed too.**
Searching the plain `PL. GRUNWALDZKI` worked correctly, but searching one
specific platform variant *directly* (e.g. typing `PL. GRUNWALDZKI Pd/t`
exactly) silently failed to expand to the full place — it resolved to just
that one stop, and routing to it returned "no connection found" even though
the plain-name search worked fine for the same trip. Root cause: when
`_build_places` merges a platform stop into its base-name group, it left
that stop's *own* single-stop exact-name group sitting in `places`
unchanged, right alongside the newly-merged group. Both groups still
technically "contained" that stop, and `place_of` (built by iterating every
group and mapping each stop to whichever group's key comes last for it) ended
up silently keyed on whichever of the two happened to win the dict-iteration
order — for `Pd/t`, that was the stale, unexpanded singleton, not the real
16-stop place. Fixed by deleting (or shrinking) a stop's own singleton group
the moment it gets merged elsewhere, so there's exactly one group per stop,
never two competing for the same `place_of` entry. Verified after the fix:
all 9 named variants of `PL. GRUNWALDZKI` (the plain name plus all 8
platforms) now resolve to the identical 16-stop set, and searching any one
of them for `KSIĘŻE WIELKIE → PL. GRUNWALDZKI Pd/t` correctly returns the
same route as searching the plain name.

**Autocomplete list deduplicated too.** The stop-name suggestion list
(`gtfs.all_stop_names`, feeding the `<datalist>` in the search form) was
untouched by the place fix above and kept listing all 9 `PL. GRUNWALDZKI...`
variants as separate suggestions — technically correct now that any of them
routes the same way, but confusing: nothing in the UI told you they'd
behave identically. `all_stop_names` now collapses platform-suffixed names
down to their base name before deduplicating, so the suggestion list shows
one `PL. GRUNWALDZKI` entry instead of 9 near-identical ones (1015 → 1007
total suggestions city-wide). This is a plain string-based collapse, not the
distance-checked clustering `_build_places` does for routing — acceptable
here because it only affects what's *suggested* while typing; actual
routing still goes through `match_stop`'s own, distance-safe place
resolution regardless of which name was picked.

---

## 2. Transfers are now a generic "bridge," not a one-off loop

**The problem.** The walking-transfer relation was built inline, once, as a
loop specific to "stops with the same name." There was nothing about its
shape that said "this is *a* way to model a transfer" versus "this is *the*
way this codebase happens to compute one specific kind of transfer."

**The fix (`gtfs.py`).** Two small, named functions now express this as an
explicit contract:

- `_walking_bridges(place_groups)` builds walking edges in the shape
  `stop_id -> (neighbor_id, ...)` — same shape as before, but now named and
  documented as *a* bridge provider, not *the* transfer mechanism.
- `_merge_bridges(*bridge_maps)` combines edges from any number of
  providers into one relation. Right now there's exactly one provider
  (walking), so this looks like it's merging a single thing with itself —
  that's expected. The point is that adding a second transfer type (bike
  share, a scooter network, anything) later means writing one function that
  returns edges in this same shape and adding it to the merge call — not
  inventing a parallel merge path, a second kind of `siblings`-like
  structure, or reworking how `_scan`/`_forward`/`_backward` think about
  transfers. Those three functions never change regardless of how many
  bridge providers exist, because they only ever consume the merged result.

---

## 3. `plan_flow` is now four named stages instead of one function

**The problem.** The entire flow-map computation — segment discovery,
brightness scoring, thresholding, and final formatting — lived in one
~300-line function with three nested closures. Understanding or changing
any one part meant holding the whole thing in your head at once.

**The fix (`planner.py`).** `plan_flow` is now a short orchestrator calling
four independently-named stages in sequence:

1. **`_discover_segments`** — for every trip in the search window, picks a
   boarding stop (the existing anti-backtrack rule, unchanged) and walks it
   forward collecting valid exits (the progress rule — see section 4 below
   for what changed here). Returns segment candidates with *no* brightness
   score yet.
2. **`_refine_brightness`** — the suffix/fixed-point pass that turns each
   exit's crude "deadline − latest" approximation into a value backed by
   real, catchable continuations. Sets each segment's final `q`.
3. **`_select_and_anchor`** — applies the brightness threshold and the
   both-end anchoring loop that keeps the drawn network connected (nothing
   starts from nowhere, nothing trails into thin air). Unchanged logic,
   just extracted.
4. **`_finalize_segments`** — merges duplicate (line, path) entries, slices
   geometry, caps to the brightest 150, and formats the response.

No scoring or filtering logic changed in this step — it's the same
computation, just possible to read, test, and reason about one stage at a
time instead of as one continuous block. This is also what made it
straightforward to verify each stage's behavior independently while
building the other three fixes below.

---

## 4. The progress check now tracks the best point reached, not the boarding snapshot

**The problem.** A ride's "am I still making progress toward the
destination" check compared every stop only against a single number
recorded once, at boarding (`board_latest`). A ride that climbed to a good
point mid-journey and then genuinely declined for several stops in a row
would never trip the check, as long as it never dropped below that first,
frozen number — even after it had clearly gotten worse than its own best
moment.

**The fix (`planner.py`, `_discover_segments`).** The check now compares
each stop against `best_latest_seen` — the best `latest[]` value observed
*anywhere along this ride so far*, updated as the walk proceeds — instead
of the value frozen at boarding. A stop only counts as "still making
progress" if it's within tolerance of the best point the ride has actually
reached, not just better than wherever it started.

**A bug found and fixed while building this.** The first version of this
logic updated `best_latest_seen` *before* comparing the current stop
against it — which meant a stop that had just set a brand-new record was
being compared against itself, and with the tolerance dialed to zero, every
single stop failed that comparison trivially. Caught by testing at
`progress_tol_sec=0` across three different real queries and seeing them
all collapse to **zero** segments — obviously wrong, since a tolerance of
zero should mean "no regression allowed," not "nothing is ever valid." The
fix: compare against `prior_best` (the best point *before* this stop), then
update the running best afterward. Re-tested the same three queries at
`tol=0` and got graceful, sensible results (19, 10, and 4 segments
respectively, down from 38/14/4 at the default) instead of a wipeout.

**What this does and doesn't fix — verified, not assumed.** Running the
exact real-world case that motivated this change (Księże Wielkie → Pl.
Grunwaldzki, 19:32) with the fix in place:

- A tram-2 run that boards mid-route and climbs to a genuine peak before
  declining several stops toward Biskupin: **fixed**. The declining tail no
  longer registers as valid progress once it falls far enough below the
  ride's own best point.
- A *different* tram-2 run that boards directly at a stop and declines
  monotonically from the very first stop (no earlier peak to compare
  against at all): **not fixed by this change alone**, and verified as
  such — at the default tolerance (180s) it still appears
  (`Chełmońskiego → Spółdzielcza`, brightness 0.551). This is expected:
  when a ride never has a better moment than where it boarded,
  "best-so-far" and "boarding value" are the same number, so there's
  nothing for the running-best comparison to improve on. Tested directly:
  this specific segment disappears once the tolerance is turned down to
  60 seconds or below — which is exactly what the new debug slider (below)
  is for.

**Why the tolerance can't just be zero by default.** Tested across three
unrelated queries: at `tol=0`, one query lost more than half its segments
(38 → 19), and the "Rynek → Grunwaldzki" and "Dworzec Główny → Biskupin"
queries also shrank. The original code comment's claim — that the `latest`
metric is genuinely noisy by a minute or two between neighboring stops,
even along legitimately good routes — held up under direct testing, not
just as an assumption. Zero tolerance isn't free; it trades away real,
useful options along with the bad ones. The default stays at 180 seconds;
the new slider lets you see that trade-off happen live instead of taking
it on faith.

**API.** `plan_flow` takes a new optional `progress_tol_sec` parameter
(default: `None`, meaning "use `PROGRESS_TOL_SEC`", currently 180).
`/api/flow` accepts it as `tol` (seconds), clamped to `[0, 600]`.

---

## 5. New debug panel

A new panel on the right side of the map (`#debug-panel` in
`templates/index.html`), separate from the main "Planer podróży" panel on
the left, holds two live controls:

- **Czułość** (brightness threshold) — moved here from the main panel,
  unchanged behavior.
- **Tolerancja regresji** (regression tolerance) — new. Maps directly to
  `progress_tol_sec` above, range 0–300 seconds, default 180 (matching the
  algorithm's own default). Dragging it re-issues the search after a short
  debounce, same live-update pattern the brightness slider already used.

This turns the tolerance from a hardcoded constant you'd have to read code
and re-run scripts to experiment with (as this whole fix was originally
diagnosed) into something you can drag and watch the map respond to in
real time.

---

## What was intentionally left out

**Principle 5 — the cross-trip non-reversal guard — was not implemented.**
This is a real, separately-verified gap: the brightness-refinement stage
(`_refine_brightness`) can still credit one trip's exit value using a
completely different trip that happens to head back the way the first trip
came from, because that check has no notion of "does this continuation
retrace ground already covered" — it only asks "is there a real, catchable
connection here." Fixing it properly means comparing candidate
continuations against the *rider's own path so far*, not just against each
trip's own internal consistency, and doing that without materially slowing
down the brightness-refinement fixed point. That's a genuinely separate
piece of work from the four changes above, and out of scope for this pass.
