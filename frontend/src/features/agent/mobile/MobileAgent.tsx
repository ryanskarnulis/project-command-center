import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import { ChevronDown, MessageSquarePlus, MoreHorizontal, SendHorizontal } from 'lucide-react'
import { apiErrorMessage } from '../../../api/errorMessage'
import { GlitchMark } from '../../../components/GlitchMark'
import type { AgentMessage, Conversation } from '../../../types/agent'
import { fireAndForget } from '../../../utils/async'
import { formatRelative } from '../../../utils/dates'
import { MicButton } from '../../../voice/MicButton'
import { playText } from '../../../voice/tts'
import { MARKDOWN_COMPONENTS, STOP_FALLBACK } from '../markdown'
import type { UseConversation } from '../useConversation'
import type { UseConversations } from '../useConversations'
import { ActionsSheet, ConversationsSheet, RenameSheet } from './AgentSheets'
import { armDeleteUndo, dismissDeleteUndo, useDeleteUndo } from './deleteUndoStore'
import { MobileTrajectory } from './MobileTrajectory'
import { formatElapsed, useElapsedSeconds } from './useElapsedSeconds'

/* M07f — /agent at phone width.
 *
 * The thread owns the viewport. The rail leaves the route (it is a sheet
 * behind the 44px title row), the agent's reply gets the full column instead
 * of a 228px bubble, every read the loop made collapses into one row, and the
 * wait shows an elapsed clock instead of three dots. Delete acts immediately
 * from a swipe or the ⋯ sheet and offers five seconds of undo, which is what
 * lets `window.confirm()` go.
 *
 * Divergences from the prototype, where its local state meets the service
 * layer:
 *
 *  - Undo is a server restore (`POST …/restore`), not a snapshot: the delete
 *    is the existing soft delete, so undo is audited and survives a reload.
 *    Undoing the active conversation navigates back to it and the thread
 *    refetches — there is no client-held copy of the messages to restore.
 *    The pending undo itself sits in `deleteUndoStore`, outside this tree,
 *    because that navigation remounts the page (see the store).
 *  - Deleting from inside the conversations sheet closes the sheet. The sheet
 *    is a native modal `<dialog>`, whose top layer would sit over the undo bar
 *    and leave it inert for its whole five seconds.
 *  - The composer's mic slot falls back to a disabled send button where the
 *    browser has no MediaRecorder; `MicButton` renders nothing there.
 */

const SLOW_RUN_AFTER_S = 5
const COMPOSER_MAX_HEIGHT_PX = 96
const VOICE_OUTPUT_KEY = 'agent-voice-output'

type Sheet =
  | { kind: 'conversations' }
  | { kind: 'actions'; target: Conversation }
  | { kind: 'rename'; target: Conversation }

interface Props {
  activeId: number | null
  list: UseConversations
  thread: UseConversation
}

