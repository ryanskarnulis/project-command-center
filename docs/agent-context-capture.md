# Agent context capture

The run trace ([`agent-tracing.md`](agent-tracing.md), `PCC_TRACE_PATH`)
says what a run decided and what each model call cost. It does not say what
the model was *shown*, and that is the open question when a run goes wrong:
was the context missing something, or holding too much? Context capture
answers it by keeping every model call exactly as it went over the wire
(#401).

It is a debugging tool. Turn it on for a session you are looking into and
turn it off after.

## Turning it on

Set `PCC_CONTEXT_PATH` to a file path. Unset (the default), nothing is
captured and the provider sends one request per call, as before. Set
`PCC_TRACE_PATH` beside it so each run's outcome can be read next to its
calls.

| Where | Value |
| --- | --- |
| dev (`backend/.env`) | `PCC_CONTEXT_PATH=../data/traces/context.jsonl` (relative to `backend/`) |
| an eval run | `PCC_CONTEXT_PATH=/tmp/context.jsonl PCC_TRACE_PATH=/tmp/evals.jsonl PCC_AGENT_EVALS=1 .venv/bin/pytest tests/test_agent_evals.py -v -s` |
| the deployed container | an override file, see below |

`data/traces/` is git-ignored. The file only grows; delete it when you are
done. It is local disk only — nothing leaves the machine.

## Watching it

```bash
python backend/scripts/watch_context.py data/traces/context.jsonl --trace data/traces/agent.jsonl
```

The watcher prints each call as it lands, grouped under its run: the prompt
the chat template rendered, then the raw response. When the run record is
written, the run's outcome follows: the ask, every tool call and its answer,
the stop reason and the reply. Options go after the paths:

- `--phase turn` (or `summary`) follows one phase.
- `--json` prints the request body instead of the rendered prompt.
- `--from-start` replays the files from the top instead of waiting at the end.

The captured text is printed exactly as captured. Headers and separators are
the only additions, and the outcome block is the only formatted part.
Standard library only; it needs no venv and does not import the app.

## The deployed app

CD deploys from a clean clone at `~/deploy/project-command-center` and
refuses to deploy over local edits there ("deploy clone is dirty",
`.github/workflows/deploy.yml`). So don't edit that clone's
`docker-compose.yml`. Don't put it in the clone's `.env` either: `.env` is
ignored by git, so it would not block CD, but every later deploy would keep
capturing until someone remembered to take it out. Put it in an override file
outside the clone and recreate the backend with both files:

```bash
cat > ~/pcc-context-capture.override.yml <<'EOF'
services:
  backend:
    environment:
      PCC_CONTEXT_PATH: /data/traces/context.jsonl
      PCC_TRACE_PATH: /data/traces/agent.jsonl
EOF
cd ~/deploy/project-command-center
docker compose -f docker-compose.yml -f ~/pcc-context-capture.override.yml up -d --no-build backend
```

`/data` is the clone's bind-mounted `./data`, owned by the host user, so
follow it from the host (from a dev checkout):

```bash
python backend/scripts/watch_context.py ~/deploy/project-command-center/data/traces/context.jsonl \
  --trace ~/deploy/project-command-center/data/traces/agent.jsonl
```

To turn it off, recreate the backend without the override. The next CD deploy
does the same, since it runs `docker compose up` without it.

```bash
cd ~/deploy/project-command-center && docker compose up -d --no-build backend
```

The capture file stays until you delete it:
`rm ~/deploy/project-command-center/data/traces/context.jsonl`.

## What a record holds

One JSONL line per model call (`kind: "model_call"`, `schema: 1`), written
when the call ends:

| Field | Meaning |
| --- | --- |
| `run_id` | The run, the same id as the trace record's `run_id`. `null` for a call made outside a run (a probe, a script). |
| `phase` | `turn` or `summary`, as in the trace's `calls[].phase`; `unknown` outside the loop. |
| `seq` | The call's place among the calls its run sent, from 0, counted when it was sent. `(run_id, seq)` is the trace record's `calls[seq]`. |
| `started_at`, `ended_at`, `ms` | Wall clock (UTC ISO; ms includes the template render). |
| `url` | Where the request went. |
| `request` | The request body, byte for byte: messages, tools, and every sampling and generation parameter. |
| `status_code`, `response` | The response body, byte for byte, including `reasoning_content` and raw tool-call argument strings. `null` when no response arrived. |
| `error` | The typed error the call raised (`ProviderRequestError`, `ProviderResponseError`, `ToolCallArgumentsError`), `""` when it did not raise. |
| `template` | `{"prompt": ...}`: the string the server's chat template rendered from the same request — the text the model tokenized. `{"error": ...}` when rendering failed. `null` when the server never answered. |

Nothing is reformatted, trimmed or redacted.

## How it works

- **At the provider seam.** Every model call goes through
  `LlamaCppProvider._post` (`app/ai/providers/llamacpp.py`), so no call can be
  missed. The request is built once with `build_request` and sent — exactly
  what `post(json=...)` did, so the bytes on the wire are unchanged — and its
  `content` is what is recorded. The response is parsed inside the capture,
  so a bad tool-call argument is recorded as the call's `error` beside the
  bytes that caused it.
- **Run id and phase by `ContextVar`.** The provider does not know which run
  or phase it is serving. The run id is the one `AgentLoop.run` binds in
  structlog's context (#400); the phase comes from
  `context_capture.model_phase`, which the loop wraps around its one call
  site.
- **The rendered prompt comes from the server.** After the call returns, the
  same request bytes are POSTed to `{root}/upstream/{model}/apply-template`.
  llama-swap proxies that to the model's own llama-server, which renders the
  messages and tools through its `--jinja` template and returns the string. A
  copy of the template in this repo would be a second version that drifts.
  llama-swap does not route `/v1/apply-template` (404); only the
  `/upstream/<model>/` path works.
- **It never costs a run.** A capture that fails to write, or a template
  request that fails, is logged (`context_capture_failed`) or recorded, and
  dropped. The call's own result, or its typed error, is always what the run
  gets.
- **Thought blocks are only recorded.** History is still built from
  `ChatResult`, which never carries them.

## Size

Each call is kept twice — the request JSON and its rendered prompt — and both
carry the system prompt, every tool schema, and the whole history so far. A
run's first call is about 21 KB of request JSON before any history, and its
rendered prompt is about as large again, so a run of a few calls writes well
over 100 KB. That is why capture is off by default.
