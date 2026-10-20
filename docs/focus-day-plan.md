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

There are three kinds of entry:

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

## Not built (follow-ups)

- Moving the session log and the running clock from `localStorage` onto the
  server (#374).
- Recurring commitments (#376).
