import type { Project } from '../../../types/project'
import type { TaskPriority } from '../../../types/task'
import { addDaysISO, parseLocalDate, toISODate } from '../../../utils/dates'
import { parseDurationInput } from '../../../utils/duration'

// Pure token parser for the quick-add bar. Deterministic TS only — no AI; the
// command bar's `/new` remains the extraction tier for messy text.
//
// Recognized tokens (whitespace-separated):
//   !urgent !high !medium !low            → priority
//   #ops                                  → project, by unique name prefix
//   ~20m ~2h ~1day                        → estimate (parseDurationInput grammar)
//   fri · friday · today · tomorrow ·
//   next week · 2026-07-15                → due date
//
// The first token of each kind wins and is removed from the title. Anything
// unrecognized — a bad duration, an ambiguous project, a second priority —
// stays literal title text, so nothing is ever silently dropped.

export interface QuickAddDraft {
  title: string
  priority: TaskPriority | null
  dueDate: string | null
  projectId: number | null
  estimatedMinutes: number | null
}

const PRIORITY_WORDS: Record<string, TaskPriority> = {
  urgent: 'urgent',
  high: 'high',
  medium: 'medium',
  low: 'low',
}

const WEEKDAYS: Record<string, number> = {
  sun: 0, sunday: 0,
  mon: 1, monday: 1,
  tue: 2, tues: 2, tuesday: 2,
  wed: 3, weds: 3, wednesday: 3,
  thu: 4, thur: 4, thurs: 4, thursday: 4,
  fri: 5, friday: 5,
  sat: 6, saturday: 6,
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/

/** Lowercase and strip separators so `#homelab` matches "Home Lab". */
function normalizeName(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]/g, '')
}

/** Resolve `#token` to a project id: exact normalized name, else unique prefix. */
function matchProject(token: string, projects: Project[]): number | null {
  const query = normalizeName(token)
  if (query === '') return null
  const names = projects.map((p) => ({ id: p.id, name: normalizeName(p.name) }))
  const exact = names.filter((p) => p.name === query)
  if (exact.length > 0) return exact.length === 1 ? exact[0].id : null
  const prefixed = names.filter((p) => p.name.startsWith(query))
  return prefixed.length === 1 ? prefixed[0].id : null
}

/** A weekday word means the next future occurrence — never today (so "fri" on a Friday is +7). */
function nextWeekday(weekday: number, now: Date): string {
  const delta = ((weekday - now.getDay() + 7) % 7) || 7
  return addDaysISO(toISODate(now), delta)
}

export function parseQuickAdd(
  input: string,
  projects: Project[],
  now: Date = new Date(),
): QuickAddDraft {
  const tokens = input.split(/\s+/).filter((t) => t !== '')
  const kept: string[] = []
  const draft: QuickAddDraft = {
    title: '',
    priority: null,
    dueDate: null,
    projectId: null,
    estimatedMinutes: null,
  }
  const today = toISODate(now)

  for (let i = 0; i < tokens.length; i++) {
    const token = tokens[i]
    const rest = token.slice(1)

    if (token.startsWith('!') && draft.priority === null) {
      const priority = PRIORITY_WORDS[rest.toLowerCase()]
      if (priority !== undefined) {
        draft.priority = priority
        continue
      }
    }

    if (token.startsWith('#') && draft.projectId === null) {
      const id = matchProject(rest, projects)
      if (id !== null) {
        draft.projectId = id
        continue
      }
    }

    if (token.startsWith('~') && draft.estimatedMinutes === null) {
      const minutes = parseDurationInput(rest)
      if (typeof minutes === 'number') {
        draft.estimatedMinutes = minutes
        continue
      }
    }

    if (draft.dueDate === null) {
      const word = token.toLowerCase()
      if (word === 'next' && tokens[i + 1]?.toLowerCase() === 'week') {
        draft.dueDate = addDaysISO(today, 7)
        i++
        continue
      }
      if (word === 'today') {
        draft.dueDate = today
        continue
      }
      if (word === 'tomorrow') {
        draft.dueDate = addDaysISO(today, 1)
        continue
      }
      const weekday = WEEKDAYS[word]
      if (weekday !== undefined) {
        draft.dueDate = nextWeekday(weekday, now)
        continue
      }
      // Round-trip guards against calendar rollover ("2026-13-40" stays literal).
      if (ISO_DATE.test(token) && toISODate(parseLocalDate(token)) === token) {
        draft.dueDate = token
        continue
      }
    }

    kept.push(token)
  }

  draft.title = kept.join(' ')
  return draft
}
