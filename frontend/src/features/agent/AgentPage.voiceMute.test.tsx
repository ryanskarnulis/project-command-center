import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getConversation, listConversations, postMessage } from '../../api/agent'
import type { AgentMessage, Conversation, ConversationDetail } from '../../types/agent'
import { RequireRouteId } from '../../routes/RequireRouteId'
import type { SpeechEvents, Vad } from '../../voice/vad'
import { AgentPage } from './AgentPage'
import { dismissDeleteUndo } from './deleteUndoStore'
import { resetInFlightRuns } from './inFlightRunStore'

/* #366 — muting spoken replies stops the reply already playing, and the
 * hands-free loop (#257) that was waiting for that reply reopens the mic.
 *
 * Unlike mobile/MobileAgent.test.tsx, the real vendored MicButton and tts run here:
 * the bug lives in how the mute, the shared audio element and the loop's
 * audioIdle() wait meet. Only the browser's audio machinery is stood in for —
 * the VAD, and an <audio> element whose clip plays until it is paused. */

vi.mock('../../api/agent', () => ({
  cancelRun: vi.fn(),
  createConversation: vi.fn(),
  deleteConversation: vi.fn(),
  getConversation: vi.fn(),
  listConversations: vi.fn(),
  postMessage: vi.fn(),
  renameConversation: vi.fn(),
  restoreConversation: vi.fn(),
}))
const createVad = vi.hoisted(() => vi.fn())
const pause = vi.hoisted(() => vi.fn())
const resume = vi.hoisted(() => vi.fn())
vi.mock('../../voice/vad', () => ({ createVad }))
vi.mock('../../voice/wav', () => ({ encodeWav: () => new Blob(['wav']) }))

const vad: Vad = { pause, resume, destroy: vi.fn() }

/** The one shared element tts.ts plays through. A clip never ends on its own
 * — a long reply, still being spoken when the user mutes. */
class FakeAudio {
  /** tts.ts creates its element once per module, so it outlives a test. */
  static instances: FakeAudio[] = []
  src = ''
  paused = true
  onended: (() => void) | null = null
  onerror: (() => void) | null = null
  onplaying: (() => void) | null = null
  constructor() {
    FakeAudio.instances.push(this)
  }
  play(): Promise<void> {
    this.paused = false
    queueMicrotask(() => this.onplaying?.())
    return Promise.resolve()
  }
  pause(): void {
    this.paused = true
  }
}

function message(overrides: Partial<AgentMessage>): AgentMessage {
  return {
    id: 1,
    conversation_id: 8,
    role: 'user',
    content: 'hello',
    tool_calls: null,
    stop_reason: null,
    created_at: '2026-09-10T10:00:00Z',
    ...overrides,
  }
}

const conversation: Conversation = {
  id: 8,
  title: "What's overdue in Homelab?",
  created_at: '2026-09-10T09:00:00Z',
  updated_at: '2026-09-10T10:00:00Z',
}
const asked = message({ id: 1, content: "what's overdue" })
const answered = message({ id: 2, role: 'assistant', content: 'Two tasks are overdue.', stop_reason: 'completed' })
const detail: ConversationDetail = {
  ...conversation,
  messages: [],
  message_count: 0,
  has_more: false,
  run_in_flight: false,
  run_started_at: null,
}

function renderAgent() {
  return render(
    <MemoryRouter initialEntries={['/agent/8']}>
      <Routes>
        <Route
          path="/agent/:conversationId"
          element={
            <RequireRouteId param="conversationId">
              <AgentPage />
            </RequireRouteId>
          }
        />
      </Routes>
    </MemoryRouter>,
  )
}

const mic = () => document.querySelector<HTMLButtonElement>('.mic-button')!

/** Start a hands-free session, say something, and wait until the agent's
 * reply is being spoken — the mic paused, waiting on audioIdle(). */
