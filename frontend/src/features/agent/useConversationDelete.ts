import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiErrorMessage } from '../../api/errorMessage'
import type { Conversation } from '../../types/agent'
import {
  armDeleteUndo,
  dismissDeleteUndo,
  useDeleteUndo,
  type PendingDeleteUndo,
} from './deleteUndoStore'
import type { UseConversations } from './useConversations'

export interface UseConversationDelete {
  /** The delete still inside its five-second undo window, if any. */
  undo: PendingDeleteUndo | null
  /** The last delete/restore failure. Component state on purpose: the list
   * hook's `error` is load-owned and the next refresh would wipe it (#229). */
  deleteError: string | null
  deleteConversation: (target: Pick<Conversation, 'id' | 'title'>) => Promise<void>
  undoDelete: () => Promise<void>
}

/**
 * Delete acts immediately and offers five seconds of undo — the M07f pattern
 * that replaced `window.confirm()`, shared by the desktop rail and the mobile
 * sheets. Undo is the server restore (`POST …/restore`), so it is audited and
 * survives a reload; undoing the active conversation navigates back to it.
 *
 * Both callers fire-and-forget these, so every rejection is surfaced in
 * `deleteError` rather than propagated. A rejected delete navigates nowhere.
 *
 * The pending undo lives in `deleteUndoStore`, not here: deleting the active
 * conversation navigates `/agent/:id` → `/agent`, which remounts the page.
 */
export function useConversationDelete(
  activeId: number | null,
  remove: UseConversations['remove'],
  restore: UseConversations['restore'],
): UseConversationDelete {
  const navigate = useNavigate()
  const undo = useDeleteUndo()
  const [deleteError, setDeleteError] = useState<string | null>(null)

  const deleteConversation = async (target: Pick<Conversation, 'id' | 'title'>) => {
    setDeleteError(null)
    const wasActive = target.id === activeId
    try {
      await remove(target.id)
    } catch (e: unknown) {
      setDeleteError(apiErrorMessage(e, 'Could not delete the conversation'))
      return
    }
    // Arm before navigating: the store outlives the remount, this instance
    // does not.
    armDeleteUndo({ id: target.id, title: target.title, wasActive })
    if (wasActive) navigate('/agent')
  }

  const undoDelete = async () => {
    if (undo === null) return
    const { id, wasActive } = undo
    dismissDeleteUndo()
    setDeleteError(null)
    try {
      await restore(id)
    } catch (e: unknown) {
      setDeleteError(apiErrorMessage(e, 'Could not restore the conversation'))
      return
    }
    if (wasActive) navigate(`/agent/${id}`)
  }

  return { undo, deleteError, deleteConversation, undoDelete }
}
