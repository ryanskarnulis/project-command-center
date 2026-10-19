import { GlitchMark } from '../../components/GlitchMark'
import { formatElapsed, SLOW_RUN_AFTER_S, useElapsedSeconds } from './useElapsedSeconds'

interface Props {
  text: string
  /** Offer Stop (#324). Omitted where the run can't be stopped from here —
   * the command bar's inline exchange has no Stop. */
  onStop?: () => void
  /** A Stop was requested and the run hasn't landed yet. */
  stopping?: boolean
}

/** The optimistic tail while a run is in flight: the user's bubble plus one
 * status block in M07f's shape (#327) — an elapsed `m:ss` clock with Stop
 * beside it, and past five seconds a note that a cold model load is slow. The
 * run is synchronous and nothing streams, so elapsed time is the only honest
 * signal. Rendered inside an `agent-messages` list by both the agent panel and
 * the command bar's inline exchange. The element mounts with the run, so the
 * clock starts at 0:00 with it. */
export function PendingExchange({ text, onStop, stopping = false }: Props) {
  const elapsed = useElapsedSeconds(true)
  return (
    <>
      <li className="agent-message agent-message--user">
        <div className="agent-bubble">{text}</div>
      </li>
      <li className="agent-message agent-message--assistant">
        <div className="agent-label">
          <GlitchMark size={18} className="agent-spider-working" />
          <span>Agent</span>
        </div>
        <div className="agent-working" role="status">
          <div>
            <span className="agent-working-line">
              {stopping
                ? 'Stopping — finishing the current step…'
                : 'Working — reading your projects and calling tools…'}
            </span>
            {elapsed >= SLOW_RUN_AFTER_S && !stopping && (
              <span className="agent-working-sub">First run today can take a minute</span>
            )}
          </div>
          <span className="agent-clock">{formatElapsed(elapsed)}</span>
          {onStop !== undefined && (
            <button
              type="button"
              className="agent-stop"
              onClick={onStop}
              disabled={stopping}
            >
              {stopping ? 'Stopping…' : 'Stop'}
            </button>
          )}
        </div>
      </li>
    </>
  )
}
