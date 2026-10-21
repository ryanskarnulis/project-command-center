# Agent run tracing

One JSONL record per agent run (#400), so a run that misbehaved can be read
back — what it was asked, what it was shown, every tool and model call, what
each call cost, why it stopped — and rebuilt as an eval scenario.

## Turning it on

Set `PCC_TRACE_PATH` to a file path. Unset (the default), nothing is written.

| Where | Value |
| --- | --- |
| dev (`backend/.env`) | `PCC_TRACE_PATH=../data/traces/agent.jsonl` (relative to `backend/`) |
| docker (root `.env`) | `PCC_TRACE_PATH=/data/traces/agent.jsonl` (the mounted `./data`) |
| an eval run | `PCC_TRACE_PATH=/tmp/evals.jsonl PCC_AGENT_EVALS=1 .venv/bin/pytest tests/test_agent_evals.py -v -s` |

`data/traces/` is git-ignored. The file only grows; rotate or delete it by
hand. It is local disk only — nothing leaves the machine.

A trace write that fails (bad path, full disk) is logged as
`agent_trace_write_failed` and never changes the run's outcome.

## Reading it

```bash
python backend/scripts/read_trace.py data/traces/agent.jsonl              # one line per run + totals
python backend/scripts/read_trace.py data/traces/agent.jsonl --last 5
python backend/scripts/read_trace.py data/traces/agent.jsonl --stop no_progress
python backend/scripts/read_trace.py data/traces/agent.jsonl --run 69deb231   # one run in full
python backend/scripts/read_trace.py data/traces/agent.jsonl --run 69deb231 --json
```

Standard library only; it needs no venv and does not import the app.

Whether each reply's claims are backed by its run's tool calls (#407):
`cd backend && .venv/bin/python -m tests.claims ../data/traces/agent.jsonl`
(`docs/agent-design.md`, "Claim accuracy").

## Where it is written

`AgentLoop.run` (`app/ai/loop.py`) writes the record on every exit: a
completed run, every limit stop, a Stop, a provider failure, the deadline, and
an internal crash (the backstop path too). The record is built from a
`RunTrace` (`app/ai/trace.py`) the loop fills in as it goes, so a failed run
still records everything it learned before it died.

Each run has a `run_id` (uuid4 hex). It is bound on the run's structlog lines,
returned on `AgentRunResult.run_id` (and on `AgentRunFailed.result`), and a
`run_id` the caller already bound in structlog's context is reused — that is
the hook for capturing more about the same run elsewhere. Context capture
(`PCC_CONTEXT_PATH`, #401) uses it to key every model call's exact request,
response and rendered prompt to the run: see
[`agent-context-capture.md`](agent-context-capture.md). The agent
route binds `conversation_id` the same way, and the record picks it up.

## The record (schema 1)

| Field | Meaning |
| --- | --- |
| `schema`, `kind` | Layout version (`1`) and `"run"`. A reader keys off `schema`; bump it on any change a reader must branch on. |
| `ts`, `started` | When the record was written / the run began (UTC ISO). |
| `run_id`, `conversation_id`, `actor` | Which run; the thread (`null` outside the route); who its writes are stamped as (`agent:loop`, `agent:conductor`). |
| `utterance` | The user message. |
| `history` | `given` / `kept` / `dropped` message counts after the context budget, and `messages`: the kept history exactly as the model saw it. |
| `stop_reason`, `limit` | The loop's stop reason, and which configured limit ended it: `max_iterations`, `max_corrections`, `max_tokens` (a `truncated` stop), `max_tool_calls`, `no_progress`, `deadline` (`timed_out`); `null` for `completed`, `cancelled` and failures. |
| `error` | Empty, or the failure message of a `provider_error` / `timed_out` / `internal_error` run. |
| `reply`, `summarised` | The saved reply (`null` when none), and whether it is the limit summary (#399) rather than the model's own closing turn. |
| `iterations`, `corrections` | Provider turns run; self-correction turns spent. |
| `tools` | Every tool call in order: `name`, `args`, and `result` or `error` (full text, as the model saw it). |
| `calls` | One entry per model call, see below. |
| `model_calls`, `unmetered_calls`, `prompt_tokens`, `completion_tokens`, `cached_tokens`, `model_ms` | Totals derived from `calls`; `unmetered_calls` counts calls with no token usage, which makes the token totals a lower bound. |
| `total_ms` | The run's wall clock. |
| `limits` | The configured `max_iterations`, `max_corrections`, `max_tokens`, `summary_max_tokens`, `max_tool_calls`, and `deadline_s` — the budget left as the first model call started (`null` with no deadline). |
| `serving` | `model` (the alias sent), `profile` (`null` until per-model profiles, #402), `system_prompt_sha256` and `summary_prompt_sha256` (the static prompt layers — the date line is excluded, so the hash moves with wording, not the calendar), `tool_schemas_sha256` (canonical JSON of every offered tool's wire schema), `tools_offered`. |

Each `calls` entry:

| Field | Meaning |
| --- | --- |
| `seq`, `phase` | Position in the run; `turn` (a loop turn) or `summary` (the limit summary call). |
| `status` | `ok`, `truncated` (cut off by `max_tokens`), `bad_args` (tool-call arguments not a JSON object), `failed` (no usable answer). |
| `ms`, `server_ms` | The loop's wall clock around the call; llama-server's own prompt + generation time (`timings`). `ms - server_ms` is queueing, transport and any cold load. |
| `prompt_tokens`, `completion_tokens`, `cached_tokens` | From the response's `usage` (`cached_tokens` is `usage.prompt_tokens_details.cached_tokens`); `null` when unknown, never a zero that reads as measured. |
| `finish_reason`, `tool_calls` | As the server sent it; how many tool calls the turn asked for. |
| `served_model`, `fingerprint` | The weights the server says it ran and its build (`system_fingerprint`). |
| `failure` | Empty, or the exception of a `bad_args` / `failed` call. |

## Rebuilding an eval scenario from a record

`utterance` + `history.messages` is the input; `tools` is the trajectory to
assert on (or against); `serving` names the configuration that produced it.
The database state the tools saw is not in the record — the tool results are,
which is usually enough to seed a scenario by hand.
