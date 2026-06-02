import { type FormEvent, useState } from 'react'
import { Modal } from '../../components/Modal'
import type { Project } from '../../types/project'
import type { Task, TaskPriority, TaskStatus, TaskUpdate } from '../../types/task'

const PRIORITIES: TaskPriority[] = ['low', 'medium', 'high', 'urgent']
const STATUSES: TaskStatus[] = ['candidate', 'accepted', 'rejected', 'done']

interface Props {
  task: Task
  projects: Project[]
  onClose: () => void
  onSave: (id: number, data: TaskUpdate) => Promise<void>
}

export function TaskEditModal({ task, projects, onClose, onSave }: Props) {
  const [title, setTitle] = useState(task.title)
  const [description, setDescription] = useState(task.description ?? '')
  const [status, setStatus] = useState<TaskStatus>(task.status)
  const [priority, setPriority] = useState<TaskPriority>(task.priority)
  const [dueDate, setDueDate] = useState(task.due_date ?? '')
  const [projectId, setProjectId] = useState(task.project_id !== null ? String(task.project_id) : '')
  const [saving, setSaving] = useState(false)

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
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
        project_id: projectId === '' ? null : Number(projectId),
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
        <input
          id="te-title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />

        <label htmlFor="te-desc">Description</label>
        <textarea
          id="te-desc"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />

        <label htmlFor="te-status">Status</label>
        <select
          id="te-status"
          value={status}
          onChange={(e) => setStatus(e.target.value as TaskStatus)}
        >
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>

        <label htmlFor="te-priority">Priority</label>
        <select
          id="te-priority"
          value={priority}
          onChange={(e) => setPriority(e.target.value as TaskPriority)}
        >
          {PRIORITIES.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>

        <label htmlFor="te-due">Due date</label>
        <input
          id="te-due"
          type="date"
          value={dueDate}
          onChange={(e) => setDueDate(e.target.value)}
        />

        <label htmlFor="te-project">Project</label>
        <select
          id="te-project"
          value={projectId}
          onChange={(e) => setProjectId(e.target.value)}
        >
          <option value="">— unassigned —</option>
          {projects.map((p) => (
            <option key={p.id} value={String(p.id)}>
              {p.name}
            </option>
          ))}
        </select>

        <div className="modal-actions">
          <button type="submit" disabled={saving || !title.trim()}>
            Save
          </button>
          <button type="button" onClick={onClose}>
            Cancel
          </button>
        </div>
      </form>
    </Modal>
  )
}
