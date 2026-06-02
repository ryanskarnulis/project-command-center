# Sprint 7 — Daily-Use Slice

> Goal: make the task/project views editable and due-date-aware so the app becomes a real
> daily driver. **Frontend-only** — the backend already supports everything here.

Status legend: `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked

## Scope (the three open "highest-priority" daily-use items from TASKS.md)

- [ ] Overdue / due-soon highlighting in the global task view
- [ ] Inline task editing (status / priority / due-date / description) — via a **modal**
- [ ] Edit project info (name / description) from the UI — via a **modal**

**Out of scope (later Sprint 7 slices — do NOT build here):** dismiss/clear inbox, trash/restore,
alias UI, task nesting/estimates/dependencies, nav/toasts/redesign.

## Key context (read before starting)

The backend is done. `PATCH /api/tasks/{id}` (`TaskUpdate`: title, description, status, priority,
due_date) and `PATCH /api/projects/{id}` (`ProjectUpdate`: name, description) exist and record
`updated` activity events. The frontend wrappers `updateTask()` (`frontend/src/api/tasks.ts`) and
`updateProject()` (`frontend/src/api/projects.ts`) already exist but are **never called** — this
slice wires them up. **No backend, no schema, no migration.** No `any` without a `// TODO`. Styles
are plain CSS in `frontend/src/index.css` (no CSS modules). Edit UX = modal dialog.

## Files to create

- `frontend/src/utils/dates.ts` — due-date helpers (pure functions)
- `frontend/src/utils/dates.test.ts` — unit tests for the helpers
- `frontend/src/components/Modal.tsx` — reusable modal (overlay + Escape + close button)
- `frontend/src/features/tasks/TaskEditModal.tsx` — task edit form in a modal
- `frontend/src/features/projects/ProjectEditModal.tsx` — project edit form in a modal
- `frontend/src/features/tasks/TasksPage.test.tsx` — smoke test for the edit flow

## Files to modify

- `frontend/src/features/tasks/useTasks.ts` — add `update()`
- `frontend/src/features/projects/useProjects.ts` — add `update()`
- `frontend/src/features/tasks/TasksPage.tsx` — Edit button, due-date rendering, mount modal
- `frontend/src/features/projects/ProjectsPage.tsx` — Edit button, mount modal
- `frontend/src/index.css` — modal + due-date CSS

---

## Step 1 — Date helpers: `frontend/src/utils/dates.ts`

`Task.due_date` is a `"YYYY-MM-DD"` string or null. Parse as a **local** date (split, don't
`new Date(string)` — that parses as UTC and shifts the day).

```ts
export type DueStatus = 'overdue' | 'due-soon' | 'none'

function parseLocalDate(s: string): Date {
  const [y, m, d] = s.split('-').map(Number)
  return new Date(y, m - 1, d)
}

/** 'overdue' if before today, 'due-soon' if within `soonDays` (default 3), else 'none'. */
export function dueStatus(due: string | null, soonDays = 3): DueStatus {
  if (!due) return 'none'
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const diffDays = Math.round((parseLocalDate(due).getTime() - today.getTime()) / 86_400_000)
  if (diffDays < 0) return 'overdue'
  if (diffDays <= soonDays) return 'due-soon'
  return 'none'
}

export function formatDueDate(due: string | null): string {
  if (!due) return ''
  return parseLocalDate(due).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}
```

## Step 2 — `frontend/src/components/Modal.tsx`

```tsx
import { type ReactNode, useEffect } from 'react'

interface ModalProps {
  open: boolean
  title: string
  onClose: () => void
  children: ReactNode
}

export function Modal({ open, title, onClose, children }: ModalProps) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-header">
          <h2>{title}</h2>
          <button className="modal-close" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  )
}
```

## Step 3 — Hooks: add `update()`

**`useTasks.ts`** — import `updateTask` from `../../api/tasks` and `TaskUpdate` from
`../../types/task`. Add `update: (id: number, data: TaskUpdate) => Promise<void>` to the `UseTasks`
interface and the return value:

```ts
const update = useCallback(
  async (id: number, data: TaskUpdate) => {
    await updateTask(id, data)
    reload()
  },
  [reload],
)
```

**`useProjects.ts`** — same pattern: import `updateProject` + `ProjectUpdate`, add
`update: (id: number, data: ProjectUpdate) => Promise<void>` to the interface and:

```ts
const update = useCallback(
  async (id: number, data: ProjectUpdate) => {
    await updateProject(id, data)
    reload()
  },
  [reload],
)
```

