# Measuring a change to what the model sees

Phase 4 of the agent plan (#396) changes the prompt, the tool text, the
sampling and the model one lever at a time. Each lever needs a fair A/B. Two
tools do it, cheap one first:

| tool | question | cost |
| --- | --- | --- |
| `backend/scripts/probe_first_call.py` | does the model **open** differently? | minutes |
| `backend/scripts/eval_campaign.sh` + `campaign_report.py` | do the **scenarios** still hold? | an hour or more |

Screen with the probe; confirm with the campaign; the eval gate in
`docs/agent-design.md` stays the merge bar.

## The rule

**Never compare arms run in separate batches.** On one llama-server,
consecutive samples of one prompt are correlated: chess measured one unchanged
prompt at 5/20, 19/40 and 31/40 in three separate batches, swings a binomial
cannot produce. Whatever the cause (KV-cache reuse, load, thermal state, a
different server uptime), an A run this morning and a B run this afternoon
differ by the session as much as by the change. So:

- arms alternate **per sample** (the probe) or **per block** (the campaign),
  with the order flipped as it goes, so drift lands on both arms alike;
- arms share sampling seeds, so sample *j* of each arm draws the same stream and
  the comparison is a paired one (McNemar on the pairs the change flipped);
- an old baseline number in `docs/agent-design.md` is a reference, never the
  control of a new experiment.

## The probe

It makes the shipped first loop call directly, N times per arm. Nothing is
copied: a recording provider takes the real `AgentLoop`'s first `chat` call
(system prompt, tool schemas, history window, token cap) and the request goes
out through the real `LlamaCppProvider` at the model's profile (#402), so the
probe cannot drift from production. `tests/test_probe_first_call.py` pins that
the control arm's request is the loop's own.

Each sample is classified from the wire result alone: `tool:<name>` per call
(joined with ` + `), or `no_tool`. A tool call with unparseable arguments is
`bad_args:<name>` (the model chose a tool). A dead server is `error:<type>`
and is counted separately, never scored. No language is parsed.

```bash
cd backend
# Two arms, 20 samples each, on the built-in smoke items.
.venv/bin/python scripts/probe_first_call.py --arm control --arm cold:temperature=0.2 --n 20

# A prompt and a tool-text change against the shipped text, on your own corpus.
.venv/bin/python scripts/probe_first_call.py --arm control \
    --arm new:prompt=/tmp/prompt.txt,tools=/tmp/tools.json \
    --items /tmp/corpus.json --n 40 --out /tmp/probe.jsonl

# The exact request an arm would send; touches no server.
.venv/bin/python scripts/probe_first_call.py --arm control --dry-run
```

Arm settings (`--arm name:key=value,key=value`; the first arm is the control):

- `prompt=FILE` replaces the static system prompt; the date line is appended
  (or put `{today}` in the file to place it yourself).
- `tools=FILE` is a JSON object `{"create_task": "new description",
  "create_task.title": "new parameter text"}`. A key naming no tool or
  parameter is an error, so a mislabelled control cannot happen silently.
- `temperature=T`, `model=ID` (that model's profile), `profile=NAME`.

An items file is a JSON list of `{"id", "ask", "history"?, "expect"?}`.
`expect` lists fnmatch patterns over the class (`"no_tool"`, `"tool:*"`,
`"tool:create_task*"`); with it the table shows passes and, per arm after the
first, the paired verdict (`pairs · A-only · B-only · p=`). Without it you get
class counts. The probe sees no database, so asks that need an id to exist are
screened on what the model does first (look up, or guess), which is the point
for the lookup-before-write work.

The default seed is paired across arms (`--seed none` for unpaired). A small
p says the arms differ on these samples; read the counts beside it for how
much, and treat a screen as a screen: confirm anything that matters in the
campaign.

## The campaign

```bash
# main in one worktree, the change in another, one server, four blocks of five.
backend/scripts/eval_campaign.sh --a ~/code/pcc-main --b ~/code/pcc-change \
    --k 'create_task or delete_is_soft' --blocks 4

# A knob the app reads from the environment: same tree, two settings.
backend/scripts/eval_campaign.sh --a . --b . --env-b LLAMACPP_MODEL=other --k '...' --blocks 4
```

Each block runs `tests/test_agent_evals.py -k <expr>` once per arm, from that
arm's own `backend/`, with `PCC_AGENT_EVALS_RUNS=5 PCC_AGENT_EVALS_MAX_RUNS=5`
(a fixed block: escalation would hand the arms different sample counts) and its
own `PCC_AGENT_EVALS_REPORT`. The arm order flips every block. Block *n* of
both arms uses `PCC_AGENT_EVALS_SEED = seed + 1000·n` (`--seed`, default 410;
`--no-seed` for none). The log records, per arm-block, the tree's HEAD, the sha
of its harness file, where `app` was imported from (the script aborts if that is
outside the tree: a gate that cannot prove which tree it measured measured
nothing), and llama-swap's `/running`. If the two trees' `test_agent_evals.py`
differ it says so: scenarios whose checks differ are not comparable.
`--fresh-per-block` unloads the model before each arm-block; use it only when
the server is yours.

Output goes to `--out` (default `$TMPDIR/pcc-campaign-<utc>/`, never the repo):
`block-<n>-<arm>.jsonl` (the harness report), `.out` (full pytest output) and
`campaign.log`. The script ends by printing the joined table; rerun it any time:

```bash
backend/.venv/bin/python backend/scripts/campaign_report.py \
    a=out/block-1-a.jsonl b=out/block-1-b.jsonl a=out/block-2-a.jsonl b=out/block-2-b.jsonl
```

```
scenario | a | b | a vs b
--- | --- | --- | ---
`create_task_simple` | 17/20 85% (5, 3, 4, 5) | 12/20 60% (4, 2, 3, 3) | pairs 20 · A-only 6 · B-only 1 · p=0.125
```

A cell is passes over scored runs, the rate, and each block's passes. The
paired column is the exact McNemar p (`evalstats.mcnemar_exact`) over the
samples both arms scored with the same seed in the same block. Infra retries
and harness errors were never samples and pair with nothing. Pairing is
approximate (llama.cpp is not bit-reproducible across batch sizes), never worse
than unpaired, and does not replace the alternating order, which is what
cancels drift.

### The frontier tier

The same discipline over `tests/test_agent_frontier.py` (#408, #460):

```bash
# dev split (the default here: iterate on dev, confirm on heldout), 4 blocks.
backend/scripts/eval_campaign.sh --frontier --a ~/code/pcc-main --b ~/code/pcc-change --blocks 4

# the number of record, a scenario subset, 10 samples a block.
backend/scripts/eval_campaign.sh --frontier --split heldout --runs 10 \
    --k 'some_scenario' --a . --b . --env-b LLAMACPP_MODEL=other
```

`--frontier` swaps the pytest target and the knobs (`PCC_AGENT_FRONTIER=1`,
`PCC_FRONTIER_SPLIT=<--split>`, `PCC_FRONTIER_RUNS=<--runs>`; without `--runs`
a block is one sample per wording). `--k` is optional. Blocks alternate,
flip order and share `PCC_AGENT_EVALS_SEED = seed + 1000·n`, so sample *j* of a
scenario draws the same wording and sampling stream in both arms. The harness
sha in the log covers `test_agent_frontier.py`, `frontier.py` and
`frontier_corpus.py`.

Each block's `block-<n>-<arm>.jsonl` is the frontier test's own report: a
`frontier_header` (build, model, split, seed) and one `frontier` record per
scenario (`FrontierResult.record`: `fingerprint`, `runs`, `passed`, `rubric`,
and `samples`, each with `variant`, `seed`, `whole` and `score`). No stdout is
scraped. `campaign_report.py` joins them into a second table:

```
frontier

scenario | a | b | a vs b
--- | --- | --- | ---
`some_scenario` | 9/20 45% rubric 0.71 (2, 3, 2, 2) | 5/20 25% rubric 0.58 (1, 2, 1, 1) | pairs 20 · A-only 5 · B-only 1 · p=0.219
```

A cell is whole-task passes over samples, the **mean rubric** (the fraction of
checkpoints met, which moves while whole passes are still near zero) and each
block's whole passes. The paired column is the exact McNemar p over the
whole-task verdicts of samples sharing (block, seed).

**The report refuses to join blocks whose fingerprints differ.** Every
scenario's `fingerprint` must be one value across all arms and blocks, and so
must the header's `split`; otherwise it exits naming the scenario and the
values. Two trees whose corpus changed (or one run on dev, one on heldout) did
not measure the same thing. A frontier result is a measurement, never a
verdict: read the pass counts and rubric beside the p.

## Reading a result

- 4 blocks of 5 is 20 samples per arm per scenario: enough to see a collapse
  (17/20 against 6/20), not a two-point shift. Ask for more blocks when the
  p is borderline, not a different statistic.
- A scenario that is not a pass in either arm tells you nothing about the
  change; look at its failure modes in the `.out` file.
- Record the table, the trees' SHAs and the seed in the PR that ships the
  change. A prompt, tool-schema or model change still has to clear the gate:
  no scenario statistically below its floor.
