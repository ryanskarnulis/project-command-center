import { GlitchMark } from '../../components/GlitchMark'

interface Props {
  text: string
  /** Offer Stop (#324). Omitted where the run can't be stopped from here —
   * the command bar's inline exchange has no Stop. */
  onStop?: () => void
  /** A Stop was requested and the run hasn't landed yet. */
  stopping?: boolean
}

/** The optimistic tail while a run is in flight: the user's bubble plus a
 * progress note (v1 is non-streaming — tool calls appear when the run lands).
 * Rendered inside an `agent-messages` list by both the agent panel and the
 * command bar's inline exchange. */
export function PendingExchange({ text, onStop, stopping = false }: Props) {
  return (
    <>
      <li className="agent-message agent-message--user">
        <div className="agent-bubble">{text}</div>
      </li>
      <li className="agent-message agent-message--assistant">
        <span className="agent-avatar" aria-hidden="true">
          <GlitchMark size={20} className="agent-spider-working" />
        </span>
        <div className="agent-message-body">
          <div className="agent-bubble agent-bubble--working">
            <span className="agent-working-dots" aria-hidden="true">
              <span />
              <span />
              <span />
            </span>
            <span role="status">
              {stopping
                ? 'Stopping — finishing the current step…'
                : 'Working — reading your projects and calling tools…'}
            </span>
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
        </div>
      </li>
    </>
  )
}
