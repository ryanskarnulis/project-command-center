import { describe, expect, it } from 'vitest'
import { columnAtClientX } from './useBucketDrag'

/**
 * A grid stub whose `.gantt-col-bg` cells return fixed rects, so the pure
 * hit-test can be exercised without real layout (jsdom has none).
 * Columns are laid out edge-to-edge: [0,50), [50,100), [100,150).
 */
function gridWith(rects: Array<{ left: number; right: number }>): HTMLElement {
  const cells = rects.map((r) => ({
    getBoundingClientRect: () => ({ left: r.left, right: r.right }) as DOMRect,
  }))
  return {
    querySelectorAll: (sel: string) =>
      sel === '.gantt-col-bg' ? (cells as unknown as NodeListOf<Element>) : ([] as never),
  } as unknown as HTMLElement
}

const GRID = gridWith([
  { left: 0, right: 50 },
  { left: 50, right: 100 },
  { left: 100, right: 150 },
])

describe('columnAtClientX', () => {
  it('returns the index of the column containing the x', () => {
    expect(columnAtClientX(GRID, 25)).toBe(0)
    expect(columnAtClientX(GRID, 75)).toBe(1)
    expect(columnAtClientX(GRID, 125)).toBe(2)
  })

  it('treats the left edge as inside and the right edge as the next column', () => {
    expect(columnAtClientX(GRID, 0)).toBe(0)
    expect(columnAtClientX(GRID, 50)).toBe(1) // right edge of col 0 = left edge of col 1
  })

  it('returns -1 when the x is off the columns', () => {
    expect(columnAtClientX(GRID, -10)).toBe(-1)
    expect(columnAtClientX(GRID, 150)).toBe(-1) // right edge of last col is exclusive
    expect(columnAtClientX(GRID, 999)).toBe(-1)
  })

  it('returns -1 for a null grid', () => {
    expect(columnAtClientX(null, 25)).toBe(-1)
  })
})
