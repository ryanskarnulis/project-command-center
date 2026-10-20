# Focus: the stored day plan

Issue #319. The Focus plan used to be **derived** on every request: rank the open
work, then pack it back to back from the start time until the capacity runs out
(`services/focus.py`). That left two verbs with no honest implementation:

- **Schedule** a "Didn't fit" item could only grow the session's capacity until
  that item, and everything ranked above it, fitted.
- A **fixed-time commitment** (a 14:00 meeting) had nowhere to live, and every
  block push-forwarded, so the day-shape arithmetic broke the moment one time
  was pinned.

Both need an explicit, stored decision for one particular day.

## Decision: an overlay, not a snapshot

We store only the decisions the user made for a day. Everything else stays
derived.

The rejected alternative was to freeze the derived plan into rows the first
time a day is opened and edit those rows afterwards. That is fully
predictable, but it goes stale. Work created, re-prioritised or unblocked after
the snapshot never appears unless someone pulls it in by hand. It also has to
answer "what is today's plan?" before the user has asked for one, which is a
second source of truth fighting the ranking rules.

With an overlay:

- A day with no decisions behaves exactly as it always has.
- A decision costs one row.
- New urgent work still flows into the gaps.

## Data model

`focus_plan_entries`, one row per decision:

| column | meaning |
|---|---|
| `plan_date` | the day the decision belongs to |
| `task_id` | the pinned task. Null means a **commitment** |
| `title` | the commitment's title. Null for a task pin |
| `start_minute` | minutes after midnight on `plan_date` (0–1439). Null means a **floating** pin. Commitments always carry one |
| `duration_minutes` | the commitment's length. Null for a task pin, which uses the task's own estimate |
| `position` | order among the day's floating pins (append = max + 1; reorder renumbers) |
| `deleted_at` | soft delete. Removing an entry is undoable via restore |

There are three kinds of entry (a fourth, the skip, came with repeating
commitments; see below):

1. **Floating pin**: "this task is in today's plan". It sits ahead of the ranked
   list in `position` order and never lands in "Didn't fit".
2. **Timed pin**: "do this task at 14:00". It is a fixed block of the task's
   estimated length.
3. **Commitment**: "14:00–15:00 Standup". A fixed block that is not a task, so
   meetings don't fill the backlog with fake tasks.

Constraints:

- A partial unique index allows one live pin per task per day.
- A check constraint keeps the kinds apart:
  - a commitment has a title, a start and a duration;
  - a pin has none of the title or duration columns.
- Only effectively top-level tasks can be pinned. A subtask is scheduled as part
  of its parent, and pinning one would count its minutes twice.

A purged task takes its entries with it, because purge is already the one
permanent delete. Writes go through `services/focus_plan.py` and each one lands
in `activity_events` as `entity_type="focus_entry"`.

## Packing

The window is `[start, start + available_minutes)`.

1. **Fixed blocks** are commitments plus timed pins.
   - Each one sits at its own time. It is never moved and never overflows.
   - It shows even when it runs past the window; the overage counts towards
     `used_minutes`.
   - A commitment that has already ended before `start` is dropped as past.
   - A timed pin that has ended before `start` while its task is still open
     becomes floating. The task is still pinned to today.
2. **Pins are ignored, never deleted,** when their task is done, trashed,
   deferred past the day, or blocked. A blocked task stays in "Blocked".
   Deferring a pinned task therefore still makes it leave the day, with no extra
   write.
3. **Free gaps** are the window minus the busy intervals. Overlapping fixed
   blocks are merged for this purpose.
4. **Floating pins** go first, in `position` order, into the first gap that
   holds them. A pin that fits no gap is placed after the end of the day
   instead of in "Didn't fit". The user asked for it, so it is shown, and
   `used_minutes` exceeds the capacity.
5. **Ranked tasks** follow, first fit:
   - The first gap that holds a task takes it, so a small task can still use
     the 40 minutes before a meeting.
   - A task that fits nowhere tries its subtasks as stand-ins, exactly as
     before, and then goes to "Didn't fit".
   - With no fixed blocks there is one gap, and this is the old greedy pack
     unchanged.

The response keeps `scheduled` as task blocks only, sorted by start time. Each
block gains `entry_id`, `pinned` and `fixed`. Commitments arrive in a separate
`commitments` list, so no consumer of a task block has to handle a block with no
task.

## Verbs

- **Schedule** (from "Didn't fit") creates a floating pin. Only that item
  enters the day. The capacity is left alone.
- **Pin at a time** sets `start_minute` on the pin. **Unpin** soft-deletes the
  entry.
- **Add commitment** creates a commitment. Creation is lane-scoped: the Focus
  page creates it, and nothing global does.
