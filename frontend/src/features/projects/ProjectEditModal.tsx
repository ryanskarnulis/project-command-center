import { type FormEvent, useEffect, useState } from 'react'
import { Modal } from '../../components/Modal'
import { createAlias, deleteAlias, listAliases } from '../../api/projects'
import type { Project, ProjectAlias, ProjectUpdate } from '../../types/project'

interface Props {
  project: Project
  onClose: () => void
  onSave: (id: number, data: ProjectUpdate) => Promise<void>
}

export function ProjectEditModal({ project, onClose, onSave }: Props) {
  const [name, setName] = useState(project.name)
  const [description, setDescription] = useState(project.description ?? '')
  const [saving, setSaving] = useState(false)

  // Aliases are managed independently of the name/description Save via the
  // dedicated Sprint 4 alias endpoints — each add/remove hits the API directly.
  const [aliases, setAliases] = useState<ProjectAlias[]>([])
  const [newAlias, setNewAlias] = useState('')
  const [aliasBusy, setAliasBusy] = useState(false)
  const [aliasError, setAliasError] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    listAliases(project.id)
      .then((data) => {
        if (active) setAliases(data)
      })
      .catch((e: unknown) => {
        if (active) {
          setAliasError(e instanceof Error ? e.message : 'Failed to load aliases')
        }
      })
    return () => {
      active = false
    }
  }, [project.id])

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

  async function handleAddAlias(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const value = newAlias.trim()
    if (!value || aliasBusy) return
    setAliasBusy(true)
    setAliasError(null)
    try {
      const created = await createAlias(project.id, { alias: value })
      setAliases((prev) => [...prev, created])
      setNewAlias('')
    } catch (e: unknown) {
      setAliasError(e instanceof Error ? e.message : 'Failed to add alias')
    } finally {
      setAliasBusy(false)
    }
  }

  async function handleRemoveAlias(aliasId: number) {
    setAliasBusy(true)
    setAliasError(null)
    try {
      await deleteAlias(project.id, aliasId)
      setAliases((prev) => prev.filter((a) => a.id !== aliasId))
    } catch (e: unknown) {
      setAliasError(e instanceof Error ? e.message : 'Failed to remove alias')
    } finally {
      setAliasBusy(false)
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

      <section>
        <h3>Aliases</h3>
        <p>
          Names that map inbox text to this project when matching extracted
          tasks.
        </p>
        {aliases.length > 0 ? (
          <ul>
            {aliases.map((alias) => (
              <li key={alias.id}>
                <span>{alias.alias}</span>
                <button
                  type="button"
                  disabled={aliasBusy}
                  aria-label={`Remove alias ${alias.alias}`}
                  onClick={() => void handleRemoveAlias(alias.id)}
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p>No aliases yet.</p>
        )}

        <form onSubmit={handleAddAlias}>
          <label htmlFor="pe-alias">Add alias</label>
          <input
            id="pe-alias"
            value={newAlias}
            onChange={(e) => setNewAlias(e.target.value)}
            placeholder="e.g. fw, firewall"
          />
          <button type="submit" disabled={aliasBusy || !newAlias.trim()}>
            Add
          </button>
        </form>

        {aliasError && <p role="alert">{aliasError}</p>}
      </section>
    </Modal>
  )
}
