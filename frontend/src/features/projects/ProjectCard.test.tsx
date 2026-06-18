import { type ReactNode } from 'react'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'
import type { Project } from '../../types/project'
import { ProjectCard } from './ProjectCard'

const project: Project = {
  id: 5,
  name: 'Firewall',
  description: 'Edge hardening',
  system_key: null,
  is_protected: false,
  created_at: '2026-06-01T00:00:00Z',
  updated_at: '2026-06-01T00:00:00Z',
}

function renderCard(p: Project = project, actions?: ReactNode) {
  return render(
    <MemoryRouter>
      <ProjectCard project={p} actions={actions} />
    </MemoryRouter>,
  )
}

describe('ProjectCard', () => {
  afterEach(cleanup)

  it('renders name + description and links to the detail hub', () => {
    renderCard()
    expect(screen.getByText('Firewall')).toBeInTheDocument()
    expect(screen.getByText('Edge hardening')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Firewall' })).toHaveAttribute(
      'href',
      '/projects/5',
    )
  })

  it('shows a Protected badge for protected projects', () => {
    renderCard({ ...project, is_protected: true })
    expect(screen.getByText('Protected')).toBeInTheDocument()
  })

  it('renders supplied actions', () => {
    renderCard(project, <button type="button">Edit</button>)
    expect(screen.getByRole('button', { name: 'Edit' })).toBeInTheDocument()
  })

  it('renders stats badges when provided', () => {
    render(
      <MemoryRouter>
        <ProjectCard
          project={project}
          stats={{ open: 2, done: 2, progress: 0.5, status: { label: 'On Track', tone: 'green' } }}
        />
      </MemoryRouter>,
    )
    expect(screen.getByText('On Track')).toBeInTheDocument()
    expect(screen.getByText('2 open · 2 done')).toBeInTheDocument()
  })
})
