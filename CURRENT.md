# Sprint 16 — Blocking-Task Emphasis

## Why this sprint

After the UX foundation slice, the strongest next backlog item was dependency
attention: the dashboard and task cards were emphasizing downstream blocked work,
but the real action is usually the highest unfinished task that is blocking the
chain.

## What shipped

- `TaskRead` now carries two derived, non-persistent fields:
  `is_blocking` and `blocked_task_count`.
- `services/task_dependencies.py` computes top-level blockers from active,
  accepted, unfinished dependency edges. In a chain like `A depends on B depends
  on C`, only `C` is marked as the root blocker and its count includes both
  downstream tasks.
- Existing task serializers now populate `is_blocked`, `is_blocking`,
  `blocked_task_count`, and roll-ups together, so task list/detail/calendar
  consumers stay consistent without a schema migration.
- The dashboard's red dependency card is now `Blocking Work`, links to
  `/tasks?status=blocking`, and lists the top root blockers plus downstream
  counts. Merely blocked downstream work is still visible, but secondary.
- `TaskCard`, `TaskDetailPage`, `TasksPage`, and project status logic now reserve
  red for root blockers; waiting downstream tasks render as neutral `Blocked`.
- Blocking task detail views now include a read-only `Blocking` section listing
  direct dependent tasks that are waiting on the current task.

## Verification

- Added backend tests for simple blocker, chained blocker, branching blocker
  counts, done/rejected/deleted tasks being ignored, and the dependents route.
- Added/updated frontend tests for dashboard blocker emphasis, the `Blocking`
  task filter, task-card badges, project-status precedence, and dependent-task
  display on task detail.
- Per user request, tests were **not run** in this environment.

## Out of scope

- No schema migration, model/provider change, eval change, prompt change, AI
  training-data change, or new dependency.
- Calendar/Gantt planning, project phases, Discord follow-ups, credential
  rotation, and rate limiting remain backlog items.
