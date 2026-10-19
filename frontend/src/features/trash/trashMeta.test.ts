import { describe, expect, it } from 'vitest'
import type { Trash } from '../../types/trash'
import {
  EMPTY_TRASH_FILTERS,
  deletedMeta,
  matchingTrashRows,
  purgeSelectionScope,
  retentionDaysLeft,
  retentionLine,
  retentionMeta,
  trashFilterChips,
  trashRows,
  type TrashRow,
} from './trashMeta'

const NOW = Date.parse('2026-09-10T12:00:00Z')
const daysAgo = (days: number) => new Date(NOW - days * 86_400_000).toISOString()

const trash: Trash = {
  projects: [
    {
      id: 1,
      name: 'Old Wiki Rewrite',
      description: null,
      system_key: null,
      sort_order: 0,
      is_protected: false,
      created_at: daysAgo(30),
      updated_at: daysAgo(2),
      deleted_at: daysAgo(2),
      archived_task_count: 3,
      purge_task_count: 4,
    },
  ],
  tasks: [
    {
      id: 5,
      project_id: 1,
      parent_task_id: null,
      estimated_minutes: 45,
      actual_minutes: null,
      repeat_interval: null,
      recurrence_id: null,
      next_occurrence_date: null,
      is_blocked: false,
      is_blocking: false,
      blocked_task_count: 0,
      has_subtasks: false,
      title: 'Write the old wiki export script',
      description: null,
      workflow_status: 'open',
      priority: 'low',
      due_date: '2026-08-04',
      deferred_until: null,
      created_at: daysAgo(30),
      updated_at: daysAgo(2),
      deleted_at: daysAgo(2),
    },
    {
      id: 6,
      project_id: 9,
      parent_task_id: null,
      estimated_minutes: null,
      actual_minutes: null,
      repeat_interval: null,
      recurrence_id: null,
      next_occurrence_date: null,
      is_blocked: false,
      is_blocking: false,
      blocked_task_count: 0,
      has_subtasks: false,
      title: 'Trial the second UPS unit',
      description: null,
      workflow_status: 'open',
      priority: 'medium',
      due_date: null,
      deferred_until: null,
      created_at: daysAgo(30),
      updated_at: daysAgo(0.25),
      deleted_at: daysAgo(0.25),
    },
  ],
}

describe('trashRows', () => {
  it('names a task’s project only when that project is itself in the trash', () => {
    const rows = trashRows(trash)
    expect(rows.tasks.map((row) => row.projectName)).toEqual(['Old Wiki Rewrite', null])
    expect(rows.projects[0]).toMatchObject({ archivedTaskCount: 3, purgeTaskCount: 4 })
  })
})

describe('deletedMeta', () => {
  const rows = trashRows(trash)

  it('leads with when, then what a restore brings back', () => {
    expect(deletedMeta(rows.projects[0], NOW)).toEqual(['Deleted 2 days ago', '3 tasks restore with it'])
    expect(deletedMeta(rows.tasks[0], NOW)).toEqual(['Deleted 2 days ago', 'Old Wiki Rewrite'])
  })

  it('prints one fact when there is nothing to add — never status, priority or due date', () => {
    expect(deletedMeta(rows.tasks[1], NOW)).toEqual(['Deleted 6 hours ago'])
  })

  it('uses the singular verb for one task', () => {
    const row: TrashRow = { ...rows.projects[0], archivedTaskCount: 1 }
    expect(deletedMeta(row, NOW)[1]).toBe('1 task restores with it')
  })
})

describe('filters', () => {
  const rows = trashRows(trash)

  it('narrows by type and by a case-insensitive title search', () => {
    expect(matchingTrashRows(rows, { search: '', type: 'projects' }).tasks).toEqual([])
    const wiki = matchingTrashRows(rows, { search: 'WIKI', type: 'all' })
    expect(wiki.projects).toHaveLength(1)
    expect(wiki.tasks.map((row) => row.id)).toEqual([5])
  })

  it('mirrors each applied filter as one chip whose removal drops just that filter', () => {
    expect(trashFilterChips(EMPTY_TRASH_FILTERS)).toEqual([])
    const chips = trashFilterChips({ search: 'wiki', type: 'tasks' })
    expect(chips.map((chip) => chip.label)).toEqual(['Tasks', '“wiki”'])
    expect(chips[0].next).toEqual({ search: 'wiki', type: 'all' })
    expect(chips[1].next).toEqual({ search: '', type: 'tasks' })
  })
})

describe('purgeSelectionScope', () => {
  const rows = trashRows(trash)

  it('counts every row the purge destroys, owned trashed tasks included', () => {
    expect(purgeSelectionScope([rows.projects[0], rows.tasks[1]])).toBe(
      '6 items (1 project, 1 task and 4 trashed tasks it owns)',
    )
  })

  it('is a bare count when the selection is all it destroys', () => {
    expect(purgeSelectionScope([rows.tasks[0], rows.tasks[1]])).toBe('2 items')
  })
})

describe('retention countdown (#321)', () => {
  const at = (days: number) => ({ expiresFrom: daysAgo(days) })

  it('counts whole days left, a part day counting as a day', () => {
    expect(retentionDaysLeft(at(0), 30, NOW)).toBe(30)
    expect(retentionDaysLeft(at(25), 30, NOW)).toBe(5)
    expect(retentionDaysLeft(at(25.5), 30, NOW)).toBe(5)
    expect(retentionDaysLeft(at(29.9), 30, NOW)).toBe(1)
  })

  it('clamps an expired row at 0 and says it goes today', () => {
    expect(retentionDaysLeft(at(31), 30, NOW)).toBe(0)
    expect(retentionMeta(at(31), 30, NOW)).toBe('Removed today')
    expect(retentionMeta(at(30), 30, NOW)).toBe('Removed today')
  })

  it('only speaks inside the last week — an exception, not a fact on every row', () => {
    expect(retentionMeta(at(22), 30, NOW)).toBeNull()
    expect(retentionMeta(at(23), 30, NOW)).toBe('7 days left')
    expect(retentionMeta(at(29.5), 30, NOW)).toBe('1 day left')
  })

  it('is silent when retention is off', () => {
    expect(retentionDaysLeft(at(400), 0, NOW)).toBeNull()
    expect(retentionMeta(at(400), 0, NOW)).toBeNull()
    expect(retentionLine(0)).toBeNull()
    expect(retentionLine(30)).toBe('removed after 30 days')
    expect(retentionLine(1)).toBe('removed after 1 day')
  })

  it('starts a task’s clock at its trashed project’s deletion when that came first', () => {
    const rows = trashRows({
      projects: [{ ...trash.projects[0], deleted_at: daysAgo(28) }],
      tasks: [{ ...trash.tasks[0], deleted_at: daysAgo(1) }, trash.tasks[1]],
    })
    // The project's purge takes every trashed task it owns.
    expect(retentionDaysLeft(rows.tasks[0], 30, NOW)).toBe(2)
    // A task whose project is not in the trash runs on its own clock.
    expect(rows.tasks[1].expiresFrom).toBe(trash.tasks[1].deleted_at)
  })
})
