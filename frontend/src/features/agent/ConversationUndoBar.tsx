import { fireAndForget } from '../../utils/async'
import type { PendingDeleteUndo } from './deleteUndoStore'

interface Props {
  undo: PendingDeleteUndo
  onUndo: () => Promise<void>
  /** `magent-undo-bar` (phone, fixed over the bottom nav) or `agent-undo-bar`
   * (desktop, pinned to the foot of the rail). */
  className: string
}

/** "Deleted · <title>  Undo" — the five-second window after a conversation delete. */
export function ConversationUndoBar({ undo, onUndo, className }: Props) {
  return (
    <div className={className} role="status">
      <span>Deleted · {undo.title ?? 'New conversation'}</span>
      <button type="button" onClick={() => fireAndForget(onUndo())}>
        Undo
      </button>
    </div>
  )
}
