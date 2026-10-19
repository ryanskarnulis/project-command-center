import { useCallback, useEffect, useRef, useState } from 'react'
import { apiErrorMessage } from '../../api/errorMessage'
import { cancelRun, getConversation, postMessage } from '../../api/agent'
import type { ConversationDetail, MessageExchange } from '../../types/agent'
import { refreshErrorMessage, sendErrorMessage } from './errorMessage'
import { setRunStopping, startRun, useInFlightRun } from './inFlightRunStore'

export interface UseConversation {
  detail: ConversationDetail | null
  loading: boolean
  error: string | null
  /** Whether messages older than the loaded page exist (#244). */
  hasMore: boolean
  /** True while `loadOlder` is in flight. */
  loadingOlder: boolean
  /** Prepend the page immediately older than the oldest loaded message. */
  loadOlder: () => Promise<void>
  /** The optimistic user bubble + "working" indicator while a run is in flight. */
  pendingText: string | null
  /** Wall-clock start of the in-flight run (null when idle) — the working
   * block's clock counts from it, so it survives a remount (#353). */
  pendingSince: number | null
  /** The thread on screen already holds the in-flight user turn (it was
   * fetched mid-run, after a remount), so the optimistic bubble is redundant
   * and the pending block shows only the working status. */
  pendingUserLanded: boolean
  /** Resolves to the assistant's reply text on success (possibly ''), or
   * null when the run failed — voice uses the text to speak the reply. */
  send: (content: string) => Promise<string | null>
  /** Ask the in-flight run to stop (#324). The pending `send` then settles
   * with the server's truthful `cancelled` turn, refetched like any other. */
  stop: () => Promise<void>
  /** True from a Stop click until the stopped run lands. */
  stopping: boolean
}

// All state is tagged with the conversation it belongs to and the exposed
// values are derived by id match — switching conversations "resets" the view
// without effect-time setState, and stale data is never shown for the wrong
// thread. Tagging alone is not enough for writes, though: the state is a
// single slot, so a slow send's refetch landing after the user navigated away
// would overwrite the thread now on screen (which would then sit on "Loading"
// forever, since its own effect does not rerun). Every write is therefore also
// guarded against the id currently routed to (`currentIdRef`).
interface Tagged<T> {
  id: number
  value: T
}

function forId<T>(tagged: Tagged<T> | null, id: number | null): T | null {
  return tagged !== null && tagged.id === id ? tagged.value : null
}

/** How often a page that did not start the run on screen re-reads the thread
 * until the server says the run has landed (#358). The run is synchronous and
 * nothing streams, so this is only "when does the reply appear" — a couple of
 * seconds' lag is invisible against a multi-second model call. */
export const REMOTE_RUN_POLL_MS = 2000

/** A server-reported run start as a wall-clock origin, or null if absent or
 * unparseable (the clock then counts from the mount). */
function parseStartedAt(value: string | null): number | null {
  if (value === null) return null
  const parsed = Date.parse(value)
  return Number.isNaN(parsed) ? null : parsed
}

/** The thread with the exchange the server just committed appended — the
 * fallback for when the post-send refetch fails (#233).
 *
 * Message ids are unique and server-assigned, so skipping the ones already
 * present keeps this idempotent if the pre-send thread somehow contains them.
 */
function withExchange(
  detail: ConversationDetail,
  exchange: MessageExchange,
): ConversationDetail {
  const seen = new Set(detail.messages.map((m) => m.id))
  const added = [exchange.user_message, exchange.assistant_message].filter(
    (m) => !seen.has(m.id),
  )
  return added.length === 0
    ? detail
    : {
        ...detail,
        messages: [...detail.messages, ...added],
        message_count: detail.message_count + added.length,
        // The exchange in hand is the run's outcome, so it has landed.
        run_in_flight: false,
        run_started_at: null,
      }
}

/** A freshly fetched newest page, with any older pages the user had already
 * loaded kept in front of it (#244).
 *
 * Without this, every post-send refetch — which asks for the default newest
 * page — would silently collapse a thread the user had scrolled back through.
 * `has_more` then belongs to the oldest message on screen, not to the fresh
 * page, so the "load older" affordance stays correct.
 */
