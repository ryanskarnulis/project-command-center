---
name: close-open-issues
description: Close all open GitHub issues by confirming each one still reproduces, dispatching one Opus subagent per surviving issue into its own git worktree and branch, then opening, CI-watching, and squash-merging each PR yourself. Use when asked to "close the open issues", "work through the backlog in gh", or fix several issues at once.
---

# Close all open issues

Fan out one subagent per issue; keep every PR in your own hands. The subagents
write code, you own the pull requests. This is the workflow that closed #128–#135.

## Roles — do not blur these

| You (orchestrator) | Subagents |
| --- | --- |
| Read every issue, confirm it reproduces, partition by file | Read `CLAUDE.md`, implement one issue |
| Assign branches and file exclusions | Run `./test.sh` until green |
| Open PRs, write PR bodies | Commit and push **their branch only** |
| `gh pr checks --watch`, squash-merge | **Never** open a PR, merge, or touch `main` |
| Clean up worktrees, sync `main` | Never touch another agent's files |

Say this explicitly in every subagent prompt: *"Do NOT open a pull request — the
orchestrator does that. Do NOT merge or push to main."* Without it they will
open their own PRs and you lose control of the merge order.

## 1. Triage

```bash
gh issue list --state open --limit 50
for i in <numbers>; do echo "===== #$i ====="; gh issue view $i --json title,body -q '.title + "\n" + .body'; done
```

Read every body in full and write down **the primary file each issue touches**.
That map is the whole basis for batching — do not skip it.

## 2. Confirm each issue is real — before it earns a wave slot

An open issue is a *claim*, not a work order. Some are already fixed, some
describe intended behavior, some never reproduced. Dispatching one anyway is
worse than wasting a subagent-hour: the agent will find *something* to change,
report success, and you will merge a fix for a bug that was never there.

Start from a current `main` — an issue "reproduces" against a stale checkout
long after it was fixed:

```bash
git checkout main && git pull --ff-only
gh issue view N --json createdAt,title -q '.createdAt + "  " + .title'
git log --oneline --since='<createdAt>' -- <the file from §1>   # fixed since filed?
```

Then check the claimed behavior is still present, by the cheapest means that
actually settles it:

- **Backend / logic** — reproduce it. A failing test is the strongest form and
  the subagent inherits it; a direct call or `curl` is enough to settle a claim.
- **Rendered page** — the `verifier-browser` skill. jsdom won't tell you whether
  a drag is broken.
- **Docs / config / defaults** — read the file. The claim is either true of the
  current tree or it isn't.

One of four verdicts per issue:

| Verdict | Action |
| --- | --- |
| Reproduces | Goes into a wave (§3). Hand the repro to the subagent (§4.3). |
| Already fixed | Close it, citing the commit or PR that fixed it. |
| Can't reproduce / too vague to test | Do **not** dispatch. Leave it open, surface in §8. |
| Works as designed | Do **not** dispatch. Leave it open, surface in §8. |

**Only "already fixed" gets closed on your own judgement** — that one is
verifiable. The other two are your reading of intent, so leave the issue open
and put it in the report; closing an issue the user still wants is not yours to
decide. The same rule as the security/posture case in §4: when the right
resolution is a judgement call, ask rather than guess.

This step is not overhead you can skip when the list is long — the verification
you do here *is* the "summary of the bug in your own words" that §4.3 requires
you to hand the subagent.

## 3. Partition into waves

Two issues that edit the same file go in **different waves**, never in parallel.
Issues on disjoint files run concurrently. Waves of ~4 are comfortable; each
worktree runs its own `npm ci` and venv bootstrap, so more than that is slow, not
faster.

Frontend issues cluster dangerously — several will land in
`frontend/src/features/tasks/`. Different *files* in that folder are fine in
parallel, but name the other agents' files in each prompt as **off-limits**:

> Other agents are concurrently editing `X.ts` and `Y.tsx` — do NOT modify those two files.

Also watch for two issues touching `README.md`, `.env.example`, or
`.github/workflows/` — those collide silently and are easy to miss.

## 4. Dispatch a subagent per issue

`Agent` tool, `subagent_type: "general-purpose"`, `model: "opus"`,
**`isolation: "worktree"`** (this is what makes parallelism safe), background.