## Step 4 — `frontend/src/features/tasks/TaskEditModal.tsx`

Form initialized from the task; on submit builds a `TaskUpdate` (send all fields — backend uses
`exclude_unset`, but sending the full set is simpler). Labels associated with controls
(`htmlFor`/`id`) so tests/AT can target them.

```tsx
import { type SubmitEvent, useState } from 'react'
import { Modal } from '../../components/Modal'
import type { Task, TaskPriority, TaskStatus, TaskUpdate } from '../../types/task'

const PRIORITIES: TaskPriority[] = ['low', 'medium', 'high', 'urgent']
const STATUSES: TaskStatus[] = ['candidate', 'accepted', 'rejected', 'done']

interface Props {
  task: Task
  onClose: () => void
  onSave: (id: number, data: TaskUpdate) => Promise<void>
}

export function TaskEditModal({ task, onClose, onSave }: Props) {
  const [title, setTitle] = useState(task.title)
  const [description, setDescription] = useState(task.description ?? '')
  const [status, setStatus] = useState<TaskStatus>(task.status)
  const [priority, setPriority] = useState<TaskPriority>(task.priority)
  const [dueDate, setDueDate] = useState(task.due_date ?? '')
  const [saving, setSaving] = useState(false)

  async function handleSubmit(e: SubmitEvent<HTMLFormElement>) {
    e.preventDefault()
    if (!title.trim()) return
    setSaving(true)
    try {
      await onSave(task.id, {
        title: title.trim(),
        description: description.trim() || null,
        status,
        priority,
        due_date: dueDate || null,
      })
      onClose()
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal open title="Edit task" onClose={onClose}>
      <form onSubmit={handleSubmit}>
        <label htmlFor="te-title">Title</label>
        <input id="te-title" value={title} onChange={(e) => setTitle(e.target.value)} />

        <label htmlFor="te-desc">Description</label>
        <textarea id="te-desc" value={description} onChange={(e) => setDescription(e.target.value)} />

        <label htmlFor="te-status">Status</label>
        <select id="te-status" value={status} onChange={(e) => setStatus(e.target.value as TaskStatus)}>
          {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>

        <label htmlFor="te-priority">Priority</label>
        <select id="te-priority" value={priority} onChange={(e) => setPriority(e.target.value as TaskPriority)}>
          {PRIORITIES.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>

        <label htmlFor="te-due">Due date</label>
        <input id="te-due" type="date" value={dueDate} onChange={(e) => setDueDate(e.target.value)} />

        <button type="submit" disabled={saving || !title.trim()}>Save</button>
        <button type="button" onClick={onClose}>Cancel</button>
      </form>
    </Modal>
  )
}
```

## Step 5 — `frontend/src/features/projects/ProjectEditModal.tsx`

Same structure, two fields (`name`, `description`). Protected projects are editable (backend allows
it — only delete is blocked), so no protection guard here.

```tsx
import { type SubmitEvent, useState } from 'react'
import { Modal } from '../../components/Modal'
import type { Project, ProjectUpdate } from '../../types/project'

interface Props {
  project: Project
  onClose: () => void
  onSave: (id: number, data: ProjectUpdate) => Promise<void>
}

export function ProjectEditModal({ project, onClose, onSave }: Props) {
  const [name, setName] = useState(project.name)
  const [description, setDescription] = useState(project.description ?? '')
  const [saving, setSaving] = useState(false)

  async function handleSubmit(e: SubmitEvent<HTMLFormElement>) {
    e.preventDefault()
    if (!name.trim()) return
    setSaving(true)
    try {
      await onSave(project.id, {
        name: name.trim(),
        description: description.trim() || null,
      })
      onClose()
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal open title="Edit project" onClose={onClose}>
      <form onSubmit={handleSubmit}>
        <label htmlFor="pe-name">Name</label>
        <input id="pe-name" value={name} onChange={(e) => setName(e.target.value)} />
        <label htmlFor="pe-desc">Description</label>
        <input id="pe-desc" value={description} onChange={(e) => setDescription(e.target.value)} />
        <button type="submit" disabled={saving || !name.trim()}>Save</button>
        <button type="button" onClick={onClose}>Cancel</button>
      </form>
    </Modal>
  )
}
```

## Step 6 — Wire into `TasksPage.tsx`

- Add imports: `TaskEditModal`, `{ dueStatus, formatDueDate }` from `../../utils/dates`, and add
  `Task` + `TaskUpdate` to the existing `../../types/task` import.
