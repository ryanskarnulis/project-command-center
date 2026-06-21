import { describe, expect, it } from 'vitest'
import type { RepeatInterval } from '../types/task'
import { formatRepeatInterval, parseRepeatInterval } from './recurrence'

describe('parseRepeatInterval', () => {
  it('parses shorthand words', () => {
    expect(parseRepeatInterval('daily')).toEqual({ unit: 'day', every: 1 })
    expect(parseRepeatInterval('weekly')).toEqual({ unit: 'week', every: 1 })
    expect(parseRepeatInterval('monthly')).toEqual({ unit: 'month', every: 1 })
  })

  it('parses "every N <unit>"', () => {
    expect(parseRepeatInterval('every 2 weeks')).toEqual({ unit: 'week', every: 2 })
    expect(parseRepeatInterval('every 3 months')).toEqual({ unit: 'month', every: 3 })
    expect(parseRepeatInterval('every 5 days')).toEqual({ unit: 'day', every: 5 })
  })

  it('parses "N <unit>" and "every <unit>" forms', () => {
    expect(parseRepeatInterval('2 weeks')).toEqual({ unit: 'week', every: 2 })
    expect(parseRepeatInterval('every week')).toEqual({ unit: 'week', every: 1 })
  })

  it('is case- and whitespace-insensitive', () => {
    expect(parseRepeatInterval('  Every 2 Weeks ')).toEqual({ unit: 'week', every: 2 })
  })

  it('returns null for empty input', () => {
    expect(parseRepeatInterval('')).toBeNull()
    expect(parseRepeatInterval('   ')).toBeNull()
  })

  it('returns null for unrecognized input', () => {
    expect(parseRepeatInterval('sometimes')).toBeNull()
    expect(parseRepeatInterval('every fortnight')).toBeNull()
    expect(parseRepeatInterval('yearly')).toBeNull()
  })

  it('rejects out-of-range counts (backend allows 1-12)', () => {
    expect(parseRepeatInterval('every 0 weeks')).toBeNull()
    expect(parseRepeatInterval('every 13 months')).toBeNull()
  })
})

describe('formatRepeatInterval', () => {
  it('collapses every:1 to shorthand', () => {
    expect(formatRepeatInterval({ unit: 'day', every: 1 })).toBe('daily')
    expect(formatRepeatInterval({ unit: 'week', every: 1 })).toBe('weekly')
    expect(formatRepeatInterval({ unit: 'month', every: 1 })).toBe('monthly')
  })

  it('renders "every N <units>" otherwise', () => {
    expect(formatRepeatInterval({ unit: 'week', every: 2 })).toBe('every 2 weeks')
    expect(formatRepeatInterval({ unit: 'month', every: 3 })).toBe('every 3 months')
  })
})

describe('parse/format round-trips', () => {
  const cases: RepeatInterval[] = [
    { unit: 'day', every: 1 },
    { unit: 'week', every: 1 },
    { unit: 'month', every: 1 },
    { unit: 'day', every: 5 },
    { unit: 'week', every: 2 },
    { unit: 'month', every: 12 },
  ]
  for (const interval of cases) {
    it(`${formatRepeatInterval(interval)}`, () => {
      expect(parseRepeatInterval(formatRepeatInterval(interval))).toEqual(interval)
    })
  }
})
