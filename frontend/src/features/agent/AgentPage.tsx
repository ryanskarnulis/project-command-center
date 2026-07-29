import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  MessageSquarePlus,
  SendHorizontal,
  Trash2,
  Volume2,
  VolumeX,
} from 'lucide-react'
import { ApiError } from '../../api/client'
import { GlitchMark } from '../../components/GlitchMark'
import { fireAndForget } from '../../utils/async'
import { formatRelative } from '../../utils/dates'
import { MicButton } from '../../voice/MicButton'
import { playText } from '../../voice/tts'
import { MessageBubble } from './MessageBubble'
import { PendingExchange } from './PendingExchange'
import { useConversation } from './useConversation'
import { useConversations } from './useConversations'

/** The failure line for a rejected `create`. Prefers the API's `detail` over
 * the generic "API error 500" — same shape as `sendErrorMessage` here and
 * `dependencyErrorMessage` in `TaskDependencies`. */
function createErrorMessage(e: unknown): string {
  if (e instanceof ApiError) {
    const detail = (e.body as { detail?: unknown } | null)?.detail
    if (typeof detail === 'string') return detail
  }
  return e instanceof Error ? e.message : 'Could not start a new conversation'
}

export function AgentPage() {
  const params = useParams<{ conversationId?: string }>()
  const navigate = useNavigate()
  const activeId =
    params.conversationId !== undefined ? Number(params.conversationId) : null

  const {
    conversations,
    loading: listLoading,
    loadingMore,
    hasMore,
    error: listError,
    refresh,
    loadMore,
    create,
    remove,
  } = useConversations()
  const onExchange = useCallback(() => void refresh(), [refresh])
  const { detail, loading, error, pendingText, send } = useConversation(
    activeId !== null && Number.isFinite(activeId) ? activeId : null,
    onExchange,
  )

  const [draft, setDraft] = useState('')
  const [createError, setCreateError] = useState<string | null>(null)
  const threadEndRef = useRef<HTMLLIElement>(null)
  const sending = pendingText !== null

  // Voice-output toggle (fleet UX rule: voice in → voice out, typed in →
  // silent — this mutes even the voiced path). Client-owned: PCC has no
  // server-side user settings, and the flag only governs local playback.
  const [voiceOutput, setVoiceOutput] = useState(
    () => localStorage.getItem('agent-voice-output') !== 'off',
  )
  const toggleVoiceOutput = () => {
    setVoiceOutput((on) => {
      localStorage.setItem('agent-voice-output', on ? 'off' : 'on')
      return !on
    })
  }

  // Voice-initiated turns speak the reply (typed turns never do). Playback
  // is started, not awaited — MicButton's hands-free loop waits on
  // audioIdle() so the mic still reopens only after the reply finishes.
  // localStorage (not the state) is read at speak time: hands-free VAD
  // callbacks close over this function from an earlier render, and the
  // stored flag is the always-current source of truth.
  const onTranscript = async (text: string) => {
    const reply = await send(text)
    if (reply && localStorage.getItem('agent-voice-output') !== 'off') {
      void playText(reply)
    }
  }

  // Keep the newest turn in view as messages/pending state arrive.
  // (Guarded call: jsdom has no scrollIntoView.)
  useEffect(() => {
    threadEndRef.current?.scrollIntoView?.({ block: 'end' })
  }, [detail?.messages.length, pendingText])

  const openConversation = (id: number) => navigate(`/agent/${id}`)

  // Both "New conversation" buttons fire-and-forget this, and that helper's
  // contract is that the rejection is already surfaced — so it has to be
  // surfaced here (#219). Component state, not the hook's `error`: that one
  // belongs to the list load and is cleared by the next successful refresh
  // (an agent turn refreshes the sidebar), which would wipe this message out
  // from under the user — the same reasoning as the dependency-removal fix in
  // #220. A failed create navigates nowhere and stays retryable.
  const startConversation = async () => {
    setCreateError(null)
    try {
      const conversation = await create()
      openConversation(conversation.id)
    } catch (e: unknown) {
      setCreateError(createErrorMessage(e))
    }
  }

  const removeConversation = async (id: number, title: string | null) => {
    if (!window.confirm(`Delete “${title ?? 'this conversation'}”?`)) return
    await remove(id)
    if (id === activeId) navigate('/agent')
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    const content = draft.trim()
    if (content === '' || sending || activeId === null) return
    setDraft('')
    await send(content)
  }

  // Enter sends, Shift+Enter makes a newline (the form's onSubmit handles
  // the actual send so both paths stay identical).
  const onComposerKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      event.currentTarget.form?.requestSubmit()
    }
  }

  return (
    <main className="agent-page">
      <aside className="agent-sidebar" aria-label="Conversations">
        <button
          type="button"
          className="agent-new-chat"
          onClick={() => fireAndForget(startConversation())}
        >
          <MessageSquarePlus size={17} aria-hidden="true" />
          New conversation
        </button>

        {/* The sidebar is always mounted, so one alert covers both buttons. */}
        {createError && <p role="alert" className="error">{createError}</p>}
        {listError && <p role="alert" className="error">{listError}</p>}
        {listLoading && <div className="page-loading">Loading…</div>}
        {!listLoading && conversations.length === 0 && (
          <p className="agent-sidebar-empty">No conversations yet.</p>
        )}

        <ul className="agent-conversation-list">
          {conversations.map((conversation) => (
            <li key={conversation.id}>
              <div
                className={`agent-conversation${
                  conversation.id === activeId ? ' active' : ''
                }`}
              >
                <button
                  type="button"
                  className="agent-conversation-open"
                  onClick={() => openConversation(conversation.id)}
                >
                  <span className="agent-conversation-title">
                    {conversation.title ?? 'New conversation'}
                  </span>
                  <span className="agent-conversation-time">
                    {formatRelative(conversation.updated_at)}
                  </span>
                </button>
                <button
                  type="button"
                  className="agent-conversation-delete"
                  aria-label={`Delete conversation ${conversation.title ?? conversation.id}`}
                  // The server rejects a delete during that conversation's run
                  // with 409 (#149); don't offer the action we know will fail.
                  disabled={sending && conversation.id === activeId}
                  title={
                    sending && conversation.id === activeId
                      ? 'The agent is working — you can delete this once it finishes'
                      : undefined
                  }
                  onClick={() =>
                    fireAndForget(
                      removeConversation(conversation.id, conversation.title),
                    )
                  }
                >
                  <Trash2 size={15} aria-hidden="true" />
                </button>
              </div>
            </li>
          ))}
        </ul>

        {/* The list is paged (#193): every older conversation stays reachable
            rather than falling off the end of the first page. */}
        {hasMore && (
          <button
            type="button"
            className="agent-load-more"
            onClick={() => fireAndForget(loadMore())}
            disabled={loadingMore}
          >
            {loadingMore ? 'Loading…' : 'Load older conversations'}
          </button>
        )}
      </aside>

      <section className="agent-thread" aria-label="Agent chat">
        {activeId === null ? (
          <div className="agent-thread-empty">
            <GlitchMark size={28} />
            <h1>Agent</h1>
            <p>
              Ask for anything your projects need — “create a task…”, “what’s
              overdue?”, “plan my day”. Every change the agent makes is audited
              and undoable from the trash.
            </p>
            <button
              type="button"
              className="agent-new-chat"
              onClick={() => fireAndForget(startConversation())}
            >
              <MessageSquarePlus size={17} aria-hidden="true" />
              Start a conversation
            </button>
          </div>
        ) : (
          <>
            {error && <p role="alert" className="error">{error}</p>}
            {loading && <div className="page-loading">Loading conversation…</div>}

            <ul className="agent-messages">
              {detail?.messages.map((message) => (
                <MessageBubble key={message.id} message={message} />
              ))}
              {pendingText !== null && <PendingExchange text={pendingText} />}
              {!loading &&
                detail?.messages.length === 0 &&
                pendingText === null && (
                  <li className="agent-thread-hint">
                    What should the agent do? It can read and change your
                    projects and tasks.
                  </li>
                )}
              {/* Autoscroll sentinel — must live INSIDE the scroll container
                  (the ul), or scrollIntoView can't scroll the thread. */}
              <li ref={threadEndRef} className="agent-thread-sentinel" aria-hidden="true" />
            </ul>

            <form className="agent-composer" onSubmit={(e) => void submit(e)}>
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={onComposerKeyDown}
                placeholder={
                  sending
                    ? 'The agent is working…'
                    : 'Message the agent (Enter to send)'
                }
                aria-label="Message the agent"
                rows={2}
                disabled={sending}
              />
              <MicButton onTranscript={onTranscript} disabled={sending} />
              <button
                type="button"
                className="agent-voice-toggle"
                onClick={toggleVoiceOutput}
                aria-pressed={voiceOutput}
                aria-label={
                  voiceOutput ? 'Mute spoken replies' : 'Unmute spoken replies'
                }
                title={voiceOutput ? 'Spoken replies on' : 'Spoken replies off'}
              >
                {voiceOutput ? (
                  <Volume2 size={17} aria-hidden="true" />
                ) : (
                  <VolumeX size={17} aria-hidden="true" />
                )}
              </button>
              <button
                type="submit"
                className="btn--primary agent-send"
                disabled={sending || draft.trim() === ''}
                aria-label="Send message"
              >
                <SendHorizontal size={17} aria-hidden="true" />
              </button>
            </form>
          </>
        )}
      </section>
    </main>
  )
}
