# The frontier tier

Live-model scenarios that are **meant to be hard**, measured over time and
never gated (#408, ported from chess#318/#339). The gate
(`docs/agent-design.md`, "Eval harness + baseline") holds floors that are
regression tripwires, not aspirations. This tier is where the aspirations go.

| Layer | Where | Must pass? | What it is for |
| --- | --- | --- | --- |
| Unit tests, including the frontier's pure parts | CI | yes | the code, the scorers, the report |
| The gate (`tests/test_agent_evals.py`) | by hand, before a prompt/model/loop merge | yes, floor 0.8 | regressions |
| **The frontier** (`tests/test_agent_frontier.py`) | by hand, on demand | **no, a score** | progress on hard, composed asks |

## Running it

```bash
cd backend
PCC_AGENT_FRONTIER=1 PCC_AGENT_EVALS_REPORT=/tmp/frontier.jsonl \
    .venv/bin/pytest tests/test_agent_frontier.py -v -s
.venv/bin/python scripts/frontier_report.py append /tmp/frontier.jsonl --label "after #411"
.venv/bin/python scripts/frontier_report.py trend                    # last six runs per split
.venv/bin/python scripts/frontier_report.py trend --variants --modes # and the latest run's detail
```

| Env var | Default | What it does |
| --- | --- | --- |
| `PCC_AGENT_FRONTIER` | unset | `1` runs the tier. Unset, every item is skipped (CI never runs it). |
| `PCC_FRONTIER_SPLIT` | `heldout` | `heldout` (the number of record) or `dev` (where a prompt change is iterated). |
| `PCC_FRONTIER_RUNS` | 5 | Samples per scenario, cycling its wordings: five is one per wording. |
| `PCC_AGENT_EVALS_SEED` | unset | The gate's paired seeds: sample *j* runs with `base + j`. |
| `PCC_AGENT_EVALS_REPORT` | unset | JSONL report path (shared with the gate's knob). |

`LLAMACPP_*` and the model's profile apply as for the gate. Check that the
shared card is idle first (`curl :8200/running`). A full held-out run is 40
samples, a few minutes warm.

`append` adds one line per split in the report to
`docs/frontier-history.jsonl`; commit it with the change it measured (or on
its own, labelled, for a model or server change). `trend` prints a scenario ×
run table per split, held-out first.

## One sample per wording, held-out by default

At the profile's temperature repeats of one wording mostly agree (chess read
one scenario at exactly 5/10 three runs in a row, alternating two wordings:
one always passed, the other always failed). A scenario's information is in
its wordings, not its repeats, so every scenario has ten, dealt five and five
to `dev` and `heldout`, and a run takes one sample per wording.

- **Held-out is the number of record.** It is the split a model is compared
  on and the one an improvement must move. Nobody tunes against it.
- **Dev is where a prompt change is iterated.** An improvement on dev alone is
  not believed.
- **Splits are dealt, not chosen.** A scenario's wordings are written as one
  pool and dealt by a seeded shuffle (`frontier.deal`, salt `408:<name>`), so
  easy phrasings cannot gather on one side. A wording is named by its place in
  the pool (`w03`); a rewritten wording stays in its split.

## What a run reports

Per scenario (`FrontierResult.record`, one `frontier` line each, after a
`frontier_header` naming the git sha and dirty flag, model, profile, the
system and summary prompt and tool-schema hashes, split, runs and seed):

- **whole-task passes** out of N with a one-sided 95% Wilson interval. A whole
  pass is every checkpoint met on one sample with every run `completed`;
- the **rubric score**, the mean fraction of checkpoints met: the number that
  moves while whole-task passes are still near zero;
- **checkpoint hits**, each checkpoint's count over the samples;
- **failure modes**, one per missed sample from a fixed vocabulary (below);
- **passes per wording**, so a number shows which phrasings it came from;
- **claims** (`tests/claims.py`, #407): every reply's operational claims held
  to the sample's tool calls, made/backed per family, every unbacked line
  quoted. In the frontier an unbacked claim misses the `claims_backed`
  checkpoint rather than failing the sample outright;
- the scenario's **fingerprint** for the split, infra deaths, and every
  sample (variant, seed, steps with ask, trajectory, refusals, reply).

An item **fails** only when the number cannot be trusted: no sample measured
(every one an infrastructure death, or a request llama-server rejected), a
checkpoint that raised (a broken scenario), or a harness bug. A low score is
never a failure.

### Failure modes

A missed sample gets exactly one name (`frontier.failure_mode`). Precedence:
a run that did not complete explains the rest; then a thread that made no
tool call at all; then the first missed checkpoint's own mode, in the order
the scenario lists them.

| mode | means |
| --- | --- |
| `stopped` | a run ended on a limit or failure stop (`no_progress`, `max_iterations`, a tool that raised) |
| `answered_from_memory` | the whole thread made no tool call (#405's self-poisoning) |
| `did_not_ask` | acted, or asked without naming the options, where the ask was ambiguous |
| `missing_write` | part of the ask never happened |
| `wrong_target` | the write landed on the wrong row |
| `wrong_value` | the right row, the wrong value (a date, a cadence, a weekend lunch) |
| `collateral` | something outside the ask changed |
| `unbacked_claim` | the reply said what the run did not do |

A new name is a decision (it moves `frontier.FAILURE_MODES` and this table),
never a typo: `Checkpoint` refuses a mode outside the list.

## Reading the history

- **A mark means the intervals separated.** `trend` puts ▲/▼ on a cell only
  when its one-sided 95% Wilson interval does not overlap the previous run's.
  At five samples that takes a big move (2/5 → 4/5 is no mark). Runs on
  different days sit on differently-warmed servers and consecutive samples
  of one prompt are correlated, so the history shows *trend*, never evidence
  that a particular change worked.
- **The total is the headline.** The `total` row sums whole-task passes over
  every scenario in the run (with the mean rubric). A scenario's own five
  rarely separate from anything; the corpus's forty do more often. The
  `claims backed` row under it is the run's claim accuracy.
- **A changed scenario is never compared with its old self.** A cell whose
  fingerprint differs from the previous cell's is marked `†` instead of
  compared. The fingerprint (`Scenario.fingerprint`) hashes the split's
  wordings (the templates, before today's dates are filled in), the
  checkpoint names, the stored history's name and `Scenario.revision`. The
  total and claims rows are marked only between runs of the same corpus (the
  same scenarios on the same fingerprints), and are `†` otherwise.
- **A claim that a change moved a score is an A/B** on one server, arms
  alternating, seeds paired (`docs/agent-campaigns.md`). The campaign script
  drives the gate today; for the frontier, run both arms' dev split in
  alternation with one `PCC_AGENT_EVALS_SEED` and compare sample *j* of each.

## Solved scenarios stay

- **`solved` is a mark, not a verdict.** `trend` marks a held-out row `yes`
  when its whole-task rate is at least 0.8 in each of the two most recent
  held-out runs, both on one fingerprint. Nothing moves on it: a solved
  scenario is neither graduated into the gate nor dropped. It is where the
  next model shows it holds the line.
- **Headroom comes from new scenarios**, written beside the solved ones.
- **Never demote.** A gate scenario that regresses is a regression and fails
  the gate; it does not move here to get quiet. Only a known red that needs a
  capability the agent does not have yet (rather than a fix to what it has)
  belongs here, with its reason and issue in the scenario's `notes`.
- **Never lower the bar.** A checkpoint found unfair is fixed, the scenario's
  `revision` bumped, and its series starts again.

## Writing a scenario

`backend/tests/frontier_corpus.py`. A `Scenario` (`tests/frontier.py`) has a
name, a tier, a `why`, ten `wordings` (each the asks of one wording, one per
turn), `checkpoints`, a `setup` that seeds the sample's DB through the
service layer, an optional stored `history` (a name in
`frontier_corpus.HISTORIES`), `notes` and a `revision`. A sample runs through
the gate's own `Sample.converse` (the conversation route, as in production),
and its `Episode` gives the checkpoints the DB before and after each turn
(`World`), every run, the open DB (for focus entries, dependencies,
cadences), the claims and today's date slots.

- **Grade the end state, the tool calls and the claims, never the wording**,
  except where the scenario is about what the reply says (the clarifying
  question has to name both candidates: `eval_threads.mention_order`).
- **Each checkpoint is one piece of the task**, with the mode its miss means.
  Order them so the first miss names the real failure. Every scenario ends
  with `claims_backed`.
- **Doing nothing must not pass.** `test_frontier_corpus.py` plays every
  scenario in CI over a scripted provider that only says "Okay.", and plays
  oracles for two scenarios to show a whole pass is reachable.
- **Dates come from today**, through `{slot}` placeholders in the ask
  (`frontier_corpus.date_slots`) and pure helpers in the checks
  (`eval_threads`), so the fingerprint does not change with the calendar.
- **Bump `revision` when a number's meaning changes unseen**: a new setup, a
  stored history's text, or a checkpoint's logic fixed under its old name.
- **Tiers:** 1 one capability past the gate (one ask, one hard reading), 2 a
  multi-turn or composed ask, 3 a whole workflow in one ask.

## The corpus (v1, #408)

| tier | scenario | the ask | checkpoints |
| --- | --- | --- | --- |
| 1 | `poisoned_complete` | "Mark the dentist appointment task as done", after 16 true turns and a last reply that falsely said it was done | `called_a_tool`, `dentist_done`, `only_the_dentist`, `claims_backed` |
| 1 | `poisoned_reschedule` | "Push the furnace filters task to {in ten days}", after a last reply that falsely said no such task exists | `called_a_tool`, `filters_rescheduled`, `due_on_target`, `only_the_filters`, `claims_backed` |
| 1 | `misheard_push` | "push the dennis appointment to friday" (and other manglings) beside a car service appointment | `dentist_moved`, `dentist_on_friday`, `car_service_untouched`, `only_the_dentist`, `claims_backed` |
| 2 | `poisoned_followup` | create "Renew the car registration" in Home, then "now make it high priority", after a last reply that falsely said it already exists | `called_a_tool`, `created_in_home`, `made_high`, `only_the_new_task`, `claims_backed` |
| 2 | `ambiguous_then_pick` | "Mark the dentist one as done" with two dentist tasks, then "The second one" | `asked_before_acting`, `named_both`, `second_done`, `only_the_second`, `claims_backed` |
| 2 | `long_composition` | complete the dentist task and add a follow-up next month, make the weekly review repeat weekly, move everything due today to tomorrow (not the overdue one), make the report wait on the data pull | `dentist_done`, `follow_up_created`, `follow_up_next_month`, `review_repeats_weekly`, `due_today_moved`, `report_waits_on_data`, `nothing_else`, `claims_backed` |
| 3 | `reorganize_project` | "Reorganize my Home project": a new Garden project with the yard tasks moved into it, the duplicate trashed, the tap fix high priority | `garden_created`, `yard_moved`, `indoor_stayed`, `duplicate_trashed`, `tap_high`, `nothing_else`, `claims_backed` |
| 3 | `plan_week` | "Plan my week in Focus": the report pinned tomorrow at 9, every other task due in the next seven days pinned on its due day, a weekday-only 12–1 lunch block | `report_tomorrow_9am`, `due_tasks_on_due_day`, `no_stray_pins`, `lunch_every_weekday`, `lunch_not_weekend`, `tasks_untouched`, `claims_backed` |

### What moved from the gate, and what stayed

The gate parked its known reds as `xfail` "until the frontier tier". Each was
read again:

- **Moved** (they need a capability, not a fix, so as tripwires they would
  only ever read red): the three `poisoned` conditions of
  `followup_high_priority`, `long_complete` and `long_reschedule` (#412, #419:
  a fresh state block every turn, code-kept memory), now `poisoned_*`; and
  `ambiguous_dentist_then_second` and `speech_push_appointment` (#420's typed
  clarification, #416's prompt rules), now `ambiguous_then_pick` and
  `misheard_push`. Each scenario's `notes` keep the gate's numbers and issues.
  `fresh` and `live_like` stay in the gate.
- **Stayed, still `xfail`:** `recurrence_make_repeating` and
  `compose_finish_and_follow_up`. Both are one plain ask the agent must be
  able to do, broken by a schema trap #411 fixes; once it lands they are
  tripwires, and the frontier never graduates a scenario back.
  `long_composition` carries both inside a harder whole.
- **Stayed, gated:** `misfire_subtask_under_parent` (15/20, `UNDECIDED`) was
  never marked; its comment still says to mark it only if it goes
  `BELOW_FLOOR`.

## Baseline

The first held-out run is the first line of `docs/frontier-history.jsonl`;
read it with `scripts/frontier_report.py trend --variants --modes`.
