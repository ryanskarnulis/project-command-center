import { type FormEvent, useState } from 'react'
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

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
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
        <input
          id="pe-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />

        <label htmlFor="pe-desc">Description</label>
        <input
          id="pe-desc"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="Optional"
        />

        <div className="modal-actions">
          <button type="submit" disabled={saving || !name.trim()}>
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