function mergeOlder(
  previous: ConversationDetail | null,
  fresh: ConversationDetail,
): ConversationDetail {
  if (previous === null || previous.messages.length === 0) return fresh
  const freshIds = new Set(fresh.messages.map((m) => m.id))
  const oldestFresh = fresh.messages[0]?.id
  const older = previous.messages.filter(
    (m) => !freshIds.has(m.id) && (oldestFresh === undefined || m.id < oldestFresh),
  )
  if (older.length === 0) return fresh
  return {
    ...fresh,
    messages: [...older, ...fresh.messages],
    has_more: previous.has_more,
  }
}

/**
 * One open conversation: history plus the send → loop → exchange round trip.
 *
 * The run is synchronous server-side (no streaming in v1), so `send` keeps an
 * optimistic user bubble + progress state up while it waits, then refetches
 * the thread from the server — the source of truth for what was persisted
 * (on failure the user turn may or may not have landed; the refetch shows
 * exactly what did). `onExchange` lets the page refresh the sidebar's
 * recency/title.
 */
export function useConversation(
  conversationId: number | null,
  onExchange?: () => void,
): UseConversation {
  const [loaded, setLoaded] = useState<Tagged<ConversationDetail> | null>(null)
  const [errorState, setErrorState] = useState<Tagged<string> | null>(null)
  const [olderPending, setOlderPending] = useState<Tagged<true> | null>(null)
  // The in-flight run (its text, clock origin and Stop state) lives in an
  // external store, not here: navigating away mid-run unmounts this hook,
  // and the page that comes back must still show the working block and Stop,
  // then the reply when it lands (#353).
  const run = useInFlightRun(conversationId)
  // Conversations whose run *this instance* started: its own `send` refetches
  // when the run lands. Any other run on screen was adopted after a remount
  // and is refetched by the effect below.
  const ownRunsRef = useRef(new Set<number>())
  // A Stop requested on a run only the server knows about (see `remoteRun`).
  const [remoteStopState, setRemoteStopState] = useState<Tagged<true> | null>(null)

  // The conversation the hook is currently routed to, readable from async
  // continuations that started before a navigation.
  const currentIdRef = useRef(conversationId)
  useEffect(() => {
    currentIdRef.current = conversationId
  }, [conversationId])

  const detail = forId(loaded, conversationId)
  const error = forId(errorState, conversationId)
  // A run this page knows of only because the server said so (#358): the page
  // was reloaded mid-run, or the run was started from another tab. The store
  // above stays the fast path for the tab (or remounted page) that started it;
  // this one is shown from the detail and polled until it lands. Its user turn
  // is already in the thread (the server commits it before the run).
  const remoteRun = run === null && detail?.run_in_flight === true
  const pendingText = run?.text ?? (remoteRun ? '' : null)
  const pendingSince =
    run?.startedAt ?? (remoteRun ? parseStartedAt(detail.run_started_at) : null)
  const afterId = run?.afterId ?? null
  const pendingUserLanded =
    remoteRun ||
    (afterId !== null &&
      detail !== null &&
      detail.messages.some((m) => m.role === 'user' && m.id > afterId))
  const loadingOlder = forId(olderPending, conversationId) === true
  const stopping =
    run?.stopping ?? (remoteRun && forId(remoteStopState, conversationId) === true)
  const loading = conversationId !== null && detail === null && error === null
  const hasMore = detail?.has_more ?? false

  useEffect(() => {
    if (conversationId === null) return
    let active = true
    getConversation(conversationId)
      .then((data) => {
        if (!active) return
        setLoaded({ id: conversationId, value: data })
        // A load that succeeds owns this conversation's outcome: drop the
        // failure it supersedes, or the recovered thread renders under a
        // stale alert (#233). Only this conversation's error, though — the
        // slot may hold one belonging to the thread the user came from.
        setErrorState((prev) => (prev !== null && prev.id === conversationId ? null : prev))
      })
      .catch((e: unknown) => {
        if (active) {
          setErrorState({
            id: conversationId,
            value: e instanceof Error ? e.message : 'Failed to load conversation',
          })
        }
      })
    return () => {
      active = false
    }
  }, [conversationId])

  // A run this instance did not start — the page remounted while it was in
  // flight — still has to land on screen: wait for its sender's refetch and
  // take that thread (or fetch one if that refetch failed). The mount-time
  // load above ran mid-run and cannot show the reply.
  const landed = run?.landed
  // `send` reads the thread on screen through this, so its identity does not
  // churn with every load.
  const loadedRef = useRef(loaded)
  useEffect(() => {
    loadedRef.current = loaded
  }, [loaded])
  const onExchangeRef = useRef(onExchange)
  useEffect(() => {
    onExchangeRef.current = onExchange
  }, [onExchange])
  useEffect(() => {
    if (conversationId === null || landed === undefined) return
    if (ownRunsRef.current.has(conversationId)) return
    let active = true
    landed
      .then(async (fresh) => {
        const data = fresh ?? (await getConversation(conversationId))
        if (!active) return
        setLoaded((prev) => ({
          id: conversationId,
          value: mergeOlder(prev !== null && prev.id === conversationId ? prev.value : null, data),
        }))
        setErrorState((prev) => (prev !== null && prev.id === conversationId ? null : prev))
        onExchangeRef.current?.()
      })
      .catch((e: unknown) => {
        if (active) setErrorState({ id: conversationId, value: refreshErrorMessage(e) })
      })
    return () => {
      active = false
    }
  }, [conversationId, landed])

  // A run only the server knows about: re-read the thread until the detail says
  // it has landed — that read already holds the reply — then refresh the
  // sidebar like any finished exchange. A failed read is surfaced and retried
  // on the next tick, so a brief outage does not strand the working block.
  useEffect(() => {
    if (conversationId === null || !remoteRun) return
    let active = true
    let timer: ReturnType<typeof setTimeout> | undefined
    const schedule = () => {
      timer = setTimeout(() => {
        getConversation(conversationId)
          .then((data) => {
            if (!active) return
            setLoaded((prev) => ({
              id: conversationId,
              value: mergeOlder(prev !== null && prev.id === conversationId ? prev.value : null, data),
            }))
            setErrorState((prev) => (prev !== null && prev.id === conversationId ? null : prev))
            if (data.run_in_flight) {
              schedule()
            } else {
              setRemoteStopState((prev) => (prev !== null && prev.id === conversationId ? null : prev))
              onExchangeRef.current?.()
            }
          })
          .catch((e: unknown) => {
            if (!active) return
            setErrorState({ id: conversationId, value: refreshErrorMessage(e) })
            schedule()
          })
      }, REMOTE_RUN_POLL_MS)
    }
    schedule()
    return () => {
      active = false
      clearTimeout(timer)
    }
  }, [conversationId, remoteRun])

  const loadOlder = useCallback(async (): Promise<void> => {
    if (conversationId === null) return
    const oldest = loaded?.id === conversationId ? loaded.value.messages[0] : undefined
    if (oldest === undefined || !loaded?.value.has_more) return
    setOlderPending({ id: conversationId, value: true })
    try {
      const page = await getConversation(conversationId, { before_id: oldest.id })
      // Same single-slot guard as `send`: a page that lands after the user
      // navigated away must not touch the thread now on screen.
      if (currentIdRef.current === conversationId) {
        setLoaded((prev) =>
          prev !== null && prev.id === conversationId
            ? { id: conversationId, value: mergeOlder(page, prev.value) }
            : prev,
        )
      }
    } catch (e: unknown) {
      if (currentIdRef.current === conversationId) {
        setErrorState({
          id: conversationId,
          value: e instanceof Error ? e.message : 'Failed to load older messages',
        })
      }
    } finally {
      setOlderPending((prev) =>
        prev !== null && prev.id === conversationId ? null : prev,
      )
    }
  }, [conversationId, loaded])

  const send = useCallback(
    async (content: string): Promise<string | null> => {
      if (conversationId === null) return null
      setErrorState(null)
      const onScreen = forId(loadedRef.current, conversationId)
      const afterId = onScreen === null ? null : (onScreen.messages.at(-1)?.id ?? 0)
      ownRunsRef.current.add(conversationId)
      const land = startRun(conversationId, content, afterId)
      let fresh: ConversationDetail | null = null
      let reply: string | null = null
      let committed: MessageExchange | null = null
      try {
        committed = await postMessage(conversationId, content)
        reply = committed.assistant_message.content ?? ''
      } catch (e: unknown) {
        if (currentIdRef.current === conversationId) {
          setErrorState({ id: conversationId, value: sendErrorMessage(e) })
        }
      }
      // Success or not, the server is the source of truth for the thread
      // (on failure the user turn may or may not have been persisted).
      try {
        fresh = await getConversation(conversationId)
        const thread = fresh
        // Only write if the user is still on this conversation — otherwise the
        // single slot would clobber whatever thread is on screen now. The
        // refetch asks for the newest page, so any older pages already loaded
        // are merged back in rather than collapsing the thread (#244).
        if (currentIdRef.current === conversationId) {
          setLoaded((prev) => ({
            id: conversationId,
            value: mergeOlder(prev !== null && prev.id === conversationId ? prev.value : null, thread),
          }))
        }
      } catch (e: unknown) {
        // Same guard: a refetch that lost the race must not touch the thread
        // the user navigated to (#99).
        if (currentIdRef.current === conversationId) {
          if (committed !== null) {
            // The run landed and only the reload failed, so the exchange in
            // hand is the best available truth — without it the optimistic
            // bubble clears back to the pre-send thread and the turn looks
            // lost, inviting a duplicate send (#233). Merge only into this
            // conversation's own thread; there is nothing to append to when
            // the slot holds another id (or the initial load never landed),
            // and inventing a detail from two messages would hide the rest
            // of the history.
            const exchange = committed
            setLoaded((prev) =>
              prev !== null && prev.id === conversationId
                ? { id: conversationId, value: withExchange(prev.value, exchange) }
                : prev,
            )
            setErrorState({ id: conversationId, value: refreshErrorMessage(e) })
          }
          // Otherwise the send error is already surfaced and is the more
          // relevant one — a failed reload is a footnote to a failed run.
        }
      }
      // Hand the fresh thread to any remounted instance waiting on this run,
      // then clear it from the store (which drops the working block).
      await land(fresh)
      ownRunsRef.current.delete(conversationId)
      onExchange?.()
      return reply
    },
    [conversationId, onExchange],
  )

  // Stop only signals the server; the run's own `send` above still owns the
  // outcome — the POST returns 200 with a `cancelled` turn and the usual
  // refetch shows it, tool calls that already ran (and their undo) included.
  // A run only the server knows about (#358) has no store entry to mark, so its
  // Stop state lives here; the poll above clears it when the run lands.
  const stop = useCallback(async (): Promise<void> => {
    if (conversationId === null) return
    let clear: () => void
    if (remoteRun) {
      setRemoteStopState({ id: conversationId, value: true })
      clear = () =>
        setRemoteStopState((prev) => (prev !== null && prev.id === conversationId ? null : prev))
    } else {
      setRunStopping(conversationId, true)
      clear = () => setRunStopping(conversationId, false)
    }
    try {
      const { cancel_requested } = await cancelRun(conversationId)
      // Nothing was running server-side: the run is already landing, or its
      // request has not reached the run lock yet. Nothing was recorded, so
      // re-arm the button rather than claim a stop that will not happen.
      if (!cancel_requested) clear()
    } catch (e: unknown) {
      clear()
      if (currentIdRef.current === conversationId) {
        setErrorState({
          id: conversationId,
          value: apiErrorMessage(e, 'Could not stop the run'),
        })
      }
    }
  }, [conversationId, remoteRun])

  return {
    detail,
    loading,
    error,
    hasMore,
    loadingOlder,
    loadOlder,
    pendingText,
    pendingSince,
    pendingUserLanded,
    send,
    stop,
    stopping,
  }
}