async function speakingReply(): Promise<FakeAudio> {
  let events: SpeechEvents | undefined
  createVad.mockImplementation(async (e: SpeechEvents) => {
    events = e
    return vad
  })
  renderAgent()
  await screen.findByText('What should the agent do? It can read and change your projects and tasks.')

  await userEvent.click(screen.getByRole('button', { name: 'Start voice conversation' }))
  await waitFor(() => expect(mic()).toHaveClass('mic-listening'))
  resume.mockClear()

  vi.mocked(getConversation).mockResolvedValue({ ...detail, messages: [asked, answered], message_count: 2 })
  act(() => events?.onSpeechEnd(new Float32Array(16)))

  const audio = FakeAudio.instances[0]
  await waitFor(() => expect(audio.src).toBe('blob:reply'))
  await waitFor(() => expect(audio.paused).toBe(false))
  expect(mic()).toHaveClass('mic-working')
  expect(resume).not.toHaveBeenCalled()
  return audio
}

describe('muting spoken replies mid-reply (#366)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    dismissDeleteUndo()
    resetInFlightRuns()
    vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() })))
    vi.stubGlobal('Audio', FakeAudio)
    vi.stubGlobal('MediaRecorder', class {})
    Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia: vi.fn() } })
    vi.stubGlobal('URL', { ...URL, createObjectURL: vi.fn(() => 'blob:reply'), revokeObjectURL: vi.fn() })
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL) =>
        Promise.resolve(
          String(input).includes('/api/voice/speak')
            ? ({ ok: true, blob: () => Promise.resolve(new Blob(['ogg'])) } as Response)
            : ({ ok: true, json: () => Promise.resolve({ text: "what's overdue" }) } as Response),
        ),
      ),
    )
    vi.mocked(listConversations).mockResolvedValue([conversation])
    vi.mocked(getConversation).mockResolvedValue(detail)
    vi.mocked(postMessage).mockResolvedValue({ user_message: asked, assistant_message: answered })
    HTMLDialogElement.prototype.showModal ??= function (this: HTMLDialogElement) { this.setAttribute('open', '') }
    HTMLDialogElement.prototype.close ??= function (this: HTMLDialogElement) { this.removeAttribute('open') }
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.useRealTimers()
    Reflect.deleteProperty(navigator, 'mediaDevices')
  })

  it('the ⋯ sheet switch stops the reply being spoken, and the hands-free mic reopens', async () => {
    const audio = await speakingReply()

    await userEvent.click(screen.getByRole('button', { name: 'Conversation actions' }))
    const sheet = await screen.findByRole('dialog', { name: "What's overdue in Homelab?" })
    await userEvent.click(within(sheet).getByRole('switch', { name: /Spoken replies/ }))

    expect(localStorage.getItem('agent-voice-output')).toBe('off')
    expect(audio.paused).toBe(true)
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:reply')
    // The loop was waiting on that reply; the stop settled it, so the VAD is
    // listening again rather than left paused.
    await waitFor(() => expect(resume).toHaveBeenCalled())
    await waitFor(() => expect(mic()).toHaveClass('mic-listening'))
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('holding the mic stops the reply being spoken without ending the session', async () => {
    const audio = await speakingReply()
    vi.useFakeTimers({ shouldAdvanceTime: true })

    const button = mic()
    fireEvent.pointerDown(button, { button: 0, clientX: 300, clientY: 700, pointerId: 1 })
    await act(async () => { await vi.advanceTimersByTimeAsync(600) })
    fireEvent.pointerUp(button, { clientX: 300, clientY: 700, pointerId: 1 })
    fireEvent.click(button, { detail: 1 })

    expect(localStorage.getItem('agent-voice-output')).toBe('off')
    expect(audio.paused).toBe(true)
    await waitFor(() => expect(resume).toHaveBeenCalled())
    // The swallowed release click didn't end the hands-free session either.
    await waitFor(() => expect(mic()).toHaveClass('mic-listening'))
    expect(vad.destroy).not.toHaveBeenCalled()
  })

  it('desktop: the mute button stops the reply being spoken too', async () => {
    vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })))
    const audio = await speakingReply()

    await userEvent.click(screen.getByRole('button', { name: 'Mute spoken replies' }))

    expect(audio.paused).toBe(true)
    await waitFor(() => expect(resume).toHaveBeenCalled())
    await waitFor(() => expect(mic()).toHaveClass('mic-listening'))
  })
})
