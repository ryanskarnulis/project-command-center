import { useState } from 'react'

export interface FieldDraft {
  /** What the field shows: the user's edit, or the server value when there is none. */
  value: string
  set: (value: string) => void
  /** The draft diverges from the server value. */
  dirty: boolean
}

/**
 * An editable copy of one server field. The draft is anchored to the entity id
 * and to this field's own server value, so it is replaced only when that value
 * (or the entity) changes — a save landing for a sibling field leaves an
 * in-progress edit here alone.
 */
export function useFieldDraft(entityId: number | null, serverValue: string): FieldDraft {
  const source = JSON.stringify([entityId, serverValue])
  const [draft, setDraft] = useState({ source: '', value: '' })
  const value = draft.source === source ? draft.value : serverValue
  return {
    value,
    set: (next) => setDraft({ source, value: next }),
    dirty: value !== serverValue,
  }
}
