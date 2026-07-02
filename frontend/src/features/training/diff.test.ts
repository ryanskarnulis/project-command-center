import { describe, expect, it } from 'vitest'
import { diffLines } from './diff'

describe('diffLines', () => {
  it('marks identical texts as all-equal', () => {
    expect(diffLines('a\nb', 'a\nb')).toEqual([
      { type: 'eq', text: 'a' },
      { type: 'eq', text: 'b' },
    ])
  })

  it('emits del for removed lines and add for inserted lines', () => {
    expect(diffLines('a\nb\nc', 'a\nx\nc')).toEqual([
      { type: 'eq', text: 'a' },
      { type: 'del', text: 'b' },
      { type: 'add', text: 'x' },
      { type: 'eq', text: 'c' },
    ])
  })

  it('handles pure additions and pure deletions at the ends', () => {
    expect(diffLines('a', 'a\nb')).toEqual([
      { type: 'eq', text: 'a' },
      { type: 'add', text: 'b' },
    ])
    expect(diffLines('a\nb', 'b')).toEqual([
      { type: 'del', text: 'a' },
      { type: 'eq', text: 'b' },
    ])
  })
})
