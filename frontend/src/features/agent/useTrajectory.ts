import { useState } from 'react'
import { useToast } from '../../components/ToastContext'
import { useTrashCount } from '../trash/trashCountContext'
import type { ToolCallRecord } from '../../types/agent'
import { describeToolCall, isMutation, undoFor } from './toolCalls'

export interface TrajectoryEntry {
  record: ToolCallRecord
  /** Stable per visit: "<messageId>:<index>". */
  key: string
}

export interface Trajectory {
  /** Successful read-only calls — collapsed behind one `Read n things` row. */
  reads: TrajectoryEntry[]
  /** Mutations and failed calls, in call order — a row each. */
  rows: TrajectoryEntry[]
  readsOpen: boolean
  toggleReads: () => void
  isUndone: (key: string) => boolean
  /** Apply `undoFor(record)` through `withToast`; a rejection leaves the
   * row's Undo in place for a retry. */
  undo: (key: string, record: ToolCallRecord) => Promise<void>
}

/**
 * M07f's trajectory model, shared by the desktop `ToolCallList` and the mobile
 * `MobileTrajectory` so the two layouts cannot drift (#327). Every successful
 * read collapses into one row — a read has no inverse, so its row would carry
 * no action; mutations keep a row each with the `undoFor` inverse; failed
 * calls stay visible — the agent's self-corrections are part of the record.
 *
 * Undo refreshes the trash count (creates get trashed / trashes get restored)
 * and is remembered per visit so callers recompute the row's link with
 * `linkFor(record, { undone })` rather than pointing at a 404.
 */
export function useTrajectory(messageId: number, records: ToolCallRecord[]): Trajectory {
  const { withToast } = useToast()
  const { refresh: refreshTrashCount } = useTrashCount()
  // Per message, not persisted: the open state belongs to this visit.
  const [readsOpen, setReadsOpen] = useState(false)
  const [undone, setUndone] = useState<Set<string>>(new Set())

  const indexed = records.map((record, index) => ({ record, key: `${messageId}:${index}` }))
  const reads = indexed.filter(({ record }) => record.error === null && !isMutation(record))
  const rows = indexed.filter(({ record }) => record.error !== null || isMutation(record))

  const undo = async (key: string, record: ToolCallRecord) => {
    const action = undoFor(record)
    if (!action) return
    await withToast(action.perform(), {
      success: `Undid: ${describeToolCall(record).toLowerCase()}`,
    })
    setUndone((prev) => new Set(prev).add(key))
    void refreshTrashCount()
  }

  return {
    reads,
    rows,
    readsOpen,
    toggleReads: () => setReadsOpen((open) => !open),
    isUndone: (key) => undone.has(key),
    undo,
  }
}
