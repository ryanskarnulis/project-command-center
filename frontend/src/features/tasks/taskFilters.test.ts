import { describe, expect, it } from 'vitest'
import { EMPTY_FILTERS, filtersFromParams, paramsFromState, selectedPriorities, viewFromParams } from './taskFilters'

describe('task view URL state', () => {
  it('uses the caller default when the URL has no view', () => {
    expect(viewFromParams(new URLSearchParams(), 'board')).toBe('board')
    expect(viewFromParams(new URLSearchParams(), 'list')).toBe('list')
  })

  it('keeps an explicit list override for a board-first project view', () => {
    const params = paramsFromState(EMPTY_FILTERS, 'smart', 'list', false, 'board')

    expect(params.get('view')).toBe('list')
    expect(viewFromParams(params, 'board')).toBe('list')
  })

  it('round-trips multiple mobile priorities while retaining legacy single-priority links', () => {
    const filters = filtersFromParams(new URLSearchParams('priority=high,urgent,high,unknown'))
    expect(selectedPriorities(filters)).toEqual(['urgent', 'high'])
    const params = paramsFromState(filters, 'due_date', 'board', false, 'board')
    expect(params.get('priority')).toBe('urgent,high')
    expect(filtersFromParams(params)).toEqual(filters)
    expect(selectedPriorities(filtersFromParams(new URLSearchParams('priority=high')))).toEqual(['high'])
    expect(selectedPriorities(filtersFromParams(new URLSearchParams('priority=unknown')))).toEqual([])
  })
})