Each prompt must contain:

1. "You are fixing GitHub issue #N. You are in an isolated git worktree — work
   ONLY here, never touch other worktrees or the main checkout."
2. "Read `CLAUDE.md` (project constitution) and follow it. Then `gh issue view N`."
3. A **summary of the bug in your own words** — cause, the file, and the expected
   behavior. Don't make them rediscover what you already read.
4. The off-limits file list (§3).
5. `git fetch origin && git reset --hard origin/main` **before** branching, for
   waves after the first — otherwise they branch from a stale `main`.
6. Branch name you chose (`fix/<slug>`), conventional commit message including
   `(#N)`, and `git push -u origin <branch>`.
7. "Run `./test.sh` and make it pass. Note: `TaskDetailPage`/`ProjectDetailPage`
   frontend test files are known to flake on a clean tree — re-run to confirm and
   mention it; do not treat as your regression."
8. "Check `git status` before committing so no stray artifacts get staged" — name
   the ones you can see, e.g. an untracked `cleanup_test_rows.sql`, or
   `.tmp-verify/` screenshots a `verifier-browser` run may drop.
9. "Report back: branch, what changed, test results, and notes for the PR
   description" — plus any judgement call you want surfaced.

For a rendered-page change, ask them to consider `verifier-browser`, and to say
so explicitly if they skip it.

**Never resolve a security/posture issue by silently flipping the project's
default.** LAN exposure is intentional here (`CLAUDE.md`, and
`~/deploy/.../.env` carries `FRONTEND_BIND=0.0.0.0`). Tell the agent the intended
resolution rather than letting it guess.

## 5. Open the PR yourself

When an agent reports back, write the PR body **from its report** — cause, the
change, tradeoffs, the judgement calls it flagged, verification. A one-line body
wastes the detail the agent just produced.

```bash
gh pr create --base main --head <branch> --title "fix(scope): summary (#N)" --body "$(cat <<'EOF'
Closes #N.
...
EOF
)"
```

Always include `Closes #N` — that is what auto-closes the issue on merge.

## 6. Watch CI, then merge

Branch protection and auto-merge are unavailable (private repo, Free plan), so
watch manually. Never merge on pending or failing checks.

```bash
gh pr checks <n> --watch --interval 15 >/dev/null 2>&1; gh pr checks <n> | tail -6
gh pr merge <n> --squash
```

Two gotchas:

- Right after `gh pr create`, checks may not be registered yet — `gh pr checks`
  says *"no checks reported"* and `--watch` returns instantly. Re-run it; do not
  read that as green.
- Do **not** pass `--delete-branch` while the agent's worktree still holds the
  branch — the merge succeeds but the command exits non-zero on the local delete.
  Clean up in §8 instead.

Expected checks: `Test (Python 3.11)`, `Test (Python 3.14)`, `frontend`, `lint`,
`sync-check`.

## 7. Act on deployment-affecting findings before merging

If an agent flags a change that could break the live deploy at
`~/deploy/project-command-center`, **check the real thing first** and put the
result in the PR body:

```bash
git -C ~/deploy/project-command-center status --porcelain   # stricter deploy gates
grep -E 'FRONTEND_BIND|FRONTEND_PORT' ~/deploy/project-command-center/.env
```

## 8. Clean up and report

```bash
for w in .claude/worktrees/agent-*; do git worktree remove --force "$w"; done
git worktree prune && git fetch --prune origin
git checkout main && git pull --ff-only
gh pr list --state open --limit 20      # empty
gh issue list --state open --limit 20   # only the §2 holdbacks, and you can name each
```

No open PRs is the pass condition. Open *issues* are fine now — but every one
still on that list must be an issue §2 held back deliberately, and you should be
able to say which verdict put it there. An issue you can't account for is one
you dropped.

Worktrees from earlier sessions accumulate here; removing them all is fine once
their branches are merged.

Then report to the user: a table of issue → PR → fix, and — separately — the
issues §2 did **not** dispatch and why (with what you tried, so the user can
overrule you), the judgement calls, documented residual limitations, and
anything skipped (e.g. a `verifier-browser` run not done). Surface those; don't
bury them in the table.
