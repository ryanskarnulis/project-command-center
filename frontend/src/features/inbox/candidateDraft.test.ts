import { describe, expect, it } from 'vitest'
import type { Task } from '../../types/task'
import { diffCandidateDraft } from './candidateDraft'

const candidate: Task = {
  id: 201,
  project_id: null,
  inbox_item_id: 10,
  parent_task_id: null,
  title: 'Fix the router',
  description: null,
  review_status: 'candidate',
  workflow_status: 'open',
  priority: 'medium',
  due_date: '2026-07-10',
  deferred_until: null,
  estimated_minutes: null,
  repeat_interval: null,
  recurrence_id: null,
  next_occurrence_date: null,
  confidence: 0.9,
  assignee_hint: 'ryan',
  created_at: '2026-06-01T10:00:00Z',
  updated_at: '2026-06-01T10:00:00Z',
  is_blocked: false,
  is_blocking: false,
  blocked_task_count: 0,
  has_subtasks: false,
}

describe('diffCandidateDraft', () => {
  it('returns undefined for an untouched draft', () => {
    expect(diffCandidateDraft(candidate, {}, 5)).toBeUndefined()
  })

  it('returns undefined when touched fields match the candidate', () => {
    const draft = { title: 'Fix the router', priority: 'medium' as const }
    expect(diffCandidateDraft(candidate, draft, 5)).toBeUndefined()
  })

  it('includes only changed fields', () => {
    const draft = { title: '  Fix the core router  ', priority: 'medium' as const }
    expect(diffCandidateDraft(candidate, draft, 5)).toEqual({
      title: 'Fix the core router',
    })
  })

  it('never emits an emptied title', () => {
    expect(diffCandidateDraft(candidate, { title: '   ' }, 5)).toBeUndefined()
  })

  it('maps cleared text fields to null', () => {
    const draft = { assignee_hint: '', due_date: null }
    expect(diffCandidateDraft(candidate, draft, 5)).toEqual({
      assignee_hint: null,
      due_date: null,
    })
  })

  it('diffs project against the effective baseline, not the candidate', () => {
    // Candidate project_id is null pre-accept; baseline is what the backend
    // would file it under (suggestion or General).
    expect(diffCandidateDraft(candidate, { project_id: 5 }, 5)).toBeUndefined()
    expect(diffCandidateDraft(candidate, { project_id: 7 }, 5)).toEqual({
      project_id: 7,
    })
    expect(diffCandidateDraft(candidate, { project_id: null }, 5)).toEqual({
      project_id: null,
    })
  })
})
