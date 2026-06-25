import { describe, expect, it } from 'vitest'
import {
  formatDuration,
  formatDurationInput,
  parseDurationInput,
  splitDuration,
  toMinutes,
} from './duration'

describe('toMinutes', () => {
  it('minutes', () => expect(toMinutes(30, 'minutes')).toBe(30))
  it('hours', () => expect(toMinutes(2, 'hours')).toBe(120))
  it('days', () => expect(toMinutes(1, 'days')).toBe(1440))
  it('weeks', () => expect(toMinutes(1, 'weeks')).toBe(10080))
  it('fractional hours round to nearest minute', () => expect(toMinutes(1.5, 'hours')).toBe(90))
})

describe('splitDuration', () => {
  it('exact weeks', () => expect(splitDuration(10080)).toEqual({ value: 1, unit: 'weeks' }))
  it('exact days', () => expect(splitDuration(2880)).toEqual({ value: 2, unit: 'days' }))
  it('exact hours', () => expect(splitDuration(120)).toEqual({ value: 2, unit: 'hours' }))
  it('minutes fallback', () => expect(splitDuration(45)).toEqual({ value: 45, unit: 'minutes' }))
  it('odd minutes stay as minutes', () => expect(splitDuration(90)).toEqual({ value: 90, unit: 'minutes' }))
})

describe('toMinutes/splitDuration round-trips', () => {
  const cases: [number, 'minutes' | 'hours' | 'days' | 'weeks'][] = [
    [30, 'minutes'],
    [2, 'hours'],
    [3, 'days'],
    [2, 'weeks'],
  ]
  for (const [value, unit] of cases) {
    it(`${value} ${unit}`, () => {
      const minutes = toMinutes(value, unit)
      expect(splitDuration(minutes)).toEqual({ value, unit })
    })
  }
})

describe('formatDuration', () => {
  it('null returns empty string', () => expect(formatDuration(null)).toBe(''))
  it('zero renders 0m', () => expect(formatDuration(0)).toBe('0m'))
  it('singular hour', () => expect(formatDuration(60)).toBe('1 hour'))
  it('plural hours', () => expect(formatDuration(120)).toBe('2 hours'))
  it('singular day', () => expect(formatDuration(1440)).toBe('1 day'))
  it('plural days', () => expect(formatDuration(2880)).toBe('2 days'))
  it('singular week', () => expect(formatDuration(10080)).toBe('1 week'))
  it('odd minutes fallback', () => expect(formatDuration(45)).toBe('45 minutes'))
})

describe('parseDurationInput', () => {
  it('clears empty and none values', () => {
    expect(parseDurationInput('')).toBeNull()
    expect(parseDurationInput('none')).toBeNull()
    expect(parseDurationInput('no estimate')).toBeNull()
  })

  it('parses compact estimates', () => {
    expect(parseDurationInput('30m')).toBe(30)
    expect(parseDurationInput('2h')).toBe(120)
    expect(parseDurationInput('1d')).toBe(1440)
    expect(parseDurationInput('1w')).toBe(10080)
  })

  it('parses word estimates and plain numbers as minutes', () => {
    expect(parseDurationInput('45 min')).toBe(45)
    expect(parseDurationInput('2 hours')).toBe(120)
    expect(parseDurationInput('1 day')).toBe(1440)
    expect(parseDurationInput('30')).toBe(30)
  })

  it('rejects invalid estimates', () => {
    expect(parseDurationInput('later')).toBeUndefined()
    expect(parseDurationInput('0m')).toBeUndefined()
  })
})

describe('formatDurationInput', () => {
  it('uses the friendly duration label', () => {
    expect(formatDurationInput(120)).toBe('2 hours')
    expect(formatDurationInput(null)).toBe('')
  })
})