- Destructure `update` from `useTasks`.
- Add state: `const [editing, setEditing] = useState<Task | null>(null)`.
- In each `<li>`, after the priority span, render the due date when present and not done:
  ```tsx
  {t.due_date && t.status !== 'done' && (
    <span className={`due due-${dueStatus(t.due_date)}`}>Due {formatDueDate(t.due_date)}</span>
  )}{' '}
  ```
- Add an **Edit** button in the row: `<button onClick={() => setEditing(t)}>Edit</button>`.
- Before `</main>`, mount the modal (`bumpActivity` already exists and refreshes the ActivityFeed):
  ```tsx
  {editing && (
    <TaskEditModal
      task={editing}
      onClose={() => setEditing(null)}
      onSave={async (id, data) => {
        await update(id, data)
        bumpActivity()
      }}
    />
  )}
  ```

## Step 7 — Wire into `ProjectsPage.tsx`

- Import `ProjectEditModal` and add `Project` to the type imports.
- Destructure `update` from `useProjects`.
- Add `const [editing, setEditing] = useState<Project | null>(null)`.
- In each project `<li>`, add `<button onClick={() => setEditing(p)}>Edit</button>` (shown for all
  projects, including protected — only Delete stays gated on `!is_protected`).
- Mount modal before `</main>`:
  ```tsx
  {editing && (
    <ProjectEditModal
      project={editing}
      onClose={() => setEditing(null)}
      onSave={update}
    />
  )}
  ```

## Step 8 — CSS in `frontend/src/index.css`

Append, reusing existing vars (`--accent`, `--border`, `--bg`, `--shadow`, `--text`):

- `.modal-overlay` — `position: fixed; inset: 0; background: rgba(0,0,0,.4)`, flex centered.
- `.modal` — `background: var(--bg)`, border, radius, padding, `max-width: 420px`, `box-shadow: var(--shadow)`.
- `.modal-header` — flex space-between, align center.
- `.modal-close` — borderless button, larger font.
- `.modal-body form` — `display:flex; flex-direction:column; gap:8px`.
- `.due` — small mono badge. `.due-overdue` — red (e.g. `#c0392b`). `.due-soon` — amber (e.g. `#b8860b`).
  Respect the existing `@media (prefers-color-scheme: dark)` block.

## Step 9 — Tests

**`frontend/src/utils/dates.test.ts`** — fix "today" with fake timers so `dueStatus` is
deterministic:
```ts
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { dueStatus, formatDueDate } from './dates'

beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date(2026, 5, 1)) }) // Jun 1 2026
afterEach(() => vi.useRealTimers())

// dueStatus('2026-05-30') => 'overdue'; ('2026-06-03') => 'due-soon';
// ('2026-06-30') => 'none'; (null) => 'none'. formatDueDate('2026-06-15') => 'Jun 15'.
```

**`frontend/src/features/tasks/TasksPage.test.tsx`** — mirror `frontend/src/features/inbox/InboxPage.test.tsx`.
Mock `../../api/tasks` (`listAllTasks` resolves one accepted task; `updateTask` a `vi.fn`). Render
the **global** view (`<TasksPage />` with no projectId → no ActivityFeed to mock) inside
`<MemoryRouter>`. Flow: click **Edit** → modal opens → change Priority select to `urgent` → click
**Save** → assert `updateTask` called with `expect.objectContaining({ priority: 'urgent' })`.

---

## Verification (definition of done)

1. `cd frontend && npm run test` — new + existing Vitest suites green.
2. `cd frontend && npm run build` — TypeScript strict build passes (no `any`).
3. Manual (backend + Ollama running per README dev commands):
   - `/tasks`: past `due_date` → red "Due …" badge; within 3 days → amber badge; far-future/none →
     no badge; done tasks → no badge.
   - **Edit** a task → modal opens → change priority + due date + status → **Save** → row reflects
     changes; on a per-project page the ActivityFeed gains an `updated` event.
   - Escape / overlay / Cancel close the modal without saving.
   - `/projects`: **Edit** a project (incl. **General**) → change name/description → **Save** → list
     updates; General still cannot be deleted.
4. `git status` shows only frontend files + `TASKS.md`/`TASKS_SPRINT.md` (no migration/schema crept in).

## Bookkeeping

- Check off the three Sprint 7 "Daily-use slice" items in `TASKS.md`.
- No README change required (no new setup/dev command/schema) — note this in the commit per the
  CLAUDE.md "done" checklist.
- Commit message at the chunk stop, per convention: `A: Sprint 7 - Daily-use slice (...)`.
