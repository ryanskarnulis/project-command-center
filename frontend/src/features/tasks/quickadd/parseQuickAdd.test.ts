import { describe, expect, it } from 'vitest'
import type { Project } from '../../../types/project'
import { parseQuickAdd } from './parseQuickAdd'

function project(id: number, name: string): Project {
  return {
    id,
    name,
    description: null,
    system_key: null,
    sort_order: 0,
    is_protected: false,
    created_at: '2026-06-01T10:00:00Z',
    updated_at: '2026-06-01T10:00:00Z',
  }
}

const projects = [project(1, 'Ops'), project(2, 'Home Lab'), project(3, 'Homework')]

// Wednesday, July 1 2026 — keeps every relative-date expectation deterministic.
const now = new Date(2026, 6, 1)

describe('parseQuickAdd', () => {
  it('parses the full token grammar in one line', () => {
    const draft = parseQuickAdd('Renew TLS cert fri !high #ops ~20m @ryan', projects, now)
    expect(draft).toEqual({
      title: 'Renew TLS cert',
      priority: 'high',
      dueDate: '2026-07-03',
      projectId: 1,
      estimatedMinutes: 20,
      assignee: 'ryan',
    })
  })

  it('leaves plain text untouched', () => {
    const draft = parseQuickAdd('Fix the VPN', projects, now)
    expect(draft.title).toBe('Fix the VPN')
    expect(draft.priority).toBeNull()
    expect(draft.dueDate).toBeNull()
    expect(draft.projectId).toBeNull()
    expect(draft.estimatedMinutes).toBeNull()
    expect(draft.assignee).toBeNull()
  })

  it('resolves a weekday to the next future occurrence, never today', () => {
    // `now` is a Wednesday: "fri" is two days out, "wed" rolls a full week.
    expect(parseQuickAdd('a fri', projects, now).dueDate).toBe('2026-07-03')
    expect(parseQuickAdd('a wed', projects, now).dueDate).toBe('2026-07-08')
  })

  it('parses today, tomorrow, next week, and ISO dates', () => {
    expect(parseQuickAdd('a today', projects, now).dueDate).toBe('2026-07-01')
    expect(parseQuickAdd('a tomorrow', projects, now).dueDate).toBe('2026-07-02')
    const nextWeek = parseQuickAdd('a next week', projects, now)
    expect(nextWeek.dueDate).toBe('2026-07-08')
    expect(nextWeek.title).toBe('a')
    expect(parseQuickAdd('a 2026-08-15', projects, now).dueDate).toBe('2026-08-15')
  })

  it('keeps an impossible ISO date as literal title text', () => {
    const draft = parseQuickAdd('pay 2026-13-40 invoice', projects, now)
    expect(draft.dueDate).toBeNull()
    expect(draft.title).toBe('pay 2026-13-40 invoice')
  })

  it('matches a project by normalized name across spaces and case', () => {
    expect(parseQuickAdd('a #homelab', projects, now).projectId).toBe(2)
    expect(parseQuickAdd('a #OPS', projects, now).projectId).toBe(1)
  })

  it('keeps an ambiguous project prefix as literal title text', () => {
    // "hom" prefixes both Home Lab and Homework.
    const draft = parseQuickAdd('a #hom', projects, now)
    expect(draft.projectId).toBeNull()
    expect(draft.title).toBe('a #hom')
  })

  it('keeps unknown priority and bad estimate tokens as literal title text', () => {
    const draft = parseQuickAdd('escalate !asap ~soon', projects, now)
    expect(draft.priority).toBeNull()
    expect(draft.estimatedMinutes).toBeNull()
    expect(draft.title).toBe('escalate !asap ~soon')
  })

  it('first token of a kind wins; later duplicates stay literal', () => {
    const draft = parseQuickAdd('ship !high !low mon tue', projects, now)
    expect(draft.priority).toBe('high')
    expect(draft.dueDate).toBe('2026-07-06')
    expect(draft.title).toBe('ship !low tue')
  })

  it('ignores a bare @ sigil', () => {
    const draft = parseQuickAdd('ping @ later', projects, now)
    expect(draft.assignee).toBeNull()
    expect(draft.title).toBe('ping @ later')
  })
})
