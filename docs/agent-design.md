# Agent design

The living reference for the agent stack. Full design narratives are in this
file's git history.

The one-sentence architecture: **the MCP server and the in-app loop are
consumers of `app/services/` — same write path as the UI routes, same
validation, same soft deletes, same `activity_events` audit trail — with the
destructive endpoints (purge, empty-trash) simply never exposed as tools.**

## Tool surface

Tools map 1:1 onto service functions — (validate args) → (open session) →
(call service) → (serialize). Read tools reuse the REST response models.

- **Tasks:** `list_tasks` (filtered, paginated, cap 200), `get_task` (+rollup
  and blocked flag), `create_task`, `update_task` (recurrence `edit_scope`
  supported), `complete_task` (triggers recurrence spawning), `reopen_task`,
  `trash_task` (the only delete — cascades, restorable).
- **Projects:** `list_projects`, `get_project`, `create_project`,
  `update_project`, `close_project`/`reopen_project`, `trash_project`.
- **Search/focus/trash/activity:** `search` (FTS-tiered — this *is* the
  retrieval story), `get_focus_plan`, `list_trash`,
  `restore_task`/`restore_project`, `list_activity`.
- **Stored day plan (#319):** `pin_focus_task` (floating, or fixed with
  `start_time`), `add_focus_commitment` (fixed non-task time),
  `remove_focus_entry` (soft) / `restore_focus_entry`. Entry ids come back in
  `get_focus_plan` (`entry_id` on blocks and commitments).
- **Dependencies/recurrence:** `list_dependencies`, `add_dependency`,
  `remove_dependency`, `skip_occurrence`, `stop_recurrence`. Recurrence
  creation/editing rides on `create_task`/`update_task`.

