import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import type { AgentMessage } from '../../types/agent'
import { MessageBubble } from './MessageBubble'

vi.mock('../../api/tasks', () => ({
  deleteTask: vi.fn(),
  markTaskDone: vi.fn(),
  reopenTask: vi.fn(),
  restoreTask: vi.fn(),
}))

vi.mock('../../api/projects', () => ({
  deleteProject: vi.fn(),
  restoreProject: vi.fn(),
}))

function message(overrides: Partial<AgentMessage>): AgentMessage {
  return {
    id: 1,
    conversation_id: 1,
    role: 'assistant',
    content: 'hello',
    tool_calls: null,
    stop_reason: 'completed',
    created_at: '2026-07-11T10:00:00Z',
    ...overrides,
  }
}

function renderBubble(msg: AgentMessage) {
  return render(
    <MemoryRouter>
      <ul>
        <MessageBubble message={msg} />
      </ul>
    </MemoryRouter>,
  )
}

describe('MessageBubble', () => {
  it('renders assistant replies as markdown', () => {
    renderBubble(
      message({
        content: 'Done — **two** tasks:\n\n- water plants\n- buy `soil`',
      }),
    )

    const bold = screen.getByText('two')
    expect(bold.tagName).toBe('STRONG')
    const items = screen.getAllByRole('listitem')
    expect(items.some((li) => li.textContent === 'water plants')).toBe(true)
    const code = screen.getByText('soil')
    expect(code.tagName).toBe('CODE')
  })

  it('opens markdown links in a new tab instead of navigating the SPA', () => {
    renderBubble(message({ content: 'See [the docs](https://example.com).' }))

    const link = screen.getByRole('link', { name: 'the docs' })
    expect(link).toHaveAttribute('href', 'https://example.com')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
  })

  it('leaves user bubbles as literal plain text', () => {
    renderBubble(message({ role: 'user', content: 'this is **not** markdown' }))

    expect(screen.getByText('this is **not** markdown')).toBeInTheDocument()
    expect(screen.queryByText('not')).not.toBeInTheDocument()
  })
})
