import { describe, expect, it } from 'vitest'
import type { Trash } from '../../../types/trash'
import {
  EMPTY_TRASH_FILTERS,
  deletedMeta,
  matchingTrashRows,
  purgeSelectionScope,
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
