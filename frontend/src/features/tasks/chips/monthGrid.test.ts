import { describe, expect, it } from 'vitest'
import { buildMonthGrid } from './monthGrid'

describe('buildMonthGrid', () => {
  it('covers July 2026 with Sunday-first alignment', () => {
    // Jul 1 2026 is a Wednesday → 3 leading nulls (Su Mo Tu).
    const weeks = buildMonthGrid(2026, 6)
    expect(weeks[0]).toEqual([
      null,
      null,
      null,
      '2026-07-01',
      '2026-07-02',
      '2026-07-03',
      '2026-07-04',
    ])
    const days = weeks.flat().filter((c) => c !== null)
    expect(days).toHaveLength(31)
    expect(days[0]).toBe('2026-07-01')
    expect(days[30]).toBe('2026-07-31')
  })

  it('handles leap-year February', () => {
    const days = buildMonthGrid(2028, 1).flat().filter((c) => c !== null)
    expect(days).toHaveLength(29)
    expect(days[28]).toBe('2028-02-29')
  })

  it('always returns whole weeks of 7', () => {
    for (const [year, month] of [
      [2026, 6],
      [2028, 1],
      [2026, 10],
    ]) {
      for (const week of buildMonthGrid(year, month)) {
        expect(week).toHaveLength(7)
      }
    }
  })
})
