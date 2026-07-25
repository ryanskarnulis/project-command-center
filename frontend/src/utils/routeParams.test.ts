import { describe, expect, it } from 'vitest'
import { isValidRouteId, parseRouteId } from './routeParams'

describe('routeParams', () => {
  it('accepts digit-only positive ids', () => {
    expect(parseRouteId('1')).toBe(1)
    expect(parseRouteId('42')).toBe(42)
    expect(isValidRouteId('7')).toBe(true)
  })

  it.each(['0', '-1', '1.5', '1e3', ' 1', '1 ', '+1', 'nope', '', undefined])(
    'rejects %s',
    (value) => {
      expect(parseRouteId(value)).toBeNull()
      expect(isValidRouteId(value)).toBe(false)
    },
  )
})
