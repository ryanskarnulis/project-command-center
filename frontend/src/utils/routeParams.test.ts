import { describe, expect, it } from 'vitest'
import { isValidRouteId, parseRouteId } from './routeParams'

describe('routeParams', () => {
  it('accepts digit-only positive ids', () => {
    expect(parseRouteId('1')).toBe(1)
    expect(parseRouteId('42')).toBe(42)
    expect(isValidRouteId('7')).toBe(true)
  })

  it('accepts the largest exactly representable id', () => {
    expect(parseRouteId(String(Number.MAX_SAFE_INTEGER))).toBe(
      Number.MAX_SAFE_INTEGER,
    )
  })

  it.each([
    '0',
    '-1',
    '1.5',
    '1e3',
    ' 1',
    '1 ',
    '+1',
    'nope',
    '',
    undefined,
    // Past Number.MAX_SAFE_INTEGER: `Number(...)` rounds these, so accepting
    // them would build an API URL for a different id (#182).
    '9007199254740993',
    // Beyond SQLite's signed 64-bit INTEGER range — the API answers 422.
    '999999999999999999999999',
  ])('rejects %s', (value) => {
    expect(parseRouteId(value)).toBeNull()
    expect(isValidRouteId(value)).toBe(false)
  })
})
