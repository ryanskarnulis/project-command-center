import { type FormEvent, useState } from 'react'
import { Modal } from '../../components/Modal'
import type { Project, ProjectCreate, ProjectUpdate } from '../../types/project'

type CreateMode = {
  mode: 'create'
  onSave: (data: ProjectCreate) => Promise<void>
}

type EditMode = {
  mode: 'edit'
  project: Project
  onSave: (id: number, data: ProjectUpdate) => Promise<void>
}

type Props = (CreateMode | EditMode) & {
  onClose: () => void
}

export function ProjectFormModal(props: Props) {
  const { onClose } = props
  const existing = props.mode === 'edit' ? props.project : null

  const [name, setName] = useState(existing?.name ?? '')
  const [description, setDescription] = useState(existing?.description ?? '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const trimmed = name.trim()
    if (!trimmed) return
    setSaving(true)
    setError(null)
    const payload = { name: trimmed, description: description.trim() || null }
    try {
      if (props.mode === 'edit') {
        await props.onSave(props.project.id, payload)
      } else {
        await props.onSave(payload)
      }
      onClose()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to save project')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal open title={props.mode === 'edit' ? 'Edit project' : 'New project'} onClose={onClose}>
      <form onSubmit={handleSubmit}>
        <label htmlFor="pf-name">Name</label>
        <input
          id="pf-name"
          autoFocus
          value={name}
          onChange={(e) => setName(e.target.value)}
        />

        <label htmlFor="pf-desc">Description</label>
        <textarea
          id="pf-desc"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="Optional"
        />

        {error && <p role="alert">{error}</p>}

        <div className="modal-actions">
          <button type="submit" disabled={saving || !name.trim()}>Save</button>
          <button type="button" onClick={onClose}>Cancel</button>
        </div>
      </form>
    </Modal>
  )
}
