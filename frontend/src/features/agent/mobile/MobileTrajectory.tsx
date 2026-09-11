import { useState } from 'react'
import { AlertTriangle, ChevronDown, ChevronUp, Eye, Wrench } from 'lucide-react'
import { Link } from 'react-router-dom'
import { useToast } from '../../../components/ToastContext'
import { fireAndForget } from '../../../utils/async'
import { useTrashCount } from '../../trash/trashCountContext'
import type { ToolCallRecord } from '../../../types/agent'
import { describeToolCall, isMutation, linkFor, shortUndoLabel, undoFor } from '../toolCalls'

interface Props {
  messageId: number
  records: ToolCallRecord[]
}

/**
 * M07f's trajectory: one bordered group per assistant turn. Every successful
 * read-only call collapses into a single `Read n things` row — a read has no
 * inverse, so its row carries no action, and three of them above a two-line
 * answer is what made the shipped route read as a tool log. Mutations keep a
 * row each with a 44px `Undo`; failed calls stay visible, struck through, with
 * their error — the agent's self-corrections are part of the record.
 *
 * Undo goes through `undoFor(record)` and `withToast` exactly as the desktop
 * `ToolCallList` does, refreshes the trash count, and recomputes the row's
 * link on `linkFor(record, { undone })` so it never points at a 404.
 */
export function MobileTrajectory({ messageId, records }: Props) {
  const { withToast } = useToast()
  const { refresh: refreshTrashCount } = useTrashCount()
  // Per message, not persisted: the open state belongs to this visit.
  const [readsOpen, setReadsOpen] = useState(false)
  // Undo keys ("<messageId>:<index>") already applied this visit.
  const [undone, setUndone] = useState<Set<string>>(new Set())

  const indexed = records.map((record, index) => ({ record, index, key: `${messageId}:${index}` }))
  const reads = indexed.filter(({ record }) => record.error === null && !isMutation(record))
  const rows = indexed.filter(({ record }) => record.error !== null || isMutation(record))

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
    <div className="magent-trace" aria-label="Agent tool calls">
      {reads.length > 0 && (
        <>
          <button
            type="button"
            className="magent-reads"
            aria-expanded={readsOpen}
            onClick={() => setReadsOpen((open) => !open)}
          >
            <Eye size={15} aria-hidden="true" />
            <span>
              Read {reads.length} {reads.length === 1 ? 'thing' : 'things'}
            </span>
            {readsOpen ? (
              <ChevronUp size={15} aria-hidden="true" />
            ) : (
              <ChevronDown size={15} aria-hidden="true" />
            )}
          </button>
          {readsOpen && (
            <ul className="magent-reads-detail">
              {reads.map(({ record, key }) => {
                const link = linkFor(record)
                return (
                  <li key={key}>
                    {link !== null ? (
                      <Link to={link}>{describeToolCall(record)}</Link>
                    ) : (
                      describeToolCall(record)
                    )}
                  </li>
                )
              })}
            </ul>
          )}
        </>
      )}
      {rows.map(({ record, key }) => {
        const failed = record.error !== null
        const action = failed ? null : undoFor(record)
        const isUndone = undone.has(key)
        const link = linkFor(record, { undone: isUndone })
        return (
          <div
            key={key}
            className={`magent-call${failed ? ' magent-call--failed' : ''}${isUndone ? ' magent-call--undone' : ''}`}
          >
            {failed ? (
              <AlertTriangle size={15} aria-hidden="true" />
            ) : (
              <Wrench size={15} aria-hidden="true" />
            )}
            <span className="magent-call-summary">
              {link !== null ? (
                <Link to={link}>{describeToolCall(record)}</Link>
              ) : (
                describeToolCall(record)
              )}
              {failed && <span className="magent-call-error"> — {record.error}</span>}
            </span>
            {action !== null &&
              (isUndone ? (
                <span className="magent-call-undone">Undone</span>
              ) : (
                <button
                  type="button"
                  className="magent-undo"
                  title={action.label}
                  aria-label={`${action.label}: ${describeToolCall(record)}`}
                  onClick={() => fireAndForget(undo(key, record))}
                >
                  {shortUndoLabel(action.label)}
                </button>
              ))}
          </div>
        )
      })}
    </div>
  )
}