**Deliberately not exposed:** `purge_task`, `purge_project`, `empty_trash`,
`reorder_projects`, the dashboard overview. Trash retention (#321) — the
scheduled purge of trash older than `TRASH_RETENTION_DAYS` — is a lifespan job
in the API process, not a tool: nothing on the tool surface can trigger or
schedule it, and its purges are attributed to `retention`, never to an agent.

## Guardrails

1. **No hard deletes, structurally** — purge/empty-trash are never registered
   as tools, so no validation bug or prompt injection reaches them.
2. **Pydantic validation at the boundary**; service domain errors return as
   tool errors the model can self-correct from — never a 500.
3. **Attribution**: every mutation lands in `activity_events` with an actor —
   `agent:mcp`, `agent:loop`, or a recognized delegate (`agent:conductor` via
   `X-Agent-Actor`; unknown values fall back to `agent:loop`, so nobody
   stamps an arbitrary identity).
4. **Structured logging** with a per-call request ID.
5. **Rate limiting** on the agent messages endpoint (`agent_messages_per_min`).

## Transport

stdio MCP server as a module of the backend package (`python -m
app.mcp.server`), registered in the repo's `.mcp.json`; it imports services
directly and opens its own sessions (SQLite WAL handles the two processes).
The in-app loop consumes the same transport-agnostic registry
(`app/tools/registry.py`) — `ToolSpec`s are byte-identical to the MCP
`inputSchema`s (parity-tested). Streamable HTTP is deferred until a consumer
needs it.

## Runtime

Served by the workspace `../llama-swap/` stack — one proxy owning the RTX
3060, one shared `gemma-4-12b` (UD-Q4_K_XL) entry for chess + PCC. Server
flags live in `../llama-swap/config.yaml` only. `-c 131072` with `q8_0` KV:
measured 9.5 GB loaded / 10.5 GB peak on a 125k needle test; generation ~112
tok/s shallow → ~41 tok/s at depth; prefill ~446–680 tok/s; cold load ~100 s
worst case (the provider timeout tolerates it). Containers reach it via
`host.docker.internal:8200`.

## Shipped architecture (pointers)

- **Provider** `app/ai/providers/llamacpp.py`: OpenAI wire over httpx,
  Pydantic-validated, typed errors, gemma quirks handled
  (`reasoning_content` never round-trips; thinking off by default; sampling
  set per request from the model profile — gemma-4-12b's is temp 1.0 /
  top-p 0.95 / top-k 64). Live smoke:
  `PCC_LLM_INTEGRATION=1 pytest tests/test_ai_llamacpp_integration.py`.
- **Model profiles** (#402) `app/ai/model_profile.py`, one TOML file per
  model in `app/ai/profiles/<llama-swap id>.toml`, picked by
  `LLAMACPP_MODEL`: `[sampling]` (`temperature`/`top_p`/`top_k`; a key left
  out is left to the server), `thinking_kwarg` (the `chat_template_kwargs`
  key; empty sends no kwargs), and the per-call caps `[phases.turn]
  max_tokens` / `[phases.summary] max_tokens` (each at most the completion
  reserve). Loading is strict — an unknown key, phase or mistyped value fails
  — and a model with no file runs on the built-in default (no sampling
  opinion, today's caps) with a `model_profile_missing` warning. The provider
  builds every request from its profile, the loop takes its caps from it, the
  run trace records its name (`serving.profile`), and the eval harness prints
  it, so measuring another model is `LLAMACPP_MODEL=<id>` plus its file.
  gemma-4-12b's sampling and thinking key are copied from
  `../agent-standard/model-profile.md`; `tests/test_model_profile.py` checks
  them for drift when that sibling is checked out (skipped in CI).
- **Loop** `app/ai/loop.py`: bounded iterations (10) + separate correction
  budget (3); schema failures are corrections, domain rejections are ordinary
  results; writes stamped `agent:loop`. Every model call carries
  `max_tokens` (the profile's turn cap — 2048 for gemma-4-12b and the
  default, the context budget's completion reserve), since llama-server's
  default is unbounded and a degenerate generation would otherwise run to the
  run budget (#397). A cut-off call (`finish_reason: "length"`) never passes
  its words on: a cut-off text turn is dropped — no reply saved, nothing
  added to history — and the run ends `truncated`; a cut-off turn's tool
  calls still run (each call's arguments arrived whole), only the text beside
  them is dropped. Work is bounded too (#398): a turn in which *every* call
  repeats an exchange the run already made — keyed on tool + canonicalised
  arguments + the answer, so a repeat that comes back different (a list
  after a write) is progress — ends the run `no_progress`; a turn with a
  schema error is left to the correction budget instead. A run dispatches at
  most `DEFAULT_MAX_TOOL_CALLS` = 24 calls (the largest deployed run made 18,
  an 8-task batch made twice after schema errors; schema-invalid calls
  count); a call past it is answered "not run" (one tool answer per call),
  never dispatched, and the run ends `tool_call_limit` after that turn.
- **Limit stops say what they did** (#399): when a run ends `max_iterations`,
  `correction_limit`, `truncated`, `no_progress` or `tool_call_limit` and at
  least one call reached the service layer (done, refused or looked up), the
  loop makes one more model call — offered no tools, capped at the
  profile's summary cap (512), bounded by the time left on the run deadline —
  that reads a fresh brief, not the run's transcript: the user's message, a
  code-built record of the run (`app/ai/run_record.py`, sorted from the
  `ToolCallRecord`s into Done / Refused / Looked up / Not run, one compact line
  per call, "Done: nothing" said outright) and the note "Whatever the record
  above does not show as done was not done." Its text is saved as the reply
  (and so enters the next turn's history); the stop reason is unchanged, so
  the UI still shows its stop line and delegates still read the limit. Reads
  are `READ_TOOLS` — exactly the registry bodies opened with
  `tool_session(..., write=False)`, pinned by a test; any other tool that
  answers is "done". No call ran, a Stop, or a summary that fails, times out,
  is cut off (`finish_reason: "length"`) or comes back empty or as a tool call:
  no reply, as before. `cancelled` and the failure stops never summarise.
- **Stop reasons**: `completed`, `max_iterations`, `correction_limit`,
  `truncated`, `no_progress`, `tool_call_limit` and `cancelled` return 200; `provider_error` (502), `timed_out` (504) and
  `internal_error` (500) are failures. Every one persists a truthful assistant
  turn. The deadline and the cancel event (`POST …/cancel`, #324,
  `app/api/run_cancel.py`) are checked at the same two boundaries — before each
  provider call and each tool dispatch — so neither interrupts a running tool;
  an in-flight provider call finishes first, and calls it proposed after Stop
  are recorded as not run. The cancel event is per run (registered inside the
  run lock), so a late Stop never leaks into the next run.
- **Prompt**: app base contract + vendored global Glitch
  (`app/ai/personality-global.md`, canonical in `../agent-standard/`; fix
  drift by re-copying) + today's date. PCC ships no app-flavor layer.
- **Persistence** (`conversations`, `conversation_messages`): assistant turns
  carry the tool trajectory + stop_reason; the user turn commits before the
  loop runs. `POST /api/agent/conversations/{id}/messages` is the one
  model-calling endpoint, synchronous by decision (SSE only if the wait feels
  bad).
- **Chat panel** `features/agent/`: visible trajectory, per-mutation undo,
  ambient entry from the search bar.

## Context budget (`app/ai/context_budget.py`)

A deterministic recent window over history — no summarizer (that's a second
GPU call; cloud is forbidden). Token estimate is `CHARS_PER_TOKEN = 3`
(deliberately over-counts, so budgets are floors). Reserves off the 131,072
window: system prompt + schemas 8,192; current turn 2,667; run tool traffic
16,384; completion 2,048. The binding constraint is prefill *time*, not the
window: `PREFILL_BUDGET_TOKENS = 24_576` (~55 s pessimistic against the 240 s
run budget) is the effective history ceiling.
`services/conversations.py::history_for_loop` is the source of truth; the
loop re-applies the same function with measured overhead before every
request. Conversation GET is paginated (`before_id`, default 100/max 500).

## Eval harness + baseline

```bash
cd backend
PCC_AGENT_EVALS=1 .venv/bin/pytest tests/test_agent_evals.py -v -s
```

Opt-in; scripted scenarios through the full loop + registry against the real
runtime, a fresh DB per sample. Asserts trajectory *shape* + DB end-state +
audit invariants, never exact sequences (temp 1.0).

**The gating rule (#403): no scenario may go statistically below its floor.**
Every scenario's floor is 0.8. A scenario is sampled in blocks of
`PCC_AGENT_EVALS_RUNS` (default 5) and judged by `tests/evalstats.py` (pure,
unit-tested in CI by `test_evalstats.py`): a one-sided 95% Wilson upper bound
*below* the floor is red; a pass rate at or above it is green; anything in
between buys another block, up to `PCC_AGENT_EVALS_MAX_RUNS` (default 20). At
the defaults 0–2/5 is red, 3/5 escalates, 4–5/5 is green, so a healthy
scenario costs exactly five samples. A count still undecided at the cap
passes, flagged `UNDECIDED`. A green therefore means *not statistically below
the floor* (a red is strong evidence of harm, a green weak evidence of
health), not "works ≥80% of the time". Don't lower a floor to get quiet: the
gate's power depends on it. Why not "N clean runs in a row": consecutive
samples on one llama-server are correlated (chess read one unchanged prompt
at 5/20, 19/40 and 31/40 in three batches), and, per chess's analysis, at a
true rate of 0.80 a fixed `rate >= 0.8` check goes green 74% of the time at
n=5 and *less* often (63%) at n=20. `tests/evalstats.py` is a copy of chess's
(provenance in its header); folding it into agent-standard is #396 phase 7.

- **Infrastructure is retried, never scored.** A provider death (dead socket,
  5xx, timeout, the run budget running out on the provider) or a 429 from the
  agent route is thrown away and re-taken, at most
  `PCC_AGENT_EVALS_INFRA_RETRIES` (5) times per scenario and
  `PCC_AGENT_EVALS_INFRA_BUDGET` (25) per suite; running out fails the
  scenario as `INFRA_ABORTED`, with no rate. A request the server rejected (a
  4xx such as a context overrun, an unparseable body) fails on the first one:
  a retry gets the same answer. Limit stops (`max_iterations`, `no_progress`,
  …) and a tool that raised (`internal_error`) are the agent's doing and are
  scored. `delegate_actor` drives the HTTP route, so each of its samples gets
  a fresh client address (the route allows 10 messages a minute per client).
- **Paired seeds for A/B.** `PCC_AGENT_EVALS_SEED=<base>` sends llama-server
  the sampling `seed` `base + j` on every call of a scenario's `j`-th scored
  sample (an infra retry re-takes the same seed). Unset sends no seed, and the
  app itself never seeds: the pinned request bytes carry no `seed` field. Run
  both arms with the same base and their own report, then
  `.venv/bin/python -m tests.eval_report A.jsonl B.jsonl` prints each
  scenario's counts and the exact two-sided McNemar test on the discordant
  pairs (`pairs N · A-only b · B-only c · p=…`). Read p with the counts, not
  instead of them. Pairing is approximate (llama.cpp is not bit-reproducible
  across batch sizes or slots) but never worse than unpaired.
- **The report.** `PCC_AGENT_EVALS_REPORT=<path>` appends JSONL: a header
  (git sha and dirty flag, model, profile, system/summary prompt and
  tool-schema sha256 — the run trace's hashes, #400 — knobs, floors), one line
  per scenario as it finishes (verdict, interval, blocks, failure modes,
  schema corrections, and every sample with its seed, outcome, stop reason,
  trajectory, every refused call's error text and `run_id`), and a closing
  suite line with the infra budget spent. The header also prints, and `-s`
  ends with a per-scenario table (count, interval, verdict, blocks, infra
  retries, iteration and duration ranges, schema corrections). Formatting and
  the paired comparison live in `tests/eval_report.py`.
- **Schema corrections per sample (#404).** Each sample counts the calls
  refused for their arguments (`Invalid arguments: …`) or an unknown tool
  name, plus turns whose arguments weren't JSON; a domain refusal ("not
  found", "blocked") is not one. A scenario reports the total over its tool
  calls, the mean per sample and how many samples needed any
  (`schema_errors`, `tool_calls`, `schema_errors_per_sample`,
  `schema_error_rate`, `samples_with_schema_errors`). A run that recovers
  still passes, so this is the number #411's schema work moves, not the
  verdict.

- **Multi-turn threads (#389, #405).** `Sample.converse(turns, thread=…,
  headers=…)` drives a scripted conversation through
  `POST /api/agent/conversations/{id}/messages`, the one path where history
  exists (the route reads it with `history_for_loop`, as in production). A
  `thread` of (user, assistant) text pairs is stored first through the
  conversations service, so a 16-turn history costs no model runs and is the
  same in every sample. Each turn is then posted, metered on its own
  (iterations, seconds, trajectory, schema corrections, refusals, reply,
  `run_id`: the report's per-sample `steps`), snapshotted (`World`: every task
  and project, trashed ones included, and the whole activity log), and
  checked straight away against that snapshot and the ones before it
  (`Thread.baseline`, `.before`, `.steps`). **One sample is the whole
  thread**: it passes only if every step's check passes, a failed check ends
  it naming the step, and an infra death at any step re-takes the whole
  thread (never scored), so the statistical gate is unchanged. A sample's
  iterations, duration, calls and schema corrections are its steps' sums.
  History is text only, so a reference ("it", "that", "the second one") can
  resolve only through what an earlier *reply* said, never an earlier tool
  result. "The n-th one" is therefore the n-th candidate that reply names, in
  the order it first names each one (`tests/eval_threads.mention_order`, by
  title or a word only that candidate has): that reply is both the list the
  user saw and the list the model has in front of it. "Friday" is the next
  Friday strictly after today, or today too when today is one
  (`weekday_targets`). Both helpers are unit-tested in CI
  (`test_eval_threads.py`).

`PCC_AGENT_EVALS_RUNS` keeps its #384 name with a new meaning: the block
size, not a fixed repeat count. `PCC_AGENT_EVALS_RUNS=1` takes one sample per
scenario (a miss is red, as before #403); setting it at or above
`PCC_AGENT_EVALS_MAX_RUNS` forces a fixed count. Each scenario is one pytest
case (no more `[runK]` ids), so `-k <scenario>` picks scenarios.

**Baseline (gemma-4-12b UD-Q4_K_XL). Under the floor rule (#403,
2026-10-20, llama-swap, `PCC_AGENT_EVALS_SEED=403`): every scenario
`ABOVE_FLOOR` on its first block — 5/5, interval [0.65, 1.00], `STABLE`,
0 infra retries; 35 samples in 69 s. Before it, as all-pass suites: 24/24 on
2026-07-11, re-confirmed under the layered personality 2026-07-11, the
context budget 2026-08-05, and llama-swap serving 2026-10-18 (28/28 with
`delegate_actor` added).** The multi-turn scenarios (#389, #405) were added
2026-10-20 at the same seed: the whole 41-case suite ran 36 passed + 5
xfailed, 205 samples in 417 s, 0 infra retries. The `long_*` and
`followup_high_priority` families run one ask under three conditions over one
shared world: `fresh` (empty conversation), `live_like` (16 stored turns of
ordinary use, all true of that world) and `poisoned` (`live_like` plus one
last exchange where the agent's *own* reply made a false claim about the very
thing asked: already done, not found, already created). Fresh and live_like
pass 30/30 between them; poisoned is red in all three (2/15), the misses
answering from the false claim with zero tool calls. Length is not the cause;
a false claim in the agent's own history is, exactly chess's self-poisoning
finding. A change regresses this baseline when a scenario
goes `BELOW_FLOOR` (a scenario marked `xfail` is a known-red before-number,
not gated; drop the mark once it goes green); re-record the table when it legitimately moves, and say
why in the PR.

| Scenario | Asserts | Iterations | Warm time |
| --- | --- | --- | --- |
| `create_task_with_fields` | routing, priority, date math, audit | 3–5 | 3.0–5.9 s |
| `find_and_complete` | described task found via read, completed | 3 | 1.2–1.8 s |
| `reschedule` | targeted due-date update only (ISO target relative to today, #380) | 3 | 1.5–4.5 s |
| `delete_is_soft` | lands in trash, restorable, audited | 3 | 1.0–3.2 s |
| `read_only_count` | exact count (the only number in the reply, numeral or word, #381), zero mutations | 3 | 1.4–2.0 s |
| `honest_about_missing` | nothing invented or acted on; frugality tripwire ≤7 iterations / ≤6 reads | 2–6 | 0.9–2.3 s |
| `delegate_actor` | via the route with `X-Agent-Actor: agent:conductor`: every run event the delegate's, prior events untouched | 3–5 | 1.5–3.2 s |
| `focus_pin_and_commitment` | "pin at 2pm, block 12 to 1 for lunch": one timed pin on the right task, one non-repeating 60-min commitment, nothing else, audit (#378) | 4 | 2.5–2.6 s |
| `focus_remove_is_soft` | dropping a commitment soft-deletes it and the service restores it whole; the pin stays (#378) | 3 | 1.5–1.6 s |
| `focus_skip_series` | today's standup skipped via `skip_focus_occurrence`, never `skip_occurrence` on the "standup notes" recurring task beside it; tomorrow still happens (#378) | 3 | 2.0 s |
| `dependency_add` | "X is blocked by Y" (titles as stored): exactly one edge, X waits on Y, X blocked and Y not, audit (#385). One of five first tried `remove_dependency` (refused) and recovered | 3–7 | 1.6–3.4 s |
| `dependency_remove` | drop one of two blockers: that edge soft-deleted, the other kept, no task trashed, audit (#385) | 4–5 | 2.0–2.4 s |
| `recurrence_make_repeating` | **xfail, known red (1/5, `BELOW_FLOOR`).** "Make the weekly review repeat every week": weekly `repeat_interval` on the one task, due date unchanged, no duplicate. The model guesses `recurrence`/`recur_interval` inside `changes`, is refused with no field named, then creates a duplicate task. Before-number for #411/#413 (#386) | 6–9 | 3.9–6.6 s |
| `recurrence_skip` | skip this week's weekly task: that occurrence trashed (not done), one live occurrence a week on, series continues (#386) | 3–4 | 1.9–2.2 s |
| `recurrence_stop` | stop the series: same task, still open and due today, `repeat_interval` cleared (#386) | 3 | 1.4–1.5 s |
| `restore_task` | "bring back the task about ordering furnace filters" (paraphrased; search can't see the trash): that task restored to its project, the other trashed task left in the trash, exactly one `restored` event and no other write (#387) | 3 | 1.8–1.9 s |
| `restore_project` | a trashed project restored with the tasks trashed with it, by id (not re-created), audit (#387) | 3 | 1.4–1.6 s |
| `project_create` | one project named as asked, no tasks, audit (#388). The first `create_project` sends `title` for `name` in 5/5 seeded samples and self-corrects on the refusal: a #411 signal | 3 | 1.5–1.7 s |
| `project_rename` | `update_project` in place: same id, new name, tasks still in it (#388) | 3 | 1.5–1.6 s |
| `project_close` | "done with it, close it" closes (`closed_at`), never trashes, tasks untouched (#388) | 3 | 1.4–1.5 s |
| `project_reopen` | a closed project (hidden from default `list_projects`) reopened, not re-created (#388) | 3–4 | 1.3–1.8 s |
| `project_trash` | trashed with both its tasks, restorable, the other project untouched (#388) | 3 | 1.3–1.4 s |
| `misfire_batch_create` | "add these five tasks to Garage: …" (id 1 a trashed project): exactly five new top-level tasks, one per item, all in Garage, no project invented, the run writes only about the five (#404). 20/20 at a fixed 20 (seed 403); schema corrections 2/122 calls, 0.1/sample — one sample sent `name` for `title` twice, then recovered | 3–7 | 4.9–14.7 s |
| `misfire_create_in_named_project` | a create naming Reading among five projects (Reading is id 6, id 1 trashed): filed in Reading by lookup, no General default (#404). 20/20; 0/40 schema corrections; always `list_projects → create_task` | 3 | 3.8–7.0 s |
| `misfire_subtask_under_parent` | a subtask under a parent named by title, beside a look-alike in another project: `parent_task_id` right, in the parent's project, no General invented (#404). **15/20 (75%), `UNDECIDED` at the cap** — gated, but closest to red. Schema corrections 11/67 calls, 0.55/sample, 9/20 samples: the first `create_task` sends `parent_id` (the deployed misfire). Most runs never find `parent_task_id` on create and recover by create-then-`update_task` (passes); 4 of the 5 misses dropped `project_id` on the retry, so the subtask landed in an invented General project under a Garage parent. A #411 before-number | 3–7 | 3.5–10.7 s |
| `misfire_complete_parent` | "mark the parent done" with three open subtasks; the service refuses (a parent's done is derived). Passes either honest end state: every subtask completed (the parent reads done) and nothing else written, *or* nothing written and the reply names the open subtasks. Fails a partial completion, any other write, or a run a limit stopped (#404). 19/20; 0/84 schema corrections. Held-and-explained every time but once: that run completed the three subtasks, then retried `complete_task` on the now-done parent, got the same refusal and stopped `no_progress`, with a summary saying the parent was still pending — the deployed loop, and a #413 before-number | 5–8 | 6.1–14.6 s |
| `misfire_edit_among_similar` | "set Review the Q3 report to high priority" among Draft/Send/Read look-alikes: only that task changes (to `high`, not an integer), every event on it (#404). 20/20; 0/40 schema corrections | 3 | 1.5–2.3 s |
| `followup_high_priority[fresh]` | **#389.** Two turns through the route: 'Add a task "Renew the car registration" to my Home project', then "Now make it high priority". Step 1 creates exactly one top-level task in Home; step 2 moves only that task's priority (resolved through history), nothing else changes, every event `agent:loop` | 7 (3 + 4) | 3.7–4.8 s |
| `followup_high_priority[live_like]` | the same two turns after the 16-turn live-like thread (#405). 5/5, but the history costs schema: 4/5 samples sent `name` for `title` on `create_task` (6 corrections/26 calls; 0/25 fresh) and recovered — a #411 signal that history makes worse | 6–8 | 3.5–5.1 s |
| `followup_high_priority[poisoned]` | **xfail, known red (0/5, `BELOW_FLOOR`).** The thread's last reply falsely says the task is "already in Home, high priority". Step 1 answers "I already added it" with zero tool calls, every time; nothing is created. A probe's step 2 then searched, found nothing, and once set the *dentist* task to high instead ("the first thing I saw in Home"). Before-number for #412/#419 | 1 | 0.6–0.8 s |
| `long_complete[fresh]` / `[live_like]` | `find_and_complete`'s ask ("Mark the dentist appointment task as done") in the shared world, empty vs. 16-turn thread (#405): only that task done, events on it. 5/5 each — length alone changes nothing | 3 | 1.1–2.5 s |
| `long_complete[poisoned]` | **xfail, known red (2/5, `BELOW_FLOOR`).** The last reply falsely claimed the task was already marked complete. 3/5 answer from it with zero tool calls ("I already marked … as done"; once "you already finished it"); the 2 passes searched and completed. Before-number for #412/#419 | 1–3 | 0.4–1.6 s |
| `long_reschedule[fresh]` / `[live_like]` | `reschedule`'s ask, paraphrased ("Push the furnace filters task to <ISO>", #380 dates) in the shared world: only the due date of that task moves. 5/5 each | 3 | 1.5–2.9 s |
| `long_reschedule[poisoned]` | **xfail, known red (0/5, `BELOW_FLOOR`).** The last reply falsely said no furnace-filters task exists. Every sample repeats "I couldn't find a task for …" with zero tool calls, sometimes naming the task's exact title in the same breath. Chess's self-poisoning, reproduced in PCC. Before-number for #412/#419 | 1 | 0.7–1.3 s |
| `ambiguous_dentist_then_second` | **xfail, known red (0/5, `BELOW_FLOOR`).** "Mark the dentist one as done" with "Book dentist appointment" and "Pay the dentist bill": step 1 must write nothing and name both; step 2 "The second one" completes the second one its question named (`mention_order`). `search` returns both every time and the model completes "Book dentist appointment" without asking. With nothing asked, a probe's "the second one" completed whatever its next list put second ("Rake the leaves" once). Before-number for #420/#416 | 3 | 1.3 s |
| `reference_rename_then_undo` | three turns: "Find the task about furnace filters" (no write), 'Rename it to "Buy MERV 13 furnace filters"' (that task's title only), "Undo that": the same task back to its original title via `update_task` (there is no undo tool; the old title is in the agent's own earlier replies). Left renamed fails whatever the reply says; no re-create, no trash | 6 (2 + 2 + 2) | 2.9–3.2 s |
| `reference_trash_then_undo` | 'Delete the "Clean the gutters" task', then "Undo that": `list_trash → restore_task` on the same id, the world back to the seed exactly, a `restored` event | 6 | 2.6–2.7 s |
| `reference_first_to_friday` | "What's open in my Work project?" (no write, names ≥2 of 3), then "Push the first one to Friday": the first task the list *reply* named moves to the next Friday (`weekday_targets`, from today's real date; the model gets only the ISO date), nothing else changes | 7 | 4.7–4.8 s |
| `delegate_thread` | two turns with `X-Agent-Actor: agent:conductor`: create "Call the plumber", then "Make it due tomorrow". Both runs resolve to the delegate, every event of both steps is the delegate's, the seed and the conversation's creation stay the user's | 7 | 3.6–3.8 s |

Standing observations: self-correction pays for itself (the recurring gemma
`name`/`title` miss always fixes on feedback); FTS5 retrieval (word-matched
and porter-stemmed since #444; before that `search` was one `LIKE` substring) is sufficient —
this table is the tripwire for ever adding embeddings; `honest_about_missing`
no longer sweeps exhaustively before conceding (pre-llama-swap: up to 10
turns, per-project lists, the trash) — under llama-swap it's `search` then at
most three `list_tasks`/`list_trash` (32 runs, 2026-10-18). The scenario now
asserts ≤7 iterations and ≤6 read calls (#329): 2 of headroom over the
observed maximum, and well short of the old sweep.

### Pinned bytes

What the model reads is pinned in CI as committed fixtures (#402), so a
model-facing change can't slip in unnoticed:

- `backend/tests/fixtures/tool_schemas.json` — the agent's tool list
  (`registry.tool_specs()` exactly as it goes on the wire) and the MCP
  server's `tools/list`, byte for byte, key order and all
  (`tests/test_tool_schema_snapshot.py`).
- `backend/tests/fixtures/canonical_run_requests.jsonl` — the exact request
  bodies of one canonical run (a real provider over a faked llama-server:
  two lookups, a refusal, a follow-up with history that stops at a limit and
  makes the summary call), one body per line, with the date fixed
  (`tests/test_request_bytes.py`). The system prompt, personality, summary
  rules, tool schemas, sampling, caps and thinking kwarg all land here.

**A diff to either fixture is a prompt or tool-schema change, so the eval
gate applies:** run the harness above before merging, and say in the PR that
the bytes moved on purpose. A change that leaves both fixtures untouched is
byte-identical on the wire for gemma-4-12b — that is the proof a refactor
needs instead of an eval run. Regenerate after a deliberate change (then read
the diff):

```bash
cd backend
PCC_UPDATE_FIXTURES=1 .venv/bin/pytest tests/test_request_bytes.py tests/test_tool_schema_snapshot.py
```

## Deferred

Embeddings/`sqlite-vec` (only if retrieval regresses) · SSE streaming (only
if the synchronous wait feels bad).
