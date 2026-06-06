import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { AppShell } from './AppShell'

describe('AppShell', () => {
  it('keeps Inbox and Tasks out of the left navigation', () => {
    render(
      <MemoryRouter>
        <AppShell>
          <main>Page</main>
        </AppShell>
      </MemoryRouter>,
    )

    const nav = screen.getByLabelText('Primary navigation')
    expect(within(nav).queryByRole('link', { name: 'Inbox' })).not.toBeInTheDocument()
    expect(within(nav).queryByRole('link', { name: 'Tasks' })).not.toBeInTheDocument()
    expect(within(nav).getByRole('link', { name: 'Command Center' })).toBeInTheDocument()
    expect(within(nav).getByRole('link', { name: 'Projects' })).toBeInTheDocument()
    expect(within(nav).getByRole('link', { name: 'Training' })).toBeInTheDocument()
  })
})
