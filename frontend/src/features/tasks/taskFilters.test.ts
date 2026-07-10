import { describe, expect, it } from 'vitest'
import { EMPTY_FILTERS, paramsFromState, viewFromParams } from './taskFilters'

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
})
