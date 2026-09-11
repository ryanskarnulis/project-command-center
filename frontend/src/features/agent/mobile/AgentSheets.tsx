import { useState, type FormEvent } from 'react'
import { Check, MessageSquarePlus, Pencil, Trash2, Volume2, VolumeX } from 'lucide-react'
import { BottomSheet } from '../../../components/BottomSheet'
import type { Conversation } from '../../../types/agent'
import { formatRelative } from '../../../utils/dates'
import { ConversationSwipeRow } from './ConversationSwipeRow'

/* The M07f bottom sheets. All three use the shared `BottomSheet` primitive, so
   they inherit its scrim, focus containment, Escape and swipe-down dismiss. */

export const RUN_IN_FLIGHT_TITLE = 'The agent is working — you can delete this once it finishes'

interface ConversationsSheetProps {
  conversations: Conversation[]
  activeId: number | null
  loading: boolean
  loadingMore: boolean
  hasMore: boolean
  /** The conversation whose run is in flight, if any — its delete is off (#149). */
  busyId: number | null
  errors: string[]
  onOpen: (id: number) => void
  onNew: () => void
  onLoadMore: () => void
  onDelete: (conversation: Conversation) => void
  onActions: (conversation: Conversation) => void
  onClose: () => void
}

/**
 * The rail, as a sheet: conversation history is a once-a-session act on a
 * phone and no longer costs permanent height. Each row swipes left to delete
 * and long-presses into its actions sheet — the gesture is never the only
 * route.
 */
export function ConversationsSheet({
  conversations,
  activeId,
  loading,
  loadingMore,
  hasMore,
  busyId,
  errors,
  onOpen,
  onNew,
  onLoadMore,
  onDelete,
  onActions,
  onClose,
}: ConversationsSheetProps) {
  return (
    <BottomSheet
      className="magent-sheet magent-conversations-sheet"
      labelledBy="magent-conversations-title"
      handleLabel="Close conversations"
      onClose={onClose}
    >
      <header className="magent-sheet-head">
        <h2 id="magent-conversations-title">Conversations</h2>
        <button type="button" className="magent-sheet-done" onClick={onClose}>
          Done
        </button>
      </header>
      <button type="button" className="magent-sheet-new" onClick={onNew}>
        <MessageSquarePlus size={17} aria-hidden="true" />
        New conversation
      </button>
      {errors.map((message) => (
        <p key={message} role="alert" className="error">
          {message}
        </p>
      ))}
      {loading && <p className="magent-sheet-note">Loading…</p>}
      {!loading && conversations.length === 0 && (
        <p className="magent-sheet-note">No conversations yet.</p>
      )}
      <ul className="magent-sheet-list">
        {conversations.map((conversation) => {
          const busy = conversation.id === busyId
          const active = conversation.id === activeId
          return (
            <li key={conversation.id} title={busy ? RUN_IN_FLIGHT_TITLE : undefined}>
              <ConversationSwipeRow
                enabled={!busy}
                onDelete={() => onDelete(conversation)}
                onLongPress={() => onActions(conversation)}
              >
                <div className={active ? 'magent-row magent-row--active' : 'magent-row'}>
                  <button
                    type="button"
                    className="magent-row-open"
                    aria-current={active ? 'true' : undefined}
                    onClick={() => onOpen(conversation.id)}
                  >
                    <span className="magent-row-title">
                      {conversation.title ?? 'New conversation'}
                    </span>
                    <span className="magent-row-time">{formatRelative(conversation.updated_at)}</span>
                  </button>
                  {active && <Check size={17} aria-hidden="true" className="magent-row-check" />}
                </div>
              </ConversationSwipeRow>
            </li>
          )
        })}
      </ul>
      {hasMore && (
        <button type="button" className="magent-sheet-more" onClick={onLoadMore} disabled={loadingMore}>
          {loadingMore ? 'Loading…' : 'Load older conversations'}
        </button>
      )}
      <p className="magent-sheet-hint">
        Swipe a conversation left to delete it. Long-press for the same actions.
      </p>
    </BottomSheet>
  )
}

interface ActionsSheetProps {
  target: Conversation
  /** True while this conversation has a run in flight — delete is off (#149). */
  busy: boolean
  voiceOutput: boolean
  onRename: () => void
  onToggleVoice: () => void
  onDelete: () => void
  onClose: () => void
}

/**
 * The `⋯` sheet: rename, the spoken-replies device preference (moved here from
 * the composer, where it was the third 34px square in a 390px row), and
 * delete. Reached from the title row for the active conversation, and by
 * long-press from any row in the conversations sheet.
 */
export function ActionsSheet({ target, busy, voiceOutput, onRename, onToggleVoice, onDelete, onClose }: ActionsSheetProps) {
  return (
    <BottomSheet
      className="magent-sheet magent-actions-sheet"
      labelledBy="magent-actions-title"
      handleLabel="Close conversation actions"
      onClose={onClose}
    >
      <header className="magent-sheet-head magent-actions-head">
        <h2 id="magent-actions-title">{target.title ?? 'New conversation'}</h2>
        <span>{formatRelative(target.updated_at)}</span>
      </header>
      <div className="magent-sheet-actions">
        <button type="button" className="magent-sheet-action" onClick={onRename}>
          <Pencil size={17} aria-hidden="true" />
          Rename
        </button>
        <button
          type="button"
          className="magent-sheet-action"
          role="switch"
          aria-checked={voiceOutput}
          onClick={onToggleVoice}
        >
          {voiceOutput ? (
            <Volume2 size={17} aria-hidden="true" />
          ) : (
            <VolumeX size={17} aria-hidden="true" />
          )}
          <span>Spoken replies</span>
          <span className="magent-voice-pill" data-on={voiceOutput}>
            {voiceOutput ? 'On' : 'Off'}
          </span>
        </button>
        <button
          type="button"
          className="magent-sheet-action danger"
          disabled={busy}
          title={busy ? RUN_IN_FLIGHT_TITLE : undefined}
          onClick={onDelete}
        >
          <Trash2 size={17} aria-hidden="true" />
          Delete conversation
        </button>
      </div>
      <button type="button" className="magent-sheet-cancel" onClick={onClose}>
        Cancel
      </button>
    </BottomSheet>
  )
}

interface RenameSheetProps {
  target: Conversation
  error: string | null
  onSave: (title: string) => void
  onClose: () => void
}

/** Rename, as a sheet with one field. `window.prompt` is the dialog every
 * other mobile route replaced; this is its replacement here. */
export function RenameSheet({ target, error, onSave, onClose }: RenameSheetProps) {
  const [title, setTitle] = useState(target.title ?? '')
  const trimmed = title.trim()

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (trimmed === '') return
    onSave(trimmed)
  }

  return (
    <BottomSheet
      className="magent-sheet magent-rename-sheet"
      labelledBy="magent-rename-title"
      handleLabel="Close rename"
      onClose={onClose}
    >
      <form onSubmit={submit} className="magent-rename-form">
        <header className="magent-sheet-head">
          <h2 id="magent-rename-title">Rename conversation</h2>
        </header>
        <input
          type="text"
          className="magent-rename-field"
          aria-label="Conversation title"
          value={title}
          maxLength={120}
          autoFocus
          onChange={(event) => setTitle(event.target.value)}
        />
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        <div className="magent-rename-actions">
          <button type="button" className="magent-sheet-cancel" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="magent-sheet-save" disabled={trimmed === ''}>
            Save
          </button>
        </div>
      </form>
    </BottomSheet>
  )
}