export function MobileAgent({ activeId, list, thread }: Props) {
  const navigate = useNavigate()
  const { conversations, loading: listLoading, loadingMore, hasMore, error: listError, loadMore, create, remove, restore, rename } = list
  const { detail, loading, error, hasMore: threadHasMore, loadingOlder, loadOlder, pendingText, send } = thread
  const sending = pendingText !== null

  const [draft, setDraft] = useState('')
  const [sheet, setSheet] = useState<Sheet | null>(null)
  const undo = useDeleteUndo()
  const [createError, setCreateError] = useState<string | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [renameError, setRenameError] = useState<string | null>(null)
  const threadEndRef = useRef<HTMLLIElement>(null)
  const composerRef = useRef<HTMLTextAreaElement>(null)
  const elapsed = useElapsedSeconds(sending)

  // Device preference, same key and semantics as desktop (see AgentPage).
  const [voiceOutput, setVoiceOutput] = useState(
    () => localStorage.getItem(VOICE_OUTPUT_KEY) !== 'off',
  )
  const toggleVoiceOutput = () => {
    setVoiceOutput((on) => {
      localStorage.setItem(VOICE_OUTPUT_KEY, on ? 'off' : 'on')
      return !on
    })
  }

  // Keep the newest turn in view. Keyed on the newest message id rather than
  // the count, so prepending an older page (#244) doesn't yank the user down.
  // (Guarded call: jsdom has no scrollIntoView.)
  const newestMessageId = detail?.messages.at(-1)?.id
  useEffect(() => {
    threadEndRef.current?.scrollIntoView?.({ block: 'end' })
  }, [newestMessageId, pendingText])

  const active: Conversation | null =
    conversations.find((conversation) => conversation.id === activeId) ??
    (detail !== null && detail.id === activeId
      ? { id: detail.id, title: detail.title, created_at: detail.created_at, updated_at: detail.updated_at }
      : null)
  const title = active?.title ?? detail?.title ?? 'New conversation'

  const openConversation = (id: number) => {
    setSheet(null)
    navigate(`/agent/${id}`)
  }

  // Same contract as desktop (#219): `create` rejects are surfaced here, in
  // component state the list's own error cannot wipe out.
  const startConversation = async () => {
    setSheet(null)
    setCreateError(null)
    try {
      const conversation = await create()
      navigate(`/agent/${conversation.id}`)
    } catch (e: unknown) {
      setCreateError(apiErrorMessage(e, 'Could not start a new conversation'))
    }
  }

  /** Delete now, offer five seconds to take it back. The restore is the
   * existing soft delete's inverse, so undo is itself audited. */
  const deleteConversation = async (target: Conversation) => {
    setSheet(null)
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

  const runUndo = async () => {
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

  const saveTitle = async (target: Conversation, nextTitle: string) => {
    setRenameError(null)
    try {
      await rename(target.id, nextTitle)
      setSheet(null)
    } catch (e: unknown) {
      setRenameError(apiErrorMessage(e, 'Could not rename the conversation'))
    }
  }

  // Voice-initiated turns speak the reply; typed turns never do. localStorage
  // is read at speak time because hands-free callbacks close over an earlier
  // render (see AgentPage for the full reasoning).
  const onTranscript = async (text: string) => {
    const reply = await send(text)
    if (reply && localStorage.getItem(VOICE_OUTPUT_KEY) !== 'off') {
      void playText(reply)
    }
  }

  const resizeComposer = (element: HTMLTextAreaElement) => {
    element.style.height = 'auto'
    element.style.height = `${Math.min(element.scrollHeight, COMPOSER_MAX_HEIGHT_PX)}px`
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    const content = draft.trim()
    if (content === '' || sending || activeId === null) return
    setDraft('')
    if (composerRef.current) composerRef.current.style.height = ''
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

  const voiceSupported =
    typeof MediaRecorder !== 'undefined' &&
    typeof navigator.mediaDevices?.getUserMedia === 'function'
  const draftEmpty = draft.trim() === ''
  const listErrors = [createError, deleteError, listError].filter((m): m is string => m !== null)

  return (
    <main className="agent-page mobile-agent">
      {activeId === null ? (
        <div className="magent-empty">
          <GlitchMark size={34} />
          <h1>Agent</h1>
          <p>
            Ask for anything your projects need — “create a task…”, “what’s
            overdue?”, “plan my day”. Every change the agent makes is audited
            and undoable from the trash.
          </p>
          {listErrors.map((message) => (
            <p key={message} role="alert" className="error">
              {message}
            </p>
          ))}
          <button type="button" className="magent-empty-start" onClick={() => fireAndForget(startConversation())}>
            <MessageSquarePlus size={17} aria-hidden="true" />
            Start a conversation
          </button>
          {conversations.length > 0 && (
            <button type="button" className="magent-empty-recents" onClick={() => setSheet({ kind: 'conversations' })}>
              {conversations.length} recent {conversations.length === 1 ? 'conversation' : 'conversations'}
            </button>
          )}
        </div>
      ) : (
        <>
          <div className="magent-title-row">
            <button
              type="button"
              className="magent-title"
              aria-label="Switch conversation"
              aria-haspopup="dialog"
              onClick={() => setSheet({ kind: 'conversations' })}
            >
              <span>{title}</span>
              <ChevronDown size={15} aria-hidden="true" />
            </button>
            <button
              type="button"
              className="magent-more"
              aria-label="Conversation actions"
              aria-haspopup="dialog"
              disabled={active === null}
              onClick={() => active && setSheet({ kind: 'actions', target: active })}
            >
              <MoreHorizontal size={18} aria-hidden="true" />
            </button>
          </div>

          {listErrors.map((message) => (
            <p key={message} role="alert" className="error magent-error">
              {message}
            </p>
          ))}
          {error && <p role="alert" className="error magent-error">{error}</p>}

          <ul className="magent-thread" aria-label="Agent chat">
            {loading && <li className="magent-thread-note">Loading conversation…</li>}
            {threadHasMore && (
              <li className="magent-thread-older">
                <button type="button" onClick={() => fireAndForget(loadOlder())} disabled={loadingOlder}>
                  {loadingOlder ? 'Loading…' : 'Load older messages'}
                </button>
              </li>
            )}
            {detail?.messages.map((message) =>
              message.role === 'user' ? (
                <UserTurn key={message.id} message={message} />
              ) : (
                <AgentTurn key={message.id} message={message} />
              ),
            )}
            {pendingText !== null && (
              <>
                <li className="magent-turn magent-turn--user">
                  <div className="magent-bubble">{pendingText}</div>
                </li>
                <li className="magent-turn magent-turn--agent">
                  <div className="magent-label">
                    <GlitchMark size={18} />
                    <span>Agent</span>
                  </div>
                  <div className="magent-working" role="status">
                    <div>
                      <span className="magent-working-line">Working — reading your projects</span>
                      {elapsed >= SLOW_RUN_AFTER_S && (
                        <span className="magent-working-sub">First run today can take a minute</span>
                      )}
                    </div>
                    <span className="magent-clock">{formatElapsed(elapsed)}</span>
                  </div>
                </li>
              </>
            )}
            {!loading && detail?.messages.length === 0 && pendingText === null && (
              <li className="magent-thread-note">
                What should the agent do? It can read and change your projects and tasks.
              </li>
            )}
            {/* Autoscroll sentinel — inside the scroll container, or
                scrollIntoView can't scroll the thread. */}
            <li ref={threadEndRef} className="magent-thread-sentinel" aria-hidden="true" />
          </ul>

          <form className="magent-composer" onSubmit={(e) => void submit(e)}>
            <textarea
              ref={composerRef}
              value={draft}
              rows={1}
              onChange={(e) => {
                setDraft(e.target.value)
                resizeComposer(e.target)
              }}
              onKeyDown={onComposerKeyDown}
              placeholder={sending ? 'The agent is working…' : 'Message the agent…'}
              aria-label="Message the agent"
              disabled={sending}
            />
            {draftEmpty && voiceSupported ? (
              <MicButton onTranscript={onTranscript} disabled={sending} />
            ) : (
              <button
                type="submit"
                className="magent-send"
                disabled={sending || draftEmpty}
                aria-label="Send message"
              >
                <SendHorizontal size={19} aria-hidden="true" />
              </button>
            )}
          </form>
        </>
      )}

      {undo && (
        <div className="magent-undo-bar" role="status">
          <span>Deleted · {undo.title ?? 'New conversation'}</span>
          <button type="button" onClick={() => fireAndForget(runUndo())}>
            Undo
          </button>
        </div>
      )}

      {sheet?.kind === 'conversations' && (
        <ConversationsSheet
          conversations={conversations}
          activeId={activeId}
          loading={listLoading}
          loadingMore={loadingMore}
          hasMore={hasMore}
          busyId={sending ? activeId : null}
          errors={listErrors}
          onOpen={openConversation}
          onNew={() => fireAndForget(startConversation())}
          onLoadMore={() => fireAndForget(loadMore())}
          onDelete={(target) => fireAndForget(deleteConversation(target))}
          onActions={(target) => setSheet({ kind: 'actions', target })}
          onClose={() => setSheet(null)}
        />
      )}

      {sheet?.kind === 'actions' && (
        <ActionsSheet
          target={sheet.target}
          busy={sending && sheet.target.id === activeId}
          voiceOutput={voiceOutput}
          onRename={() => {
            setRenameError(null)
            setSheet({ kind: 'rename', target: sheet.target })
          }}
          onToggleVoice={toggleVoiceOutput}
          onDelete={() => fireAndForget(deleteConversation(sheet.target))}
          onClose={() => setSheet(null)}
        />
      )}

      {sheet?.kind === 'rename' && (
        <RenameSheet
          target={sheet.target}
          error={renameError}
          onSave={(nextTitle) => fireAndForget(saveTitle(sheet.target, nextTitle))}
          onClose={() => setSheet(null)}
        />
      )}
    </main>
  )
}

function UserTurn({ message }: { message: AgentMessage }) {
  return (
    <li className="magent-turn magent-turn--user">
      <div className="magent-bubble">{message.content}</div>
      <span className="magent-time">{formatRelative(message.created_at)}</span>
    </li>
  )
}

/** The agent's reply is not a bubble: label line, trajectory, then the body
 * across the full column. One speaker gets a bubble, and it isn't the page. */
function AgentTurn({ message }: { message: AgentMessage }) {
  const stopReason = message.stop_reason
  return (
    <li className="magent-turn magent-turn--agent">
      <div className="magent-label">
        <GlitchMark size={18} />
        <span>Agent · {formatRelative(message.created_at)}</span>
      </div>
      {message.tool_calls !== null && message.tool_calls.length > 0 && (
        <MobileTrajectory messageId={message.id} records={message.tool_calls} />
      )}
      {message.content !== null && (
        <div className="magent-body">
          <ReactMarkdown components={MARKDOWN_COMPONENTS}>{message.content}</ReactMarkdown>
        </div>
      )}
      {stopReason !== null && stopReason !== 'completed' && (
        <p className="magent-stop-note" role="status">
          {STOP_FALLBACK[stopReason]}
        </p>
      )}
    </li>
  )
}
