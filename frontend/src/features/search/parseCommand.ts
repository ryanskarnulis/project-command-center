// Pure parser for the command bar. Turns the raw input string into a discriminated
// command so the rendering/dispatch logic in `CommandSearch` stays declarative and
// this logic stays unit-testable without rendering.
//
//   <text>            → { kind: 'search', query }      (no leading slash)
//   /new <text>       → { kind: 'new', text }
//   /done <query>     → { kind: 'done', query }
//   /  ·  /new  ·  /done  (no argument) → { kind: 'hint', verb }   (disabled hint)
//   /unknown ...      → { kind: 'search', query }      (unrecognized verb)
//
// The verb is matched case-insensitively and must be a whole word (separated from
// its argument by whitespace), so `/newfoo` is an unknown verb, not `/new foo`.

/** Which empty-argument state produced a hint row. */
export type HintVerb = 'root' | 'new' | 'done'

export type Command =
  | { kind: 'search'; query: string }
  | { kind: 'new'; text: string }
  | { kind: 'done'; query: string }
  | { kind: 'hint'; verb: HintVerb }

export function parseCommand(raw: string): Command {
  const trimmed = raw.trim()

  if (!trimmed.startsWith('/')) {
    return { kind: 'search', query: trimmed }
  }

  // Everything after the leading slash; split the verb from its argument on the
  // first run of whitespace.
  const body = trimmed.slice(1)
  if (body === '') {
    return { kind: 'hint', verb: 'root' }
  }

  const spaceIndex = body.search(/\s/)
  const verb = (spaceIndex === -1 ? body : body.slice(0, spaceIndex)).toLowerCase()
  const arg = spaceIndex === -1 ? '' : body.slice(spaceIndex + 1).trim()

  if (verb === 'new') {
    return arg === '' ? { kind: 'hint', verb: 'new' } : { kind: 'new', text: arg }
  }
  if (verb === 'done') {
    return arg === '' ? { kind: 'hint', verb: 'done' } : { kind: 'done', query: arg }
  }

  // Unrecognized verb: fall back to a plain search over the literal input.
  return { kind: 'search', query: trimmed }
}
