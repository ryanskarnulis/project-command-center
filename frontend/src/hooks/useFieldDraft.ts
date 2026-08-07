import { useState } from 'react'

/** Identity of the record a draft belongs to; changing it resets the draft. */
export type DraftKey = string | number | null

interface DraftState {
  key: DraftKey
  /** Server value this draft was anchored to when it was last written. */
  source: string
  value: string
}

export interface FieldDraft {
  /** What the input should render right now. */
  value: string
  /** True while the user's text diverges from the server's value. */
  dirty: boolean
  /** Record a keystroke, re-anchoring the draft to the current server value. */
  set: (next: string) => void
}

/**
 * Re-anchor a draft against the server's current value for its field.
 *
 * The draft is anchored to the server value it was typed against. While that
 * anchor holds, the draft is authoritative. Once the server moves the field,
 * the draft yields — unless the user is actively diverging on *this* field, in
 * which case their in-progress edit survives and is re-anchored to the new
 * server value so the next server change is judged against fresh information.
 */
function reconcile(draft: DraftState, key: DraftKey, serverValue: string): DraftState {
  const clean: DraftState = { key, source: serverValue, value: serverValue }
  // A different record entirely: the previous one's draft says nothing about it.
  if (draft.key !== key) return clean
  // Still anchored to what the server last told us — the draft stands as typed.
  if (draft.source === serverValue) return draft
  // The server moved this field. A draft the user never diverged on, or one the
  // server has since caught up to (this field's own save landing), goes clean.
  if (draft.value === draft.source || draft.value === serverValue) return clean
  // The user is mid-edit here: keep their text rather than silently wiping it.
  return { key, source: serverValue, value: draft.value }
}

/**
 * A text-input draft anchored to one server-backed field.
 *
 * Anchoring per field is the point: a page that fingerprints several fields
 * together throws away *every* draft when the server changes *any* of them, so
 * saving one field silently discards whatever the user has typed into another
 * while the PATCH was in flight (#255). Each field tracks its own anchor, so a
 * save of one leaves the others' in-progress edits alone, while server-side
 * changes (e.g. from the agent) still flow into fields the user isn't editing.
 *
 * @param key         Identity of the record being edited; a change discards the
 *                    draft, so switching tasks never leaks text between them.
 * @param serverValue The field's current value as last returned by the server.
 */
export function useFieldDraft(key: DraftKey, serverValue: string): FieldDraft {
  const [draft, setDraft] = useState<DraftState>(() => ({
    key,
    source: serverValue,
    value: serverValue,
  }))

  // Reconcile during render — the pattern this codebase already uses to reset
  // per-record state on a switch. The re-anchor has to be *remembered*: derive
  // it only and a draft the server has caught up to keeps looking divergent
  // forever, so later server changes could never reach the field again.
  const active = reconcile(draft, key, serverValue)
  if (active !== draft) setDraft(active)

  return {
    value: active.value,
    dirty: active.value !== serverValue,
    set: (next: string) => setDraft({ key, source: serverValue, value: next }),
  }
}
