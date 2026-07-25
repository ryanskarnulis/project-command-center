/**
 * Single policy for dynamic route ids: digit-only, positive, no sign, no
 * decimal point, no whitespace. `Number('nope')` is NaN and `Number(' 1 ')`
 * is 1, so route boundaries must not rely on `Number(...)` alone.
 */
export function isValidRouteId(value: string | undefined): value is string {
  return value !== undefined && /^\d+$/.test(value) && Number(value) > 0
}

/** Returns the parsed positive integer id, or null when the param is invalid. */
export function parseRouteId(value: string | undefined): number | null {
  return isValidRouteId(value) ? Number(value) : null
}
