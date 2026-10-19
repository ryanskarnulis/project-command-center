import { AlertTriangle, ChevronDown, ChevronUp, Eye, Wrench } from 'lucide-react'
import { Link } from 'react-router-dom'
import { fireAndForget } from '../../utils/async'
import type { ToolCallRecord } from '../../types/agent'
import { describeToolCall, linkFor, undoFor } from './toolCalls'
import { useTrajectory } from './useTrajectory'

/**
 * The tool trajectory persisted on one assistant message, in M07f's shape
 * (#327): one bordered group, every successful read collapsed into a single
 * `Read n things` row that expands, then a row per mutation with the undo
 * affordance its clean inverse allows. Failed calls stay visible, struck
 * through, with their error — the agent's self-corrections are part of the
 * record, not something to hide.
 *
 * The grouping and the undo live in `useTrajectory`, shared with the phone's
 * `MobileTrajectory`. Desktop keeps the full undo label as the visible text
 * ("Undo (reopen)", "Reopen (keeps next occurrence)") — it has the width the
 * phone row doesn't.
 */
export function ToolCallList({
  messageId,
  records,
}: {
  messageId: number
  records: ToolCallRecord[]
}) {
  const { reads, rows, readsOpen, toggleReads, isUndone, undo } = useTrajectory(
    messageId,
    records,
  )

  return (
    <div className="agent-trace" aria-label="Agent tool calls">
      {reads.length > 0 && (
        <>
          <button
            type="button"
            className="agent-trace-reads"
            aria-expanded={readsOpen}
            onClick={toggleReads}
          >
            <Eye size={14} aria-hidden="true" />
            <span>
              Read {reads.length} {reads.length === 1 ? 'thing' : 'things'}
            </span>
            {readsOpen ? (
              <ChevronUp size={14} aria-hidden="true" />
            ) : (
              <ChevronDown size={14} aria-hidden="true" />
            )}
          </button>
          {readsOpen && (
            <ul className="agent-trace-reads-detail">
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
        const undone = isUndone(key)
        // After an undo the entity has moved (trashed / restored), so the
        // link is recomputed rather than pointing at a 404.
        const link = linkFor(record, { undone })
        return (
          <div
            key={key}
            className={`agent-trace-call${failed ? ' agent-trace-call--failed' : ''}${
              undone ? ' agent-trace-call--undone' : ''
            }`}
          >
            {failed ? (
              <AlertTriangle size={14} aria-hidden="true" />
            ) : (
              <Wrench size={14} aria-hidden="true" />
            )}
            <span className="agent-trace-call-summary">
              {link !== null ? (
                <Link to={link}>{describeToolCall(record)}</Link>
              ) : (
                describeToolCall(record)
              )}
              {failed && <span className="agent-trace-call-error"> — {record.error}</span>}
            </span>
            {action !== null &&
              (undone ? (
                <span className="agent-trace-call-undone">Undone</span>
              ) : (
                <button
                  type="button"
                  className="agent-trace-undo"
                  onClick={() => fireAndForget(undo(key, record))}
                >
                  {action.label}
                </button>
              ))}
          </div>
        )
      })}
    </div>
  )
}
