import { describe, expect, it } from 'vitest'
import { parseCommand } from './parseCommand'

describe('parseCommand', () => {
  it('treats plain text (no leading slash) as a search', () => {
    expect(parseCommand('firewall audit')).toEqual({
      kind: 'search',
      query: 'firewall audit',
    })
  })

  it('trims surrounding whitespace on a plain search', () => {
    expect(parseCommand('  firewall  ')).toEqual({
      kind: 'search',
      query: 'firewall',
    })
  })

  it('parses /done with a query into a completion command', () => {
    expect(parseCommand('/done audit firewall')).toEqual({
      kind: 'done',
      query: 'audit firewall',
    })
  })

  it('is case-insensitive on the verb', () => {
    expect(parseCommand('/Done thing')).toEqual({ kind: 'done', query: 'thing' })
  })

  it('trims the argument', () => {
    expect(parseCommand('/done    spaced out   ')).toEqual({
      kind: 'done',
      query: 'spaced out',
    })
  })

  it('returns a root hint for a bare slash', () => {
    expect(parseCommand('/')).toEqual({ kind: 'hint', verb: 'root' })
    expect(parseCommand('   /   ')).toEqual({ kind: 'hint', verb: 'root' })
  })

  it('returns a verb hint when /done has no argument', () => {
    expect(parseCommand('/done')).toEqual({ kind: 'hint', verb: 'done' })
  })

  it('falls back to search for an unrecognized verb', () => {
    expect(parseCommand('/foo bar')).toEqual({
      kind: 'search',
      query: '/foo bar',
    })
  })

  it('requires a whitespace separator so /donefoo is an unknown verb', () => {
    expect(parseCommand('/donefoo')).toEqual({
      kind: 'search',
      query: '/donefoo',
    })
  })
})
