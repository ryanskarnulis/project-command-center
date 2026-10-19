import { describe, expect, it } from 'vitest'
import type { Task } from '../../types/task'
import { estimateVsActual } from './estimateActual'

type Times = Pick<Task, 'workflow_status' | 'estimated_minutes' | 'actual_minutes'>

function done(estimated_minutes: number | null, actual_minutes: number | null): Times {
  return { workflow_status: 'done', estimated_minutes, actual_minutes }
}

describe('estimateVsActual', () => {
  it('reads an overrun as over', () => {
    expect(estimateVsActual(done(30, 45))).toEqual({
      estimateLabel: 'est 30m',
      actualLabel: 'took 45m',
      verdict: 'over',
      description: 'Took 45 minutes, 15 minutes over the 30 minutes estimate',
    })
  })

  it('reads a faster finish as under', () => {
    const result = estimateVsActual(done(60, 40))
    expect(result?.estimateLabel).toBe('est 1h')
    expect(result?.actualLabel).toBe('took 40m')
    expect(result?.verdict).toBe('under')
    expect(result?.description).toBe('Took 40 minutes, 20 minutes under the 1 hour estimate')
  })

  it('reads a match as equal', () => {
    const result = estimateVsActual(done(120, 120))
    expect(result?.estimateLabel).toBe('est 2h')
    expect(result?.actualLabel).toBe('took 2h')
    expect(result?.verdict).toBe('equal')
    expect(result?.description).toBe('Took 2 hours, exactly as estimated')
  })

  it('uses the shared short duration form past an hour', () => {
    const result = estimateVsActual(done(90, 180))
    expect(result?.estimateLabel).toBe('est 90m')
    expect(result?.actualLabel).toBe('took 3h')
  })

  it('has nothing to compare with an estimate only (an untimed completion)', () => {
    expect(estimateVsActual(done(30, null))).toBeNull()
  })

  it('has nothing to compare with an actual only', () => {
    expect(estimateVsActual(done(null, 45))).toBeNull()
  })

  it('has nothing to compare with neither', () => {
    expect(estimateVsActual(done(null, null))).toBeNull()
  })

  it('keeps a zero actual distinct from a missing one', () => {
    expect(estimateVsActual(done(30, 0))?.actualLabel).toBe('took 0m')
  })

  it('ignores tasks that are not done', () => {
    expect(
      estimateVsActual({ workflow_status: 'open', estimated_minutes: 30, actual_minutes: 45 }),
    ).toBeNull()
  })
})