- **Defer** is unchanged: it writes `deferred_until`, and rule 2 above ignores
  the pin.
- **Reorder** (#375) puts floating pins in a new order:
  `PUT /api/focus/pins/order` with `{plan_date, entry_ids}`, the agent tool
  `reorder_focus_pins`, `focus_plan.reorder_pins` underneath.
  - `entry_ids` may be any subset of the day's live task pins. The named pins
    trade the slots they already hold, and pins left out keep their place.
    A client only sees the pins the plan currently places, so the ignored ones
    (done, deferred, blocked) can't be named and must not move.
  - The day's pins are then renumbered `0..n-1`, which also untangles position
    ties a restore can leave. A new pin still appends.
  - A commitment has no order and is refused (409), as is an id that isn't a
    live entry of that day, or a repeated id. A timed pin is accepted: its
    position only matters once it has floated, which is exactly when the plan
    shows it as floating.
  - One `activity_events` row per reorder that changes anything
    (`action="reordered"`, `entity_id` 0, like the project order), naming the
    new sequence. Undo is reordering back; the mobile undo bar does that.
  - The plan reports each floating pin's place as `pin_order` on its block.
    First-fit packing can put a later pin earlier on the clock, so clients
    reorder by `pin_order`, never by start time.
  - UI: on desktop a floating pin's row drags onto another floating pin's
    slot, and its grip moves it with the arrow keys. On the mobile timeline
    swipes are taken, so the row's ⋯ sheet offers Move up / Move down.

## The mobile day shape

`buildDayShape` lays rows end to end and lets the current block's overrun push
everything after it. With fixed rows:

- A fixed row keeps the server's start time.
- A flexible row that would collide with one jumps to just after it.
- An overrun that eats into a fixed row is named in the push notice. The
  notice does not move the commitment.

## The session log and the running clock (#374)

The mobile page keeps two things the plan itself can't: the blocks already
finished today (done work leaves `get_focus_plan`, but the timeline still owes
its receipts and the capacity its spent minutes) and the clock on the current
block. Both used to live in each browser's `localStorage`, so a device switch
lost them. They now live on the server.

**Decision: a separate table, not columns on `focus_plan_entries`.** Entries are
decisions about a day, and a day with none behaves exactly as it always has.
The session is a record of what the day cost. Mixing the two would give a
finished block a "pin" that the packer must then learn to ignore.

`focus_sessions`, two kinds of row told apart by `finished_at`:

| column | meaning |
|---|---|
| `plan_date` | the day the block belongs to |
| `task_id` | the block's task. Never null: the clock only ever runs on a task block, and commitments aren't timed |
| `leg_started_at` | clock only: server time the current run leg began. Null means paused |
| `accumulated_ms` | clock only: time banked by earlier legs |
| `finished_at` | null means this row is **the clock**; set means a **finished block** |
| `minutes` | finished only: what the block was booked at (the clock's reading, or the estimate when untimed) |
| `planned_minutes` | finished only: the estimate it was laid out with, so the day shape can tell ran-long from ran-short |
| `deleted_at` | soft delete: a cleared clock, or a finish that was undone |

Constraints:

- **One live clock in the whole table**, not per day: a person works one block
  at a time. A partial unique index over the expression `(finished_at IS NULL)`
  where `finished_at IS NULL AND deleted_at IS NULL` enforces it. Autogenerate
  can't reflect expression indexes on SQLite, so the migration writes it by
  hand and `alembic check` warns that it skipped it.
- One live finished block per task per day.
- A check constraint keeps the kinds apart: a clock has no booking, and a
  finished block has a booking and no running leg.

Verbs, each idempotent, so two of them landing in either order settle the same
way (the mobile page sends them separately and in quick succession):

- `PUT /api/focus/clock` `{plan_date, task_id, state, elapsed_ms}` starts,
  pauses, resumes or restores the clock. The client sends **elapsed time, not a
  timestamp**. The server starts the leg at its own now, so two devices whose
  clocks disagree still agree on the block, and `GET /api/focus/clock` reports
  `elapsed_ms` as of the response. A clock on another block or another day is
  cleared first: starting the next block is how a stale clock goes away.
- `DELETE /api/focus/clock?task_id=&plan_date=` clears it, but only when it is
  still that block's. A device still showing the last block must not clear the
  clock another device has started on the next.
- `PUT /api/focus/sessions/{plan_date}/tasks/{task_id}` `{minutes,
  planned_minutes}` books a finished block. Booking again re-books it. It also
  stops that block's own clock. A clock on any other block is left alone,
  because completing a later row from its sheet must not cost the current block
  its time.
- `DELETE` on the same path takes the block back out. This is the undo of a
  completion; the undo then puts the clock back with a `PUT`.
- `GET /api/focus/sessions?plan_date=` lists the day's finished blocks with
  their task titles.

Writes go through `services/focus_sessions.py` on the `BEGIN IMMEDIATE` write
session, since every one is a check-then-insert. Each lands in
`activity_events` as `entity_type="focus_session"`. A task purge takes the
task's rows with it.

There is **no agent tool**. The clock is a hands-on, per-device control, and
the agent has no use for "start my clock". What the agent might want, "what
did today cost?", is a read that can join `get_focus_plan` later without
changing this table.

Client (`mobile/useFocusClock.ts`, `mobile/useFocusSessionLog.ts`):

- The hooks keep their public shape. State changes optimistically, and the
  request follows on one shared queue (`mobile/sessionSync.ts`), so writes land
  in the order they were made and a read sees the writes before it.
- A running clock's elapsed time is sampled when its request leaves, not when
  it was queued.
- **Offline / a failed write.** The screen never waits on the network, so
  the question is only what the server ends up holding:
  - A write that doesn't *arrive* (no signal, timeout, a 5xx) stays in an
    in-memory outbox, in order. It is sent again ahead of the next write or
    read, on the browser's `online` event, and on a backoff timer (5 s
    doubling to 5 min). Writes are idempotent and a running clock samples its
    elapsed time when it is finally sent, so a late delivery books what an
    on-time one would have.
  - While the outbox holds anything, reads are skipped: the server is behind
    this device, and painting its answer would undo what the user just did.
  - A write the server *refuses* (4xx: the task was purged, a bad body) is
    dropped, and the hook re-reads the server, which is then the truth.
  - The outbox is not persisted. Reloading while still offline drops the
    unsent writes (the plan can't load offline anyway), and the server keeps
    the last state it received. A second device sees this one's changes only
    once they have been delivered.
- Returning to the tab re-reads both.
- **One-time move:** the first load after the upgrade uploads any old
  `focus.session` / `focus.clock` keys, then removes them. An old clock is only
  adopted when the server has none. A key stays for a retry if the server was
  unreachable.

## Repeating commitments (#376)

A daily 10:00 standup used to need re-adding every day. A commitment can now
repeat, and the overlay rule above still holds: a series is one stored
decision, and its occurrences are worked out per day when the plan is read.
Nothing is stored for a future day ahead of time.

**Decision: a column on the commitment row, not a separate rule table.** A
series *is* a commitment (title, start, length), plus a cadence. A rule table
would duplicate those columns and give the plan two places to read a
commitment from. Two nullable columns on `focus_plan_entries`:

| column | meaning |
|---|---|
| `repeat_interval` | the cadence, the task shape `{"unit": "day"\|"week"\|"month", "every": 1-12}`, plus for `unit: "week"` an optional weekday set `"days": [0-6]` (Monday 0, #427). Set means this commitment is a **series** that repeats from its `plan_date` (the first occurrence) on |
| `series_id` | set only on a **skip**: the series whose occurrence on `plan_date` doesn't happen |

The kind check gains a fourth kind: a skip carries `series_id` and nothing
else (no task, title, time, length or cadence). The other kinds carry no
`series_id`, and only a commitment may carry a cadence. A partial unique
index allows one live skip per series per day. `repeat_interval` is
`JSON(none_as_null=True)`, so Python `None` is SQL `NULL` and never the JSON
`null` the check would count as set.

**Which days a series covers** (`focus_plan.occurs_on`), counted from the
first occurrence, never chained from the previous one:

- `day` / `week`: every `every` days / `7 × every` days from `plan_date`.
- `month`: every `every` months, on the first occurrence's day-of-month,
  clamped to a short month's last day. A series starting on 31 January lands
  on 28 February and on 31 March. This differs on purpose from task
  recurrence, which chains from the last due date and so drifts to the 28th
  for good.
- `week` with `days` (#427): each listed weekday of every `every`-th
  calendar week (Monday-based), counted from the week `plan_date` falls in,
  and never before `plan_date`. `plan_date` need not be a listed day: a
  Mon-Fri standup added on a Saturday starts on Monday.

**Weekday sets, and why not a `weekday` unit (#427).** `days` is a set
inside the existing weekly unit rather than a new `"unit": "weekday"`: one
shape covers Mon-Fri, Mon/Wed/Fri and "every other Tue and Thu", where a
`weekday` unit would have no sensible `every` and would need another unit
for each new pattern. `days` must be non-empty, 0-6, without repeats, and
only with `unit: "week"`; it is stored and returned sorted, and a cadence
without it keeps the plain task shape (no `days` key). The schema is
`CommitmentRepeatInterval`, a commitments-only subclass of the task
`RepeatInterval`: task recurrence chains due dates through
`next_due_date` and keeps its own shape unchanged. No migration: the column
is JSON and its check only looks at null-ness.

**How a day reads its commitments** (`focus_plan.day_commitments`): that
day's one-off commitments, plus every live series with `plan_date <= day`
that `occurs_on` the day, minus the series with a live skip for that day. The
series query is all live series up to the day, filtered in Python. A person
has a handful, so it isn't indexed. The packer treats an occurrence exactly
like a one-off commitment: a fixed block, dropped as past once it has ended
before the window starts. Task pins are still read from that day's own rows.

A plan's `CommitmentBlock` carries `repeat_interval`. For an occurrence,
`entry_id` is the **series** row, the same id on every day.

### Verbs

- **Add** (`POST /api/focus/commitments` with an optional `repeat_interval`)
  creates the series. Creation stays lane-scoped: the Focus page's
  add-commitment form (desktop) and the session sheet (mobile) gain a Repeat
  choice of Once, Daily, Weekdays (Mon-Fri, #427), Weekly or Monthly. The
  API takes any task cadence, plus any weekly weekday set.
- **Skip one day** (`POST /api/focus/entries/{series_id}/skip` with
  `{plan_date}`) stores a skip row for that day and returns it. It is
  idempotent: skipping a skipped day returns the skip already there. Skipping
  a one-off commitment, a pin, or a day the series doesn't cover is a 409.
  - **Undo** is removing the skip (`DELETE /api/focus/entries/{skip_id}`), a
    soft delete like every other removal. Restoring a removed skip skips the
    day again. Nothing is hard-deleted.
  - Activity: `skipped` when stored, then `deleted` / `restored` as for any
    entry, with summaries that name the series and the day.
- **Stop the series**: remove the series row (`DELETE` on its id). This takes
  out every occurrence, past days included, and restore brings them all back.
  A past day's plan isn't a record of what happened (`focus_sessions` is), so
  losing a past occurrence from it costs nothing. "Stop from today on, keep
  the earlier days" would need an end date, and is out of scope (below).
- **Edit the series**: `PATCH` on the series row changes the time, title or
  length of every occurrence, past and future. On both Focus pages, Move on a
  repeating commitment says so ("Move every day" on mobile; the desktop toast
  says "every time"). A skip has nothing to edit (409).

**Out of scope, deliberately:**

- **Editing one occurrence** ("just today, standup is at 10:30"). The shape
  leaves room for it: a skip-like row for that day carrying its own time. For
  now, skip the day and add a one-off commitment instead.
- An **end date** ("stop from today on") and a **count** ("ten times").
- **Changing the cadence** of an existing commitment. `PATCH` takes no
  `repeat_interval`, so a one-off can't be turned into a series or back.
  Remove the series and add a new one.
- **Weekdays only.** The task cadence has no "every weekday", so a Mon–Fri
  standup is a daily series with weekend days skipped, or five weekly series.
- **Showing a skipped day.** A skipped occurrence just isn't in that day's
  plan. The mobile undo bar can bring it back; after that, only the API can
  (remove the skip).

### UI

- The cadence is a coloured word in the meta line (`daily`, `weekly`,
  `every 2 weeks`, `weekdays`, `weekly on Mon, Wed`), beside `fixed`, never
  a pill.
- Desktop: a repeating commitment's row adds **Skip day**, and its Remove
  reads **Remove series**.
- Mobile: the `⋯` sheet adds **Skip this day**, with the undo bar (its undo
  removes the skip), and its Remove reads **Remove series**. A skipped
  occurrence leaves the timeline, and `buildDayShape` needs no change: an
  occurrence arrives as an ordinary fixed row.

### Agent

The tool schemas are unchanged in this PR, so the eval gate isn't triggered by
an input-schema change. `add_focus_commitment` still adds a one-off. Its
service call simply leaves the new `repeat_interval` argument at its default.

Two things the agent does see:

- Results gain the new nullable fields: `repeat_interval` on
  `get_focus_plan`'s commitments, and `repeat_interval` / `series_id` on
  entries. Since #427 a `repeat_interval` in those results may carry `days`
  (output only; no tool takes a cadence yet, see #426).
- `remove_focus_entry` given a series' `entry_id` removes the whole series.
  It stays undoable with `restore_focus_entry`, and the activity summary names
  the cadence ("…, daily from 2026-10-20, removed from Focus").

Letting the agent create a series or skip one day means a tool-schema change,
which runs the eval gate, so it is a follow-up.

## Not built (follow-ups)

- Editing one occurrence of a repeating commitment, an end date for a series,
  and agent tools for series and skips (#376's follow-ups; the agent's repeat
  options are #426). The forms offer only the Mon-Fri weekday set; other sets
  are API-only.
