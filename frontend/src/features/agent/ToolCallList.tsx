import { useState } from 'react'
import { AlertTriangle, Eye, Wrench } from 'lucide-react'
import { useToast } from '../../components/ToastContext'
import { useTrashCount } from '../trash/trashCountContext'
import type { ToolCallRecord } from '../../types/agent'
import { describeToolCall, isMutation, undoFor } from './toolCalls'

/**
 * The tool trajectory persisted on one assistant message: every call the loop
 * made, with an undo affordance on the mutations that have a clean inverse.
 * Failed calls stay visible (struck through) — the agent's self-corrections
 * are part of the record, not something to hide.
 */
export function ToolCallList({
  messageId,
  records,
}: {
  messageId: number
  records: ToolCallRecord[]
}) {
  const { withToast } = useToast()
  const { refresh: refreshTrashCount } = useTrashCount()
  // Undo keys ("<messageId>:<index>") already applied this visit, so the
  // button collapses to a "Undone" marker instead of double-firing.
  const [undone, setUndone] = useState<Set<string>>(new Set())

  const undo = async (key: string, record: ToolCallRecord) => {
    const action = undoFor(record)
    if (!action) return
    await withToast(action.perform(), {
      success: `Undid: ${describeToolCall(record).toLowerCase()}`,
    })
    setUndone((prev) => new Set(prev).add(key))
    // Creates get trashed / trashes get restored — keep the badge honest.
    void refreshTrashCount()
  }

  return (
    <ul className="agent-tool-calls" aria-label="Agent tool calls">
      {records.map((record, index) => {
        const key = `${messageId}:${index}`
        const failed = record.error !== null
        const action = failed ? null : undoFor(record)
        return (
          <li
            key={key}
            className={`agent-tool-call${failed ? ' agent-tool-call--failed' : ''}${
              isMutation(record) ? ' agent-tool-call--mutation' : ''
            }`}
          >
            {failed ? (
              <AlertTriangle size={14} aria-hidden="true" />
            ) : isMutation(record) ? (
              <Wrench size={14} aria-hidden="true" />
            ) : (
              <Eye size={14} aria-hidden="true" />
            )}
            <span className="agent-tool-call-summary">
              {describeToolCall(record)}
              {failed && (
                <span className="agent-tool-call-error"> — {record.error}</span>
              )}
            </span>
            {action !== null &&
              (undone.has(key) ? (
                <span className="agent-tool-call-undone">Undone</span>
              ) : (
                <button
                  type="button"
                  className="secondary-action agent-undo"
                  onClick={() => void undo(key, record)}
                >
                  {action.label}
                </button>
              ))}
          </li>
        )
      })}
    </ul>
  )
}
