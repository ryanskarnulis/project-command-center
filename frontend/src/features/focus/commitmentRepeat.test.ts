import { describe, expect, it } from 'vitest'
import { commitmentRepeatInterval, formatCommitmentRepeat } from './commitmentRepeat'

describe('commitment cadences (#427)', () => {
  it('maps each form choice to the API shape', () => {
    expect(commitmentRepeatInterval('once')).toBeNull()
    expect(commitmentRepeatInterval('day')).toEqual({ unit: 'day', every: 1 })
    expect(commitmentRepeatInterval('weekdays')).toEqual({
      unit: 'week',
      every: 1,
      days: [0, 1, 2, 3, 4],
    })
  })

  it('names a cadence the way the backend does', () => {
    expect(formatCommitmentRepeat({ unit: 'day', every: 1 })).toBe('daily')
    expect(formatCommitmentRepeat({ unit: 'week', every: 2 })).toBe('every 2 weeks')
    expect(formatCommitmentRepeat({ unit: 'week', every: 1, days: [4, 3, 2, 1, 0] })).toBe(
      'weekdays',
    )
    expect(formatCommitmentRepeat({ unit: 'week', every: 1, days: [0, 2, 4] })).toBe(
      'weekly on Mon, Wed, Fri',
    )
    expect(formatCommitmentRepeat({ unit: 'week', every: 2, days: [1, 3] })).toBe(
      'every 2 weeks on Tue, Thu',
    )
  })
})
