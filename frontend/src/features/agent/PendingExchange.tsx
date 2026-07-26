import { GlitchMark } from '../../components/GlitchMark'

/** The optimistic tail while a run is in flight: the user's bubble plus a
 * progress note (v1 is non-streaming — tool calls appear when the run lands).
 * Rendered inside an `agent-messages` list by both the agent panel and the
 * command bar's inline exchange. */
export function PendingExchange({ text }: { text: string }) {
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
          <div className="agent-bubble agent-bubble--working" role="status">
            <span className="agent-working-dots" aria-hidden="true">
              <span />
              <span />
              <span />
            </span>
            Working — reading your projects and calling tools…
          </div>
        </div>
      </li>
    </>
  )
}
