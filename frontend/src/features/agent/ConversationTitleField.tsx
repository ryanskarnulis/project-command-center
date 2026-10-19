import { useRef, useState, type KeyboardEvent } from 'react'
import type { Conversation } from '../../types/agent'

/** Mirrors the backend's MAX_CONVERSATION_TITLE_LENGTH (and the phone's
 * rename sheet). */
const MAX_TITLE_LENGTH = 120

interface Props {
  conversation: Conversation
  /** Resolves once the rename is saved; rejects to keep the field open. */
  onSave: (title: string) => Promise<void>
  onCancel: () => void
}

/**
 * Desktop rename (#327): the rail row's title becomes a field in place. Enter
 * or leaving the field saves, Escape cancels; an empty or unchanged title just
 * closes it. A failed save keeps the field (and the draft) for a retry — the
 * error itself is the caller's to show, in component state like the delete
 * errors (#229). The phone's twin is `RenameSheet`.
 */
export function ConversationTitleField({ conversation, onSave, onCancel }: Props) {
  const current = conversation.title ?? ''
  const [draft, setDraft] = useState(current)
  const [saving, setSaving] = useState(false)
  // Enter/Escape settle the edit; the blur that follows (focus leaving a
  // disabled or unmounting input) must not settle it a second time.
  const settled = useRef(false)

  const commit = async () => {
    if (settled.current) return
    const title = draft.trim()
    if (title === '' || title === current) {
      settled.current = true
      onCancel()
      return
    }
    settled.current = true
    setSaving(true)
    try {
      await onSave(title)
    } catch {
      // Surfaced by the caller; reopen the field for another try.
      settled.current = false
      setSaving(false)
    }
  }

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') {
      event.preventDefault()
      void commit()
    } else if (event.key === 'Escape') {
      event.preventDefault()
      settled.current = true
      onCancel()
    }
  }

  return (
    <input
      type="text"
      className="agent-conversation-title-field"
      aria-label="Conversation title"
      value={draft}
      maxLength={MAX_TITLE_LENGTH}
      readOnly={saving}
      autoFocus
      onFocus={(event) => event.currentTarget.select()}
      onChange={(event) => setDraft(event.target.value)}
      onKeyDown={onKeyDown}
      onBlur={() => void commit()}
    />
  )
}
