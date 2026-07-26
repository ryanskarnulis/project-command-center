/**
 * Single policy for dynamic route ids: digit-only, positive, no sign, no
 * decimal point, no whitespace. `Number('nope')` is NaN and `Number(' 1 ')`
 * is 1, so route boundaries must not rely on `Number(...)` alone.
 *
 * Also rejects anything JavaScript cannot represent exactly: `Number(...)`
 * silently rounds past `Number.MAX_SAFE_INTEGER`, so a pasted 24-digit id would
 * otherwise become a *different* id before the API URL was built (#182). The API
 * rejects out-of-range ids with a 422; this guard renders the in-app Not Found
 * surface rather than requesting an id that was already corrupted.
 */
export function isValidRouteId(value: string | undefined): value is string {
  if (value === undefined || !/^\d+$/.test(value)) return false
  const parsed = Number(value)
  if (parsed <= 0 || !Number.isSafeInteger(parsed)) return false
  // Round-trip so only an exactly representable id passes. Leading zeros are
  // stripped first, since `007` has always been accepted as `7`.
  return String(parsed) === value.replace(/^0+(?=\d)/, '')
}

/** Returns the parsed positive integer id, or null when the param is invalid. */
export function parseRouteId(value: string | undefined): number | null {
  return isValidRouteId(value) ? Number(value) : null
}
